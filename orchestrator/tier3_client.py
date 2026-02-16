import aiohttp

class Tier3Client:
    def __init__(self):
        self.base_url = "http://127.0.0.1:10000"

    async def execute_task(self, envelope):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/compute",
                json={"content": envelope["inputs"]["content"]}
            ) as response:
                return await response.json()
