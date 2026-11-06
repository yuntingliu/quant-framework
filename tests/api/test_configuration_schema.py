"""Pure transfer contracts: no files, provider initialization, or live snapshots."""

from __future__ import annotations

import copy
import json
import os
import socket
import traceback
from dataclasses import FrozenInstanceError
from pathlib import Path
from urllib.parse import quote, urlsplit

import pytest

from apps.api.services import configuration_schema as schema

CANARIES = ("fixture-model-secret-7913", "fixture-brave-secret-2941", "fixture-rq-secret-8627")


def document():
    return {
        "format": schema.FORMAT,
        "version": 1,
        "models": {
            "version": 1,
            "selection": {"kind": "profile", "id": "router"},
            "profiles": [
                {
                    "id": "router",
                    "name": "Research",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "vendor/model:free",
                }
            ],
        },
        "search": {
            "version": 2,
            "selection": {"kind": "profile", "id": "web"},
            "profiles": [
                {"id": "web", "name": "Web", "provider": "openrouter", "model_profile_id": "router"}
            ],
        },
        "rqdata": {
            "version": 1,
            "selection": {"kind": "profile", "id": "market"},
            "profiles": [
                {
                    "id": "market",
                    "name": "Market",
                    "provider": "rqdata",
                    "user": "saved-account",
                    "host": "rq.example:16011",
                }
            ],
        },
    }


def sources():
    source = copy.deepcopy(document())
    for service, secret_field, canary in zip(
        schema.SERVICES, ("api_key", "brave_api_key", "password"), CANARIES
    ):
        section = source[service]
        section["active_id"] = section.pop("selection")["id"]
        section["profiles"][0][secret_field] = canary
    return {service: source[service] for service in schema.SERVICES}


def project(source):
    return schema.project_settings_snapshots(
        **source,
        selections={service: document()[service]["selection"] for service in schema.SERVICES},
    )


def failure(call, code=None, canaries=CANARIES):
    with pytest.raises(schema.TransferValidationError) as caught:
        call()
    error = caught.value
    if code is not None:
        assert error.code == code
    visible = (
        str(error)
        + repr(error)
        + json.dumps(vars(error))
        + "".join(traceback.format_exception(error))
    )
    for canary in canaries:
        assert canary not in visible
    assert error.__context__ is None
    assert error.__cause__ is None
    return error


def test_private_snapshot_projection_is_allowlisted_and_never_serializes_secret_values():
    class PrivateSecret:
        def __str__(self):
            raise AssertionError("A credential value must never be serialized")

        __repr__ = __str__

    source = sources()
    for service, field in zip(schema.SERVICES, ("api_key", "brave_api_key", "password")):
        source[service]["profiles"][0][field] = PrivateSecret()
    snapshots = project(source)
    exported = schema.export_configuration(**snapshots)
    assert exported == document()
    assert schema.parse_transfer_json(schema.serialize_transfer_document(exported)) == document()
    for snapshot in snapshots.values():
        assert snapshot.content not in repr(snapshot)
        assert set(vars(snapshot)) == {"service", "content"}
    with pytest.raises(FrozenInstanceError):
        snapshots["models"].content = "changed"


def test_canaries_environment_values_and_unrelated_state_are_not_exported(monkeypatch):
    for name in (
        "CONEXUS_MODEL_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "RQ_PASSWORD",
        "RQ_USER",
        "RQ_HOST",
        "ALPHALAB_RUNTIME_DIR",
    ):
        monkeypatch.setenv(name, "fixture-environment-private-value-4268")
    result = schema.serialize_transfer_document(schema.export_configuration(**project(sources())))
    for secret in (*CANARIES, "fixture-environment-private-value-4268"):
        assert secret not in result
    for field in (
        "api_key",
        "brave_api_key",
        "password",
        "has_api_key",
        "has_brave_api_key",
        "has_password",
        "environment_configured",
        "runtime_dir",
        "reports",
    ):
        assert field not in result


