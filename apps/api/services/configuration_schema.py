"""Pure, credential-free service-configuration transfer format.

Snapshots must come from saved settings, not effective/environment-resolved
profiles or public API responses. This module performs no storage, environment,
provider, or network access. Snapshot coordination and import application are
separate concerns; exporting three caller-supplied snapshots is not an atomic
read of a running installation.
"""

from __future__ import annotations

import ipaddress
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, NoReturn
from urllib.parse import unquote, urlsplit

FORMAT = "alphalab.service-configuration"
TRANSFER_VERSION = 1
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_DEPTH = 8
MAX_PROFILES_PER_SERVICE = 20
MAX_NODES = 4096
SERVICES = ("models", "search", "rqdata")

_PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_OPENROUTER_URL = "https://openrouter.ai/api/v1"
_PROFILE_FIELDS = {
    "models": {"id", "name", "base_url", "model"},
    "search": {"id", "name", "provider", "model_profile_id"},
    "rqdata": {"id", "name", "provider", "user", "host"},
}
_SECRET_FIELD = {"models": "api_key", "search": "brave_api_key", "rqdata": "password"}
_MAX_URL_DECODINGS = 5
_MALFORMED_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_CREDENTIAL_PATH = re.compile(
    r"(?:^|[^A-Za-z0-9_-])(?:brave[_-]?api[_-]?key|api[_-]?key|access[_-]?token|authorization|auth|bearer|password|passwd|secret|token|key)(?=$|[^A-Za-z0-9_])"
    r"|(?:^|[^A-Za-z0-9_-])(?:sk-[A-Za-z0-9_-]+|gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)",
    re.IGNORECASE,
)
_MESSAGES = {
    "INVALID_JSON": "Provide one valid UTF-8 JSON document.",
    "DUPLICATE_KEY": "Duplicate JSON properties are not allowed.",
    "DOCUMENT_TOO_LARGE": "The configuration document exceeds the size limit.",
    "DOCUMENT_TOO_DEEP": "The configuration document exceeds the nesting limit.",
    "TOO_MANY_VALUES": "The configuration document contains too many values.",
    "INVALID_STRUCTURE": "Check the configuration document structure.",
    "FIELD_NOT_ALLOWED": "The configuration contains a property that is not allowed.",
    "UNSUPPORTED_VERSION": "The configuration schema version is not supported.",
    "INVALID_VALUE": "Check the configuration field format.",
    "INVALID_SELECTION": "Check the explicit configuration selection.",
    "INVALID_REFERENCE": "A configuration reference does not resolve to a compatible saved profile.",
    "DUPLICATE_PROFILE": "Profile IDs must be unique within each service.",
    "TOO_MANY_PROFILES": "The configuration exceeds the profile-count limit.",
    "UNSAFE_URL": "Use an HTTPS endpoint or loopback HTTP without credentials or URL parameters.",
    "INVALID_SNAPSHOT": "Provide a validated saved-settings snapshot and its selection intent.",
    "CREDENTIAL_IN_METADATA": "Saved metadata must not contain a supplied credential.",
}


class TransferValidationError(ValueError):
    """An error containing only a static code, message, and schema-owned path."""

    def __init__(self, code: str, path: str = "") -> None:
        self.code = code
        self.path = path
        super().__init__(_MESSAGES[code])


def _fail(code: str, path: str = "") -> NoReturn:
    raise TransferValidationError(code, path)


def _object(value: object, allowed: set[str], required: set[str], path: str) -> dict:
    if type(value) is not dict:
        _fail("INVALID_STRUCTURE", path)
    if set(value) - allowed:
        # Never include an unknown property name: it can itself contain a secret.
        _fail("FIELD_NOT_ALLOWED", path)
    if required - set(value):
        _fail("INVALID_STRUCTURE", path)
    return value


def _text(value: object, maximum: int, path: str) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or len(value) > maximum
        or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value)
    ):
        _fail("INVALID_VALUE", path)
    return value


def _identifier(value: object, path: str) -> str:
    if type(value) is not str or not _PROFILE_ID.fullmatch(value):
        _fail("INVALID_VALUE", path)
    return value


def _model_id(value: object, path: str) -> str:
    model = _text(value, 200, path)
    candidate = model.strip()
    if (
        PurePosixPath(candidate).is_absolute()
        or PureWindowsPath(candidate).is_absolute()
        or candidate.startswith("\\\\")
    ):
        _fail("INVALID_VALUE", path)
    return model


