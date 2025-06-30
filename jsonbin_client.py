# 📁 jsonbin_client.py
import aiohttp

class JsonBinClient:
    def __init__(self, bin_id: str, api_key: str):
        self.url = f"https://api.jsonbin.io/v3/b/{bin_id}"
        self.headers = {
            "X-Master-Key": api_key,
            "Content-Type": "application/json"
        }

    async def get(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(self.url, headers=self.headers) as resp:
                result = await resp.json()
                return result["record"]

    async def set(self, data):
        async with aiohttp.ClientSession() as session:
            async with session.put(self.url, json=data, headers=self.headers) as resp:
                return await resp.json()