@pytest.mark.parametrize(
    "service,field,encoded",
    [
        (service, field, encoded)
        for service, field in [
            ("models", "id"),
            ("models", "name"),
            ("models", "model"),
            ("models", "base_url"),
            ("search", "id"),
            ("search", "name"),
            ("search", "model_profile_id"),
            ("rqdata", "id"),
            ("rqdata", "name"),
            ("rqdata", "user"),
            ("rqdata", "host"),
        ]
        for encoded in (False, True)
        if not encoded or field not in {"id", "model_profile_id", "host"}
    ],
)
def test_known_credentials_in_approved_metadata_are_rejected(service, field, encoded):
    source = sources()[service]
    canary = CANARIES[schema.SERVICES.index(service)]
    value = "".join(f"%{ord(char):02x}" for char in canary) if encoded else canary
    if field == "base_url":
        value = "https://gateway.example/opaque/" + value
    elif field == "host":
        value += ".example"
    source["profiles"][0][field] = value
    if field == "id":
        source["active_id"] = value
    before = copy.deepcopy(source)
    failure(
        lambda: schema.project_settings_snapshot(
            service, source, selection={"kind": "profile", "id": source["active_id"]}
        ),
        "CREDENTIAL_IN_METADATA",
    )
    assert source == before


@pytest.mark.parametrize("service", schema.SERVICES)
def test_credentials_from_later_inactive_profiles_protect_all_saved_metadata(service):
    source = sources()[service]
    field = {"models": "api_key", "search": "brave_api_key", "rqdata": "password"}[service]
    canary = CANARIES[schema.SERVICES.index(service)]
    credential_holder = {**source["profiles"][0], "id": "z-inactive", "name": "Inactive"}
    source["profiles"][0][field] = ""
    source["profiles"][0]["name"] = "prefix-" + canary + "-suffix"
    source["profiles"].append(credential_holder)
    failure(
        lambda: schema.project_settings_snapshot(
            service, source, selection=document()[service]["selection"]
        ),
        "CREDENTIAL_IN_METADATA",
    )


@pytest.mark.parametrize("credential_service", schema.SERVICES)
@pytest.mark.parametrize(
    "metadata_service,field",
    [
        ("models", "name"),
        ("models", "model"),
        ("models", "base_url"),
        ("search", "name"),
        ("rqdata", "name"),
        ("rqdata", "user"),
        ("rqdata", "host"),
    ],
)
def test_batch_projection_checks_all_supplied_credentials_across_services(
    credential_service, metadata_service, field
):
    source = sources()
    canary = CANARIES[schema.SERVICES.index(credential_service)]
    value = canary
    if field == "base_url":
        value = "https://gateway.example/opaque/" + canary
    elif field == "host":
        value += ".example"
    source[metadata_service]["profiles"][0][field] = value
    before = copy.deepcopy(source)
    failure(lambda: project(source), "CREDENTIAL_IN_METADATA")
    assert source == before


def test_batch_projection_checks_legacy_brave_credentials_against_other_services():
    source = sources()
    source["search"] = {"version": 1, "provider": "brave", "brave_api_key": CANARIES[1]}
    source["rqdata"]["profiles"][0]["user"] = CANARIES[1]
    selections = {service: document()[service]["selection"] for service in schema.SERVICES}
    selections["search"] = {"kind": "profile", "id": "default"}
    failure(
        lambda: schema.project_settings_snapshots(**source, selections=selections),
        "CREDENTIAL_IN_METADATA",
    )


@pytest.mark.parametrize("credential_service", schema.SERVICES)
@pytest.mark.parametrize("layers", [1, 2, 4])
def test_known_credentials_in_opaque_nested_url_paths_are_rejected(
    credential_service, layers, caplog
):
    source = sources()
    canary = CANARIES[schema.SERVICES.index(credential_service)]
    encoded = "".join(f"%{ord(char):02x}" for char in canary)
    for _ in range(layers - 1):
        encoded = quote(encoded, safe="")
    source["models"]["profiles"][0]["base_url"] = "https://gateway.example/opaque/" + encoded
    failure(lambda: project(source), "CREDENTIAL_IN_METADATA")
    for secret in CANARIES:
        assert secret not in caplog.text


def test_known_credential_checks_do_not_apply_a_minimum_secret_length():
    source = sources()["models"]
    source["profiles"][0]["api_key"] = "R"
    failure(
        lambda: schema.project_settings_snapshot(
            "models", source, selection=document()["models"]["selection"]
        ),
        "CREDENTIAL_IN_METADATA",
    )


def test_legacy_brave_credential_is_checked_before_snapshot_serialization():
    source = {
        "version": 1,
        "provider": "brave",
        "brave_api_key": CANARIES[1],
        "model_profile_id": CANARIES[1],
    }
    failure(
        lambda: schema.project_settings_snapshot(
            "search", source, selection={"kind": "profile", "id": "default"}
        ),
        "CREDENTIAL_IN_METADATA",
    )


