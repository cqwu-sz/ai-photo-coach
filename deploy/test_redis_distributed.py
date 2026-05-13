"""End-to-end check: do api-a and api-b honour ONE shared OTP RPM
ceiling when REDIS_URL is set?

Run after `docker compose -f deploy/docker-compose.ratelimit-test.yml up`.

We hammer both instances concurrently with > 2× the configured cap
and assert the total accepted is ≤ cap (give or take 1 for race).
If we forgot to plumb Redis through, each instance would accept up
to its own cap, doubling the total.
"""
from __future__ import annotations

import asyncio
import sys
import time

import httpx

# Cap is hard-coded in app.services.otp._GLOBAL_RPM (v17c) = 50.
# We send 80 (40 per instance) and expect total ≤ 55 (cap + small slack).
EXPECTED_CAP = 50
PER_INSTANCE = 40
SLACK = 5

INSTANCES = ["http://localhost:8001", "http://localhost:8002"]


async def _hit(client: httpx.AsyncClient, base: str, idx: int) -> int:
    """Returns 1 on accept (200), 0 on rate-limit (429/503)."""
    # Use unique target per request so per-target cooldown doesn't
    # mask the global RPM check. We're testing the GLOBAL ceiling.
    target = f"+86138{int(time.time()*1000) % 100_000_000:08d}{idx:04d}"[:14]
    try:
        r = await client.post(
            f"{base}/auth/otp/request",
            json={"channel": "phone", "target": target},
            timeout=5,
        )
        return 1 if r.status_code == 200 else 0
    except Exception as e:                                          # noqa: BLE001
        print(f"  ! {base} idx={idx} err={e}")
        return 0


async def main() -> int:
    async with httpx.AsyncClient() as client:
        # Wait for both instances to be up.
        for base in INSTANCES:
            for _ in range(20):
                try:
                    if (await client.get(f"{base}/healthz",
                                            timeout=2)).status_code == 200:
                        break
                except Exception:                                   # noqa: BLE001
                    await asyncio.sleep(1)
            else:
                print(f"FAIL: {base} never came up", file=sys.stderr)
                return 2

        # Fire both instances in parallel within a single minute window.
        t0 = time.time()
        tasks = []
        for base in INSTANCES:
            for i in range(PER_INSTANCE):
                tasks.append(_hit(client, base, i))
        results = await asyncio.gather(*tasks)
        elapsed = time.time() - t0

    accepted = sum(results)
    total = len(results)
    print(f"\n=== Redis distributed limit check ===")
    print(f"  Instances:     {INSTANCES}")
    print(f"  Sent total:    {total}  (per instance: {PER_INSTANCE})")
    print(f"  Accepted:      {accepted}")
    print(f"  Expected cap:  {EXPECTED_CAP} (+{SLACK} slack)")
    print(f"  Wall time:     {elapsed:.1f}s")

    if accepted > EXPECTED_CAP + SLACK:
        print(f"\nFAIL: accepted {accepted} > cap {EXPECTED_CAP}+{SLACK}.")
        print("  → rate_buckets did NOT aggregate across instances.")
        print("  → Check REDIS_URL is set on both api-a and api-b,")
        print("    and that `redis` python pkg is installed in the image.")
        return 1
    if accepted < max(1, EXPECTED_CAP // 2):
        print(f"\nFAIL: accepted {accepted} suspiciously low.")
        print("  → Maybe both instances are 429-storming each other?")
        return 1

    print("\nPASS: cap is shared. Redis backend is doing its job.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
