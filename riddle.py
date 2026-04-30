import os
import discord
from discord import app_commands, Interaction, Role
from discord.ext import commands
from discord.ui import View, Modal, TextInput
from typing import Optional
import aiohttp
import logging
from dotenv import load_dotenv

# =========================
# CONFIG
# =========================
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
# MODAL
# =========================
class RiddleEditModal(discord.ui.Modal, title="Edit Riddle"):
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
# SAFE FETCH (nur für submit / optional future use)
# =========================
async def fetch_riddle_safe():
    empty = {
        "text": "",
        "solution": "",
        "award": "",
        "image-url": "",
        "solution-url": "",
        "button-id": ""
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                JSONBIN_BASE_URL + "/latest",
                headers=HEADERS
            ) as r:
                if r.status != 200:
                    return empty
                data = await r.json()
                return data.get("record", empty)
    except Exception as e:
        logger.warning(f"Fetch failed: {e}")
        return empty


# =========================
# CHAMPIONS VIEW
# =========================
class ChampionsView(View):
    def __init__(self, interaction, entries, page=0, guild=None, image_url=None, total=None):
        super().__init__(timeout=None)

        self.interaction = interaction
        self.entries = entries
        self.page = page
        self.guild = guild

        self.entries_per_page = 6
        self.max_page = max((len(entries) - 1) // self.entries_per_page, 0)

        self.total_solved = total or sum(e[1] for e in entries)

        self.default_image_url = "https://cdn.discordapp.com/attachments/1383652563408392232/1462480133737943063/riddle_sexy.gif"
        self.page1_image_url = image_url or "https://cdn.discordapp.com/attachments/1383652563408392232/1462484539128680715/riddle_porn01.gif"

    async def get_page_embed(self):
        start = self.page * self.entries_per_page
        end = start + self.entries_per_page
        page_entries = self.entries[start:end]

        embed = discord.Embed(
            title=f"🏆 Riddle Champions ⁉️ Total: 🧩 {self.total_solved}",
            description=f"Page {self.page + 1}/{self.max_page + 1}",
            color=discord.Color.gold()
        )

        for i, (uid, solved, percent, xp) in enumerate(page_entries, start=start + 1):
            embed.add_field(
                name=f"🎖️ {i}. User {uid}",
                value=f"🧩 {solved} | 📊 {percent:.1f}% | 🧠 {xp} XP",
                inline=False
            )

        embed.set_image(url=self.page1_image_url if self.page == 0 else self.default_image_url)
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: Interaction, button):
        await interaction.response.defer()
        if self.page > 0:
            self.page -= 1
        await interaction.message.edit(embed=await self.get_page_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: Interaction, button):
        await interaction.response.defer()
        if self.page < self.max_page:
            self.page += 1
        await interaction.message.edit(embed=await self.get_page_embed(), view=self)


# =========================
# COG
# =========================
class RiddleEditor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # =========================
    # RIDDLE COMMAND (FIXED CORE)
    # =========================
    @app_commands.command(name="riddle")
    async def riddle(self, interaction: Interaction, mention: Optional[Role] = None):

        required_role_id = 1393762463861702787

        if not any(r.id == required_role_id for r in interaction.user.roles):
            await interaction.response.send_message("🚫 No permission.", ephemeral=True)
            return

        # ⚠️ ABSOLUTE RULE: NO AWAIT BEFORE MODAL

        data = {
            "text": "",
            "solution": "",
            "award": "",
            "image-url": "",
            "solution-url": "",
            "button-id": str(mention.id) if mention else ""
        }

        modal = RiddleEditModal(data)

        try:
            await interaction.response.send_modal(modal)
        except discord.NotFound:
            logger.warning("Interaction expired before modal")


    # =========================
    # CHAMPIONS
    # =========================
    @app_commands.command(name="riddle_champ")
    async def riddle_champ(
        self,
        interaction: Interaction,
        visible: Optional[bool] = False,
        image: Optional[str] = None,
        mention: Optional[Role] = None,
    ):

        await interaction.response.defer(ephemeral=not visible)

        async with aiohttp.ClientSession() as session:
            async with session.get(SOLVED_BIN_URL + "/latest", headers=HEADERS) as resp:
                if resp.status != 200:
                    await interaction.followup.send("❌ Failed.", ephemeral=True)
                    return
                data = await resp.json()

        raw = data.get("record", data)

        # =========================
        # RAW PARSING (CORRECT)
        # =========================
        entries = []
        for uid, stats in raw.items():
            solved = stats.get("solved_riddles", 0)
            xp = stats.get("xp", 0)
            entries.append((int(uid), solved, xp))

        # sort by solved + xp
        entries.sort(key=lambda x: (x[1], x[2]), reverse=True)

        total = sum(s for _, s, _ in entries)

        # =========================
        # PERCENT LAYER (SEPARATE)
        # =========================
        percent_entries = [
            (uid, solved, (solved / total * 100 if total else 0), xp)
            for uid, solved, xp in entries
        ]

        view = ChampionsView(
            interaction,
            percent_entries,
            guild=interaction.guild,
            image_url=image,
            total=total
        )

        embed = await view.get_page_embed()

        await interaction.followup.send(
            content=mention.mention if (visible and mention) else None,
            embed=embed,
            view=view,
            ephemeral=not visible
        )

# =========================
# SETUP
# =========================
async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleEditor(bot))