@pytest.mark.parametrize("service", schema.SERVICES)
def test_empty_credentials_do_not_reject_safe_metadata_or_change_selection(service):
    source = sources()
    field = {"models": "api_key", "search": "brave_api_key", "rqdata": "password"}[service]
    source[service]["profiles"][0][field] = ""
    exported = schema.export_configuration(**project(source))
    assert exported == document()
    serialized = schema.serialize_transfer_document(exported)
    for canary in CANARIES:
        assert canary not in serialized


def test_export_and_migration_are_detached_and_do_not_mutate_the_inputs():
    source = sources()
    before = copy.deepcopy(source)
    snapshots = project(source)
    result = schema.export_configuration(**snapshots)
    result["models"]["profiles"][0]["name"] = "Changed result"
    source["models"]["profiles"][0]["name"] = "Changed source"
    assert schema.export_configuration(**snapshots) == document()
    source["models"]["profiles"][0]["name"] = "Research"
    assert source == before


def test_functions_never_read_or_write_storage_environment_or_providers(monkeypatch):
    source = sources()

    def forbidden(*args, **kwargs):
        pytest.fail("Pure transfer functions must not perform I/O or environment resolution")

    with monkeypatch.context() as guard:
        guard.setattr("builtins.open", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(Path, "read_text", forbidden)
        guard.setattr(Path, "write_text", forbidden)
        guard.setattr(os, "open", forbidden)
        guard.setattr(os, "getenv", forbidden)
        guard.setattr(os, "environ", None)
        guard.setattr(socket, "socket", forbidden)
        guard.setattr(socket, "create_connection", forbidden)
        snapshots = project(source)
        single = schema.project_settings_snapshot(
            "models", source["models"], selection=document()["models"]["selection"]
        )
        assert single == snapshots["models"]
        exported = schema.export_configuration(**snapshots)
        assert (
            schema.parse_transfer_json(schema.serialize_transfer_document(exported)) == document()
        )


@pytest.mark.parametrize("service", schema.SERVICES)
@pytest.mark.parametrize("kind", ["profile", "environment", "none"])
def test_selection_intent_round_trips_without_resolving_environment(service, kind):
    value = document()
    identifier = value[service]["selection"]["id"]
    value[service]["selection"] = {
        "kind": kind,
        **({"id": identifier} if kind == "profile" else {}),
    }
    assert schema.parse_transfer_json(schema.serialize_transfer_document(value)) == value
    source = sources()
    source[service]["active_id"] = identifier if kind == "profile" else None
    snapshots = schema.project_settings_snapshots(
        **source, selections={name: value[name]["selection"] for name in schema.SERVICES}
    )
    for name in schema.SERVICES:
        assert (
            schema.project_settings_snapshot(name, source[name], selection=value[name]["selection"])
            == snapshots[name]
        )
    assert schema.export_configuration(**snapshots) == value


@pytest.mark.parametrize("service", schema.SERVICES)
def test_legacy_null_active_selection_means_environment_and_is_not_disabled(service):
    value = document()
    value[service].pop("selection")
    value[service]["active_id"] = None
    original = copy.deepcopy(value)
    result = schema.validate_transfer_document(value)
    assert result[service]["selection"] == {"kind": "environment"}
    assert value == original
    assert schema.parse_transfer_json(schema.serialize_transfer_document(result)) == result


@pytest.mark.parametrize("service", schema.SERVICES)
def test_legacy_active_id_and_literal_environment_profile_id_are_not_ambiguous(service):
    value = document()
    value[service]["profiles"][0]["id"] = "environment"
    value[service].pop("selection")
    value[service]["active_id"] = "environment"
    if service == "models":
        value["search"]["profiles"][0]["model_profile_id"] = "environment"
    result = schema.validate_transfer_document(value)
    assert result[service]["selection"] == {"kind": "profile", "id": "environment"}
    assert schema.parse_transfer_json(schema.serialize_transfer_document(result)) == result


@pytest.mark.parametrize("provider", ["openrouter", "brave", "off"])
def test_search_v1_migrates_in_memory_and_off_remains_a_saved_profile(provider):
    value = document()
    value["search"] = {"version": 1, "provider": provider, "model_profile_id": "router"}
    before = copy.deepcopy(value)
    result = schema.validate_transfer_document(value)
    assert result["search"] == {
        "version": 2,
        "selection": {"kind": "profile", "id": "default"},
        "profiles": [
            {"id": "default", "name": "Default", "provider": provider, "model_profile_id": "router"}
        ],
    }
    assert value == before
    assert schema.parse_transfer_json(schema.serialize_transfer_document(result)) == result
    private = {**before["search"], "brave_api_key": CANARIES[1]}
    snapshot = schema.project_settings_snapshot(
        "search", private, selection={"kind": "profile", "id": "default"}
    )
    assert CANARIES[1] not in snapshot.content
    assert json.loads(snapshot.content) == result["search"]
    assert private == {**before["search"], "brave_api_key": CANARIES[1]}


def test_search_v1_optional_reference_and_legacy_saved_selections_migrate():
    value = document()
    value["search"] = {"version": 1, "provider": "brave"}
    for service in ("models", "rqdata"):
        value[service]["active_id"] = value[service].pop("selection")["id"]
    result = schema.validate_transfer_document(value)
    assert result["search"]["profiles"][0]["model_profile_id"] is None
    assert result["models"]["selection"] == document()["models"]["selection"]


@pytest.mark.parametrize(
    "field",
    [
        "api_key",
        "brave_api_key",
        "password",
        "token",
        "authorization",
        "has_api_key",
        "environment_configured",
        "runtime_paths",
        "research",
        "reports",
        CANARIES[0],
        "__proto__",
        "$ref",
    ],
)
@pytest.mark.parametrize("location", ["root", "section", "profile", "selection"])
def test_unknown_and_credential_properties_are_rejected_without_echoing_keys_or_values(
    field, location
):
    value = document()
    target = {
        "root": value,
        "section": value["models"],
        "profile": value["models"]["profiles"][0],
        "selection": value["models"]["selection"],
    }[location]
    target[field] = CANARIES[1]
    error = failure(lambda: schema.validate_transfer_document(value), "FIELD_NOT_ALLOWED")
    assert field not in error.path


@pytest.mark.parametrize(
    "service,field", [("models", "api_key"), ("search", "brave_api_key"), ("rqdata", "password")]
)
@pytest.mark.parametrize("placeholder", ["", "********", None, False])
def test_even_empty_or_masked_credentials_are_forbidden_in_transfer(service, field, placeholder):
    value = document()
    value[service]["profiles"][0][field] = placeholder
    failure(lambda: schema.serialize_transfer_document(value), "FIELD_NOT_ALLOWED")


@pytest.mark.parametrize(
    "payload",
    [
        '{"x":1,"x":2}',
        '{"x":1,"\\u0078":2}',
        '{"profiles":[{"id":"a","id":"b"}]}',
        '{"fixture-model-secret-7913":1,"fixture-model-secret-7913":2}',
    ],
)
def test_duplicate_json_keys_are_rejected_at_every_depth(payload):
    failure(lambda: schema.parse_transfer_json(payload), "DUPLICATE_KEY")


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        "fixture-model-secret-7913",
        '{"x":"fixture-model-secret-7913"',
        "{} {}",
        "",
        "\ud800",
        123,
        None,
    ],
)
def test_parser_failures_never_keep_raw_decoder_exception_context(payload):
    failure(lambda: schema.parse_transfer_json(payload), "INVALID_JSON")


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e9999"])
def test_nonfinite_numbers_are_rejected(literal):
    failure(lambda: schema.parse_transfer_json('{"version":' + literal + "}"), "INVALID_VALUE")


