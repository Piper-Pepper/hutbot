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
# SAFE FETCH
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
            async with session.get(JSONBIN_BASE_URL + "/latest", headers=HEADERS) as r:
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
        super().__init__(timeout=180)

        self.interaction = interaction
        self.client = interaction.client
        self.entries = entries
        self.page = page
        self.guild = guild

        self.per_page = 6
        self.max_page = max((len(entries) - 1) // self.per_page, 0)

        self.total_solved = total or sum(e[1] for e in entries)

        self.default_image = "https://cdn.discordapp.com/attachments/1383652563408392232/1462480133737943063/riddle_sexy.gif"
        self.first_page_image = image_url or "https://cdn.discordapp.com/attachments/1383652563408392232/1462484539128680715/riddle_porn01.gif"

    async def resolve_user(self, uid: int):
        try:
            if self.guild:
                return await self.guild.fetch_member(uid)
        except:
            pass

        try:
            return await self.client.fetch_user(uid)
        except:
            return None

    async def get_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        page_entries = self.entries[start:end]

        embed = discord.Embed(
            title=f"🏆 Riddle Champions | 🧩 {self.total_solved}",
            description=f"Page {self.page + 1} / {self.max_page + 1}",
            color=discord.Color.gold()
        )

        # TOP #1 GLOBAL
        if self.entries:
            top_user = await self.resolve_user(self.entries[0][0])
            if top_user:
                embed.set_author(
                    name=f"👑 Riddle Master #1: {getattr(top_user, 'display_name', str(top_user))}",
                    icon_url=top_user.display_avatar.url
                )
                embed.set_thumbnail(url=top_user.display_avatar.url)

        # ENTRIES
        for i, (uid, solved, percent, xp) in enumerate(page_entries, start=start + 1):
            user = await self.resolve_user(uid)
            name = user.display_name if user else f"User {uid}"

            embed.add_field(
                name=f"🎖️ {i}. {name}",
                value=f"🧩 {solved} | 📊 {percent:.1f}% | 🧠 {xp} XP",
                inline=False
            )

        embed.set_image(
            url=self.first_page_image if self.page == 0 else self.default_image
        )

        return embed

    # =========================
    # BUTTONS (ONLY ONCE!)
    # =========================
    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: Interaction, button):
        await interaction.response.defer()
        if self.page > 0:
            self.page -= 1
        await interaction.message.edit(embed=await self.get_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: Interaction, button):
        await interaction.response.defer()
        if self.page < self.max_page:
            self.page += 1
        await interaction.message.edit(embed=await self.get_embed(), view=self)


# =========================
# COG
# =========================
class RiddleEditor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="riddle")
    async def riddle(self, interaction: Interaction, mention: Optional[Role] = None):

        required_role_id = 1393762463861702787

        if not any(r.id == required_role_id for r in interaction.user.roles):
            await interaction.response.send_message("🚫 No permission.", ephemeral=True)
            return

        data = await fetch_riddle_safe()

        if mention:
            data["button-id"] = str(mention.id)

        await interaction.response.send_modal(RiddleEditModal(data))


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
            async with session.get(SOLVED_BIN_URL + "/latest", headers=HEADERS) as r:
                data = await r.json()

        raw = data.get("record", data)

        entries = []
        for uid, stats in raw.items():
            solved = stats.get("solved_riddles", 0)
            xp = stats.get("xp", 0)
            entries.append((int(uid), solved, xp))

        entries.sort(key=lambda x: (x[1], x[2]), reverse=True)

        total = sum(s for _, s, _ in entries)

        percent_entries = [
            (uid, solved, (solved / total * 100 if total else 0), xp)
            for uid, solved, xp in entries
        ]

        view = ChampionsView(interaction, percent_entries, guild=interaction.guild, image_url=image, total=total)

        embed = await view.get_embed()

        await interaction.followup.send(
            content=mention.mention if (visible and mention) else None,
            embed=embed,
            view=view,
            ephemeral=not visible
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleEditor(bot))