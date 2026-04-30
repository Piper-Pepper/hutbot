import os
import json
import time
import asyncio
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

DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1462480133737943063/riddle_sexy.gif"

HTTP_TIMEOUT_SEC = 12
HTTP_RETRIES_DEFAULT = 2

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


def has_riddle_data(data: dict) -> bool:
    return bool((data or {}).get("text") or (data or {}).get("solution"))


def safe_display_name(user: Optional[discord.abc.User], fallback: str) -> str:
    if user is None:
        return fallback
    return getattr(user, "display_name", getattr(user, "name", fallback))


def make_headers() -> dict:
    return {
        "X-Master-Key": JSONBIN_API_KEY or "",
        "Content-Type": "application/json"
    }


# =========================
# JSONBIN HTTP
# =========================
async def jsonbin_request(
    method: str,
    url: str,
    *,
    payload: Optional[dict] = None,
    retries: int = HTTP_RETRIES_DEFAULT,
    timeout_sec: int = HTTP_TIMEOUT_SEC
) -> tuple[bool, int, dict]:
    if not is_configured():
        logger.error("JSONBIN_API_KEY missing.")
        return False, 0, {}

    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    backoff = 0.5
    last_status = 0
    last_data: dict = {}

    for attempt in range(retries + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method=method,
                    url=url,
                    headers=make_headers(),
                    json=payload
                ) as resp:
                    last_status = resp.status
                    body = await resp.text()
                    parsed: dict = {}

                    if body:
                        try:
                            obj = json.loads(body)
                            if isinstance(obj, dict):
                                parsed = obj
                        except json.JSONDecodeError:
                            parsed = {}

                    if 200 <= resp.status < 300:
                        return True, resp.status, parsed

                    # retry nur bei 429/5xx
                    should_retry = resp.status == 429 or 500 <= resp.status < 600
                    logger.warning(
                        f"JSONBin {method} {url} failed: status={resp.status}, retry={should_retry}, attempt={attempt + 1}/{retries + 1}"
                    )
                    last_data = parsed

                    if not should_retry or attempt >= retries:
                        return False, resp.status, parsed

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(
                f"JSONBin {method} {url} exception on attempt {attempt + 1}/{retries + 1}: {e}"
            )
            if attempt >= retries:
                break
        except Exception as e:
            logger.exception(f"JSONBin {method} unexpected exception: {e}")
            if attempt >= retries:
                break

        await asyncio.sleep(backoff)
        backoff *= 2

    return False, last_status, last_data


async def jsonbin_get_record(bin_url: str, *, retries: int = HTTP_RETRIES_DEFAULT) -> dict:
    ok, _, data = await jsonbin_request(
        "GET",
        f"{bin_url}/latest",
        retries=retries
    )
    if not ok:
        return {}

    record = data.get("record", {})
    return record if isinstance(record, dict) else {}


async def jsonbin_put_record(bin_url: str, record: dict, *, retries: int = HTTP_RETRIES_DEFAULT) -> bool:
    ok, _, _ = await jsonbin_request(
        "PUT",
        bin_url,
        payload=record,
        retries=retries
    )
    return ok


async def fetch_riddle_safe(*, retries: int = HTTP_RETRIES_DEFAULT) -> dict:
    empty = get_empty_riddle()
    record = await jsonbin_get_record(JSONBIN_BASE_URL, retries=retries)
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
# MODAL
# =========================
class RiddleUpsertModal(Modal):
    def __init__(
        self,
        *,
        mode: str,
        mention: Optional[Role] = None,
        existing_data: Optional[dict] = None
    ):
        title = "Edit Riddle" if mode == "edit" else "Create Riddle"
        super().__init__(title=title)

        data = existing_data or {}
        self.mode = mode
        self.mention = mention
        self.existing_button_id = data.get("button-id")

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

        button_id = str(self.mention.id) if self.mention else self.existing_button_id

        updated = {
            "text": clean_value(self.text.value),
            "solution": clean_value(self.solution.value),
            "award": clean_value(self.award.value),
            "image-url": clean_value(self.image_url.value),
            "solution-url": clean_value(self.solution_url.value),
            "button-id": clean_value(button_id),
            "riddler": str(interaction.user.id)
        }
        updated = {k: v for k, v in updated.items() if v is not None}

        ok = await jsonbin_put_record(JSONBIN_BASE_URL, updated)
        if ok:
            msg = "✅ Updated!" if self.mode == "edit" else "✅ Created!"
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed while saving to JSONBin.", ephemeral=True)