@pytest.mark.parametrize("version", [None, True, False, 0, -1, 2, "1", 1.0])
def test_transfer_version_is_strict(version):
    value = document()
    value["version"] = version
    failure(lambda: schema.validate_transfer_document(value), "UNSUPPORTED_VERSION")


@pytest.mark.parametrize(
    "service,version",
    [
        ("models", 2),
        ("models", True),
        ("search", 0),
        ("search", 3),
        ("search", "2"),
        ("rqdata", 2),
        ("rqdata", 1.0),
    ],
)
def test_unknown_native_schema_versions_are_rejected(service, version):
    value = document()
    value[service]["version"] = version
    failure(lambda: schema.validate_transfer_document(value), "UNSUPPORTED_VERSION")


@pytest.mark.parametrize(
    "identifier",
    ["", "UPPER", "../path", "a/b", " a", "a ", "x" * 65, "用户", "a\u200b", 1, False, None, []],
)
def test_profile_ids_are_strict_without_coercion(identifier):
    value = document()
    value["models"]["profiles"][0]["id"] = identifier
    failure(lambda: schema.validate_transfer_document(value), "INVALID_VALUE")


@pytest.mark.parametrize("service", schema.SERVICES)
def test_duplicates_and_missing_saved_selection_are_rejected(service):
    value = document()
    value[service]["profiles"] *= 2
    failure(lambda: schema.validate_transfer_document(value), "DUPLICATE_PROFILE")
    value = document()
    value[service]["selection"]["id"] = "missing"
    failure(lambda: schema.validate_transfer_document(value), "INVALID_REFERENCE")


