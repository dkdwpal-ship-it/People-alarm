"""업로드된 문서를 백그라운드에서 한 건씩 분석하는 작업 큐."""

from __future__ import annotations

import datetime as dt
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .config import active
from .llm import LLMConnectionError, LLMHTTPError, LLMTimeoutError
from .loaders import SUPPORTED_SUFFIXES, check_format, find_documents
from .models import Task
from .store import Store, file_hash

Extractor = Callable[[Path, dt.date], list[Task]]
MAX_UPLOAD_BYTES = 32 * 1024 * 1024


def friendly_error(e: Exception) -> str:
    """분석 실패 사유를 사용자에게 보여줄 문장으로."""
    cfg = active()
    if isinstance(e, LLMTimeoutError):
        return f"사내 LLM 서버 응답이 {cfg.timeout}초 안에 오지 않았습니다. 서버가 바쁘면 잠시 후 다시 분석하세요."
    if isinstance(e, LLMConnectionError):
        return f"사내 LLM 서버({cfg.base_url})에 연결하지 못했습니다. 서버가 켜져 있는지, 사내망에 연결되어 있는지 확인하세요."
    if isinstance(e, LLMHTTPError):
        msg = e.message.lower()
        if e.status == 404 and "model" in msg:
            return f"LLM 서버에서 모델 '{cfg.model}'을(를) 찾지 못했습니다. `people-alarm check-llm`으로 모델 이름을 확인하세요."
        if e.status == 404:
            return f"LLM 서버 주소가 맞지 않습니다({cfg.base_url}). 주소가 /v1로 끝나는지 확인하세요."
        if "context" in msg and "length" in msg:
            return "문서 조각이 모델의 최대 입력 길이를 넘었습니다. PEOPLE_ALARM_LLM_CHUNK_CHARS 값을 줄여 주세요."
        if e.status == 400:
            return f"LLM 서버가 요청을 거절했습니다: {e.message}"
        return f"LLM 서버 오류(HTTP {e.status})입니다. 잠시 후 다시 분석하세요."
    return str(e) or e.__class__.__name__


def safe_filename(raw: str) -> str:
    """경로 조작을 막고 지원 형식만 허용한 파일명. 허용되지 않으면 ValueError."""
    name = Path(raw.replace("\\", "/")).name.strip()
    if not name or name.startswith((".", "~$")):
        raise ValueError("파일 이름이 올바르지 않습니다.")
    if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
        allowed = " ".join(sorted(SUPPORTED_SUFFIXES))
        raise ValueError(f"지원하지 않는 형식입니다. 가능한 형식: {allowed}")
    return name


@dataclass
class Job:
    name: str
    status: str = "queued"  # queued | running | done | error
    task_count: int | None = None
    error: str | None = None
    updated: float = field(default_factory=time.time)


class Analyzer:
    def __init__(self, data_dir: Path, docs_dir: Path, extract: Extractor, today: Callable[[], dt.date]):
        self.data_dir = data_dir
        self.docs_dir = docs_dir
        self.extract = extract
        self.today = today
        self.lock = threading.Lock()  # tasks.json / done.json 쓰기 보호
        self._jobs: dict[str, Job] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._work, daemon=True).start()

    # --- 요청 처리 ---
    def save_upload(self, raw_name: str, body: bytes) -> str:
        name = safe_filename(raw_name)
        if len(body) > MAX_UPLOAD_BYTES:
            raise ValueError("파일이 너무 큽니다 (최대 32MB).")
        check_format(name, body)  # DRM·암호·옛 형식 파일은 저장하기 전에 이유와 함께 거절
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.docs_dir / f".{name}.uploading"
        tmp.write_bytes(body)
        tmp.replace(self.docs_dir / name)
        self.submit(name)
        return name

    def submit(self, name: str) -> None:
        if not (self.docs_dir / name).is_file():
            raise FileNotFoundError(name)
        with self.lock:
            job = self._jobs.get(name)
            if job and job.status in ("queued", "running"):
                return  # 이미 대기/분석 중
            self._jobs[name] = Job(name)
        self._queue.put(name)

    def documents(self) -> list[dict]:
        """docs 폴더의 문서 목록과 분석 상태."""
        with self.lock:  # 저장소와 작업 상태를 같은 시점으로 읽는다
            store = Store(self.data_dir)
            jobs = {k: asdict(v) for k, v in self._jobs.items()}
        counts: dict[str, int] = {}
        for t in store.tasks:
            counts[t.source] = counts.get(t.source, 0) + 1
        out = []
        for path in find_documents(self.docs_dir) if self.docs_dir.exists() else []:
            stat = path.stat()
            out.append({
                "name": path.name,
                "size": stat.st_size,
                "modified": dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="minutes"),
                "analyzed": store.is_unchanged(path),
                "taskCount": counts.get(path.name, 0),
                "job": jobs.get(path.name),
            })
        return sorted(out, key=lambda d: d["modified"], reverse=True)

    # --- 작업자 ---
    def _set(self, name: str, **fields) -> None:
        with self.lock:
            job = self._jobs[name]
            for k, v in fields.items():
                setattr(job, k, v)
            job.updated = time.time()

    def _work(self) -> None:
        while True:
            name = self._queue.get()
            path = self.docs_dir / name
            self._set(name, status="running")
            try:
                digest = file_hash(path)
                tasks = self.extract(path, self.today())  # 오래 걸리므로 잠금 밖에서
                with self.lock:
                    store = Store(self.data_dir)  # 그 사이 바뀐 완료 기록 등을 다시 읽음
                    store.replace_document_tasks(path, tasks, sha256=digest)
                    store.save()
                self._set(name, status="done", task_count=len(tasks), error=None)
            except Exception as e:  # 한 문서 실패가 서버를 멈추지 않도록
                self._set(name, status="error", error=friendly_error(e))
            finally:
                self._queue.task_done()
