import asyncio
import logging
import os
import aiohttp
from aiohttp import web

APP_URL = os.getenv("APP_URL", "")

async def handle(request):
    return web.Response(text="OK", status=200)

async def self_ping():
    """Har 30 dakike me khud ko ping karo — sleep mode se bachne ke liye."""
    await asyncio.sleep(10)
    while True:
        try:
            if APP_URL:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{APP_URL}/health", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        logging.info(f"Self ping: {resp.status}")
        except Exception as e:
            logging.warning(f"Self ping failed: {e}")
        await asyncio.sleep(1800)  # 30 minute = 1800 seconds

async def start_health_server():
    app = web.Application()
    app.router.add_get("/", handle)
    app.router.add_get("/health", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8000)
    await site.start()
    logging.info("Health check server started on port 8000")

    asyncio.create_task(self_ping())