@pytest.mark.parametrize(
    "selection",
    [
        {},
        {"kind": "other"},
        {"kind": "profile"},
        {"kind": "none", "id": None},
        {"kind": "environment", "id": "router"},
        {"kind": False},
        None,
    ],
)
def test_selection_shape_is_explicit(selection):
    value = document()
    value["models"]["selection"] = selection
    failure(
        lambda: schema.validate_transfer_document(value),
        "INVALID_SELECTION" if selection else "INVALID_STRUCTURE",
    )


def test_neither_ambiguous_nor_unspecified_selection_is_accepted():
    value = document()
    value["models"]["active_id"] = None
    failure(lambda: schema.validate_transfer_document(value), "INVALID_SELECTION")
    value["models"].pop("selection")
    value["models"].pop("active_id")
    failure(lambda: schema.validate_transfer_document(value), "INVALID_SELECTION")


@pytest.mark.parametrize("reference", ["missing", "", "../router", False, 7])
def test_explicit_search_references_cannot_fall_back_to_another_model(reference):
    value = document()
    value["search"]["profiles"][0]["model_profile_id"] = reference
    failure(
        lambda: schema.validate_transfer_document(value),
        "INVALID_REFERENCE" if reference == "missing" else "INVALID_VALUE",
    )


def test_explicit_references_require_openrouter_but_not_an_active_or_credentialed_profile():
    value = document()
    value["models"]["selection"] = {"kind": "none"}
    assert schema.validate_transfer_document(value) == value
    value["models"]["profiles"][0]["base_url"] = "https://another.example/v1"
    failure(lambda: schema.validate_transfer_document(value), "INVALID_REFERENCE")


@pytest.mark.parametrize(
    "operation", [schema.validate_transfer_document, schema.serialize_transfer_document]
)
def test_unsorted_search_reference_errors_preserve_the_original_input_index(operation):
    value = document()
    value["search"]["profiles"] = [
        {"id": "z-valid", "name": "Valid", "provider": "openrouter", "model_profile_id": "router"},
        {
            "id": "a-invalid",
            "name": "Invalid",
            "provider": "openrouter",
            "model_profile_id": "missing",
        },
    ]
    value["search"]["selection"] = {"kind": "profile", "id": "z-valid"}
    before = copy.deepcopy(value)
    error = failure(lambda: operation(value), "INVALID_REFERENCE")
    assert error.path == "/search/profiles/1/model_profile_id"
    error = failure(lambda: schema.parse_transfer_json(json.dumps(value)), "INVALID_REFERENCE")
    assert error.path == "/search/profiles/1/model_profile_id"
    assert value == before
    value["search"]["profiles"][1]["model_profile_id"] = "router"
    result = schema.validate_transfer_document(value)
    assert [row["id"] for row in result["search"]["profiles"]] == ["a-invalid", "z-valid"]
    assert value["search"]["profiles"][0]["id"] == "z-valid"


@pytest.mark.parametrize("kind", ["environment", "none"])
def test_dynamic_search_reference_is_metadata_only_and_does_not_resolve_environment(kind):
    value = document()
    value["models"]["selection"] = {"kind": kind}
    value["search"]["profiles"][0]["model_profile_id"] = None
    assert schema.validate_transfer_document(value) == value


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example/v1",
        "file:///private/key",
        "https://user:fixture-model-secret-7913@example.com/v1",
        "https://user@example.com/v1",
        "https://example.com/v1?key=fixture-model-secret-7913",
        "https://example.com/v1#fixture-model-secret-7913",
        "https://example.com/v1?",
        "https://example.com/v1#",
        "https://example.com/token/fixture-model-secret-7913/v1",
        "https://example.com/api-key=fixture-model-secret-7913/v1",
        "https://example.com/sk-or-v1-examplecredential/v1",
        "https://example.com/%74oken/secret/v1",
        "https://example.com/v1%3fapi_key%3dsecret",
        "https://user%40example.com/v1",
        "https://example.com:fixture-model-secret-7913/v1",
        "https://example.com:0/v1",
        "https://example.com:65536/v1",
        "https://[invalid/v1",
        "https://example.com\\@other.example/v1",
        "https://example.com/\nsecret",
        "https://example.com/%0asecret",
        "https://example.com/%e2%80%aesecret",
        "https:///v1",
        "//example.com/v1",
        "https://example.com/%ff",
        "https://user%3Afixture-model-secret-7913%40example.com/v1",
        "https://user%253Afixture-model-secret-7913%2540example.com/v1",
        "https://example.com/v1;api_key=fixture-model-secret-7913",
        "https://example.com/v1;mode=x&API_KEY=fixture-model-secret-7913",
        "https://example.com/v1;mode=x,Access_Token=fixture-model-secret-7913",
        "https://example.com/v1/api_key.fixture-model-secret-7913",
        "https://example.com/v1/ToKeN,fixture-model-secret-7913",
        "https://example.com/v1;api_key[]=fixture-model-secret-7913",
    ],
)
def test_unsafe_and_credential_bearing_urls_are_rejected(url):
    value = document()
    value["models"]["profiles"][0]["base_url"] = url
    value["search"]["profiles"][0]["model_profile_id"] = None
    failure(lambda: schema.validate_transfer_document(value), "UNSAFE_URL")


