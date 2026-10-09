"""A request whose Host header is not one of ALLOWED_HOSTS is refused before any route runs.

nginx forwards the client's Host unchanged (`proxy_set_header Host $host`), so without this the
app answers any name pointed at it: links and redirects built from the request carry a host an
attacker chose. `make smoke` probes the same thing on the deployed stack.
"""

from httpx import AsyncClient


async def test_forged_host_is_refused(client: AsyncClient) -> None:
    resp = await client.get("/health/live", headers={"Host": "smoke-forged.invalid"})
    assert resp.status_code == 400


async def test_allowed_host_is_served(client: AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert resp.status_code == 200
