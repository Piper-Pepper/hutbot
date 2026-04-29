import os
import discord
from discord import app_commands, Interaction
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
from dotenv import load_dotenv
import aiohttp
import logging
from typing import Optional

load_dotenv()
JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")

JSONBIN_BIN_ID = "685442458a456b7966b13207"
SOLVED_BIN_ID = "686699c18960c979a5b67e34"

JSONBIN_BASE_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
SOLVED_BIN_URL = f"https://api.jsonbin.io/v3/b/{SOLVED_BIN_ID}"

HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY,
    "Content-Type": "application/json"
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =========================
# CHAMPIONS VIEW (UNCHANGED)
# =========================
class ChampionsView(View):
    def __init__(self, interaction, entries, page=0, guild=None, image_url=None, total=None):
        super().__init__(timeout=None)
        self.interaction = interaction
        self.entries = entries
        self.page = page
        self.guild = guild
        self.entries_per_page = 6
        self.max_page = (len(entries) - 1) // self.entries_per_page

        self.total_solved = total or sum(e[1] for e in entries)

        self.default_image_url = "https://cdn.discordapp.com/attachments/1383652563408392232/1462480133737943063/riddle_sexy.gif"
        self.page1_image_url = image_url or "https://cdn.discordapp.com/attachments/1383652563408392232/1462484539128680715/riddle_porn01.gif"

        self.prev.disabled = self.page <= 0
        self.next.disabled = self.page >= self.max_page

    async def get_page_embed(self):
        start = self.page * self.entries_per_page
        end = start + self.entries_per_page
        page_entries = self.entries[start:end]

        embed = discord.Embed(
            title=f"🏆 Riddle Champions ⁉️ Total Solves:🧩{self.total_solved}",
            description=f"Page {self.page + 1} of {self.max_page + 1}",
            color=discord.Color.gold()
        )

        for i, (user_id, solved, percent, xp) in enumerate(page_entries, start=start + 1):
            user = None

            if self.guild:
                try:
                    user = await self.guild.fetch_member(user_id)
                except:
                    user = None

            if not user:
                try:
                    user = await self.interaction.client.fetch_user(user_id)
                except:
                    user = None

            name = user.display_name if user else "Unknown"

            embed.add_field(
                name=f"🎖️ {i}. {name}",
                value=f"🧩 {solved} | 📊 {percent:.1f}% | 🧠 {xp} XP",
                inline=False
            )

        embed.set_image(url=self.page1_image_url if self.page == 0 else self.default_image_url)

        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: Interaction, button: Button):
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=await self.get_page_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: Interaction, button: Button):
        if self.page < self.max_page:
            self.page += 1
            await interaction.response.edit_message(embed=await self.get_page_embed(), view=self)


# =========================
# MODAL
# =========================
class RiddleEditModal(Modal, title="Edit Riddle"):
    def __init__(self, data):
        super().__init__()

        self.button_id = data.get("button-id", "")

        self.text = TextInput(label="Text", default=data.get("text", ""), style=discord.TextStyle.paragraph)
        self.solution = TextInput(label="Solution", default=data.get("solution", ""), style=discord.TextStyle.paragraph)
        self.award = TextInput(label="Award", default=data.get("award", ""), required=False)
        self.image_url = TextInput(label="Image URL", default=data.get("image-url", ""), required=False)
        self.solution_url = TextInput(label="Solution URL", default=data.get("solution-url", ""), required=False)

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
            async with session.put(JSONBIN_BASE_URL, headers=HEADERS, json=updated) as r:
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
        self.riddle_cache = None

    async def cog_load(self):
        """Load riddle once at startup (SAFE, not interaction time)"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(JSONBIN_BASE_URL + "/latest", headers=HEADERS) as r:
                    data = await r.json()
                    self.riddle_cache = data.get("record", {})
                    logger.info("Riddle cache loaded")
        except Exception as e:
            logger.error(f"Cache load failed: {e}")
            self.riddle_cache = {}

    @app_commands.command(name="riddle")
    async def riddle(self, interaction: Interaction, mention: Optional[discord.Role] = None):

        required_role_id = 1393762463861702787

        if not any(r.id == required_role_id for r in interaction.user.roles):
            await interaction.response.send_message("🚫 No permission.", ephemeral=True)
            return

        # 🚨 NOTHING BEFORE THIS LINE
        data = self.riddle_cache or {}

        if mention:
            data["button-id"] = str(mention.id)

        modal = RiddleEditModal(data)

        await interaction.response.send_modal(modal)


# =========================
# SETUP
# =========================
async def setup(bot: commands.Bot):
    cog = RiddleEditor(bot)
    await bot.add_cog(cog)