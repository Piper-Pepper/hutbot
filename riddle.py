import os
import time
import json
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


def has_riddle_data(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    return bool(data.get("text") or data.get("solution"))


def headers() -> dict:
    return {
        "X-Master-Key": JSONBIN_API_KEY or "",
        "Content-Type": "application/json"
    }


# =========================
# JSONBIN
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
        logger.error("JSONBIN_API_KEY missing.")
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


async def jsonbin_get_record(bin_url: str, retries: int = HTTP_RETRIES) -> dict:
    ok, _, data = await jsonbin_request("GET", f"{bin_url}/latest", retries=retries)
    if not ok:
        return {}
    record = data.get("record", {})
    return record if isinstance(record, dict) else {}


async def jsonbin_put_record(bin_url: str, record: dict, retries: int = HTTP_RETRIES) -> bool:
    ok, _, _ = await jsonbin_request("PUT", bin_url, payload=record, retries=retries)
    return ok


async def fetch_riddle_safe(retries: int = HTTP_RETRIES) -> dict:
    rec = await jsonbin_get_record(JSONBIN_BASE_URL, retries=retries)
    if not rec:
        return empty_riddle()

    return {
        "text": rec.get("text"),
        "solution": rec.get("solution"),
        "award": rec.get("award"),
        "image-url": rec.get("image-url"),
        "solution-url": rec.get("solution-url"),
        "button-id": rec.get("button-id"),
        "riddler": rec.get("riddler")
    }


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
        on_saved
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
            await interaction.followup.send("❌ JSONBIN_API_KEY fehlt in der .env.", ephemeral=True)
            return

        # mention/button-id Verhalten:
        # - Wenn bei /riddle mention mitgegeben: überschreiben
        # - Sonst bei Edit: alten Wert behalten
        # - Sonst bei Create: None
        old_button_id = clean_value(self.current_data.get("button-id"))
        new_button_id = str(self.mention_override.id) if self.mention_override else old_button_id

        payload = {
            "text": clean_value(self.text.value),
            "solution": clean_value(self.solution.value),
            "award": clean_value(self.award.value),
            "image-url": clean_value(self.image_url.value),
            "solution-url": clean_value(self.solution_url.value),
            "button-id": new_button_id,
            "riddler": str(interaction.user.id)
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        ok = await jsonbin_put_record(JSONBIN_BASE_URL, payload)
        if ok:
            try:
                await self.on_saved(payload)
            except Exception:
                pass
            await interaction.followup.send("✅ Updated!" if self.mode == "edit" else "✅ Created!", ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed while saving to JSONBin.", ephemeral=True)


# =========================
# CHAMPIONS VIEW
# =========================
class ChampionsView(View):
    def __init__(
        self,
        *,
        bot: commands.Bot,
        guild: discord.Guild,
        entries: list[tuple[int, int, float, int]],
        total_solved: int,
        image_url: Optional[str] = None,
        owner_id: Optional[int] = None
    ):
        super().__init__(timeout=300)

        self.bot = bot
        self.guild = guild
        self.entries = entries
        self.total_solved = total_solved
        self.page = 0
        self.entries_per_page = 6
        self.max_page = max((len(entries) - 1) // self.entries_per_page, 0)

        self.owner_id = owner_id
        self.page1_image_url = image_url or DEFAULT_IMAGE_URL
        self.default_image_url = DEFAULT_IMAGE_URL

        self.name_cache: dict[int, str] = {}
        self.avatar_cache: dict[int, str] = {}
        self.message: Optional[discord.Message] = None

        self._sync_buttons()

    def _sync_buttons(self):
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.max_page

    async def _resolve_member(self, uid: int) -> tuple[str, Optional[str]]:
        if uid in self.name_cache:
            return self.name_cache[uid], self.avatar_cache.get(uid)

        member = self.guild.get_member(uid)
        if member is None:
            try:
                member = await self.guild.fetch_member(uid)
            except discord.NotFound:
                name = f"User {uid} (nicht auf Server)"
                self.name_cache[uid] = name
                return name, None
            except discord.HTTPException:
                name = f"User {uid}"
                self.name_cache[uid] = name
                return name, None

        name = member.display_name  # <- server nick / server displayname
        avatar = None
        try:
            avatar = member.display_avatar.url
        except Exception:
            avatar = None

        self.name_cache[uid] = name
        if avatar:
            self.avatar_cache[uid] = avatar

        return name, avatar

    async def build_embed(self) -> discord.Embed:
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
            top_name, top_avatar = await self._resolve_member(top_uid)

            if top_avatar:
                embed.set_author(name=f"👑 Riddle Master #1: {top_name}", icon_url=top_avatar)
                embed.set_thumbnail(url=top_avatar)
            else:
                embed.set_author(name=f"👑 Riddle Master #1: {top_name}")

        if not page_entries:
            embed.add_field(name="Noch keine Daten", value="Es wurden noch keine Rätsel gelöst.", inline=False)
        else:
            for i, (uid, solved, percent, xp) in enumerate(page_entries, start=start + 1):
                name, _ = await self._resolve_member(uid)
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
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, custom_id="riddlechamp_next")
    async def next_btn(self, interaction: Interaction, _: discord.ui.Button):
        if self.page < self.max_page:
            self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)


# =========================
# COG
# =========================
class RiddleEditor(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self._cache_lock = asyncio.Lock()
        self._riddle_cache: dict = empty_riddle()
        self._cache_ts: float = 0.0
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
                data = await fetch_riddle_safe(retries=1)
                async with self._cache_lock:
                    self._riddle_cache = data
                    self._cache_ts = time.monotonic()
            except Exception as e:
                logger.warning(f"cache worker error: {e}")
            await asyncio.sleep(45)

    async def _set_cache(self, data: dict):
        async with self._cache_lock:
            self._riddle_cache = data
            self._cache_ts = time.monotonic()

    async def _get_best_riddle_data(self) -> Optional[dict]:
        async with self._cache_lock:
            cached = dict(self._riddle_cache)
            ts = self._cache_ts

        # Frischer Cache
        if (time.monotonic() - ts) < 120:
            return cached

        # Kurz direkt fetchen (unter 3s wegen modal response)
        try:
            fresh = await asyncio.wait_for(fetch_riddle_safe(retries=0), timeout=2.0)
            await self._set_cache(fresh)
            return fresh
        except asyncio.TimeoutError:
            logger.warning("/riddle fetch timeout, using cache/fallback")
        except Exception as e:
            logger.warning(f"/riddle fetch failed: {e}")

        # Fallback: vorhandener Cache (auch wenn alt)
        if cached:
            return cached

        # Wenn gar nichts da ist, None -> lieber saubere Fehlermeldung statt falsches Modal
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

    @app_commands.command(name="riddle", description="Create or edit the current riddle")
    @app_commands.guild_only()
    async def riddle(self, interaction: Interaction, mention: Optional[Role] = None):
        if not is_configured():
            await interaction.response.send_message("❌ JSONBIN_API_KEY fehlt in der .env.", ephemeral=True)
            return

        if not self._has_permission(interaction):
            await interaction.response.send_message("🚫 No permission.", ephemeral=True)
            return

        data = await self._get_best_riddle_data()
        if data is None:
            await interaction.response.send_message(
                "❌ Konnte Riddle nicht rechtzeitig laden. Bitte `/riddle` nochmal ausführen.",
                ephemeral=True
            )
            return

        edit_mode = has_riddle_data(data)
        modal = RiddleUpsertModal(
            mode="edit" if edit_mode else "create",
            mention_override=mention,
            current_data=data if edit_mode else empty_riddle(),
            on_saved=self._set_cache
        )

        try:
            await interaction.response.send_modal(modal)
        except discord.NotFound:
            logger.warning("send_modal failed: interaction expired (10062)")
        except discord.HTTPException as e:
            logger.exception(f"send_modal failed: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Interaction abgelaufen. Bitte nochmal `/riddle`.",
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

    @app_commands.command(name="riddle_champ", description="Show riddle champions leaderboard")
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

        raw = await jsonbin_get_record(SOLVED_BIN_URL, retries=HTTP_RETRIES)

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

            entries_raw.append((uid_i, solved, xp))

        entries_raw.sort(key=lambda x: (x[1], x[2]), reverse=True)

        total_solved = sum(s for _, s, _ in entries_raw)
        entries = [
            (uid, solved, (solved / total_solved * 100.0 if total_solved else 0.0), xp)
            for uid, solved, xp in entries_raw
        ]

        if interaction.guild is None:
            await interaction.followup.send("❌ Nur im Server nutzbar.", ephemeral=True)
            return

        view = ChampionsView(
            bot=self.bot,
            guild=interaction.guild,
            entries=entries,
            total_solved=total_solved,
            image_url=image,
            owner_id=(interaction.user.id if not visible else None)
        )

        msg = await interaction.followup.send(
            content=mention.mention if (visible and mention) else None,
            embed=await view.build_embed(),
            view=view,
            ephemeral=not visible,
            wait=True
        )
        view.message = msg

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