def _version(value: object, supported: tuple[int, ...], path: str) -> int:
    if type(value) is not int or value not in supported:
        _fail("UNSUPPORTED_VERSION", path)
    return value


def _selection(value: object, path: str) -> dict:
    choice = _object(value, {"kind", "id"}, {"kind"}, path)
    kind = choice["kind"]
    if type(kind) is not str or kind not in {"profile", "environment", "none"}:
        _fail("INVALID_SELECTION", path)
    if kind == "profile":
        if "id" not in choice:
            _fail("INVALID_SELECTION", path)
        return {"kind": "profile", "id": _identifier(choice["id"], path + "/id")}
    if "id" in choice:
        _fail("INVALID_SELECTION", path)
    return {"kind": kind}


def _valid_hostname(value: str) -> bool:
    if "%" in value:
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if ":" in value or re.fullmatch(r"[0-9.]+", value):
            return False
        try:
            hostname = value.rstrip(".").encode("idna").decode("ascii")
        except UnicodeError:
            return False
        return len(hostname) <= 253 and all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in hostname.split(".")
        )
    return True


def _endpoint(value: object, path: str) -> str:
    if type(value) is not str or not value.strip() or len(value) > 2000:
        _fail("UNSAFE_URL", path)
    url = value
    if _MALFORMED_ESCAPE.search(url):
        _fail("UNSAFE_URL", path)
    candidate = url
    for layer in range(_MAX_URL_DECODINGS):
        if "\\" in candidate or any(
            char.isspace() or unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in candidate
        ):
            _fail("UNSAFE_URL", path)
        try:
            parsed = urlsplit(candidate)
            port = parsed.port
            # Only the wire URL must have valid UTF-8 percent escapes. Decoded
            # literal percent characters (e.g. %25ff) are valid path data;
            # further decoding is a conservative check for hidden unsafe forms.
            decoded = unquote(candidate, errors="strict" if layer == 0 else "replace")
        except (ValueError, UnicodeError):
            parsed = None
        if parsed is None:
            _fail("UNSAFE_URL", path)
        if (
            not parsed.hostname
            or not _valid_hostname(parsed.hostname)
            or parsed.netloc.endswith(":")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "?" in candidate
            or "#" in candidate
            or (port is not None and not 1 <= port <= 65535)
            or parsed.scheme not in {"https", "http"}
            or (
                parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            )
            or _CREDENTIAL_PATH.search(parsed.path)
        ):
            _fail("UNSAFE_URL", path)
        if decoded == candidate:
            return url.rstrip("/")
        candidate = decoded
    _fail("UNSAFE_URL", path)


def _rq_host(value: object, path: str) -> str:
    host = _text(value, 255, path)
    if any(char.isspace() or char in "/\\?#@%" for char in host):
        _fail("INVALID_VALUE", path)
    try:
        parsed = urlsplit("//" + host)
        hostname, port = parsed.hostname, parsed.port
        valid_port = port is None or 1 <= port <= 65535
        if not hostname or not _valid_hostname(hostname):
            valid_port = False
    except ValueError:
        valid_port = False
    if not valid_port or host.endswith(":"):
        _fail("INVALID_VALUE", path)
    return host


def _profile(service: str, value: object, path: str) -> dict:
    fields = _PROFILE_FIELDS[service]
    row = _object(value, fields, fields, path)
    result = {
        "id": _identifier(row["id"], path + "/id"),
        "name": _text(row["name"], 100, path + "/name"),
    }
    if service == "models":
        result.update(
            base_url=_endpoint(row["base_url"], path + "/base_url"),
            model=_model_id(row["model"], path + "/model"),
        )
    elif service == "search":
        if type(row["provider"]) is not str or row["provider"] not in {
            "openrouter",
            "brave",
            "off",
        }:
            _fail("INVALID_VALUE", path + "/provider")
        reference = row["model_profile_id"]
        result.update(
            provider=row["provider"],
            model_profile_id=None
            if reference is None
            else _identifier(reference, path + "/model_profile_id"),
        )
    else:
        if row["provider"] != "rqdata":
            _fail("INVALID_VALUE", path + "/provider")
        result.update(
            provider="rqdata",
            user=_text(row["user"], 200, path + "/user"),
            host=_rq_host(row["host"], path + "/host"),
        )
    return result


