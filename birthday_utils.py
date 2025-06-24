import os
import aiohttp
import asyncio
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Richtiges Laden aus Umgebungsvariablen
JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")
BIRTHDAY_BIN_ID = os.getenv("BIRTHDAY_BIN_ID")
BUTTONS_BIN_ID = os.getenv("BUTTONS_BIN_ID")

JSONBIN_HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY,
    "Content-Type": "application/json"
}

# --- JSONBin: Daten abrufen ---
async def fetch_jsonbin(bin_id):
    async with aiohttp.ClientSession() as session:
        url = f"https://api.jsonbin.io/v3/b/{bin_id}/latest"
        async with session.get(url, headers=JSONBIN_HEADERS) as resp:
            if resp.status != 200:
                print(f"⚠️ JSONBin fetch failed: {resp.status}")
                return {}
            data = await resp.json()
            return data.get("record", {})

# --- JSONBin: Daten aktualisieren ---
async def update_jsonbin(bin_id, new_data):
    async with aiohttp.ClientSession() as session:
        url = f"https://api.jsonbin.io/v3/b/{bin_id}"
        async with session.put(url, headers=JSONBIN_HEADERS, json=new_data) as resp:
            if resp.status != 200:
                print(f"⚠️ JSONBin update failed: {resp.status}")
            return await resp.json()

# --- Geburtstag speichern oder aktualisieren ---
async def birthday_edit(user_id: int, month: int, day: int, timezone: str, year: int | None = None, image_url: str | None = None):
    data = await fetch_jsonbin(BIRTHDAY_BIN_ID)

    data[str(user_id)] = {
        "member_id": str(user_id),
        "month": int(month),
        "day": int(day),
        "year": int(year) if year else None,
        "timezone": timezone,
        "image_url": image_url or None
    }

    await update_jsonbin(BIRTHDAY_BIN_ID, data)

# --- Buttons für Persistenz speichern ---
async def save_button_location(button_id: str, channel_id: int, message_id: int, guild_id: int):
    data = await fetch_jsonbin(BUTTONS_BIN_ID)

    data[button_id] = {
        "channel_id": str(channel_id),
        "message_id": str(message_id),
        "guild_id": str(guild_id)
    }

    await update_jsonbin(BUTTONS_BIN_ID, data)

# --- Geburtstagsliste abrufen ---
async def get_all_birthdays():
    return await fetch_jsonbin(BIRTHDAY_BIN_ID)

# --- Prüfen, ob heute Geburtstag ist ---
def is_birthday_today(birthday_entry, user_timezone: str):
    try:
        # TODO: Timezone berücksichtigen
        now = datetime.utcnow()  # aktuell in UTC
        return (
            int(birthday_entry["day"]) == now.day and
            int(birthday_entry["month"]) == now.month
        )
    except Exception as e:
        print(f"⚠️ Birthday check failed: {e}")
        return False
