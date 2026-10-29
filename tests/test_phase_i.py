import asyncio

import httpx

from app.main import app


def test_console_is_self_contained_and_has_required_operational_controls():
    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/")
            assert response.status_code == 200
            html = response.text
            for text in ("CrashCart", "Run live", "Replay", "Swarm workers", "Telemetry dashboard",
                         "tail baseline", "Incident verdict", "synthetic incident", "DRY-RUN"):
                assert text.lower() in html.lower()
            assert 'repeat(8' in html
            assert "https://" not in html
            assert "fonts.googleapis" not in html
    asyncio.run(scenario())