def encoded_parameter_url(segment, layers):
    for _ in range(layers):
        segment = quote(segment, safe="")
    url = "https://gateway.example/v1/" + segment
    parsed = urlsplit(url)
    assert parsed.scheme == "https"
    assert parsed.hostname == "gateway.example"
    assert parsed.netloc == "gateway.example"
    assert parsed.username is None and parsed.password is None
    assert parsed.path.startswith("/v1/")
    assert not parsed.query and not parsed.fragment
    return url


@pytest.mark.parametrize("layers", [0, 1, 2, 3, 4])
@pytest.mark.parametrize(
    "label,canary",
    [
        ("api_key", CANARIES[0]),
        ("BrAvE_ApI_KeY", CANARIES[1]),
        ("ACCESS_TOKEN", CANARIES[0]),
        ("Password", CANARIES[2]),
        ("Authorization", CANARIES[1]),
    ],
)
def test_credential_path_parameters_are_rejected_through_nested_encodings(layers, label, canary):
    value = document()
    value["models"]["profiles"][0]["base_url"] = encoded_parameter_url(
        "mode=x&" + label + "=" + canary, layers
    )
    value["search"]["profiles"][0]["model_profile_id"] = None
    failure(lambda: schema.validate_transfer_document(value), "UNSAFE_URL")


@pytest.mark.parametrize("layers", [0, 1, 2, 3, 4])
def test_safe_nested_path_parameters_are_accepted_within_the_decoding_limit(layers):
    value = document()
    value["models"]["profiles"][0]["base_url"] = encoded_parameter_url("mode=x&region=eu", layers)
    value["search"]["profiles"][0]["model_profile_id"] = None
    assert schema.validate_transfer_document(value) == value


@pytest.mark.parametrize("layers", [5, 8])
def test_excessive_url_decoding_depth_is_rejected_without_credential_labels(layers):
    value = document()
    value["models"]["profiles"][0]["base_url"] = encoded_parameter_url("mode=x&region=eu", layers)
    value["search"]["profiles"][0]["model_profile_id"] = None
    failure(lambda: schema.validate_transfer_document(value), "UNSAFE_URL")


@pytest.mark.parametrize("path", ["/v1/%", "/v1/%0", "/v1/%GG", "/v1/%0g", "/v1/ok%20bad%"])
def test_malformed_percent_escapes_are_rejected_before_url_decoding(path):
    value = document()
    value["models"]["profiles"][0]["base_url"] = "https://gateway.example" + path
    value["search"]["profiles"][0]["model_profile_id"] = None
    failure(lambda: schema.parse_transfer_json(json.dumps(value)), "UNSAFE_URL")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:1234/v1",
        "http://[::1]:1234/v1",
        "https://gateway.example/custom/v1",
        "https://openrouter.ai/api/v1/",
        "https://gateway.example/v1;version=2&region=eu",
        "https://gateway.example/100%25/v1",
        "https://gateway.example/literal%25zz/v1",
        "https://gateway.example/literal%25ff/v1",
        "https://gateway.example/percent%2525/v1",
        "https://gateway.example/%63ustom/v1",
        "https://gateway.example/%E6%A8%A1%E5%9E%8B/v1",
        "https://gateway.example/custom%2Fv1",
    ],
)
def test_supported_endpoints_are_preserved_without_probing(url):
    value = document()
    value["models"]["profiles"][0]["base_url"] = url
    value["search"]["profiles"][0]["model_profile_id"] = None
    result = schema.validate_transfer_document(value)
    assert result["models"]["profiles"][0]["base_url"] == url.rstrip("/")
    assert schema.parse_transfer_json(schema.serialize_transfer_document(result)) == result


