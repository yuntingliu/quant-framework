"""One-shot spawned RQData execution, with private bounded parent-push IPC.

Only explicitly authorized parent bindings call this module. It never opens a
configuration store, adopts JSON, resolves environment credentials or accepts
worker requests for another installation. Every process executes one job only.
Recipes are trusted Python, not sandboxed against deliberate filesystem access.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import multiprocessing
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import unquote

from pandas import CategoricalDtype

from alphalab.dataio.errors import DataLoadError
from alphalab.utils.configuration_maintenance import hold_worker_execution
from alphalab.utils.configuration_store import _safe_call

MAX_PRIVATE_BYTES = 32 * 1024
MAX_RESULT_BYTES = 1024 * 1024
_ID = re.compile(r"[0-9a-f]{32}")
_SYSTEM_ENV = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "SYSTEMDRIVE",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONDONTWRITEBYTECODE",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV",
        "CONDA_SHLVL",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "PROCESSOR_ARCHITECTURE",
        "NUMBER_OF_PROCESSORS",
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    }
)


def request_metadata(value, installation_id, revision=None):
    """Exclude only verified system envelope IDs, never configurable metadata."""
    result = value.copy()
    if "configuration_backend" in result:
        if result.pop("configuration_backend") != "sqlite":
            raise ValueError
    if "configuration_installation_id" in result:
        if result.pop("configuration_installation_id") != installation_id:
            raise ValueError
    if "configuration_capture" in result:
        capture = result.pop("configuration_capture")
        if (
            type(capture) is not dict
            or set(capture) != {"installation_id", "revision", "attempt_id"}
            or capture["installation_id"] != installation_id
            or capture["revision"] != revision
            or type(capture["attempt_id"]) is not str
            or not _ID.fullmatch(capture["attempt_id"])
        ):
            raise ValueError
    if "recipe_source_sha256" in result:
        source = result.get("recipe_source")
        if (
            type(source) is not str
            or result.pop("recipe_source_sha256") != hashlib.sha256(source.encode()).hexdigest()
        ):
            raise ValueError
    return result


def reject_secret(value, password):
    """Compare known credential equivalents without returning input in errors."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
        elif isinstance(item, str) and password:
            for _ in range(6):
                if password in item:
                    raise ValueError("Private RQData output was rejected.")
                decoded = unquote(item, errors="replace")
                if decoded == item:
                    break
                item = decoded
            else:
                raise ValueError("Private RQData output was rejected.")


def reject_frame(frame, password):
    """Reject private text before writes, including unused category dictionaries."""
    reject_secret([list(frame.columns), list(frame.index.names)], password)
    for column in frame.select_dtypes(include=["object", "string", "category"]).columns:
        values = frame[column]
        if isinstance(values.dtype, CategoricalDtype):
            # Parquet retains the full dictionary, not just categories used by rows.
            reject_secret(values.cat.categories.tolist(), password)
        for value in values:
            reject_secret(value, password)
    if frame.index.dtype.kind == "O":
        for value in frame.index:
            reject_secret(value, password)


class _Discard(io.TextIOBase):
    def write(self, value):
        return len(value)


def _safe_sdk_call(action):
    failed = False
    try:
        return action()
    except BaseException:
        failed = True
    if failed:
        raise DataLoadError("RQData operation failed.")


class _SDK:
    __slots__ = ("_module",)

    def __init__(self, module):
        self._module = module

    def __getattr__(self, name):
        import importlib

        if self._module is None:
            self._module = _safe_sdk_call(lambda: importlib.import_module("rqdatac"))
        value = _safe_sdk_call(lambda: getattr(self._module, name))
        return (
            (lambda *a, **kw: _safe_sdk_call(lambda: value(*a, **kw))) if callable(value) else value
        )

    def __repr__(self):
        return "CapturedRQSDK(<private>)"


def _no_legacy(*args, **kwargs):
    raise DataLoadError("Explicit RQData worker configuration is required.")


