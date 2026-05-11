import pytest

from app.services import circuit_breaker


@pytest.mark.asyncio
async def test_breaker_opens_after_threshold():
    b = circuit_breaker.CircuitBreaker("t", failure_threshold=3, cooldown_sec=60)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            async with b.guarded("op"):
                raise RuntimeError("upstream boom")
    assert await b.is_open()
    with pytest.raises(circuit_breaker.CircuitOpen):
        async with b.guarded("op"):
            pass


@pytest.mark.asyncio
async def test_breaker_resets_on_success():
    b = circuit_breaker.CircuitBreaker("t", failure_threshold=2, cooldown_sec=60)
    with pytest.raises(RuntimeError):
        async with b.guarded("op"):
            raise RuntimeError("x")
    async with b.guarded("op"):
        pass
    assert not await b.is_open()


@pytest.mark.asyncio
async def test_breaker_singleton():
    b1 = circuit_breaker.get("share-test")
    b2 = circuit_breaker.get("share-test")
    assert b1 is b2
