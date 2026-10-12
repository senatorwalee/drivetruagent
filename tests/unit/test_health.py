import httpx

from drivetruagent.main import create_app


async def test_live_returns_ok() -> None:
    # ASGITransport calls the app in memory, so no server needs to be running.
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