def _section(service: str, value: object) -> dict:
    path = "/" + service
    if type(value) is not dict:
        _fail("INVALID_STRUCTURE", path)
    version = _version(
        value.get("version"), (1, 2) if service == "search" else (1,), path + "/version"
    )
    if service == "search" and version == 1:
        legacy = _object(
            value, {"version", "provider", "model_profile_id"}, {"version", "provider"}, path
        )
        row = {
            "id": "default",
            "name": "Default",
            "provider": legacy["provider"],
            "model_profile_id": legacy.get("model_profile_id"),
        }
        return {
            "version": 2,
            "selection": {"kind": "profile", "id": "default"},
            "profiles": [_profile(service, row, path + "/profiles/0")],
        }
    section = _object(
        value, {"version", "selection", "active_id", "profiles"}, {"version", "profiles"}, path
    )
    if ("selection" in section) == ("active_id" in section):
        _fail("INVALID_SELECTION", path)
    if "selection" in section:
        selection = _selection(section["selection"], path + "/selection")
    else:
        active = section["active_id"]
        selection = (
            {"kind": "environment"}
            if active is None
            else {"kind": "profile", "id": _identifier(active, path + "/active_id")}
        )
    profiles = section["profiles"]
    if type(profiles) is not list:
        _fail("INVALID_STRUCTURE", path + "/profiles")
    if len(profiles) > MAX_PROFILES_PER_SERVICE:
        _fail("TOO_MANY_PROFILES", path + "/profiles")
    rows = [
        _profile(service, row, f"{path}/profiles/{index}") for index, row in enumerate(profiles)
    ]
    identifiers = {row["id"] for row in rows}
    if len(identifiers) != len(rows):
        _fail("DUPLICATE_PROFILE", path + "/profiles")
    if selection["kind"] == "profile" and selection["id"] not in identifiers:
        _fail("INVALID_REFERENCE", path + "/selection/id")
    return {
        "version": version,
        "selection": selection,
        "profiles": rows,
    }


def _check_tree(value: object) -> None:
    pending = [(value, 1, frozenset())]
    count, size = 0, 0
    while pending:
        node, depth, ancestors = pending.pop()
        count += 1
        if count > MAX_NODES:
            _fail("TOO_MANY_VALUES")
        if type(node) in {dict, list}:
            if depth > MAX_DEPTH:
                _fail("DOCUMENT_TOO_DEEP")
            if id(node) in ancestors:
                _fail("INVALID_STRUCTURE")
            if len(node) > MAX_NODES:
                _fail("TOO_MANY_VALUES")
            size += 2 + max(0, len(node) - 1) + (len(node) if type(node) is dict else 0)
            next_ancestors = ancestors | {id(node)}
            if type(node) is dict:
                if any(type(key) is not str for key in node):
                    _fail("INVALID_STRUCTURE")
                nodes = list(node.keys()) + list(node.values())
            else:
                nodes = node
            pending.extend((item, depth + 1, next_ancestors) for item in nodes)
        elif type(node) not in {str, int, float, bool, type(None)}:
            _fail("INVALID_STRUCTURE")
        elif type(node) is float and not math.isfinite(node):
            _fail("INVALID_VALUE")
        elif type(node) is str:
            if len(node) > MAX_DOCUMENT_BYTES:
                _fail("DOCUMENT_TOO_LARGE")
            try:
                size += 2 + len(node.encode("utf-8"))
            except UnicodeError:
                invalid_unicode = True
            else:
                invalid_unicode = False
            if invalid_unicode:
                _fail("INVALID_VALUE")
            size += sum(
                1 if char in '\\"\b\f\n\r\t' else 5 if ord(char) < 32 else 0 for char in node
            )
        else:
            if type(node) is int and node.bit_length() > 64:
                _fail("INVALID_VALUE")
            size += len(str(node))
        if size > MAX_DOCUMENT_BYTES:
            _fail("DOCUMENT_TOO_LARGE")


def _json_text(value: object) -> str:
    try:
        text = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        size = len(text.encode("utf-8"))
    except (ValueError, UnicodeError):
        failure = True
    else:
        failure = False
    if failure:
        _fail("INVALID_STRUCTURE")
    if size > MAX_DOCUMENT_BYTES:
        _fail("DOCUMENT_TOO_LARGE")
    return text