# =========================
# CHAMPIONS VIEW
# =========================
class ChampionsView(View):
    def __init__(
        self,
        *,
        entries: list[tuple[int, int, float, int]],
        total_solved: int,
        page_image_url: Optional[str],
        name_cache: dict[int, str],
        avatar_cache: dict[int, str],
        owner_id: Optional[int] = None
    ):
        super().__init__(timeout=300)

        self.entries = entries
        self.total_solved = total_solved
        self.page = 0
        self.entries_per_page = 6
        self.max_page = max((len(self.entries) - 1) // self.entries_per_page, 0)

        self.owner_id = owner_id
        self.name_cache = name_cache
        self.avatar_cache = avatar_cache

        self.default_image_url = DEFAULT_IMAGE_URL
        self.page1_image_url = page_image_url or self.default_image_url

        self.message: Optional[discord.Message] = None

        self._sync_buttons()

    def _sync_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "riddlechamp_prev":
                    child.disabled = self.page <= 0
                elif child.custom_id == "riddlechamp_next":
                    child.disabled = self.page >= self.max_page

    def _get_name(self, uid: int) -> str:
        return self.name_cache.get(uid, f"User {uid}")

    def _get_avatar(self, uid: int) -> Optional[str]:
        return self.avatar_cache.get(uid)

    def build_embed(self) -> discord.Embed:
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
            top_name = self._get_name(top_uid)
            top_avatar = self._get_avatar(top_uid)

            embed.set_author(name=f"👑 Riddle Master #1: {top_name}", icon_url=top_avatar or discord.Embed.Empty)
            if top_avatar:
                embed.set_thumbnail(url=top_avatar)

        if not page_entries:
            embed.add_field(
                name="Noch keine Daten",
                value="Es wurden noch keine Rätsel gelöst.",
                inline=False
            )
        else:
            for i, (uid, solved, percent, xp) in enumerate(page_entries, start=start + 1):
                name = self._get_name(uid)
                embed.add_field(
                    name=f"🎖️ {i}. {name}",
                    value=f"🧩 {solved} | 📊 {percent:.1f}% | 🧠 {xp} XP",
                    inline=False
                )

        embed.set_image(url=self.page1_image_url if self.page == 0 else self.default_image_url)
        return embed

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

    @discord.ui.button(
        label="Previous",
        style=discord.ButtonStyle.secondary,
        custom_id="riddlechamp_prev"
    )
    async def prev_button(self, interaction: Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(
        label="Next",
        style=discord.ButtonStyle.secondary,
        custom_id="riddlechamp_next"
    )
    async def next_button(self, interaction: Interaction, button: discord.ui.Button):
        if self.page < self.max_page:
            self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


# =========================
# COG
# =========================
class RiddleEditor(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._riddle_cache: dict = get_empty_riddle()
        self._riddle_cache_ts: float = 0.0

    async def _get_riddle_for_modal_fast(self) -> dict:
        # Kurzer Cache, damit /riddle meistens instant bleibt
        now = time.monotonic()
        if now - self._riddle_cache_ts < 90:
            return self._riddle_cache

        try:
            # sehr kurzer Timeout, damit send_modal nicht abläuft (10062)
            data = await asyncio.wait_for(fetch_riddle_safe(retries=0), timeout=1.4)
            if isinstance(data, dict):
                self._riddle_cache = data
                self._riddle_cache_ts = time.monotonic()
                return data
        except asyncio.TimeoutError:
            logger.warning("/riddle fetch timeout, using cache/fallback")
        except Exception as e:
            logger.exception(f"/riddle fetch failed: {e}")

        return self._riddle_cache if self._riddle_cache else get_empty_riddle()

    def _member_has_permission(self, interaction: Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member):
            if interaction.guild is None:
                return False
            member = interaction.guild.get_member(interaction.user.id)

        if not member:
            return False

        return any(role.id == REQUIRED_ROLE_ID for role in member.roles)

    def _build_user_cache(
        self,
        guild: Optional[discord.Guild],
        entries: list[tuple[int, int, int]]
    ) -> tuple[dict[int, str], dict[int, str]]:
        name_cache: dict[int, str] = {}
        avatar_cache: dict[int, str] = {}

        for uid, _, _ in entries:
            user_obj: Optional[discord.abc.User] = None

            if guild is not None:
                member = guild.get_member(uid)
                if member is not None:
                    user_obj = member

            if user_obj is None:
                user_obj = self.bot.get_user(uid)

            if user_obj is not None:
                name_cache[uid] = safe_display_name(user_obj, f"User {uid}")
                try:
                    avatar_cache[uid] = user_obj.display_avatar.url
                except Exception:
                    pass

        return name_cache, avatar_cache

    @app_commands.command(name="riddle", description="Create or edit the current riddle.")
    @app_commands.guild_only()
    async def riddle(self, interaction: Interaction, mention: Optional[Role] = None):
        if not is_configured():
            await interaction.response.send_message("❌ JSONBIN_API_KEY fehlt in der .env.", ephemeral=True)
            return

        if not self._member_has_permission(interaction):
            await interaction.response.send_message("🚫 No permission.", ephemeral=True)
            return

        data = await self._get_riddle_for_modal_fast()
        edit_mode = has_riddle_data(data)

        modal = RiddleUpsertModal(
            mode="edit" if edit_mode else "create",
            mention=(None if edit_mode else mention),
            existing_data=(data if edit_mode else None)
        )

        try:
            await interaction.response.send_modal(modal)
        except discord.NotFound:
            # 10062, Interaction abgelaufen
            logger.warning("send_modal failed with NotFound (likely 10062 Unknown interaction)")
        except discord.HTTPException as e:
            logger.exception(f"send_modal HTTPException: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Interaction abgelaufen. Bitte `/riddle` nochmal ausführen.",
                    ephemeral=True
                )

    @riddle.error
    async def riddle_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        logger.exception(f"/riddle error: {error}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Fehler bei `/riddle`.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Fehler bei `/riddle`.", ephemeral=True)
        except Exception:
            pass

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

        raw = await jsonbin_get_record(SOLVED_BIN_URL, retries=HTTP_RETRIES_DEFAULT)

        entries_raw: list[tuple[int, int, int]] = []
        for uid, stats in raw.items():
            uid_int = to_int(uid, default=-1)
            if uid_int <= 0:
                continue

            solved = 0
            xp = 0

            if isinstance(stats, dict):
                solved = to_int(stats.get("solved_riddles", 0), default=0)
                xp = to_int(stats.get("xp", 0), default=0)
            else:
                # Fallback falls Datenformat mal kaputt ist
                solved = to_int(stats, default=0)

            entries_raw.append((uid_int, max(0, solved), max(0, xp)))

        entries_raw.sort(key=lambda x: (x[1], x[2]), reverse=True)

        total_solved = sum(s for _, s, _ in entries_raw)
        entries: list[tuple[int, int, float, int]] = [
            (uid, solved, (solved / total_solved * 100.0 if total_solved else 0.0), xp)
            for uid, solved, xp in entries_raw
        ]

        name_cache, avatar_cache = self._build_user_cache(interaction.guild, entries_raw)

        view = ChampionsView(
            entries=entries,
            total_solved=total_solved,
            page_image_url=image,
            name_cache=name_cache,
            avatar_cache=avatar_cache,
            owner_id=(interaction.user.id if not visible else None)
        )

        embed = view.build_embed()

        sent_msg = await interaction.followup.send(
            content=mention.mention if (visible and mention) else None,
            embed=embed,
            view=view,
            ephemeral=not visible,
            wait=True
        )
        view.message = sent_msg

    @riddle_champ.error
    async def riddle_champ_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        logger.exception(f"/riddle_champ error: {error}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Fehler bei `/riddle_champ`.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Fehler bei `/riddle_champ`.", ephemeral=True)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleEditor(bot))