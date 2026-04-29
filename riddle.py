import os
import discord
from discord import app_commands, Interaction
from discord.ext import commands
from discord.ui import View, Modal, TextInput
from dotenv import load_dotenv
import aiohttp
import logging
from typing import Optional

load_dotenv()

JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")

JSONBIN_BIN_ID = "685442458a456b7966b13207"

JSONBIN_BASE_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"

HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY,
    "Content-Type": "application/json"
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =========================
# MODAL
# =========================
class RiddleEditModal(Modal, title="Edit Riddle"):
    def __init__(self, data: dict):
        super().__init__()

        self.button_id = data.get("button-id", "")

        self.text = TextInput(
            label="Text",
            default=data.get("text", ""),
            style=discord.TextStyle.paragraph
        )

        self.solution = TextInput(
            label="Solution",
            default=data.get("solution", ""),
            style=discord.TextStyle.paragraph
        )

        self.award = TextInput(
            label="Award",
            default=data.get("award", ""),
            required=False
        )

        self.image_url = TextInput(
            label="Image URL",
            default=data.get("image-url", ""),
            required=False
        )

        self.solution_url = TextInput(
            label="Solution URL",
            default=data.get("solution-url", ""),
            required=False
        )

        self.add_item(self.text)
        self.add_item(self.solution)
        self.add_item(self.award)
        self.add_item(self.image_url)
        self.add_item(self.solution_url)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        updated = {
            "text": self.text.value,
            "solution": self.solution.value,
            "award": self.award.value,
            "image-url": self.image_url.value,
            "solution-url": self.solution_url.value,
            "button-id": self.button_id,
            "riddler": str(interaction.user.id)
        }

        async with aiohttp.ClientSession() as session:
            async with session.put(
                JSONBIN_BASE_URL,
                headers=HEADERS,
                json=updated
            ) as r:
                if r.status == 200:
                    await interaction.followup.send("✅ Saved!", ephemeral=True)
                else:
                    await interaction.followup.send("❌ Save failed.", ephemeral=True)


# =========================
# COG
# =========================
class RiddleEditor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def fetch_latest_riddle(self):
        """Always get fresh data → fixes stale cache issues"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                JSONBIN_BASE_URL + "/latest",
                headers=HEADERS
            ) as r:
                data = await r.json()
                return data.get("record", {})

    @app_commands.command(name="riddle")
    async def riddle(
        self,
        interaction: Interaction,
        mention: Optional[discord.Role] = None
    ):

        required_role_id = 1393762463861702787

        # ensure we have member context
        if not hasattr(interaction.user, "roles"):
            await interaction.response.send_message("🚫 No permission context.", ephemeral=True)
            return

        if not any(r.id == required_role_id for r in interaction.user.roles):
            await interaction.response.send_message("🚫 No permission.", ephemeral=True)
            return

        # 🔥 ALWAYS FRESH DATA (fixes your issue)
        data = await self.fetch_latest_riddle()

        # optional override
        if mention:
            data["button-id"] = str(mention.id)

        modal = RiddleEditModal(data)

        # IMPORTANT: no awaits before this
        await interaction.response.send_modal(modal)


# =========================
# SETUP
# =========================
async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleEditor(bot))