@pytest.mark.parametrize(
    "model",
    [
        "C:/private/runtime/models/weights.bin",
        r"C:\private\runtime\models\weights.bin",
        r"\\server\share\models\weights.bin",
        r"\\?\C:\private\weights.bin",
        "//server/share/models/weights.bin",
        "/private/runtime/models/weights.bin",
        " /private/runtime/models/weights.bin ",
    ],
)
def test_absolute_filesystem_paths_are_not_portable_model_ids(model):
    value = document()
    value["models"]["profiles"][0]["model"] = model
    failure(lambda: schema.validate_transfer_document(value), "INVALID_VALUE")
    source = sources()["models"]
    source["profiles"][0]["model"] = model
    failure(
        lambda: schema.project_settings_snapshot(
            "models", source, selection=value["models"]["selection"]
        ),
        "INVALID_VALUE",
    )


@pytest.mark.parametrize(
    "model", ["model-name", "provider/model-name", "org/team/model:v1", "vendor/model:free"]
)
def test_portable_model_identifiers_with_slashes_remain_supported(model):
    value = document()
    value["models"]["profiles"][0]["model"] = model
    assert schema.parse_transfer_json(schema.serialize_transfer_document(value)) == value


@pytest.mark.parametrize(
    "host",
    [
        "https://rq.example:16011",
        "user:secret@rq.example",
        "rq.example/path",
        "rq.example?key=secret",
        "rq.example#secret",
        "rq.example:notaport",
        "rq.example:0",
        "rq.example:65536",
        "rq.example:",
        "rq.example ",
        "[not-ipv6]:16011",
    ],
)
def test_rq_host_does_not_accept_urls_or_credentials(host):
    value = document()
    value["rqdata"]["profiles"][0]["host"] = host
    failure(lambda: schema.validate_transfer_document(value), "INVALID_VALUE")


@pytest.mark.parametrize(
    "host", ["rq.example:16011", "rq.example", "127.0.0.1:16011", "[::1]:16011"]
)
def test_rq_hosts_are_metadata_not_connection_tests(host):
    value = document()
    value["rqdata"]["profiles"][0]["host"] = host
    assert schema.validate_transfer_document(value)["rqdata"]["profiles"][0]["host"] == host