def validate_transfer_document(value: object) -> dict[str, Any]:
    """Validate and migrate a credential-free object, returning a detached value.

    ``selection`` is mandatory in canonical output. A legacy ``active_id: null``
    means environment fallback, never explicit disablement. An environment
    selection contains no resolved values and authorizes no runtime behavior.
    """
    _check_tree(value)
    document = _object(
        value, {"format", "version", *SERVICES}, {"format", "version", *SERVICES}, ""
    )
    if document["format"] != FORMAT:
        _fail("INVALID_VALUE", "/format")
    _version(document["version"], (TRANSFER_VERSION,), "/version")
    result = {"format": FORMAT, "version": TRANSFER_VERSION}
    result.update((service, _section(service, document[service])) for service in SERVICES)
    models = {row["id"]: row for row in result["models"]["profiles"]}
    for index, row in enumerate(result["search"]["profiles"]):
        reference = row["model_profile_id"]
        if reference is not None and (
            reference not in models or models[reference]["base_url"] != _OPENROUTER_URL
        ):
            _fail("INVALID_REFERENCE", f"/search/profiles/{index}/model_profile_id")
    # Diagnostics above refer to the caller's original array positions.
    for service in SERVICES:
        result[service]["profiles"].sort(key=lambda row: row["id"])
    _json_text(result)
    return result


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("DUPLICATE_KEY")
        result[key] = value
    return result


def _nonfinite(_value: str) -> NoReturn:
    _fail("INVALID_VALUE")


def _decode_json(payload: str | bytes) -> object:
    if type(payload) not in {str, bytes}:
        _fail("INVALID_JSON")
    if len(payload) > MAX_DOCUMENT_BYTES:
        _fail("DOCUMENT_TOO_LARGE")
    try:
        data = payload.encode("utf-8") if type(payload) is str else payload
    except UnicodeError:
        failure = True
    else:
        failure = False
    if failure:
        _fail("INVALID_JSON")
    if len(data) > MAX_DOCUMENT_BYTES:
        _fail("DOCUMENT_TOO_LARGE")
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        failure = True
    else:
        failure = False
    if failure:
        _fail("INVALID_JSON")
    # Bound parser recursion before json.loads. Brackets inside strings are data.
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                _fail("DOCUMENT_TOO_DEEP")
        elif char in "]}":
            depth -= 1
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        code = exc.code if isinstance(exc, TransferValidationError) else "INVALID_JSON"
    else:
        code = None
    if code is not None:
        # Raise outside the except block: JSONDecodeError.doc contains raw input.
        _fail(code)
    return value


def parse_transfer_json(payload: str | bytes) -> dict[str, Any]:
    """Parse bounded JSON without retaining raw decoder errors or echoing input."""
    return validate_transfer_document(_decode_json(payload))


def serialize_transfer_document(document: object) -> str:
    """Serialize only a validated, canonical, credential-free transfer document."""
    return _json_text(validate_transfer_document(document))


@dataclass(frozen=True)
class SettingsSnapshot:
    """Immutable, sanitized metadata produced from one saved settings snapshot."""

    service: str
    content: str = field(repr=False)


def _project_native_settings(
    service: str, settings: object, *, selection: object
) -> tuple[dict, list[str]]:
    if type(service) is not str or service not in SERVICES or type(settings) is not dict:
        _fail("INVALID_SNAPSHOT")
    choice = _selection(selection, "/" + service + "/selection")
    version = _version(
        settings.get("version"), (1, 2) if service == "search" else (1,), "/" + service + "/version"
    )
    credentials = []
    if service == "search" and version == 1:
        source = _object(
            settings,
            {"version", "provider", "model_profile_id", "brave_api_key"},
            {"version", "provider"},
            "/search",
        )
        secret = source.get("brave_api_key")
        if type(secret) is str and secret:
            credentials.append(secret)
        section = _section(
            service,
            {
                key: source[key]
                for key in ("version", "provider", "model_profile_id")
                if key in source
            },
        )
        if choice != section["selection"]:
            _fail("INVALID_SNAPSHOT", "/search/selection")
    else:
        source = _object(
            settings,
            {"version", "active_id", "profiles"},
            {"version", "active_id", "profiles"},
            "/" + service,
        )
        if source["active_id"] is None:
            if choice["kind"] == "profile":
                _fail("INVALID_SNAPSHOT", "/" + service + "/selection")
        elif choice != {"kind": "profile", "id": source["active_id"]}:
            _fail("INVALID_SNAPSHOT", "/" + service + "/selection")
        profiles = source["profiles"]
        if type(profiles) is not list:
            _fail("INVALID_SNAPSHOT")
        if len(profiles) > MAX_PROFILES_PER_SERVICE:
            _fail("TOO_MANY_PROFILES", "/" + service + "/profiles")
        rows = []
        fields = _PROFILE_FIELDS[service]
        for index, value in enumerate(profiles):
            row = _object(
                value, fields | {_SECRET_FIELD[service]}, fields, f"/{service}/profiles/{index}"
            )
            secret = row.get(_SECRET_FIELD[service])
            if type(secret) is str and secret:
                credentials.append(secret)
            rows.append({key: row[key] for key in fields})
        section = _section(service, {"version": version, "selection": choice, "profiles": rows})
    _check_tree(section)
    return section, credentials


