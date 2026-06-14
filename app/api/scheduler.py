from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.runtime.registry import AppRegistry
from app.runtime.runtime import AppRuntimeService

from .labels import load_app_config_metadata
from .payloads import AppPayloadService

JOB_WEIGHTS = {"autorunner": 1, "statanalyser": 1, "wfa": 2}


class AppBatchScheduler:
    def __init__(
        self,
        runtime: AppRuntimeService,
        registry: AppRegistry,
        payloads: AppPayloadService,
    ):
        self.runtime = runtime
        self.registry = registry
        self.payloads = payloads
        self.capacity = min(4, max(2, (os.cpu_count() or 4) // 2))
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._active_weight = 0
        self._batches: Dict[str, Dict[str, Any]] = {}
        self._pending: List[Dict[str, str]] = []
        self._dispatcher = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._dispatcher.start()

    def submit_batch(self, module: str, config_paths: List[str]) -> Dict[str, Any]:
        batch_id = self._new_batch_id()
        created_at = self._now_iso()
        jobs = []
        for index, config_path in enumerate(config_paths):
            job_module = self._resolve_job_module(module, config_path)
            metadata = load_app_config_metadata(config_path, job_module)
            jobs.append(
                {
                    "job_id": f"{batch_id}_{index + 1:02d}",
                    "module": job_module,
                    "config_path": str(Path(config_path).resolve()),
                    "label": Path(config_path).name,
                    "display_label": metadata.get("display_label"),
                    "label_badges": list(metadata.get("badges", [])),
                    "weight": JOB_WEIGHTS[job_module],
                    "status": "queued",
                    "stage": "queued",
                    "stage_message": "Queued",
                    "run_id": None,
                    "created_at": created_at,
                    "started_at": None,
                    "updated_at": created_at,
                    "stage_started_at": None,
                    "completed_at": None,
                    "logs": [],
                    "error": None,
                    "result_refs": {},
                }
            )
        batch = {
            "batch_id": batch_id,
            "module": module,
            "status": "queued",
            "created_at": created_at,
            "updated_at": created_at,
            "completed_at": None,
            "jobs": jobs,
            "events": [],
        }
        with self._condition:
            self._batches[batch_id] = batch
            for job in jobs:
                self._pending.append({"batch_id": batch_id, "job_id": job["job_id"]})
            self._append_event(batch_id, "batch_submitted", {"job_count": len(jobs)})
            self._condition.notify_all()
        return self.get_batch(batch_id)

    def list_active_batches(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = [
                self._public_batch(batch, include_events=False)
                for batch in self._batches.values()
                if batch["status"] in {"queued", "running"}
            ]
        rows.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return rows

    def get_batch(self, batch_id: str) -> Dict[str, Any]:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                raise KeyError(batch_id)
            return self._public_batch(batch, include_events=True)

    def get_events_since(self, batch_id: str, offset: int) -> List[Dict[str, Any]]:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return []
            return batch["events"][offset:]

    def _dispatch_loop(self) -> None:
        while True:
            with self._condition:
                next_ref = self._next_schedulable_job_ref()
                if next_ref is None:
                    self._condition.wait(timeout=0.5)
                    continue
                job = self._locate_job(next_ref["batch_id"], next_ref["job_id"])
                if job is None:
                    continue
                self._pending = [ref for ref in self._pending if ref != next_ref]
                self._active_weight += int(job["weight"])
                now = self._now_iso()
                job["status"] = "running"
                job["stage"] = "starting"
                job["stage_message"] = "Starting"
                job["started_at"] = now
                job["updated_at"] = now
                job["stage_started_at"] = now
                self._batches[next_ref["batch_id"]]["status"] = "running"
                self._batches[next_ref["batch_id"]]["updated_at"] = now
                self._append_event(
                    next_ref["batch_id"],
                    "job_started",
                    {
                        "job_id": job["job_id"],
                        "label": job["label"],
                        "display_label": job.get("display_label"),
                    },
                )
                threading.Thread(
                    target=self._run_job,
                    args=(next_ref["batch_id"], job["job_id"]),
                    daemon=True,
                ).start()

    def _run_job(self, batch_id: str, job_id: str) -> None:
        job = self._locate_job(batch_id, job_id)
        if job is None:
            return

        def emit(stage: str, message: str) -> None:
            with self._lock:
                live_job = self._locate_job(batch_id, job_id)
                if live_job is None:
                    return
                now = self._now_iso()
                if live_job.get("stage") != stage:
                    live_job["stage_started_at"] = now
                live_job["stage"] = stage
                live_job["stage_message"] = message
                live_job["updated_at"] = now
                live_job["logs"] = (
                    live_job.get("logs", []) + [f"[{stage}] {message}"]
                )[-250:]
                self._append_event(
                    batch_id,
                    "job_log",
                    {"job_id": job_id, "stage": stage, "message": message},
                )

        try:
            module = str(job["module"])
            config_path = str(job["config_path"])
            if module == "autorunner":
                result = self.runtime.run_autorunner_config(config_path, emit)
                payload_module = "autorunner"
            elif module == "wfa":
                result = self.runtime.run_wfa_config(config_path, emit)
                payload_module = "wfanalyser"
            else:
                result = self.runtime.run_statanalyser_config(config_path, emit)
                payload_module = "statanalyser"
            run_id = result.get("run_id")
            status = str(result.get("status", "completed"))
            if run_id and status in {"completed", "partial"}:
                self.payloads.ensure_run_payloads(run_id, module=payload_module)
            registry_entry = self.registry.load_registry_entry(run_id) if run_id else {}
            with self._lock:
                live_job = self._locate_job(batch_id, job_id)
                if live_job is not None:
                    now = self._now_iso()
                    live_job["status"] = status
                    live_job["stage"] = registry_entry.get("status", status)
                    live_job["stage_message"] = f"Finished with status {status}"
                    live_job["run_id"] = run_id
                    live_job["updated_at"] = now
                    live_job["completed_at"] = now
                    live_job["result_refs"] = {
                        "module": module,
                        "run_id": run_id,
                        "semantic_label": registry_entry.get("semantic_label"),
                    }
                    self._append_event(
                        batch_id,
                        "job_finished",
                        {"job_id": job_id, "run_id": run_id, "status": status},
                    )
        except Exception as exc:
            with self._lock:
                live_job = self._locate_job(batch_id, job_id)
                if live_job is not None:
                    now = self._now_iso()
                    live_job["status"] = "failed"
                    live_job["stage"] = "failed"
                    live_job["stage_message"] = str(exc)
                    live_job["error"] = str(exc)
                    live_job["updated_at"] = now
                    live_job["completed_at"] = now
                    live_job["logs"] = (
                        live_job.get("logs", []) + [f"[failed] {exc}"]
                    )[-250:]
                    self._append_event(
                        batch_id,
                        "job_failed",
                        {"job_id": job_id, "error": str(exc)},
                    )
        finally:
            with self._condition:
                self._active_weight = max(0, self._active_weight - int(job["weight"]))
                self._refresh_batch_status(batch_id)
                self._condition.notify_all()

    def _refresh_batch_status(self, batch_id: str) -> None:
        batch = self._batches.get(batch_id)
        if batch is None:
            return
        statuses = [job.get("status") for job in batch["jobs"]]
        if any(status in {"queued", "running"} for status in statuses):
            batch["status"] = (
                "running" if any(status == "running" for status in statuses) else "queued"
            )
            batch["updated_at"] = self._now_iso()
            return
        if any(status == "failed" for status in statuses) and any(
            status in {"completed", "partial"} for status in statuses
        ):
            batch["status"] = "partial"
        elif any(status == "failed" for status in statuses):
            batch["status"] = "failed"
        elif any(status == "partial" for status in statuses):
            batch["status"] = "partial"
        else:
            batch["status"] = "completed"
        if batch["completed_at"] is None:
            batch["completed_at"] = self._now_iso()
        batch["updated_at"] = batch["completed_at"]
        self._append_event(batch_id, "batch_status", {"status": batch["status"]})

    def _next_schedulable_job_ref(self) -> Optional[Dict[str, str]]:
        for ref in self._pending:
            job = self._locate_job(ref["batch_id"], ref["job_id"])
            if job is None:
                continue
            if self._active_weight + int(job["weight"]) <= self.capacity:
                return ref
        return None

    def _locate_job(self, batch_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        batch = self._batches.get(batch_id)
        if batch is None:
            return None
        for job in batch["jobs"]:
            if job["job_id"] == job_id:
                return job
        return None

    def _append_event(self, batch_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        batch = self._batches.get(batch_id)
        if batch is None:
            return
        batch["events"].append(
            {
                "type": event_type,
                "timestamp": self._now_iso(),
                **payload,
            }
        )
        batch["updated_at"] = batch["events"][-1]["timestamp"]
        batch["events"] = batch["events"][-500:]

    def _public_batch(self, batch: Dict[str, Any], *, include_events: bool) -> Dict[str, Any]:
        payload = {
            "batch_id": batch["batch_id"],
            "module": batch["module"],
            "status": batch["status"],
            "created_at": batch["created_at"],
            "updated_at": batch.get("updated_at"),
            "completed_at": batch["completed_at"],
            "jobs": [
                {
                    "job_id": job["job_id"],
                    "module": job["module"],
                    "label": job["label"],
                    "display_label": job.get("display_label"),
                    "label_badges": list(job.get("label_badges", [])),
                    "status": job["status"],
                    "stage": job["stage"],
                    "stage_message": job.get("stage_message"),
                    "weight": job.get("weight"),
                    "run_id": job["run_id"],
                    "created_at": job.get("created_at"),
                    "started_at": job.get("started_at"),
                    "updated_at": job.get("updated_at"),
                    "stage_started_at": job.get("stage_started_at"),
                    "completed_at": job.get("completed_at"),
                    "error": job["error"],
                    "logs": list(job.get("logs", [])),
                    "result_refs": dict(job.get("result_refs", {})),
                }
                for job in batch["jobs"]
            ],
        }
        if include_events:
            payload["events"] = list(batch["events"])
        return payload

    @staticmethod
    def _resolve_job_module(module: str, config_path: str) -> str:
        if module != "mixed":
            return module
        path = str(config_path).lower()
        if "\\workspace\\wfa\\" in path:
            return "wfa"
        return "autorunner"

    @staticmethod
    def _new_batch_id() -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S_") + f"{int(time.time_ns()) % 1000000:06d}"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")