def test_byte_limit_including_utf8_and_whitespace_is_enforced():
    text = schema.serialize_transfer_document(document())
    padded = text + " " * (schema.MAX_DOCUMENT_BYTES - len(text.encode("utf-8")))
    assert schema.parse_transfer_json(padded) == document()
    failure(lambda: schema.parse_transfer_json(padded + " "), "DOCUMENT_TOO_LARGE")
    failure(
        lambda: schema.parse_transfer_json('"' + "汉" * (schema.MAX_DOCUMENT_BYTES // 2) + '"'),
        "DOCUMENT_TOO_LARGE",
    )
    value = document()
    value["unknown"] = "x" * schema.MAX_DOCUMENT_BYTES
    failure(lambda: schema.validate_transfer_document(value), "DOCUMENT_TOO_LARGE")


def test_depth_cycles_and_container_budget_are_bounded_before_serialization():
    failure(
        lambda: schema.parse_transfer_json(
            "[" * (schema.MAX_DEPTH + 1) + "0" + "]" * (schema.MAX_DEPTH + 1)
        ),
        "DOCUMENT_TOO_DEEP",
    )
    cycle = {}
    cycle["cycle"] = cycle
    failure(lambda: schema.validate_transfer_document(cycle), "INVALID_STRUCTURE")
    failure(
        lambda: schema.validate_transfer_document([None] * (schema.MAX_NODES + 1)),
        "TOO_MANY_VALUES",
    )
    value = document()
    value["models"]["profiles"][0]["name"] = '[[["\\]]]'
    assert schema.parse_transfer_json(schema.serialize_transfer_document(value)) == value


@pytest.mark.parametrize("service", schema.SERVICES)
def test_profile_count_limit_and_input_order_canonicalization(service):
    value = document()
    row = value[service]["profiles"][0]
    value[service]["profiles"] += [{**row, "id": f"p{i:02}"} for i in reversed(range(19))]
    output = schema.validate_transfer_document(value)
    assert len(output[service]["profiles"]) == 20
    assert [row["id"] for row in output[service]["profiles"]] == sorted(
        row["id"] for row in value[service]["profiles"]
    )
    value[service]["profiles"].append({**row, "id": "excess"})
    failure(lambda: schema.validate_transfer_document(value), "TOO_MANY_PROFILES")


def test_independent_installation_snapshots_do_not_share_state(monkeypatch):
    first = project(sources())
    second_source = sources()
    second_source["models"]["profiles"][0]["name"] = "Second installation"
    second_source["models"]["profiles"][0]["api_key"] = "fixture-second-installation-private-key"
    second = project(second_source)
    monkeypatch.setenv("ALPHALAB_RUNTIME_DIR", "private-second-installation-runtime")
    assert schema.export_configuration(**first) == document()
    exported_second = schema.export_configuration(**second)
    assert exported_second["models"]["profiles"][0]["name"] == "Second installation"
    assert "fixture-second-installation-private-key" not in schema.serialize_transfer_document(
        exported_second
    )


@pytest.mark.parametrize(
    "field", ["source", "mode", "environment_configured", "runtime_dir", "private_file", "reports"]
)
def test_public_effective_or_unrelated_snapshots_are_rejected(field):
    value = sources()["models"]
    value[field] = CANARIES[0]
    failure(
        lambda: schema.project_settings_snapshot(
            "models", value, selection=document()["models"]["selection"]
        ),
        "FIELD_NOT_ALLOWED",
    )


def test_snapshot_selection_must_agree_with_saved_active_identity():
    source = sources()
    failure(
        lambda: schema.project_settings_snapshot(
            "models", source["models"], selection={"kind": "environment"}
        ),
        "INVALID_SNAPSHOT",
    )
    source["models"]["active_id"] = None
    failure(
        lambda: schema.project_settings_snapshot(
            "models", source["models"], selection={"kind": "profile", "id": "router"}
        ),
        "INVALID_SNAPSHOT",
    )
    failure(
        lambda: schema.project_settings_snapshot(
            "unknown", source["models"], selection={"kind": "none"}
        ),
        "INVALID_SNAPSHOT",
    )


@pytest.mark.parametrize(
    "content",
    [
        '{"api_key":"fixture-model-secret-7913"}',
        "[]",
        "[" * 9 + "0" + "]" * 9,
        " " * (schema.MAX_DOCUMENT_BYTES + 1),
        '{"x":1,"x":2}',
    ],
)
def test_hand_constructed_snapshot_cannot_bypass_validation(content):
    snapshots = project(sources())
    snapshots["models"] = schema.SettingsSnapshot("models", content)
    failure(lambda: schema.export_configuration(**snapshots))


def test_snapshot_services_cannot_be_swapped():
    snapshots = project(sources())
    snapshots["models"] = snapshots["rqdata"]
    failure(lambda: schema.export_configuration(**snapshots), "INVALID_SNAPSHOT")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:/v1",
        "https://exa!mple.com/v1",
        "https://example..com/v1",
        "https://example%zz.com/v1",
        "https://999.999.999.999/v1",
    ],
)
def test_malformed_endpoint_authorities_are_rejected(url):
    value = document()
    value["models"]["profiles"][0]["base_url"] = url
    failure(lambda: schema.validate_transfer_document(value), "UNSAFE_URL")


def test_nonsecret_path_prefixes_are_not_mistaken_for_token_prefixes():
    value = document()
    value["models"]["profiles"][0]["base_url"] = "https://gateway.example/task-model/v1"
    value["search"]["profiles"][0]["model_profile_id"] = None
    assert schema.validate_transfer_document(value) == value


@pytest.mark.parametrize(
    "metadata",
    ["", "x" * 101, "fixture-model-secret-7913\u202e", "fixture-model-secret-7913\n", None, 7],
)
def test_profile_names_are_bounded_and_not_log_control_sequences(metadata):
    value = document()
    value["models"]["profiles"][0]["name"] = metadata
    failure(lambda: schema.validate_transfer_document(value), "INVALID_VALUE")


def test_malformed_unicode_does_not_retain_input_in_exception_context():
    value = document()
    value["unknown"] = CANARIES[0] + "\ud800"
    failure(lambda: schema.validate_transfer_document(value), "INVALID_VALUE")


@pytest.mark.parametrize("value", [None, False, [], (), {1: "value"}])
def test_invalid_json_object_structures_fail_safely(value):
    failure(lambda: schema.validate_transfer_document(value), "INVALID_STRUCTURE")


def test_profile_namespaces_and_empty_explicit_selections_are_independent():
    value = document()
    for service in schema.SERVICES:
        value[service]["profiles"][0]["id"] = "same"
        value[service]["selection"]["id"] = "same"
    value["search"]["profiles"][0]["model_profile_id"] = "same"
    assert schema.validate_transfer_document(value) == value
    for service in schema.SERVICES:
        value[service]["profiles"] = []
        value[service]["selection"] = {"kind": "none"}
    assert schema.parse_transfer_json(schema.serialize_transfer_document(value)) == value
