from __future__ import annotations

import shutil
from datetime import date, datetime, timezone
from pathlib import Path

from filelock import FileLock
from tinydb import Query, TinyDB


class Database:
    """Repository over a single-file TinyDB store.

    Tables: interests (name, last_checked), runs (timestamp, agent, failures),
    results (run_id, name, window_start, window_end, markdown, edited_markdown).
    """

    def __init__(self, path: Path, lock_timeout: float = 10):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(f"{self.path}.lock", timeout=lock_timeout)
        with self._lock:
            self._db = self._open_with_recovery()

    def _open_with_recovery(self) -> TinyDB:
        db = TinyDB(self.path)
        try:
            db.tables()
            return db
        except ValueError:
            db.close()
            shutil.move(self.path, self.path.with_name(self.path.name + ".bak"))
            return TinyDB(self.path)

    @property
    def _interests(self):
        return self._db.table("interests", cache_size=0)

    @property
    def _runs(self):
        return self._db.table("runs", cache_size=0)

    @property
    def _results(self):
        return self._db.table("results", cache_size=0)

    @property
    def _models(self):
        return self._db.table("models", cache_size=0)

    def get_last_checked(self, name: str) -> date | None:
        with self._lock:
            doc = self._interests.get(Query().name == name)
            if doc and doc.get("last_checked"):
                return date.fromisoformat(doc["last_checked"])
            return None

    def set_last_checked(self, name: str, day: date) -> None:
        with self._lock:
            self._interests.upsert(
                {"name": name, "last_checked": day.isoformat()}, Query().name == name
            )

    def get_models(self, agent: str) -> dict | None:
        with self._lock:
            doc = self._models.get(Query().agent == agent)
            return dict(doc) if doc else None

    def set_models(self, agent: str, models: list[str]) -> None:
        with self._lock:
            self._models.upsert(
                {
                    "agent": agent,
                    "models": list(models),
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                },
                Query().agent == agent,
            )

    def create_run(self, agent: str) -> int:
        with self._lock:
            return self._runs.insert(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "agent": agent,
                    "failures": [],
                }
            )

    def add_result(
        self, run_id: int, name: str, window_start: date, window_end: date, markdown: str
    ) -> None:
        with self._lock:
            self._results.insert(
                {
                    "run_id": run_id,
                    "name": name,
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                    "markdown": markdown,
                    "edited_markdown": None,
                }
            )

    def add_failure(
        self, run_id: int, name: str, reason: str, raw_output: str | None = None
    ) -> None:
        with self._lock:
            run = self._runs.get(doc_id=run_id)
            failures = list(run["failures"]) + [
                {
                    "name": name,
                    "reason": reason,
                    "raw_output": raw_output[:4000] if raw_output else None,
                }
            ]
            self._runs.update({"failures": failures}, doc_ids=[run_id])

    def get_run(self, run_id: int) -> dict | None:
        with self._lock:
            doc = self._runs.get(doc_id=run_id)
            if doc is None:
                return None
            return {"id": run_id, **doc}

    def latest_run_id(self) -> int | None:
        with self._lock:
            ids = [doc.doc_id for doc in self._runs.all()]
            return max(ids) if ids else None

    def results_for_run(self, run_id: int) -> list[dict]:
        with self._lock:
            docs = self._results.search(Query().run_id == run_id)
            return [{"id": d.doc_id, **d} for d in sorted(docs, key=lambda d: d.doc_id)]

    def list_runs(self) -> list[dict]:
        with self._lock:
            runs = [{"id": d.doc_id, **d} for d in self._runs.all()]
            return sorted(runs, key=lambda r: r["id"], reverse=True)

    def set_edited_markdown(self, result_id: int, markdown: str | None) -> None:
        with self._lock:
            self._results.update({"edited_markdown": markdown}, doc_ids=[result_id])

    def search_results(self, text: str, limit: int = 50) -> list[dict]:
        with self._lock:
            needle = text.strip().lower()
            if not needle:
                return []
            hits: list[dict] = []
            for doc in reversed(self._results.all()):
                body = doc.get("edited_markdown") or doc["markdown"]
                if needle in f"{doc['name']}\n{body}".lower():
                    hits.append({"id": doc.doc_id, **doc})
                    if len(hits) >= limit:
                        break
            return hits

    def close(self) -> None:
        with self._lock:
            self._db.close()
