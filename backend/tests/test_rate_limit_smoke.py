import pytest

from app.services import rate_limit


@pytest.mark.asyncio
async def test_consume_drains_then_blocks():
    rate_limit.reset_for_tests()
    for _ in range(3):
        assert await rate_limit.consume("t", "u",
                                          capacity=3, refill_per_sec=0.0)
    assert not await rate_limit.consume("t", "u",
                                          capacity=3, refill_per_sec=0.0)


@pytest.mark.asyncio
async def test_consume_per_identity_independent():
    rate_limit.reset_for_tests()
    assert await rate_limit.consume("t", "a", capacity=1, refill_per_sec=0.0)
    assert not await rate_limit.consume("t", "a", capacity=1, refill_per_sec=0.0)
    assert await rate_limit.consume("t", "b", capacity=1, refill_per_sec=0.0)
