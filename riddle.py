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
# HELPERS
# =========================
def clean_value(v: Optional[str]):
    if v is None:
        return None
    v = v.strip()
    return v if v else None


# =========================
# SAFE FETCH
# =========================
async def fetch_riddle_safe():
    empty = {
        "text": None,
        "solution": None,
        "award": None,
        "image-url": None,
        "solution-url": None,
        "button-id": None,
        "riddler": None
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(JSONBIN_BASE_URL + "/latest", headers=HEADERS) as r:
                if r.status != 200:
                    return empty

                data = await r.json()
                record = data.get("record", {})

                return {
                    "text": record.get("text"),
                    "solution": record.get("solution"),
                    "award": record.get("award"),
                    "image-url": record.get("image-url"),
                    "solution-url": record.get("solution-url"),
                    "button-id": record.get("button-id"),
                    "riddler": record.get("riddler")
                }

    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return empty


# =========================
# MODALS
# =========================
class RiddleCreateModal(discord.ui.Modal, title="Create Riddle"):
    def __init__(self, mention: Optional[Role]):
        super().__init__()
        self.mention = mention

        self.text = TextInput(label="Text", style=discord.TextStyle.paragraph)
        self.solution = TextInput(label="Solution", style=discord.TextStyle.paragraph)
        self.award = TextInput(label="Award", required=False)
        self.image_url = TextInput(label="Image URL", required=False)
        self.solution_url = TextInput(label="Solution URL", required=False)

        self.add_item(self.text)
        self.add_item(self.solution)
        self.add_item(self.award)
        self.add_item(self.image_url)
        self.add_item(self.solution_url)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        updated = {
            "text": clean_value(self.text.value),
            "solution": clean_value(self.solution.value),
            "award": clean_value(self.award.value),
            "image-url": clean_value(self.image_url.value),
            "solution-url": clean_value(self.solution_url.value),
            "button-id": str(self.mention.id) if self.mention else None,
            "riddler": str(interaction.user.id)
        }

        updated = {k: v for k, v in updated.items() if v is not None}

        async with aiohttp.ClientSession() as session:
            async with session.put(JSONBIN_BASE_URL, headers=HEADERS, json=updated) as r:
                if r.status == 200:
                    await interaction.followup.send("✅ Created!", ephemeral=True)
                else:
                    await interaction.followup.send("❌ Failed.", ephemeral=True)


class RiddleEditModal(discord.ui.Modal, title="Edit Riddle"):
    def __init__(self, data: dict):
        super().__init__()

        self.button_id = data.get("button-id")

        self.text = TextInput(
            label="Text",
            default=data.get("text") or ""
        )

        self.solution = TextInput(
            label="Solution",
            default=data.get("solution") or ""
        )

        self.award = TextInput(
            label="Award",
            default=data.get("award") or "",
            required=False
        )

        self.image_url = TextInput(
            label="Image URL",
            default=data.get("image-url") or "",
            required=False
        )

        self.solution_url = TextInput(
            label="Solution URL",
            default=data.get("solution-url") or "",
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
            "text": clean_value(self.text.value),
            "solution": clean_value(self.solution.value),
            "award": clean_value(self.award.value),
            "image-url": clean_value(self.image_url.value),
            "solution-url": clean_value(self.solution_url.value),
            "button-id": self.button_id,
            "riddler": str(interaction.user.id)
        }

        updated = {k: v for k, v in updated.items() if v is not None}

        async with aiohttp.ClientSession() as session:
            async with session.put(JSONBIN_BASE_URL, headers=HEADERS, json=updated) as r:
                if r.status == 200:
                    await interaction.followup.send("✅ Updated!", ephemeral=True)
                else:
                    await interaction.followup.send("❌ Failed.", ephemeral=True)


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

        self.entries_per_page = 6
        self.max_page = max((len(entries) - 1) // self.entries_per_page, 0)

        self.total_solved = total or sum(e[1] for e in entries)

        self.default_image_url = "https://cdn.discordapp.com/attachments/1383652563408392232/1462480133737943063/riddle_sexy.gif"
        self.page1_image_url = image_url or self.default_image_url

    async def resolve_user(self, uid: int):
        if self.guild:
            member = self.guild.get_member(uid)
            if member:
                return member

        try:
            return await self.client.fetch_user(uid)
        except:
            return None

    async def get_embed(self):
        start = self.page * self.entries_per_page
        end = start + self.entries_per_page
        page_entries = self.entries[start:end]

        embed = discord.Embed(
            title=f"🏆 Riddle Champions ⁉️ Total: 🧩 {self.total_solved}",
            description=f"Page {self.page + 1}/{self.max_page + 1}",
            color=discord.Color.gold()
        )

        if self.entries:
            top_uid = self.entries[0][0]
            top_user = await self.resolve_user(top_uid)

            if top_user:
                embed.set_author(
                    name=f"👑 Riddle Master #1: {top_user.display_name}",
                    icon_url=top_user.display_avatar.url
                )
                embed.set_thumbnail(url=top_user.display_avatar.url)

        for i, (uid, solved, percent, xp) in enumerate(page_entries, start=start + 1):
            user = await self.resolve_user(uid)
            name = user.display_name if user else f"User {uid}"

            embed.add_field(
                name=f"🎖️ {i}. {name}",
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

        member = interaction.user
        if not isinstance(member, discord.Member):
            member = interaction.guild.get_member(interaction.user.id)

        if not member or not any(r.id == required_role_id for r in member.roles):
            await interaction.response.send_message("🚫 No permission.", ephemeral=True)
            return

        await interaction.response.send_modal(RiddleCreateModal(mention))

    @app_commands.command(name="riddle_champ")
    async def riddle_champ(self, interaction: Interaction,
                           visible: Optional[bool] = False,
                           image: Optional[str] = None,
                           mention: Optional[Role] = None):

        await interaction.response.defer(ephemeral=not visible)

        async with aiohttp.ClientSession() as session:
            async with session.get(SOLVED_BIN_URL + "/latest", headers=HEADERS) as resp:
                data = await resp.json()

        raw = data.get("record", {})

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

        view = ChampionsView(
            interaction,
            percent_entries,
            guild=interaction.guild,
            image_url=image,
            total=total
        )

        embed = await view.get_embed()

        await interaction.followup.send(
            content=mention.mention if (visible and mention) else None,
            embed=embed,
            view=view,
            ephemeral=not visible
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleEditor(bot))