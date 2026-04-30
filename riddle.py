import os
import logging
from typing import Optional, Any

import aiohttp
import discord
from discord import app_commands, Interaction, Role
from discord.ext import commands
from discord.ui import View, Modal, TextInput
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

REQUIRED_ROLE_ID = 1393762463861702787
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=20)

HEADERS = {
    "X-Master-Key": JSONBIN_API_KEY or "",
    "Content-Type": "application/json"
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =========================
# HELPERS
# =========================
def clean_value(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    return v if v else None


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def is_configured() -> bool:
    return bool(JSONBIN_API_KEY and JSONBIN_API_KEY.strip())


def get_empty_riddle() -> dict:
    return {
        "text": None,
        "solution": None,
        "award": None,
        "image-url": None,
        "solution-url": None,
        "button-id": None,
        "riddler": None
    }


def get_display_name(user: discord.abc.User) -> str:
    return getattr(user, "display_name", getattr(user, "name", "Unknown"))


# =========================
# JSONBIN CLIENT HELPERS
# =========================
async def jsonbin_get_record(bin_url: str) -> dict:
    if not is_configured():
        logger.error("JSONBIN_API_KEY missing.")
        return {}

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            async with session.get(f"{bin_url}/latest", headers=HEADERS) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"JSONBin GET failed ({resp.status}): {body[:500]}")
                    return {}

                data = await resp.json(content_type=None)
                record = data.get("record", {})
                return record if isinstance(record, dict) else {}
    except Exception as e:
        logger.exception(f"JSONBin GET exception: {e}")
        return {}


async def jsonbin_put_record(bin_url: str, record: dict) -> bool:
    if not is_configured():
        logger.error("JSONBIN_API_KEY missing.")
        return False

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            async with session.put(bin_url, headers=HEADERS, json=record) as resp:
                if resp.status == 200:
                    return True

                body = await resp.text()
                logger.error(f"JSONBin PUT failed ({resp.status}): {body[:500]}")
                return False
    except Exception as e:
        logger.exception(f"JSONBin PUT exception: {e}")
        return False


async def fetch_riddle_safe() -> dict:
    empty = get_empty_riddle()
    record = await jsonbin_get_record(JSONBIN_BASE_URL)
    if not record:
        return empty

    return {
        "text": record.get("text"),
        "solution": record.get("solution"),
        "award": record.get("award"),
        "image-url": record.get("image-url"),
        "solution-url": record.get("solution-url"),
        "button-id": record.get("button-id"),
        "riddler": record.get("riddler")
    }


# =========================
# MODALS
# =========================
class RiddleCreateModal(Modal, title="Create Riddle"):
    def __init__(self, mention: Optional[Role]):
        super().__init__()
        self.mention = mention

        self.text = TextInput(
            label="Text",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000
        )
        self.solution = TextInput(
            label="Solution",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000
        )
        self.award = TextInput(label="Award", required=False, max_length=200)
        self.image_url = TextInput(label="Image URL", required=False, max_length=1000)
        self.solution_url = TextInput(label="Solution URL", required=False, max_length=1000)

        self.add_item(self.text)
        self.add_item(self.solution)
        self.add_item(self.award)
        self.add_item(self.image_url)
        self.add_item(self.solution_url)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        if not is_configured():
            await interaction.followup.send("❌ JSONBIN_API_KEY fehlt in der .env.", ephemeral=True)
            return

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

        ok = await jsonbin_put_record(JSONBIN_BASE_URL, updated)
        if ok:
            await interaction.followup.send("✅ Created!", ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed while saving to JSONBin.", ephemeral=True)


class RiddleEditModal(Modal, title="Edit Riddle"):
    def __init__(self, data: dict):
        super().__init__()

        self.button_id = data.get("button-id")

        self.text = TextInput(
            label="Text",
            style=discord.TextStyle.paragraph,
            default=data.get("text") or "",
            required=True,
            max_length=4000
        )
        self.solution = TextInput(
            label="Solution",
            style=discord.TextStyle.paragraph,
            default=data.get("solution") or "",
            required=True,
            max_length=4000
        )
        self.award = TextInput(
            label="Award",
            default=data.get("award") or "",
            required=False,
            max_length=200
        )
        self.image_url = TextInput(
            label="Image URL",
            default=data.get("image-url") or "",
            required=False,
            max_length=1000
        )
        self.solution_url = TextInput(
            label="Solution URL",
            default=data.get("solution-url") or "",
            required=False,
            max_length=1000
        )

        self.add_item(self.text)
        self.add_item(self.solution)
        self.add_item(self.award)
        self.add_item(self.image_url)
        self.add_item(self.solution_url)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        if not is_configured():
            await interaction.followup.send("❌ JSONBIN_API_KEY fehlt in der .env.", ephemeral=True)
            return

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

        ok = await jsonbin_put_record(JSONBIN_BASE_URL, updated)
        if ok:
            await interaction.followup.send("✅ Updated!", ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed while saving to JSONBin.", ephemeral=True)


# =========================
# CHAMPIONS VIEW
# =========================
class ChampionsView(View):
    def __init__(
        self,
        interaction: Interaction,
        entries: list[tuple[int, int, float, int]],
        page: int = 0,
        guild: Optional[discord.Guild] = None,
        image_url: Optional[str] = None,
        total: Optional[int] = None,
        owner_id: Optional[int] = None
    ):
        super().__init__(timeout=300)

        self.client = interaction.client
        self.entries = entries
        self.page = page
        self.guild = guild
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None

        self.entries_per_page = 6
        self.max_page = max((len(entries) - 1) // self.entries_per_page, 0)
        self.total_solved = total if total is not None else sum(e[1] for e in entries)

        self.default_image_url = "https://cdn.discordapp.com/attachments/1383652563408392232/1462480133737943063/riddle_sexy.gif"
        self.page1_image_url = image_url or self.default_image_url

        self._user_cache: dict[int, discord.abc.User] = {}
        self._sync_buttons()

    def _sync_buttons(self):
        self.prev.disabled = self.page <= 0
        self.next.disabled = self.page >= self.max_page

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await interaction.response.send_message("🚫 Das Menü gehört nicht dir.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    async def resolve_user(self, uid: int) -> Optional[discord.abc.User]:
        if uid in self._user_cache:
            return self._user_cache[uid]

        user_obj: Optional[discord.abc.User] = None

        if self.guild:
            member = self.guild.get_member(uid)
            if member:
                user_obj = member

        if user_obj is None:
            try:
                user_obj = await self.client.fetch_user(uid)
            except Exception:
                user_obj = None

        if user_obj is not None:
            self._user_cache[uid] = user_obj

        return user_obj

    async def get_embed(self) -> discord.Embed:
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
                    name=f"👑 Riddle Master #1: {get_display_name(top_user)}",
                    icon_url=top_user.display_avatar.url
                )
                embed.set_thumbnail(url=top_user.display_avatar.url)

        if not page_entries:
            embed.add_field(name="Noch keine Daten", value="Es wurden noch keine Rätsel gelöst.", inline=False)
        else:
            for i, (uid, solved, percent, xp) in enumerate(page_entries, start=start + 1):
                user = await self.resolve_user(uid)
                name = get_display_name(user) if user else f"User {uid}"
                embed.add_field(
                    name=f"🎖️ {i}. {name}",
                    value=f"🧩 {solved} | 📊 {percent:.1f}% | 🧠 {xp} XP",
                    inline=False
                )

        embed.set_image(url=self.page1_image_url if self.page == 0 else self.default_image_url)
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        self._sync_buttons()

        embed = await self.get_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: Interaction, button: discord.ui.Button):
        if self.page < self.max_page:
            self.page += 1
        self._sync_buttons()

        embed = await self.get_embed()
        await interaction.response.edit_message(embed=embed, view=self)


# =========================
# COG
# =========================
class RiddleEditor(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="riddle", description="Create or edit the current riddle.")
    @app_commands.guild_only()
    async def riddle(self, interaction: Interaction, mention: Optional[Role] = None):
        if not is_configured():
            await interaction.response.send_message("❌ JSONBIN_API_KEY fehlt in der .env.", ephemeral=True)
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            if interaction.guild is None:
                await interaction.response.send_message("🚫 Nur auf dem Server nutzbar.", ephemeral=True)
                return
            member = interaction.guild.get_member(interaction.user.id)

        if not member or not any(r.id == REQUIRED_ROLE_ID for r in member.roles):
            await interaction.response.send_message("🚫 No permission.", ephemeral=True)
            return

        data = await fetch_riddle_safe()
        has_riddle = bool(data.get("text") or data.get("solution"))

        if has_riddle:
            await interaction.response.send_modal(RiddleEditModal(data))
        else:
            await interaction.response.send_modal(RiddleCreateModal(mention))

    @app_commands.command(name="riddle_champ", description="Show riddle champions leaderboard.")
    @app_commands.guild_only()
    async def riddle_champ(
        self,
        interaction: Interaction,
        visible: Optional[bool] = False,
        image: Optional[str] = None,
        mention: Optional[Role] = None
    ):
        if not is_configured():
            await interaction.response.send_message("❌ JSONBIN_API_KEY fehlt in der .env.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=not visible, thinking=True)

        raw = await jsonbin_get_record(SOLVED_BIN_URL)

        entries: list[tuple[int, int, int]] = []
        for uid, stats in raw.items():
            if not isinstance(stats, dict):
                continue

            uid_int = to_int(uid, default=-1)
            if uid_int <= 0:
                continue

            solved = to_int(stats.get("solved_riddles", 0), default=0)
            xp = to_int(stats.get("xp", 0), default=0)
            entries.append((uid_int, solved, xp))

        entries.sort(key=lambda x: (x[1], x[2]), reverse=True)
        total = sum(s for _, s, _ in entries)

        percent_entries = [
            (uid, solved, (solved / total * 100 if total else 0.0), xp)
            for uid, solved, xp in entries
        ]

        view = ChampionsView(
            interaction=interaction,
            entries=percent_entries,
            guild=interaction.guild,
            image_url=image,
            total=total,
            owner_id=(interaction.user.id if not visible else None)
        )

        embed = await view.get_embed()

        sent_msg = await interaction.followup.send(
            content=mention.mention if (visible and mention) else None,
            embed=embed,
            view=view,
            ephemeral=not visible,
            wait=True
        )
        view.message = sent_msg


async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleEditor(bot))