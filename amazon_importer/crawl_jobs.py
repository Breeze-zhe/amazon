"""Durable job status and an exclusive crawl lease shared by API processes."""
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
from threading import Event, Lock, Thread
from uuid import uuid4


class CrawlBusy(RuntimeError):
    pass


@contextmanager
def crawl_lease(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CrawlBusy("已有抓取任务运行，请先暂停或等待完成") from exc
        yield
    finally:
        # Closing the descriptor releases the OS lease even after a crash.
        handle.close()


class CrawlJobs:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.mutex = Lock()
        self.signals = {}

    @property
    def lease_path(self):
        return self.directory / "crawl.lock"

    @contextmanager
    def db(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.directory / "jobs.sqlite3", timeout=15)
        connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL,
            snapshot TEXT NOT NULL, created TEXT NOT NULL)""")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def update(self, job_id, **values):
        with self.mutex, self.db() as db:
            # Serialize read-modify-write with stop requests from other Flask
            # processes too, so a progress update cannot erase "stopping".
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT snapshot FROM jobs WHERE id=?", (job_id,)).fetchone()
            snapshot = json.loads(row[0])
            active = {"queued", "running", "stopping"}
            if values.pop("recover_if_active", False) and snapshot["status"] not in active:
                return snapshot
            if values.get("status") == "stopping" and snapshot["status"] not in active:
                return snapshot
            if snapshot["status"] == "stopping" and values.get("status") == "running":
                values["status"] = "stopping"
            if values.pop("increment", False):
                values["processed"] = snapshot["processed"] + 1
            snapshot.update(values, updated=datetime.now().astimezone().isoformat())
            db.execute("UPDATE jobs SET snapshot=?, status=? WHERE id=?",
                       (json.dumps(snapshot, ensure_ascii=False), snapshot["status"], job_id))
            return snapshot

    def get(self, job_id=None):
        with self.db() as db:
            row = db.execute(
                "SELECT id, snapshot FROM jobs WHERE id=?" if job_id else
                "SELECT id, snapshot FROM jobs ORDER BY created DESC LIMIT 1",
                (job_id,) if job_id else (),
            ).fetchone()
        if not row:
            return None
        snapshot = json.loads(row[1])
        if snapshot["status"] in {"queued", "running", "stopping"}:
            try:
                with crawl_lease(self.lease_path):
                    snapshot = self.update(row[0], status="interrupted", phase="interrupted", recover_if_active=True)
            except CrawlBusy:
                pass
        return snapshot

    def submit(self, payload, kind, execute):
        # Acquire before creating the job, so concurrent API processes cannot
        # both accept a crawl that would overwrite the same daily records.
        lease = crawl_lease(self.lease_path)
        lease.__enter__()
        job_id = uuid4().hex
        signal = Event()
        snapshot = {"id": job_id, "kind": kind, "status": "queued", "phase": "queued",
                    "processed": 0, "total": 0, "last_item": None}
        try:
            with self.db() as db:
                db.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, ?)",
                           (job_id, "queued", json.dumps(payload), json.dumps(snapshot),
                            datetime.now().astimezone().isoformat()))
            self.signals[job_id] = signal

            def run():
                try:
                    self.update(job_id, status="running")
                    def progress(record=None, **values):
                        if record:
                            # Increment under the same mutex used for updates;
                            # callbacks arrive from multiple site workers.
                            values["increment"] = True
                            values["last_item"] = {key: record.get(key) for key in ("data_id", "asin", "country_code", "db_action")}
                        self.update(job_id, **values)
                        with self.db() as db:
                            state = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
                        if state == "stopping":
                            signal.set()
                    result = execute(payload, kind, progress, signal)
                    self.update(job_id, status="paused" if signal.is_set() else "finished", result=result)
                except Exception as exc:
                    self.update(job_id, status="failed", phase="failed", error=str(exc))
                finally:
                    self.signals.pop(job_id, None)
                    lease.__exit__(None, None, None)

            Thread(target=run, name=f"crawl-{job_id[:8]}", daemon=True).start()
        except BaseException:
            lease.__exit__(None, None, None)
            raise
        return snapshot

    def stop(self, job_id):
        snapshot = self.get(job_id)
        if snapshot and snapshot["status"] in {"queued", "running", "stopping"}:
            snapshot = self.update(job_id, status="stopping", pause_reason="user_requested")
            signal = self.signals.get(job_id)
            if signal:
                signal.set()
        return snapshot

    def payload(self, job_id):
        with self.db() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        return json.loads(row[0]) if row else {}
