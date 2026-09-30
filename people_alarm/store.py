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

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.done_path.write_text(
            json.dumps(sorted(set(self._done)), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # --- 문서 / 업무 ---
    def is_unchanged(self, path: Path) -> bool:
        doc = self._data["documents"].get(path.name)
        return doc is not None and doc["sha256"] == file_hash(path)

    def replace_document_tasks(self, path: Path, tasks: list[Task]) -> None:
        """문서 하나의 업무를 새 추출 결과로 교체한다."""
        self._data["tasks"] = [t for t in self._data["tasks"] if t["source"] != path.name]
        self._data["tasks"].extend(t.model_dump() for t in tasks)
        self._data["documents"][path.name] = {"sha256": file_hash(path)}

    def remove_missing_documents(self, present: set[str]) -> list[str]:
        removed = [name for name in self._data["documents"] if name not in present]
        for name in removed:
            del self._data["documents"][name]
        self._data["tasks"] = [t for t in self._data["tasks"] if t["source"] in present]
        return removed

    @property
    def tasks(self) -> list[Task]:
        return [Task.model_validate(t) for t in self._data["tasks"]]

    # --- 완료 기록 ---
    @staticmethod
    def _key(task_id: str, due: str) -> str:
        return f"{task_id}@{due}"

    def is_done(self, task_id: str, due: str) -> bool:
        return self._key(task_id, due) in self._done

    def mark_done(self, task_id: str, due: str) -> None:
        self._done.append(self._key(task_id, due))
