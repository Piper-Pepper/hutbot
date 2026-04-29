import os
import discord
from discord import app_commands, Interaction, Role
from discord.ext import commands
from discord.ui import View, Modal, TextInput
from dotenv import load_dotenv
import aiohttp
import logging
from typing import Optional

# =========================
# 🔐 CONFIG
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
# 🏆 CHAMPIONS VIEW
# =========================
class ChampionsView(View):
    def __init__(self, interaction, bot, entries, page=0, guild=None, image_url=None, total=None):
        super().__init__(timeout=None)

        self.interaction = interaction
        self.bot = bot
        self.entries = entries
        self.page = page
        self.guild = guild

        self.per_page = 6
        self.max_page = max(0, (len(entries) - 1) // self.per_page)

        self.total_solved = sum(e[1] for e in entries) if total is None else total

        self.default_image = (
            "https://cdn.discordapp.com/attachments/1383652563408392232/1462480133737943063/riddle_sexy.gif"
        )
        self.first_page_image = image_url or (
            "https://cdn.discordapp.com/attachments/1383652563408392232/1462484539128680715/riddle_porn01.gif"
        )

        self.update_buttons()

    def update_buttons(self):
        self.prev.disabled = self.page <= 0
        self.next.disabled = self.page >= self.max_page

    async def get_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        page = self.entries[start:end]

        embed = discord.Embed(
            title=f"🏆 Riddle Champions | 🧩 {self.total_solved}",
            description=f"Page {self.page + 1} / {self.max_page + 1}",
            color=discord.Color.gold()
        )

        if not page:
            embed.description = "No data."
            return embed

        # 👑 Top user
        if self.page == 0:
            uid = page[0][0]
            user = self.bot.get_user(uid) or (self.guild.get_member(uid) if self.guild else None)

            if user:
                embed.set_author(name=f"👑 Riddle Master: {user.name}", icon_url=user.display_avatar.url)
                embed.set_thumbnail(url=user.display_avatar.url)

        # 📊 Entries
        for i, (uid, solved, percent, xp) in enumerate(page, start=start + 1):

            user = self.bot.get_user(uid) or (self.guild.get_member(uid) if self.guild else None)

            name = user.display_name if user and hasattr(user, "display_name") else (user.name if user else "Unknown")

            embed.add_field(
                name=f"🎖️ {i}. {name}",
                value=f"🧩 {solved} | 📊 {percent:.1f}% | 🧠 {xp} XP",
                inline=False
            )

        embed.set_image(url=self.first_page_image if self.page == 0 else self.default_image)

        if self.guild:
            embed.set_footer(text=self.guild.name, icon_url=self.guild.icon.url if self.guild.icon else None)

        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=await self.get_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: Interaction, button: discord.ui.Button):
        if self.page < self.max_page:
            self.page += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=await self.get_embed(), view=self)


# =========================
# 📤 MODAL
# =========================
class RiddleEditModal(Modal, title="Edit Riddle"):
    def __init__(self, data, guild):
        super().__init__()
        self.guild = guild

        self.text = TextInput(label="Text", default=data.get("text", ""), style=discord.TextStyle.paragraph)
        self.solution = TextInput(label="Solution", default=data.get("solution", ""), style=discord.TextStyle.paragraph)
        self.award = TextInput(label="Award", default=data.get("award", ""), required=False)
        self.image = TextInput(label="Image URL", default=data.get("image-url", ""), required=False)
        self.solution_img = TextInput(label="Solution Image", default=data.get("solution-url", ""), required=False)

        self.add_item(self.text)
        self.add_item(self.solution)
        self.add_item(self.award)
        self.add_item(self.image)
        self.add_item(self.solution_img)

        self.button_id = data.get("button-id", "")

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        data = {
            "text": self.text.value,
            "solution": self.solution.value,
            "award": self.award.value,
            "image-url": self.image.value,
            "solution-url": self.solution_img.value,
            "button-id": self.button_id,
            "riddler": str(interaction.user.id)
        }

        async with aiohttp.ClientSession() as session:
            async with session.put(JSONBIN_BASE_URL, headers=HEADERS, json=data) as r:
                if r.status == 200:
                    await interaction.followup.send("✅ Updated!", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ Error {r.status}", ephemeral=True)


# =========================
# 🎮 COG
# =========================
class RiddleEditor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # =========================
    # /riddle (FIXED INTERACTION SAFE)
    # =========================
    @app_commands.command(name="riddle", description="Edit riddle")
    async def riddle(self, interaction: Interaction, mention: Optional[Role] = None):

        required_role_id = 1393762463861702787

        if not any(r.id == required_role_id for r in interaction.user.roles):
            await interaction.response.send_message("🚫 No permission", ephemeral=True)
            return

        # ⚡ FAST LOAD (NO BLOCKING BEFORE MODAL)
        data = {}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(JSONBIN_BASE_URL + "/latest", headers=HEADERS) as r:
                    result = await r.json()
                    data = result.get("record", {})
        except:
            data = {}

        # optional role save (safe quick call)
        if mention:
            data["button-id"] = str(mention.id)
            async with aiohttp.ClientSession() as session:
                await session.put(JSONBIN_BASE_URL, headers=HEADERS, json=data)

        # 💥 CRITICAL: must be LAST discord response
        await interaction.response.send_modal(RiddleEditModal(data, interaction.guild))

    # =========================
    # /riddle_champ
    # =========================
    @app_commands.command(name="riddle_champ", description="Leaderboard")
    async def riddle_champ(self, interaction: Interaction, visible: Optional[bool] = False, image: Optional[str] = None, mention: Optional[Role] = None):

        await interaction.response.defer(ephemeral=not visible)

        async with aiohttp.ClientSession() as session:
            async with session.get(SOLVED_BIN_URL + "/latest", headers=HEADERS) as r:
                data = await r.json()

        raw = data.get("record", data)

        entries = [
            (int(uid), v.get("solved_riddles", 0), v.get("xp", 0))
            for uid, v in raw.items()
        ]

        entries.sort(key=lambda x: (x[1], x[2]), reverse=True)

        total = sum(x[1] for x in entries)

        enriched = [
            (uid, s, (s / total * 100) if total else 0, xp)
            for uid, s, xp in entries
        ]

        view = ChampionsView(interaction, self.bot, enriched, guild=interaction.guild, image_url=image, total=total)

        embed = await view.get_embed()

        content = None
        if visible:
            content = "<@&1380610400416043089>"
            if mention:
                content += f" {mention.mention}"

        await interaction.followup.send(content=content, embed=embed, view=view, ephemeral=not visible)


# =========================
# 🚀 SETUP
# =========================
async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleEditor(bot))