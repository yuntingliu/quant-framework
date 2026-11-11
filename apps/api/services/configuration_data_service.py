"""Explicit SQLite data execution; no production singleton or backend switch.

One backend owner lends its maintenance lifetime to the scheduler and every
one-shot process. Configuration is captured after dequeue/queue-lock admission,
never at submission; interrupted/recovered jobs start a fresh capture/process.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import wait
from pathlib import Path
from uuid import uuid4

from alphalab.dataio.configuration_execution import execute_captured_rq, request_metadata
from alphalab.dataio.recipes import execute_data_recipe, inspect_data_recipe_source
from alphalab.dataio.runtime import RuntimeStore
from alphalab.dataio.sync import SyncJobManager, build_sync_plan
from alphalab.utils.configuration_backend import ConfigurationBackend
from alphalab.utils.configuration_maintenance import execution_access
from alphalab.utils.configuration_store import ConfigurationStoreError
from apps.api.services.configuration_profile_service import (
    ConfigurationProfileServices,
    _guard,
    _scan_metadata,
    _snapshot_credential_values,
)
from apps.api.services.configuration_runtime_service import RQJobConfiguration
from apps.api.services.data_sync_service import _public_job


class ConfigurationDataServices:
    """An explicit, closeable scheduler over the owning installation only.

    sdk_factory is an internal trusted test/dependency injection, never an HTTP
    field, environment name or worker-controlled import. No SDK lives in parent.
    """

    __slots__ = (
        "backend",
        "_profiles",
        "_sdk_factory",
        "_timeout",
        "_stop",
        "_lease",
        "manager",
        "_closed",
        "_condition",
        "_active",
        "_released",
    )

    @_guard
    def __init__(self, backend, *, environment=None, sdk_factory=None, timeout_seconds=1800):
        if type(backend) is not ConfigurationBackend or backend.mode != "sqlite":
            raise ValueError
        if type(timeout_seconds) not in {int, float} or not 0 < timeout_seconds <= 3600:
            raise ValueError
        self.backend, self._sdk_factory, self._timeout = backend, sdk_factory, timeout_seconds
        self._stop, self._closed = threading.Event(), False
        self._condition, self._active, self._released = threading.Condition(), 0, False
        self._lease = backend.borrow()
        self._lease.__enter__()
        try:
            self._profiles = ConfigurationProfileServices(backend.store, environment=environment)
            self._profiles.read_snapshot()
            self.manager = SyncJobManager(
                backend.root,
                job_runner=self._run,
                request_validator=self._validate_submission,
                recover_pending=False,
                configuration_backend=backend,
            )
            with RuntimeStore(backend.root).dataset_lock(
                "captured-rq-execution", timeout_seconds=5
            ):
                pass
            self.manager.recover_pending()
        except BaseException:
            self._stop.set()
            if hasattr(self, "manager"):
                self.manager.shutdown(wait=True)
            self._lease.__exit__(None, None, None)
            raise

    def __repr__(self):
        return "ConfigurationDataServices(<private>)"

    def __reduce_ex__(self, protocol):
        raise TypeError("Private data execution cannot be serialized implicitly.")

    @_guard
    def _validate_submission(self, value):
        with self.backend.borrow():
            if self._closed:
                raise ValueError
            snapshot = self._profiles.read_snapshot()
            _scan_metadata(
                request_metadata(value, snapshot.installation_id),
                _snapshot_credential_values(snapshot._snapshot, self._profiles._environment),
            )
            value.update(
                configuration_backend="sqlite",
                configuration_installation_id=snapshot.installation_id,
            )

    def _execute(
        self,
        *,
        operation,
        capture=None,
        source=None,
        job_id=None,
        cancelled=None,
        timeout_seconds=None,
    ):
        with self._condition:
            if self._closed:
                raise ConfigurationStoreError("STORE_UNAVAILABLE")
            self._active += 1
        try:
            return self._execute_process(
                operation=operation,
                capture=capture,
                source=source,
                job_id=job_id,
                cancelled=cancelled,
                timeout_seconds=timeout_seconds,
            )
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def _execute_process(self, *, operation, capture, source, job_id, cancelled, timeout_seconds):
        snapshot = self._profiles.read_snapshot() if capture is None else capture
        return execute_captured_rq(
            self.backend.root,
            installation_id=snapshot.installation_id,
            revision=snapshot.revision,
            configuration=None if capture is None else capture.extract_private(),
            operation=operation,
            source=source,
            job_id=job_id,
            sdk_factory=self._sdk_factory,
            timeout_seconds=self._timeout if timeout_seconds is None else timeout_seconds,
            cancelled=lambda: self._stop.is_set() or (cancelled is not None and cancelled()),
        )

    @_guard
    def _run(self, root, job_id):
        operations = (
            self.manager.operations if hasattr(self, "manager") else RuntimeStore(root).operations
        )
        try:
            with self.backend.borrow(), execution_access(self.backend.root):
                if Path(root) != self.backend.root:
                    raise ValueError
                if self._closed or self._stop.is_set():
                    operations.update_job(
                        job_id, status="cancelled", message="Cancelled before execution"
                    )
                    return operations.get_job(job_id)
                with RuntimeStore(root).dataset_lock("sync-job-queue", timeout_seconds=5):
                    with RuntimeStore(root).dataset_lock(
                        "captured-rq-execution", timeout_seconds=5
                    ):
                        pass
                    job = operations.get_job(job_id)
                    if job is None:
                        raise ValueError
                    if job["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                        return job
                    if self._stop.is_set() or operations.is_cancel_requested(job_id):
                        operations.update_job(
                            job_id, status="cancelled", message="Cancelled before execution"
                        )
                        return operations.get_job(job_id)
                    request = job["request"].copy()
                    snapshot = self._profiles.read_snapshot()
                    if (
                        request.get("configuration_backend") != "sqlite"
                        or request.get("configuration_installation_id") != snapshot.installation_id
                    ):
                        raise ValueError
                    request.pop("configuration_capture", None)
                    _scan_metadata(
                        request_metadata(request, snapshot.installation_id),
                        _snapshot_credential_values(
                            snapshot._snapshot, self._profiles._environment
                        ),
                    )
                    operations.replace_job_request(job_id, request)
                    capture = RQJobConfiguration(self._profiles)
                    request["configuration_capture"] = {
                        "installation_id": capture.installation_id,
                        "revision": capture.revision,
                        "attempt_id": uuid4().hex,
                    }
                    operations.replace_job_request(job_id, request)
                    operations.update_job(
                        job_id, status="running", message="Executing captured RQData configuration"
                    )
                    result = self._execute(
                        operation="job",
                        capture=capture,
                        job_id=job_id,
                        cancelled=lambda: operations.is_cancel_requested(job_id),
                    )
                    if not result.get("ok"):
                        cancelled = result.get("reason") == "cancelled"
                        operations.update_job(
                            job_id,
                            status="cancelled" if cancelled else "failed",
                            message="RQData job cancelled" if cancelled else "RQData job failed",
                            error=None
                            if cancelled
                            else "RQData execution unavailable or timed out.",
                        )
        except BaseException as error:
            operations.update_job(
                job_id,
                status="failed",
                message="RQData job failed",
                error="RQData configuration or execution is unavailable.",
            )
            if not isinstance(error, Exception):
                raise
        return operations.get_job(job_id)

    @_guard
    def connection_test(self, template_id=None):
        with self.backend.borrow():
            if self._closed:
                raise ValueError
            capture = RQJobConfiguration(self._profiles)
            result = self._execute(operation="connection", capture=capture, source=template_id)
            if not result.get("ok"):
                raise ConfigurationStoreError("STORE_UNAVAILABLE")
            return result["value"]

    @_guard
    def plan_recipe(self, source):
        with self.backend.borrow():
            if self._closed:
                raise ValueError
            self._validate_submission({"recipe_source": source})
            return execute_data_recipe(
                source,
                mode="plan",
                root=self.backend.root,
                timeout_seconds=self._timeout,
                execution=self._recipe_execution,
                configuration_backend=self.backend,
            )

    def _recipe_execution(self, source, *, mode, root, timeout_seconds, cancelled):
        if mode != "plan" or Path(root) != self.backend.root:
            raise ConfigurationStoreError("INVALID_ARGUMENT")
        result = self._execute(
            operation="plan", source=source, timeout_seconds=timeout_seconds, cancelled=cancelled
        )
        if not result.get("ok"):
            raise ConfigurationStoreError("STORE_UNAVAILABLE")
        return result["value"]

    @_guard
    def save_recipe(self, project_id, source, *, expected_source_sha256=None):
        with self.backend.borrow():
            if (
                not project_id
                or len(project_id) > 64
                or not all(c.isalnum() or c in "_-" for c in project_id)
            ):
                raise ValueError
            self._validate_submission({"project_id": project_id, "recipe_source": source})
            inspect_data_recipe_source(source)
            try:
                return self.manager.operations.save_recipe_draft(
                    project_id, source, expected_source_sha256=expected_source_sha256
                )
            except RuntimeError:
                raise ConfigurationStoreError("REVISION_CONFLICT") from None

    @_guard
    def public_job(self, item):
        with self.backend.borrow():
            if self._closed:
                raise ConfigurationStoreError("STORE_UNAVAILABLE")
            return self._public_job(item)

    def _public_job(self, item):
        if item is None:
            return None
        if (
            item["request"].get("configuration_backend") != "sqlite"
            or item["request"].get("configuration_installation_id")
            != self._profiles.read_snapshot().installation_id
        ):
            return None
        # Requests/logs remain private; only the existing sanitized projection
        # and system-generated configuration capture identifiers are public.
        value = _public_job(item)
        capture = item["request"].get("configuration_capture")
        if capture is not None:
            value["configuration"] = capture.copy()
        return value

    @_guard
    def rq_settings(
        self,
        *,
        profile=None,
        activate=True,
        clear_password=False,
        expected_revision=None,
        selection=None,
    ):
        with self.backend.borrow():
            if self._closed:
                raise ValueError
            if selection is not None:
                if self.backend.durable and selection.get("kind") == "environment":
                    raise ConfigurationStoreError("INVALID_CONFIGURATION")
                return self._profiles.rqdata.select(selection, expected_revision=expected_revision)
            if profile is not None:
                return self._profiles.rqdata.save_profile(
                    profile,
                    activate=activate,
                    clear_password=clear_password,
                    expected_revision=expected_revision,
                )
            return self._profiles.rqdata.public_settings()

    @_guard
    def plan(self, request):
        with self.backend.borrow():
            self._validate_submission(request.model_dump(mode="json"))
            return build_sync_plan(request, root=self.backend.root)

    def close(self, *, timeout_seconds=8):
        if type(timeout_seconds) not in {int, float} or not 0 <= timeout_seconds <= 30:
            raise ConfigurationStoreError("INVALID_ARGUMENT")
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            if self._released:
                return
            self._closed = True
            self._stop.set()
        self.manager.shutdown(wait=False)
        with self.manager._lock:
            pending = tuple(self.manager._futures.values())
        if pending and wait(pending, timeout=max(0, deadline - time.monotonic())).not_done:
            raise ConfigurationStoreError("STORE_BUSY")
        with self._condition:
            if not self._condition.wait_for(
                lambda: self._active == 0, timeout=max(0, deadline - time.monotonic())
            ):
                # Keep ownership if a caller still holds an active operation.
                raise ConfigurationStoreError("STORE_BUSY")
            if not self._released:
                self._lease.__exit__(None, None, None)
                self._released = True

    def __enter__(self):
        return self

    def __exit__(self, *_error):
        self.close()
