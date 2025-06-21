# riddle_core.py

import aiohttp
import asyncio
from datetime import datetime
import os

class Core:
    RIDDLE_CREATOR_ROLE_ID = 1380610400416043089
    DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg"
    DEFAULT_SOLUTION_IMAGE = "https://cdn.discordapp.com/attachments/1383652563408392232/1384295668176388229/zombie_piper.gif"

    JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")
    JSONBIN_BIN_ID = os.getenv("JSONBIN_BIN_ID")
    JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
    JSONBIN_HEADERS = {
        "X-Master-Key": JSONBIN_API_KEY,
        "Content-Type": "application/json"
    }

    @staticmethod
    def get_timestamp():
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

class RiddleManager:
    def __init__(self):
        self.cache = {}
        self.lock = asyncio.Lock()

    async def load_data(self):
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(Core.JSONBIN_URL, headers=Core.JSONBIN_HEADERS) as resp:
                    if resp.status == 200:
                        json_data = await resp.json()
                        self.cache = json_data.get("record", {})
                        print(f"[RiddleManager] Loaded {len(self.cache)} riddles from jsonbin.io")
                    else:
                        print(f"[RiddleManager] Failed to load data: HTTP {resp.status}")
            except Exception as e:
                print(f"[RiddleManager] Exception while loading data: {e}")

    async def save_data(self):
        async with self.lock:
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.put(Core.JSONBIN_URL, headers=Core.JSONBIN_HEADERS, json=self.cache) as resp:
                        if resp.status in (200, 201):
                            print(f"[RiddleManager] Saved {len(self.cache)} riddles to jsonbin.io")
                        else:
                            print(f"[RiddleManager] Failed to save data: HTTP {resp.status}")
                except Exception as e:
                    print(f"[RiddleManager] Exception while saving data: {e}")

    def add_riddle(self, riddle_id: str, data: dict):
        self.cache[riddle_id] = data

    def get_riddle(self, riddle_id: str):
        return self.cache.get(riddle_id)

    def remove_riddle(self, riddle_id: str):
        if riddle_id in self.cache:
            del self.cache[riddle_id]

riddle_manager = RiddleManager()
core = Core()
