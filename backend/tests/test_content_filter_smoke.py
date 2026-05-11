from app.services import content_filter


def test_clean_passes():
    assert content_filter.is_clean("外滩观景平台")
    assert content_filter.is_clean("Cool spot 2")


def test_too_short_blocked():
    assert not content_filter.is_clean("a")
    assert not content_filter.is_clean("")


def test_too_long_blocked():
    assert not content_filter.is_clean("外滩" * 30)


def test_url_blocked():
    assert not content_filter.is_clean("see http://evil.com")
    assert not content_filter.is_clean("www.spam.com")


def test_bad_token_blocked():
    assert not content_filter.is_clean("操你 spot")
    assert not content_filter.is_clean("fuck this")


def test_sanitise_returns_none_when_dirty():
    assert content_filter.sanitise(None) is None
    assert content_filter.sanitise("a") is None
    assert content_filter.sanitise("外滩观景平台") == "外滩观景平台"