def _recipe(source, mode, root, client, password):
    import alphalab.data_sdk.v1 as sdk
    import alphalab.data_sdk.v1.recipe as recipe_sdk
    from alphalab.dataio.recipes import _execute_loaded_recipe, inspect_data_recipe_source

    class CapturedAPI(recipe_sdk.RQDataAPI):
        def _module(self):
            if client is None:
                return _no_legacy()
            return client.connect()

    class CapturedContext(recipe_sdk.DataRecipeContext):
        def publish(self, dataset, frame):
            reject_secret(dataset, password)
            reject_frame(frame, password)
            return super().publish(dataset, frame)

        def output(self, name, value):
            # Public result values are checked again before crossing IPC.
            reject_secret([name, str(value)], password)
            return super().output(name, value)

    previous = sdk.rq, recipe_sdk.rq
    sdk.rq = recipe_sdk.rq = CapturedAPI()
    try:
        inspection = inspect_data_recipe_source(source)
        result = _execute_loaded_recipe(
            source, mode=mode, root=root, context_factory=CapturedContext
        )
        reject_secret(result, password)
        return {**result, "source_sha256": inspection.source_sha256, "stdout": "", "stderr": ""}
    finally:
        sdk.rq, recipe_sdk.rq = previous


def _worker(pipe, root, installation_id, revision, operation, job_id, source, sdk_factory):
    from alphalab.utils.configuration_decision import check_backend_authority

    try:
        hold_worker_execution(root)
        check_backend_authority(root, backend="inactive_sqlite")
    except BaseException:
        # No ready acknowledgement means no captured credentials may be sent.
        pipe.close()
        return
    return _admitted_worker(
        pipe, root, installation_id, revision, operation, job_id, source, sdk_factory
    )


def _admitted_worker(pipe, root, installation_id, revision, operation, job_id, source, sdk_factory):
    if operation == "plan":
        # Planning has no SDK and must not create/write runtime lock metadata.
        return _worker_exclusive(
            pipe, root, installation_id, revision, operation, job_id, source, sdk_factory
        )
    # This child owns a separate execution lock before announcing readiness.
    # It survives parent queue-lock loss, so a restarted scheduler can wait for
    # the old child to exit before recovering statuses or capturing a new job.
    from alphalab.dataio.runtime import RuntimeStore

    try:
        with RuntimeStore(root).dataset_lock("captured-rq-execution", timeout_seconds=5):
            return _worker_exclusive(
                pipe, root, installation_id, revision, operation, job_id, source, sdk_factory
            )
    except BaseException:
        try:
            pipe.send_bytes(b'{"ok":false,"reason":"worker_failed"}')
        except (OSError, EOFError):
            pass
        finally:
            pipe.close()


