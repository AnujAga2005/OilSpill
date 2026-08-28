"""Background jobs for the API.

Detection and drift take tens of seconds on a full scene, which is far too long to hold
an HTTP connection open for. Every long stage therefore runs as a job: the request
returns an id immediately, the client polls, and the job carries its own progress log so
the dashboard can show what is happening rather than an indeterminate spinner.

Deliberately in-process and bounded: a fixed worker pool, a capped log per job, and a
capped number of retained finished jobs. There is no external queue to install and no
unbounded growth if a client walks away mid-run.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any, Callable

MAX_LOG_LINES = 400
MAX_FINISHED = 64

Work = Callable[[Callable[[str], None]], Any]


@dataclass
class Job:
    """One unit of work and everything the client is allowed to see about it."""

    id: str
    kind: str
    scene: str
    state: str = "queued"  # queued | running | done | failed | cancelled
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    log: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    result: Any = None
    error: str | None = None
    detail: str | None = None
    _cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def to_dict(self, include_result: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "scene": self.scene,
            "state": self.state,
            "createdEpoch": round(self.created, 3),
            "elapsedSeconds": round(
                (self.finished or time.time()) - (self.started or self.created), 3
            ),
            "log": list(self.log),
            "message": self.log[-1] if self.log else None,
            "error": self.error,
            "detail": self.detail,
        }
        if include_result and self.state == "done":
            payload["result"] = self.result
        return payload


class JobRunner:
    """A tiny fixed-size thread pool.

    One worker by default: the model is NumPy and already saturates the available BLAS
    threads, so running two inferences concurrently makes both slower rather than either
    faster. The pool exists to get work off the request thread, not to add parallelism.
    """

    def __init__(self, workers: int = 1) -> None:
        self._queue: Queue[str] = Queue()
        self._jobs: dict[str, Job] = {}
        self._work: dict[str, Work] = {}
        self._order: deque[str] = deque()
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._threads = [
            threading.Thread(target=self._loop, name=f"spilltrace-worker-{index}", daemon=True)
            for index in range(max(1, workers))
        ]
        for thread in self._threads:
            thread.start()

    # -- submission --------------------------------------------------------
    def submit(self, kind: str, scene: str, work: Work) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, scene=scene)
        with self._lock:
            self._jobs[job.id] = job
            self._work[job.id] = work
            self._order.append(job.id)
            self._evict_locked()
        self._queue.put(job.id)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, scene: str | None = None) -> list[Job]:
        with self._lock:
            jobs = [self._jobs[key] for key in self._order if key in self._jobs]
        if scene:
            jobs = [job for job in jobs if job.scene == scene]
        return sorted(jobs, key=lambda job: job.created, reverse=True)

    def cancel(self, job_id: str) -> bool:
        """Ask a job to stop. Queued jobs stop immediately; running jobs stop at their
        next progress checkpoint, because a NumPy convolution cannot be interrupted."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state in ("done", "failed", "cancelled"):
                return False
            job._cancel.set()
            if job.state == "queued":
                job.state = "cancelled"
                job.finished = time.time()
                job.log.append("cancelled before it started")
                self._work.pop(job_id, None)
        return True

    def shutdown(self) -> None:
        self._stopping.set()

    # -- internals ---------------------------------------------------------
    def _evict_locked(self) -> None:
        """Drop the oldest finished jobs once the retained set is full."""
        finished = [
            key
            for key in self._order
            if key in self._jobs and self._jobs[key].state in ("done", "failed", "cancelled")
        ]
        while len(finished) > MAX_FINISHED:
            key = finished.pop(0)
            self._jobs.pop(key, None)
            self._work.pop(key, None)
            try:
                self._order.remove(key)
            except ValueError:
                pass

    def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                job_id = self._queue.get(timeout=0.25)
            except Empty:
                continue
            with self._lock:
                job = self._jobs.get(job_id)
                work = self._work.pop(job_id, None)
            if job is None or work is None or job.cancelled:
                continue

            job.state = "running"
            job.started = time.time()
            job.log.append("started")

            def say(message: str, _job: Job = job) -> None:
                _job.log.append(message)
                if _job.cancelled:
                    raise JobCancelled(_job.id)

            try:
                job.result = work(say)
                job.state = "done"
                job.log.append("finished")
            except JobCancelled:
                job.state = "cancelled"
                job.log.append("cancelled")
            except Exception as exc:  # noqa: BLE001 - a job must not kill the worker
                job.state = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                # The full traceback stays server-side in `detail`; the summary above is
                # what the UI shows, so a stack trace never lands in front of a user.
                job.detail = traceback.format_exc(limit=8)
                job.log.append(job.error)
            finally:
                job.finished = time.time()
                with self._lock:
                    self._evict_locked()


class JobCancelled(RuntimeError):
    """Raised inside a job's progress callback once cancellation is requested."""
