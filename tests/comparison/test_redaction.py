from apps.api.services.comparison.redaction import redact_obj


def test_redaction_removes_sensitive():
    obj = {'token': 'abc123', 'path': 'C:\\\\Users\\\\x\\\\secret.txt', 'msg': 'Bearer sk-test123456789012345'}
    r = redact_obj(obj)
    assert r['token'] == '<redacted>'
    assert '<abs-path>' in str(r['path']) or r['path'] == '<redacted>' or '<abs-path>' in r['path']
    assert 'sk-' not in str(r)
