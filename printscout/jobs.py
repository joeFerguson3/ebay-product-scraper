# Runs tasks in the background

from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Job:
    kind: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10]) # generate unique id
    status: str = "running"  # running, done, failed, cancelled
    progress: float = 0.0
    message: str = ""
    stop_event: threading.Event = field(default_factory=threading.Event)
    logs: deque = field(default_factory=lambda: deque(maxlen=400))

    # message is the latest line
    def log(self, message: str) -> None:
        self.message = message
        self.logs.append(message)

    # set progress to a value between 0.0 and 1.0
    def set_progress(self, value: float) -> None:
        self.progress = max(0.0, min(1.0, value))

    # turns job into a dictionary
    def to_dict(self, with_logs: bool = False) -> dict:
        d = {"id": self.id, "kind": self.kind, "status": self.status,
             "progress": self.progress, "message": self.message}
        if with_logs:
            d["logs"] = list(self.logs)
        return d


# 
class JobManager:
    def __init__(self) -> None:
        # all jobs with id
        self.all: dict[str, Job] = {}

    def start(self, kind: str, fn: Callable[[Job], None]) -> Job:
        for j in list(self.all.values()):
            if j.kind == kind and j.status == "running":
                raise RuntimeError(f"A {kind} job is already running")
        job = Job(kind=kind)
        self.all[job.id] = job

        def runner() -> None:
            try:
                fn(job)
                job.status = "done"
            except Exception as exc:
                job.status = "failed"
                job.log(f"Failed: {exc}")
           
            if job.stop_event.is_set():
                job.status = "cancelled"
            if job.status == "done":
                job.progress = 1.0

        threading.Thread(target=runner, daemon=True).start()
        return job

    # asks a job to stop
    def stop(self, job_id: str) -> bool:
        job = self.all.get(job_id)
        if not job or job.status != "running":
            return False
        job.stop_event.set()
        job.log("Stopping")
        return True
