from apps.api.services.comparison import redact_obj, redact_text


def test_redaction_removes_sensitive():
    obj = {"token": "abc123", "path": "C:\\Users\\x\\secret.txt", "msg": "Bearer sk-test123456789012345"}
    r = redact_obj(obj)
    assert r["token"] == "<redacted>"
    assert r["path"] == "<abs-path>"
    assert "sk-" not in str(r)
    assert "Bearer <redacted>" == r["msg"]


def test_redaction_covers_nested_structures():
    obj = {
        "outer": {
            "api_key": "key-1",
            "connection_file": "conn.json",
            "items": [{"password": "hunter2"}, "plain text"],
        },
    }

    redacted = redact_obj(obj)

    assert redacted["outer"]["api_key"] == "<redacted>"
    assert redacted["outer"]["connection_file"] == "<redacted>"
    assert redacted["outer"]["items"][0]["password"] == "<redacted>"
    assert redacted["outer"]["items"][1] == "plain text"


def test_redaction_removes_long_hex_ids_and_bearer_tokens():
    text = "id 0123456789abcdef0123456789abcdef01234567 auth Bearer abc.def.ghi"

    redacted = redact_text(text)

    assert "0123456789abcdef" not in redacted
    assert "<redacted>" in redacted
    assert "Bearer <redacted>" in redacted


def test_redaction_handles_windows_and_unix_absolute_paths():
    text = "wrote C:\\Users\\max\\results\\run.json and /home/max/results/run.json"

    redacted = redact_text(text)

    assert "C:\\Users" not in redacted
    assert "/home/max" not in redacted
    assert redacted.count("<abs-path>") == 2


def test_redaction_keeps_ordinary_text_and_non_strings():
    assert redact_text("no secrets here") == "no secrets here"
    assert redact_obj({"count": 3, "ok": True, "nothing": None}) == {"count": 3, "ok": True, "nothing": None}


def test_redaction_keeps_relative_paths():
    assert redact_text("results/main/equity.csv") == "results/main/equity.csv"


def test_redaction_does_not_treat_token_counts_as_secrets():
    obj = {"tokens": 1200, "usage": {"total_tokens": 900}, "token": "abc"}

    redacted = redact_obj(obj)

    assert redacted["tokens"] == 1200
    assert redacted["usage"]["total_tokens"] == 900
    assert redacted["token"] == "<redacted>"
    assert redacted["usage"].get("total_tokens") == 900