def _reject_known_credentials(service: str, section: dict, credentials: list[str]) -> None:
    if not credentials:
        return
    for index, row in enumerate(section["profiles"]):
        for key in sorted(row):
            candidate = row[key]
            if type(candidate) is not str:
                continue
            path = f"/{service}/profiles/{index}/{key}"
            for _ in range(_MAX_URL_DECODINGS + 1):
                if any(secret in candidate for secret in credentials):
                    _fail("CREDENTIAL_IN_METADATA", path)
                decoded = unquote(candidate, errors="replace")
                if decoded == candidate:
                    break
                candidate = decoded
            else:
                _fail("INVALID_VALUE", path)


def _snapshot(service: str, section: dict) -> SettingsSnapshot:
    section["profiles"].sort(key=lambda row: row["id"])
    return SettingsSnapshot(service, _json_text(section))


def project_settings_snapshot(
    service: str, settings: object, *, selection: object
) -> SettingsSnapshot:
    """Project one saved snapshot, rejecting its known credentials in metadata.

    Selection intent is explicit because native active_id=None is ambiguous.
    Only nonempty credential strings supplied in this snapshot are compared;
    they are never coerced, serialized, or retained in the returned snapshot.
    Use project_settings_snapshots for checks across all three native inputs.
    """
    section, credentials = _project_native_settings(service, settings, selection=selection)
    _reject_known_credentials(service, section, credentials)
    return _snapshot(service, section)


def project_settings_snapshots(
    *, models: object, search: object, rqdata: object, selections: object
) -> dict[str, SettingsSnapshot]:
    """Project supplied native inputs with cross-service credential checks, no I/O.

    Credentials are used only for in-memory comparisons before serialization;
    neither they nor their fingerprints are retained in any returned snapshot.
    The caller remains responsible for saved provenance and snapshot coherence.
    """
    choices = _object(selections, set(SERVICES), set(SERVICES), "")
    sections, credentials = {}, []
    for service, settings in zip(SERVICES, (models, search, rqdata)):
        section, known = _project_native_settings(service, settings, selection=choices[service])
        sections[service] = section
        credentials.extend(known)
    for service in SERVICES:
        _reject_known_credentials(service, sections[service], credentials)
    return {service: _snapshot(service, sections[service]) for service in SERVICES}


def export_configuration(
    *, models: SettingsSnapshot, search: SettingsSnapshot, rqdata: SettingsSnapshot
) -> dict[str, Any]:
    """Export supplied saved snapshots; this function provides no live atomicity."""
    document = {"format": FORMAT, "version": TRANSFER_VERSION}
    for service, snapshot in zip(SERVICES, (models, search, rqdata)):
        if not isinstance(snapshot, SettingsSnapshot) or snapshot.service != service:
            _fail("INVALID_SNAPSHOT")
        # A manually constructed snapshot must also pass bounded JSON decoding
        # and the complete document's field and reference validation.
        document[service] = _decode_json(snapshot.content)
    return validate_transfer_document(document)


__all__ = [
    "FORMAT",
    "TRANSFER_VERSION",
    "MAX_DOCUMENT_BYTES",
    "MAX_DEPTH",
    "MAX_PROFILES_PER_SERVICE",
    "SettingsSnapshot",
    "TransferValidationError",
    "project_settings_snapshot",
    "project_settings_snapshots",
    "export_configuration",
    "validate_transfer_document",
    "parse_transfer_json",
    "serialize_transfer_document",
]