def _worker_exclusive(
    pipe, root, installation_id, revision, operation, job_id, source, sdk_factory
):
    """Entrypoint is fixed, private pipe is inherited by this test/parent-owned child."""
    response = {"ok": False, "reason": "worker_failed"}
    completed = threading.Event()
    try:
        # One disposable child only: discard native extension stdio as well as
        # Python streams. Never redirect the API process's file descriptors.
        null = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null, 1)
            os.dup2(null, 2)
        finally:
            os.close(null)
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(_Discard()), contextlib.redirect_stderr(_Discard()):
            pipe.send_bytes(b"ready")
            payload = json.loads(pipe.recv_bytes(MAX_PRIVATE_BYTES))
            if (
                type(payload) is not dict
                or set(payload) != {"protocol", "root_key", "installation_id", "revision", "rq"}
                or payload["protocol"] != 1
                or payload["installation_id"] != installation_id
                or payload["revision"] != revision
                or not _ID.fullmatch(installation_id)
                or not _ID.fullmatch(revision)
                or payload["root_key"]
                != hashlib.sha256(os.path.normcase(root).encode()).hexdigest()
            ):
                raise ValueError

            def watch_parent():
                while not completed.wait(0.1):
                    try:
                        if pipe.poll(0.1):
                            pipe.recv_bytes(32)
                            os._exit(71)  # No further messages are authorized.
                    except (EOFError, OSError):
                        if not completed.is_set():
                            os._exit(72)  # Stop this owned child if parent disappears.

            threading.Thread(target=watch_parent, daemon=True, name="rq-owner-pipe").start()
            from alphalab.dataio import rq_profiles
            from alphalab.dataio.providers.rq import RQDataClient, RQDataConfig
            from alphalab.dataio.rq_sync import RQAcquirer
            from alphalab.dataio.runtime import OperationsStore
            from alphalab.dataio.sync import RQSyncService, SyncRequest

            # One child, one execution: these guards and proxy aliases never
            # mutate the parent or another job. Ambient secrets are not a source.
            for key in list(os.environ):
                if key.upper() not in _SYSTEM_ENV:
                    os.environ.pop(key, None)
            os.environ["ALPHALAB_RUNTIME_DIR"] = root  # A path, never a credential/backend toggle.
            rq_profiles.read_settings = _no_legacy
            RQDataClient.from_env = classmethod(_no_legacy)
            RQDataConfig.from_env = classmethod(_no_legacy)
            RQDataConfig.from_active_settings = classmethod(_no_legacy)
            config = payload["rq"]
            client, password = None, ""
            if operation != "plan":
                if (
                    type(config) is not dict
                    or set(config) != {"RQ_USER", "RQ_PASSWORD", "RQ_HOST"}
                    or any(
                        type(value) is not str or not value.strip() or len(value) > 8192
                        for value in config.values()
                    )
                ):
                    raise ValueError
                password = config["RQ_PASSWORD"]
                module = None if sdk_factory is None else sdk_factory()
                client = RQDataClient(
                    RQDataConfig(config["RQ_USER"], password, config["RQ_HOST"]),
                    module=_SDK(module),
                )
            elif config is not None:
                raise ValueError

            update = OperationsStore.update_job
            replace = OperationsStore.replace_job_request

            def safe_update(instance, identifier, **changes):
                try:
                    reject_secret(changes, password)
                except ValueError:
                    changes.update(
                        message="RQData job failed",
                        error="RQData operation failed.",
                        status="failed",
                    )
                return update(instance, identifier, **changes)

            def safe_replace(instance, identifier, request):
                reject_secret(request_metadata(request, installation_id, revision), password)
                return replace(instance, identifier, request)

            OperationsStore.update_job = safe_update
            OperationsStore.replace_job_request = safe_replace
            from alphalab.dataio.runtime import RuntimeStore

            write = RuntimeStore.write

            def safe_write(instance, dataset, frame, **kwargs):
                reject_secret(dataset, password)
                reject_frame(frame, password)
                return write(instance, dataset, frame, **kwargs)

            RuntimeStore.write = safe_write

            if operation == "plan":
                value = _recipe(source, "plan", root, None, "")
            elif operation == "connection":
                from alphalab.dataio.rq_templates import (
                    DEFAULT_RQ_SYNC_TEMPLATE_ID,
                    get_rq_sync_template,
                )

                template = get_rq_sync_template(source or DEFAULT_RQ_SYNC_TEMPLATE_ID)
                latest = client.connect().get_latest_trading_date(market=template.market)
                value = {
                    "status": "connected",
                    "connected": True,
                    "latest_trading_date": str(latest),
                    "template_id": template.id,
                    "market": template.market,
                    "instrument_types": list(template.instrument_types),
                    "rqdatac_version": "unknown",
                }
            elif operation == "job":
                operations = OperationsStore(root)
                job = operations.get_job(job_id)
                if job is None or job["status"] not in {"queued", "running"}:
                    raise ValueError
                acquirer = RQAcquirer(client)
                if job["request"].get("kind") == "python_recipe":
                    source = job["request"]["recipe_source"]
                    execution = _recipe(source, "run", root, client, password)
                    audit = {
                        "recipe_source_sha256": execution["source_sha256"],
                        "recipe_stdout": "",
                        "recipe_stderr": "",
                        "recipe_planned": execution["planned"],
                        "recipe_published": execution["published"],
                        "recipe_outputs": execution["outputs"],
                    }
                    request = execution.get("sync_request")
                    if request is not None:
                        request = SyncRequest.model_validate(request).model_dump(mode="json")
                        reject_secret(request, password)
                    operations.replace_job_request(
                        job_id, {**job["request"], **(request or {}), **audit}
                    )
                    if request is None:
                        operations.update_job(
                            job_id,
                            status="succeeded",
                            progress=1,
                            total=1,
                            message="Python data recipe completed",
                        )
                        value = {"completed": True}
                    else:
                        RQSyncService(root, acquirer=acquirer).run(job_id)
                        value = {"completed": True}
                else:
                    RQSyncService(root, acquirer=acquirer).run(job_id)
                    value = {"completed": True}
            else:
                raise ValueError
            reject_secret(value, password)
            response = {"ok": True, "value": value}
    except BaseException:
        pass  # No original exception, traceback, SDK output or cause crosses IPC.
    completed.set()
    try:
        data = json.dumps(response, allow_nan=False).encode()
        pipe.send_bytes(
            data if len(data) <= MAX_RESULT_BYTES else b'{"ok":false,"reason":"worker_failed"}'
        )
    finally:
        pipe.close()


