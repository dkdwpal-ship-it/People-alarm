"""추출된 업무와 완료 기록을 JSON 파일로 보관."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import Task


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.tasks_path = data_dir / "tasks.json"
        self.done_path = data_dir / "done.json"
        self._data = self._load(self.tasks_path, {"documents": {}, "tasks": []})
        self._done: list[str] = self._load(self.done_path, [])

    @staticmethod
    def _load(path: Path, default):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return default

    @staticmethod
    def _write_atomic(path: Path, obj) -> None:
        # 대시보드가 읽는 도중 반쯤 쓰인 파일을 보지 않도록 임시 파일에 쓰고 바꿔치기한다.
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._write_atomic(self.tasks_path, self._data)
        self._write_atomic(self.done_path, sorted(set(self._done)))

    # --- 문서 / 업무 ---
    def is_unchanged(self, path: Path) -> bool:
        doc = self._data["documents"].get(path.name)
        return doc is not None and doc.get("sha256") == file_hash(path)

    def replace_document_tasks(self, path: Path, tasks: list[Task], sha256: str | None = None) -> None:
        """문서 하나의 업무를 새 추출 결과로 교체한다.

        sha256은 분석을 시작할 때의 해시. 분석 도중 파일이 바뀌었다면 다음 분석 때 다시 잡힌다.
        """
        self._data["tasks"] = [t for t in self._data["tasks"] if t["source"] != path.name]
        self._data["tasks"].extend(t.model_dump() for t in tasks)
        doc = self._data["documents"].setdefault(path.name, {})
        doc["sha256"] = sha256 or file_hash(path)  # 제외 여부(excluded)는 다시 분석해도 유지

    def remove_missing_documents(self, present: set[str]) -> list[str]:
        removed = [name for name in self._data["documents"] if name not in present]
        for name in removed:
            del self._data["documents"][name]
        self._data["tasks"] = [t for t in self._data["tasks"] if t["source"] in present]
        return removed

    @property
    def tasks(self) -> list[Task]:
        """일정에 쓰는 업무 (제외한 문서의 업무는 빠짐)."""
        excluded = self.excluded_documents
        return [Task.model_validate(t) for t in self._data["tasks"] if t["source"] not in excluded]

    @property
    def all_tasks(self) -> list[Task]:
        """분석된 전체 업무 (제외한 문서 포함)."""
        return [Task.model_validate(t) for t in self._data["tasks"]]

    # --- 문서 제외 / 삭제 ---
    @property
    def excluded_documents(self) -> set[str]:
        return {name for name, d in self._data["documents"].items() if d.get("excluded")}

    def is_excluded(self, name: str) -> bool:
        return name in self.excluded_documents

    def set_excluded(self, name: str, excluded: bool) -> None:
        """문서를 일정에서 빼거나 다시 넣는다. 분석 결과는 지우지 않는다."""
        doc = self._data["documents"].setdefault(name, {})
        if excluded:
            doc["excluded"] = True
        else:
            doc.pop("excluded", None)

    def forget_document(self, name: str) -> int:
        """문서 기록과 그 문서의 업무를 모두 지운다. 지운 업무 수를 반환."""
        before = len(self._data["tasks"])
        self._data["tasks"] = [t for t in self._data["tasks"] if t["source"] != name]
        self._data["documents"].pop(name, None)
        return before - len(self._data["tasks"])

    # --- 완료 기록 ---
    @staticmethod
    def _key(task_id: str, due: str) -> str:
        return f"{task_id}@{due}"

    def is_done(self, task_id: str, due: str) -> bool:
        return self._key(task_id, due) in self._done

    def mark_done(self, task_id: str, due: str) -> None:
        self._done.append(self._key(task_id, due))

    def unmark_done(self, task_id: str, due: str) -> None:
        key = self._key(task_id, due)
        self._done = [k for k in self._done if k != key]

    @property
    def done_keys(self) -> list[str]:
        return sorted(set(self._done))
