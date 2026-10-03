"""POST /login is rate-limited and the limit REFUSES (fw.credential_routes_limited).

A limiter that is declared but never refuses is the defect FLEET-RATE-LIMIT-STANDARD was written
for, so this asserts the 429 itself, and that the key is the address nginx resolved
(``X-Real-IP``), not a client-chosen ``X-Forwarded-For`` entry that would buy a fresh bucket per
request.
"""

from httpx import AsyncClient

from app import config

BAD = {"username": "nobody", "password": "wrong-password"}


async def _exhaust(client: AsyncClient, headers: dict[str, str]) -> None:
    for _ in range(config.WEB_LOGIN_RATE_LIMIT):
        resp = await client.post("/login", data=BAD, headers=headers)
        assert resp.status_code == 400, resp.status_code


async def test_login_refused_after_the_limit(client: AsyncClient) -> None:
    headers = {"X-Real-IP": "203.0.113.10"}
    await _exhaust(client, headers)

    refused = await client.post("/login", data=BAD, headers=headers)
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 0
    assert "Too many login attempts" in refused.text


async def test_forged_forwarded_for_gets_no_fresh_bucket(client: AsyncClient) -> None:
    headers = {"X-Real-IP": "203.0.113.20"}
    await _exhaust(client, headers)

    forged = await client.post(
        "/login",
        data=BAD,
        headers={**headers, "X-Forwarded-For": "198.51.100.99"},
    )
    assert forged.status_code == 429


async def test_limit_is_per_client(client: AsyncClient) -> None:
    await _exhaust(client, {"X-Real-IP": "203.0.113.30"})

    other = await client.post("/login", data=BAD, headers={"X-Real-IP": "203.0.113.31"})
    assert other.status_code == 400