def execute_captured_rq(
    root,
    *,
    installation_id,
    revision,
    configuration,
    operation,
    job_id=None,
    source=None,
    sdk_factory=None,
    timeout_seconds=1800,
    cancelled=None,
):
    """Private synchronous runner: credentials travel only through the owned pipe.

    sdk_factory is an explicitly injected trusted dependency (normally None),
    never a request, environment selector or worker-supplied import path.
    """

    def run():
        if (
            type(timeout_seconds) not in {int, float}
            or not 0 < timeout_seconds <= 3600
            or not _ID.fullmatch(installation_id)
            or not _ID.fullmatch(revision)
        ):
            raise ValueError
        location = str(Path(root).resolve())
        payload = json.dumps(
            {
                "protocol": 1,
                "root_key": hashlib.sha256(os.path.normcase(location).encode()).hexdigest(),
                "installation_id": installation_id,
                "revision": revision,
                "rq": configuration,
            },
            allow_nan=False,
        ).encode()
        if len(payload) > MAX_PRIVATE_BYTES:
            raise ValueError
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=_worker,
            args=(
                child,
                location,
                installation_id,
                revision,
                operation,
                job_id,
                source,
                sdk_factory,
            ),
            name="alphalab-captured-rq",
            daemon=True,
        )
        started = False
        deadline = time.monotonic() + timeout_seconds
        try:
            process.start()
            started = True
            child.close()
            sent = False
            while time.monotonic() < deadline:
                if cancelled is not None and cancelled():
                    return {"ok": False, "reason": "cancelled"}
                if parent.poll(min(0.05, max(0, deadline - time.monotonic()))):
                    if not sent:
                        if parent.recv_bytes(32) != b"ready":
                            raise ValueError
                        parent.send_bytes(payload)
                        sent = True
                    else:
                        value = json.loads(parent.recv_bytes(MAX_RESULT_BYTES))
                        if type(value) is not dict or type(value.get("ok")) is not bool:
                            raise ValueError
                        if cancelled is not None and cancelled():
                            return {"ok": False, "reason": "cancelled"}
                        return value
                elif not process.is_alive():
                    return {"ok": False, "reason": "worker_failed"}
            return {"ok": False, "reason": "timeout"}
        finally:
            child.close()
            parent.close()
            if started:
                process.join(timeout=1)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=2)
            process.close()

    return _safe_call(run)
