from app.services import request_token


def test_token_roundtrip():
    payload = request_token.payload_for("device-abc", "portrait")
    tok = request_token.issue(payload, secret="testsecret")
    assert tok and len(tok) > 20
    assert request_token.verify(tok, payload, secret="testsecret")


def test_token_rejects_wrong_payload():
    tok = request_token.issue("a|b", secret="s")
    assert not request_token.verify(tok, "a|c", secret="s")


def test_token_rejects_wrong_secret():
    tok = request_token.issue("a|b", secret="s1")
    assert not request_token.verify(tok, "a|b", secret="s2")


def test_token_expires():
    tok = request_token.issue("a|b", secret="s")
    assert request_token.verify(tok, "a|b", secret="s", ttl_sec=10)
    # Negative TTL → considered expired immediately.
    assert not request_token.verify(tok, "a|b", secret="s", ttl_sec=-1)


def test_token_garbage_returns_false():
    assert not request_token.verify("not-a-token", "a|b", secret="s")
    assert not request_token.verify("", "a|b", secret="s")
