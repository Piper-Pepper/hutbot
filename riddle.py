import os
import json
import time
import asyncio
import logging
from typing import Optional, Any, Callable, Awaitable

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
HTTP_RETRIES = 2

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


def to_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def is_configured() -> bool:
    return bool(JSONBIN_API_KEY and JSONBIN_API_KEY.strip())


def empty_riddle() -> dict:
    return {
        "text": None,
        "solution": None,
        "award": None,
        "image-url": None,
        "solution-url": None,
        "button-id": None,
        "riddler": None
    }


def normalize_riddle_record(record: dict) -> dict:
    if not isinstance(record, dict):
        return empty_riddle()

    return {
        "text": record.get("text"),
        "solution": record.get("solution"),
        "award": record.get("award"),
        "image-url": record.get("image-url"),
        "solution-url": record.get("solution-url"),
        "button-id": record.get("button-id"),
        "riddler": record.get("riddler")
    }


def has_riddle_data(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    return bool(clean_value(data.get("text")) or clean_value(data.get("solution")))


def headers() -> dict:
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
    retries: int = HTTP_RETRIES,
    timeout_sec: int = HTTP_TIMEOUT_SEC
) -> tuple[bool, int, dict]:
    if not is_configured():
        logger.error("JSONBIN_API_KEY is missing.")
        return False, 0, {}

    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    last_status = 0
    last_data: dict = {}
    backoff = 0.4

    for attempt in range(retries + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(method, url, headers=headers(), json=payload) as resp:
                    last_status = resp.status
                    txt = await resp.text()

                    parsed: dict = {}
                    if txt:
                        try:
                            obj = json.loads(txt)
                            if isinstance(obj, dict):
                                parsed = obj
                        except json.JSONDecodeError:
                            parsed = {}

                    if 200 <= resp.status < 300:
                        return True, resp.status, parsed

                    retryable = (resp.status == 429) or (500 <= resp.status < 600)
                    logger.warning(
                        f"JSONBin {method} failed: status={resp.status}, retryable={retryable}, attempt={attempt + 1}/{retries + 1}"
                    )
                    last_data = parsed

                    if not retryable or attempt >= retries:
                        return False, resp.status, parsed

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"JSONBin {method} exception: {e} (attempt {attempt + 1}/{retries + 1})")
            if attempt >= retries:
                break
        except Exception as e:
            logger.exception(f"JSONBin {method} unexpected exception: {e}")
            if attempt >= retries:
                break

        await asyncio.sleep(backoff)
        backoff *= 2

    return False, last_status, last_data


async def jsonbin_get_record(bin_url: str, *, retries: int = HTTP_RETRIES) -> tuple[bool, dict]:
    ok, _, data = await jsonbin_request("GET", f"{bin_url}/latest", retries=retries)
    if not ok:
        return False, {}
    record = data.get("record", {})
    if not isinstance(record, dict):
        return True, {}
    return True, record


async def jsonbin_put_record(bin_url: str, record: dict, *, retries: int = HTTP_RETRIES) -> bool:
    ok, _, _ = await jsonbin_request("PUT", bin_url, payload=record, retries=retries)
    return ok


async def fetch_riddle_safe(*, retries: int = HTTP_RETRIES) -> tuple[bool, dict]:
    ok, record = await jsonbin_get_record(JSONBIN_BASE_URL, retries=retries)
    if not ok:
        return False, empty_riddle()
    return True, normalize_riddle_record(record)


