# riddle_core.py

import aiohttp
import asyncio
from datetime import datetime
import os

class Core:
    RIDDLE_CREATOR_ROLE_ID = 1380610400416043089
    FIXED_CHANNEL_ID = 1346843244067160074
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
        # Liefert aktuellen UTC-Zeitstempel als string
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

class RiddleManager:
    def __init__(self):
        self.cache = {}  # Lokaler Cache aller Rätsel (dict)
        self.lock = asyncio.Lock()  # Async-Lock für race conditions bei Speicherzugriff

    async def load_data(self):
        # Lädt Rätsel aus JSONBin (Cloud-Speicher) und füllt cache
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
        # Speichert aktuellen cache asynchron zurück zu JSONBin
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
        # Fügt ein neues Rätsel zum Cache hinzu
        self.cache[riddle_id] = data

    def get_riddle(self, riddle_id: str):
        # Holt ein Rätsel anhand der ID oder None, falls nicht vorhanden
        return self.cache.get(riddle_id)

    def remove_riddle(self, riddle_id: str):
        # Löscht ein Rätsel aus dem Cache, falls es existiert
        if riddle_id in self.cache:
            del self.cache[riddle_id]

# Singleton-Instanzen zum Importieren in anderen Modulen
riddle_manager = RiddleManager()
core = Core()
