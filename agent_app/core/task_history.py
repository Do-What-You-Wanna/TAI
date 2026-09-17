"""任务历史存储：把每次执行的任务记录持久化到本地 JSON。

条目结构（TaskRecord）：
- id / created_at / task_text / status / assignments / results / summary / duration_ms
后面会作为“任务历史”页的数据来源。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from .router import Assignment

HISTORY_FILE = Path(__file__).resolve().parent.parent / "config" / "tasks_history.json"


class TaskHistory:
    """简单、线程安全的任务历史记录器。"""

    def __init__(self, path: Path = HISTORY_FILE) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, records: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

    def add(self, record: dict) -> dict:
        """追加一条记录（自动补 id 与时间戳），返回带 id 的记录。"""
        record.setdefault("id", uuid.uuid4().hex[:12])
        record.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        with self._lock:
            records = self._read()
            records.append(record)
            self._write(records)
        return record

    def all(self, reverse: bool = True) -> list[dict]:
        """返回全部记录；默认最新在前。"""
        with self._lock:
            recs = self._read()
        return list(reversed(recs)) if reverse else recs

    def get(self, record_id: str) -> dict | None:
        with self._lock:
            for r in self._read():
                if r.get("id") == record_id:
                    return r
        return None

    def clear(self) -> None:
        with self._lock:
            self._write([])


def assignment_to_dict(a: Assignment) -> dict:
    """把分配结果转成可持久化字典。"""
    return {
        "task_index": a.task.index,
        "task_title": a.task.title,
        "task_skill": a.task.skill,
        "model_key": a.model_key,
        "reason": a.reason,
        "score": a.score,
    }