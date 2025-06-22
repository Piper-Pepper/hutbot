import os
import aiohttp
import asyncio

API_KEY = os.getenv("JSONBIN_API_KEY")
BIN_ID = os.getenv("SOLUTIONS_BIN_ID")

HEADERS = {
    "Content-Type": "application/json",
    "X-Master-Key": API_KEY
}

class SolutionManager:
    def __init__(self):
        self.cache = {}  # Struktur: { riddle_id: { user_id: {solution_text, button_message_id} } }

    async def load_data(self):
        async with aiohttp.ClientSession() as session:
            url = f"https://api.jsonbin.io/v3/b/{BIN_ID}/latest"
            async with session.get(url, headers=HEADERS) as response:
                if response.status == 200:
                    data = await response.json()
                    self.cache = data.get("record", {})
                    # Safety: Stelle sicher, dass Struktur Dict von Dicts ist
                    for rid in list(self.cache.keys()):
                        if not isinstance(self.cache[rid], dict):
                            self.cache[rid] = {}
                else:
                    print(f"⚠️ Failed to load solutions: {response.status}")
                    self.cache = {}

    async def save_data(self):
        async with aiohttp.ClientSession() as session:
            url = f"https://api.jsonbin.io/v3/b/{BIN_ID}"
            async with session.put(url, headers=HEADERS, json=self.cache) as response:
                if response.status != 200:
                    print(f"⚠️ Failed to save solutions: {response.status}")

    def add_solution(self, riddle_id, user_id, solution_text):
        if riddle_id not in self.cache:
            self.cache[riddle_id] = {}
        self.cache[riddle_id][str(user_id)] = {
            "solution_text": solution_text,
            # button_message_id wird erst gesetzt, wenn Daumenbuttons gepostet werden
            "button_message_id": None
        }

    def set_solution_button_message_id(self, riddle_id, user_id, message_id):
        if riddle_id not in self.cache:
            self.cache[riddle_id] = {}
        if str(user_id) not in self.cache[riddle_id]:
            self.cache[riddle_id][str(user_id)] = {}
        self.cache[riddle_id][str(user_id)]["button_message_id"] = message_id

    def get_all_solutions(self):
        return self.cache

solution_manager = SolutionManager()