# =========================
# MODAL
# =========================
class RiddleUpsertModal(Modal):
    def __init__(
        self,
        *,
        mode: str,  # "create" | "edit"
        mention_override: Optional[Role],
        current_data: dict,
        on_saved: Optional[Callable[[dict], Awaitable[None]]] = None
    ):
        super().__init__(title="Edit Riddle" if mode == "edit" else "Create Riddle")

        self.mode = mode
        self.mention_override = mention_override
        self.current_data = current_data or empty_riddle()
        self.on_saved = on_saved

        self.text = TextInput(
            label="Text",
            style=discord.TextStyle.paragraph,
            default=self.current_data.get("text") or "",
            required=True,
            max_length=4000
        )
        self.solution = TextInput(
            label="Solution",
            style=discord.TextStyle.paragraph,
            default=self.current_data.get("solution") or "",
            required=True,
            max_length=4000
        )
        self.award = TextInput(
            label="Award",
            default=self.current_data.get("award") or "",
            required=False,
            max_length=200
        )
        self.image_url = TextInput(
            label="Image URL",
            default=self.current_data.get("image-url") or "",
            required=False,
            max_length=1000
        )
        self.solution_url = TextInput(
            label="Solution URL",
            default=self.current_data.get("solution-url") or "",
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
            await interaction.followup.send("❌ JSONBIN_API_KEY is missing in .env.", ephemeral=True)
            return

        text = clean_value(self.text.value)
        solution = clean_value(self.solution.value)

        if not text or not solution:
            await interaction.followup.send("❌ Text and Solution cannot be empty.", ephemeral=True)
            return

        old_button_id = clean_value(self.current_data.get("button-id"))
        new_button_id = str(self.mention_override.id) if self.mention_override else old_button_id

        payload = {
            "text": text,
            "solution": solution,
            "award": clean_value(self.award.value),
            "image-url": clean_value(self.image_url.value),
            "solution-url": clean_value(self.solution_url.value),
            "button-id": new_button_id,
            "riddler": str(interaction.user.id)
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        ok = await jsonbin_put_record(JSONBIN_BASE_URL, payload)
        if not ok:
            await interaction.followup.send("❌ Failed while saving to JSONBin.", ephemeral=True)
            return

        if self.on_saved:
            try:
                await self.on_saved(payload)
            except Exception:
                pass

        await interaction.followup.send("✅ Updated!" if self.mode == "edit" else "✅ Created!", ephemeral=True)


# =========================
# CHAMPIONS VIEW
# =========================
class ChampionsView(View):
    def __init__(
        self,
        *,
        entries: list[tuple[int, int, float, int]],
        total_solved: int,
        name_cache: dict[int, str],
        avatar_cache: dict[int, str],
        image_url: Optional[str] = None,
        owner_id: Optional[int] = None
    ):
        super().__init__(timeout=300)

        self.entries = entries
        self.total_solved = total_solved
        self.name_cache = name_cache
        self.avatar_cache = avatar_cache

        self.page = 0
        self.entries_per_page = 6
        self.max_page = max((len(entries) - 1) // self.entries_per_page, 0)

        self.page1_image_url = image_url or DEFAULT_IMAGE_URL
        self.default_image_url = DEFAULT_IMAGE_URL
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None

        self._sync_buttons()

    def _sync_buttons(self):
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.max_page

    def _name(self, uid: int) -> str:
        return self.name_cache.get(uid, f"Unknown User ({uid})")

    def _avatar(self, uid: int) -> Optional[str]:
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
            top_name = self._name(top_uid)
            top_avatar = self._avatar(top_uid)

            if top_avatar:
                embed.set_author(name=f"👑 Riddle Master #1: {top_name}", icon_url=top_avatar)
                embed.set_thumbnail(url=top_avatar)
            else:
                embed.set_author(name=f"👑 Riddle Master #1: {top_name}")

        if not page_entries:
            embed.add_field(name="No data yet", value="No riddles have been solved yet.", inline=False)
        else:
            for i, (uid, solved, percent, xp) in enumerate(page_entries, start=start + 1):
                embed.add_field(
                    name=f"🎖️ {i}. {self._name(uid)}",
                    value=f"🧩 {solved} | 📊 {percent:.1f}% | 🧠 {xp} XP",
                    inline=False
                )

        embed.set_image(url=self.page1_image_url if self.page == 0 else self.default_image_url)
        return embed

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await interaction.response.send_message("🚫 This menu is not yours.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="riddlechamp_prev")
    async def prev_btn(self, interaction: Interaction, _: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, custom_id="riddlechamp_next")
    async def next_btn(self, interaction: Interaction, _: discord.ui.Button):
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

        self._cache_lock = asyncio.Lock()

        self._riddle_cache = empty_riddle()
        self._riddle_cache_ts = 0.0
        self._riddle_cache_ready = False

        self._cache_task: Optional[asyncio.Task] = None

    async def cog_load(self):
        self._cache_task = asyncio.create_task(self._cache_worker())

    async def cog_unload(self):
        if self._cache_task and not self._cache_task.done():
            self._cache_task.cancel()
            try:
                await self._cache_task
            except Exception:
                pass

    async def _cache_worker(self):
        await asyncio.sleep(2)
        while True:
            try:
                ok, data = await fetch_riddle_safe(retries=1)
                if ok:
                    async with self._cache_lock:
                        self._riddle_cache = data
                        self._riddle_cache_ts = time.monotonic()
                        self._riddle_cache_ready = True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"riddle cache worker error: {e}")
            await asyncio.sleep(45)

    async def _update_riddle_cache(self, data: dict):
        async with self._cache_lock:
            self._riddle_cache = data
            self._riddle_cache_ts = time.monotonic()
            self._riddle_cache_ready = True

    async def _get_riddle_for_modal(self) -> Optional[dict]:
        # 1) Fast live fetch (under interaction deadline)
        try:
            ok, data = await asyncio.wait_for(fetch_riddle_safe(retries=0), timeout=1.7)
            if ok:
                await self._update_riddle_cache(data)
                return data
        except asyncio.TimeoutError:
            logger.warning("/riddle live fetch timeout, trying cache")
        except Exception as e:
            logger.warning(f"/riddle live fetch failed: {e}")

        # 2) Cache fallback
        async with self._cache_lock:
            if self._riddle_cache_ready:
                return dict(self._riddle_cache)

        # 3) No reliable data
        return None

    def _has_permission(self, interaction: Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member):
            if interaction.guild is None:
                return False
            member = interaction.guild.get_member(interaction.user.id)

        if not member:
            return False

        return any(r.id == REQUIRED_ROLE_ID for r in member.roles)

    async def _resolve_identity(self, guild: discord.Guild, uid: int) -> tuple[str, Optional[str]]:
        # Prefer guild member name
        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                member = None

        if member is not None:
            name = member.display_name
            avatar = None
            try:
                avatar = member.display_avatar.url
            except Exception:
                avatar = None
            return name, avatar

        # Fallback: global user
        user = self.bot.get_user(uid)
        if user is None:
            try:
                user = await self.bot.fetch_user(uid)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                user = None

        if user is not None:
            name = user.global_name or user.name
            avatar = None
            try:
                avatar = user.display_avatar.url
            except Exception:
                avatar = None
            return name, avatar

        return f"Unknown User ({uid})", None

    async def _build_identity_cache(
        self,
        guild: discord.Guild,
        entries_raw: list[tuple[int, int, int]]
    ) -> tuple[dict[int, str], dict[int, str]]:
        name_cache: dict[int, str] = {}
        avatar_cache: dict[int, str] = {}

        unique_ids = list(dict.fromkeys(uid for uid, _, _ in entries_raw))
        sem = asyncio.Semaphore(5)

        async def worker(uid: int):
            async with sem:
                name, avatar = await self._resolve_identity(guild, uid)
                name_cache[uid] = name
                if avatar:
                    avatar_cache[uid] = avatar

        await asyncio.gather(*(worker(uid) for uid in unique_ids))
        return name_cache, avatar_cache

    @app_commands.command(
        name="riddle",
        description="Create a new riddle or edit the currently stored riddle."
    )
    @app_commands.describe(
        mention="Optional role to store as the riddle mention/button role."
    )
    @app_commands.guild_only()
    async def riddle(self, interaction: Interaction, mention: Optional[Role] = None):
        if not is_configured():
            await interaction.response.send_message("❌ JSONBIN_API_KEY is missing in .env.", ephemeral=True)
            return

        if not self._has_permission(interaction):
            await interaction.response.send_message("🚫 No permission.", ephemeral=True)
            return

        data = await self._get_riddle_for_modal()
        if data is None:
            await interaction.response.send_message(
                "❌ Could not load the current riddle in time. Please run `/riddle` again.",
                ephemeral=True
            )
            return

        mode = "edit" if has_riddle_data(data) else "create"

        modal = RiddleUpsertModal(
            mode=mode,
            mention_override=mention,
            current_data=data,
            on_saved=self._update_riddle_cache
        )

        try:
            await interaction.response.send_modal(modal)
        except discord.NotFound:
            logger.warning("send_modal failed: interaction expired (10062)")
        except discord.HTTPException as e:
            logger.exception(f"send_modal HTTPException: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Interaction expired. Please run `/riddle` again.",
                    ephemeral=True
                )

    @riddle.error
    async def riddle_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        logger.exception(f"/riddle error: {error}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Error in `/riddle`.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Error in `/riddle`.", ephemeral=True)
        except Exception:
            pass

    @app_commands.command(
        name="riddle_champ",
        description="Show the riddle champions leaderboard."
    )
    @app_commands.describe(
        visible="If true, send publicly. If false, send only to you.",
        image="Optional custom image URL for page 1.",
        mention="Optional role mention (only used when visible=true)."
    )
    @app_commands.guild_only()
    async def riddle_champ(
        self,
        interaction: Interaction,
        visible: Optional[bool] = False,
        image: Optional[str] = None,
        mention: Optional[Role] = None
    ):
        if not is_configured():
            await interaction.response.send_message("❌ JSONBIN_API_KEY is missing in .env.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=not visible, thinking=True)

        ok, raw = await jsonbin_get_record(SOLVED_BIN_URL, retries=HTTP_RETRIES)
        if not ok:
            raw = {}

        entries_raw: list[tuple[int, int, int]] = []
        for uid, stats in raw.items():
            uid_i = to_int(uid, default=-1)
            if uid_i <= 0:
                continue

            solved = 0
            xp = 0

            if isinstance(stats, dict):
                solved = max(0, to_int(stats.get("solved_riddles", 0), default=0))
                xp = max(0, to_int(stats.get("xp", 0), default=0))
            else:
                solved = max(0, to_int(stats, default=0))
                xp = 0

            entries_raw.append((uid_i, solved, xp))

        entries_raw.sort(key=lambda x: (x[1], x[2]), reverse=True)
        total_solved = sum(s for _, s, _ in entries_raw)

        entries: list[tuple[int, int, float, int]] = [
            (uid, solved, (solved / total_solved * 100.0 if total_solved else 0.0), xp)
            for uid, solved, xp in entries_raw
        ]

        if interaction.guild is None:
            await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        name_cache, avatar_cache = await self._build_identity_cache(interaction.guild, entries_raw)

        view = ChampionsView(
            entries=entries,
            total_solved=total_solved,
            name_cache=name_cache,
            avatar_cache=avatar_cache,
            image_url=image,
            owner_id=(interaction.user.id if not visible else None)
        )

        sent = await interaction.followup.send(
            content=mention.mention if (visible and mention) else None,
            embed=view.build_embed(),
            view=view,
            ephemeral=not visible,
            wait=True
        )
        view.message = sent

    @riddle_champ.error
    async def riddle_champ_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        logger.exception(f"/riddle_champ error: {error}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Error in `/riddle_champ`.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Error in `/riddle_champ`.", ephemeral=True)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleEditor(bot))