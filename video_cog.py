import discord
from discord.ext import commands
from discord import ui

import aiohttp
import asyncio
import io
import sqlite3
import os
import json
import threading
import contextlib

from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv


load_dotenv()


# =====================================================
# CONFIG
# =====================================================

VIDEO_CHANNEL_ID = 1528774135172300840
MORDIEM_API = os.getenv("MORDIEM_API")

VIDEO_QUEUE_URL = "https://api.mordiem.com/api/v1/video/queue"
VIDEO_RETRIEVE_URL = "https://api.mordiem.com/api/v1/video/retrieve"

CONTROL_PREFIX = "🎬 **AI Video Generator**"
CONTROL_MESSAGE_TEXT = "🎬 **AI Video Generator**\nChoose your model:"
GENERATOR_SELECT_CUSTOM_ID = "video_model_select"
CONTROL_LOOKBACK_LIMIT = 20

PROMPT_PREVIEW_PROGRESS = 420
PROMPT_PREVIEW_RESULT = 900
REASON_PREVIEW_LIMIT = 450

# Neu: harte Abbruchgrenzen bei Provider-500-Schleife
MAX_CONSECUTIVE_5XX = 8
MAX_5XX_WINDOW_SECONDS = 180


# =====================================================
# ROLES / DAILY LIMITS
# =====================================================

ROLE_LIMITS = {
    1377051179615522926: 15,   # Tier 1
    1375147276413964408: 25,   # Tier 2
    1376592697606930593: 35,   # Tier 3
    1381791848875430069: 40,   # Tier 4
    1375666588404940830: 50,   # Tier 5
    1375584380914896978: 60,   # Tier 6
    1346414581643219029: 500
}


# =====================================================
# MODELS
# =====================================================

VIDEO_MODELS = {
    "wan-2-7-enhanced-text-to-video": {
        "name": "WAN 2.7 Enhanced 🔞",
        "mode": "video",
        "resolution": "720p",
        "max_seconds": 15
    },
    "wan-2-7-text-to-video": {
        "name": "WAN 2.7",
        "mode": "video",
        "resolution": "720p",
        "max_seconds": 15
    },
    "happyhorse-1-1-text-to-video": {
        "name": "HappyHorse 1.1",
        "mode": "video",
        "resolution": "720p",
        "max_seconds": 15
    },
    "pixverse-c1-text-to-video": {
        "name": "PixVerse C1",
        "mode": "video",
        "resolution": "720p",
        "max_seconds": 15
    },
    "grok-imagine-text-to-video-private": {
        "name": "Grok Imagine",
        "mode": "video",
        "resolution": "720p",
        "max_seconds": 15
    }
}

DEFAULT_MODEL = "wan-2-7-enhanced-text-to-video"

DURATION_FACTOR = {
    5: 0.90,
    10: 0.97,
    15: 1.00
}

BASE_TARGET_MS = {
    5: 90000,
    10: 140000,
    15: 190000
}

MODEL_TIME_FACTOR = {
    "wan-2-7-text-to-video": 1.00,
    "wan-2-7-enhanced-text-to-video": 0.95,
    "happyhorse-1-1-text-to-video": 1.05,
    "pixverse-c1-text-to-video": 1.00,
    "grok-imagine-text-to-video-private": 0.78,
    "ltx-2-v2-3-full-text-to-video": 1.10
}


# =====================================================
# HELPERS
# =====================================================

def utc_now():
    return datetime.now(timezone.utc)


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def format_reset(dt):
    if not dt:
        return "unknown"
    ts = int(dt.timestamp())
    return f"<t:{ts}:f> • <t:{ts}:R>"


def trim_text(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + " [...]"


def codeblock_safe(text: str) -> str:
    return (text or "").replace("```", "'''").strip()


def detect_media_type(binary: bytes):
    if not binary or len(binary) < 8:
        return None
    if binary.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image"
    if binary.startswith(b"\xff\xd8\xff"):
        return "image"
    if len(binary) >= 12 and binary[4:8] == b"ftyp":
        return "video"
    return None


def progress_bar(percent: int, blocks: int = 14):
    p = max(0, min(100, percent))
    filled = int(blocks * p / 100)
    return "█" * filled + "░" * (blocks - filled)


# =====================================================
# SQLITE
# =====================================================

class VideoDatabase:
    def __init__(self):
        self.lock = threading.Lock()
        self.db = sqlite3.connect("videos.sqlite", check_same_thread=False)

        with self.lock:
            cur = self.db.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS usage (
                    user_id TEXT,
                    seconds INTEGER,
                    created TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS active_jobs (
                    user_id TEXT PRIMARY KEY,
                    queue_id TEXT
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_user_created ON usage (user_id, created)"
            )
            self.db.commit()
            cur.close()

    def execute(self, query, params=()):
        with self.lock:
            cur = self.db.cursor()
            cur.execute(query, params)
            self.db.commit()
            cur.close()

    def fetchall(self, query, params=()):
        with self.lock:
            cur = self.db.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()
            cur.close()
            return rows

    def fetchone(self, query, params=()):
        with self.lock:
            cur = self.db.cursor()
            cur.execute(query, params)
            row = cur.fetchone()
            cur.close()
            return row


# =====================================================
# MODEL SELECT
# =====================================================

class ModelSelect(ui.Select):
    def __init__(self, cog, disabled=False):
        self.cog = cog
        options = []

        for model_key, model in VIDEO_MODELS.items():
            options.append(
                discord.SelectOption(
                    label=model["name"],
                    value=model_key,
                    description=f"{model['mode']} • {model['resolution']} • max {model['max_seconds']}s"
                )
            )

        super().__init__(
            placeholder="Choose your model",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=GENERATOR_SELECT_CUSTOM_ID,
            disabled=disabled
        )

    async def callback(self, interaction: discord.Interaction):
        if self.cog.is_global_busy():
            await interaction.response.send_message(
                "⏳ A render is currently running. Please wait until it finishes.",
                ephemeral=True
            )
            return

        limit = self.cog.get_user_limit(interaction.user)
        if limit <= 0:
            await interaction.response.send_message(
                "❌ You don't have a video tier.",
                ephemeral=True
            )
            return

        model_key = self.values[0]
        await interaction.response.send_modal(PromptModal(self.cog, model_key))


class ModelPickerView(ui.View):
    def __init__(self, cog, disabled=False):
        super().__init__(timeout=None)
        self.add_item(ModelSelect(cog, disabled=disabled))


# =====================================================
# PROMPT MODAL
# =====================================================

class PromptModal(ui.Modal):
    def __init__(self, cog, model_key):
        super().__init__(title="AI Video Generator")
        self.cog = cog
        self.model_key = model_key

        self.prompt = ui.TextInput(
            label="Video prompt",
            placeholder="Describe your video...",
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True
        )
        self.add_item(self.prompt)

    async def on_submit(self, interaction: discord.Interaction):
        if self.cog.is_global_busy():
            await interaction.response.send_message(
                "⏳ A render is currently running. Please wait until it finishes.",
                ephemeral=True
            )
            return

        remaining, reset = await self.cog.get_usage_info(interaction.user)
        view = DurationView(
            self.cog,
            interaction.user,
            self.prompt.value,
            self.model_key,
            remaining_seconds=remaining
        )

        if not view.allowed_seconds:
            await interaction.response.send_message(
                f"❌ Not enough render time.\n\n"
                f"Remaining: **{remaining}s**\n"
                f"Reset: **{format_reset(reset)}**",
                ephemeral=True
            )
            await self.cog.refresh_button(force=True, disabled=self.cog.is_global_busy())
            return

        await interaction.response.send_message(
            "⏱ Choose video length:",
            view=view,
            ephemeral=True
        )


# =====================================================
# DURATION SELECTION
# =====================================================

class DurationButton(ui.Button):
    def __init__(self, seconds: int):
        style_map = {
            5: discord.ButtonStyle.green,
            10: discord.ButtonStyle.blurple,
            15: discord.ButtonStyle.red
        }
        super().__init__(
            label=f"{seconds} seconds",
            style=style_map.get(seconds, discord.ButtonStyle.gray)
        )
        self.seconds = seconds

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if isinstance(view, DurationView):
            await view.choose(interaction, self.seconds)


class DurationView(ui.View):
    def __init__(self, cog, user, prompt, model_key, remaining_seconds: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.user = user
        self.prompt = prompt
        self.model_key = model_key
        self.remaining_seconds = max(0, safe_int(remaining_seconds, 0))

        model = VIDEO_MODELS.get(model_key, VIDEO_MODELS[DEFAULT_MODEL])
        max_seconds = safe_int(model.get("max_seconds", 15), 15)

        self.allowed_seconds = [s for s in (5, 10, 15) if s <= max_seconds and s <= self.remaining_seconds]

        if not self.allowed_seconds:
            self.add_item(ui.Button(label="No valid duration", disabled=True, style=discord.ButtonStyle.gray))
        else:
            for sec in self.allowed_seconds:
                self.add_item(DurationButton(sec))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "❌ This menu belongs to another user.",
                ephemeral=True
            )
            return False
        return True

    async def choose(self, interaction, seconds):
        if self.cog.is_global_busy():
            await interaction.response.edit_message(
                content="⏳ A render is currently running. Please wait until it finishes.",
                view=None
            )
            return

        model = VIDEO_MODELS.get(self.model_key, VIDEO_MODELS[DEFAULT_MODEL])
        max_seconds = safe_int(model.get("max_seconds", 15), 15)

        remaining, reset = await self.cog.get_usage_info(self.user)
        allowed_now = [s for s in (5, 10, 15) if s <= max_seconds and s <= remaining]

        if seconds not in allowed_now:
            if allowed_now:
                await interaction.response.edit_message(
                    content=(
                        f"❌ Not enough time for **{seconds}s**.\n"
                        f"You currently have **{remaining}s** left.\n\n"
                        f"Choose an available duration:"
                    ),
                    view=DurationView(
                        self.cog,
                        self.user,
                        self.prompt,
                        self.model_key,
                        remaining_seconds=remaining
                    )
                )
            else:
                await interaction.response.edit_message(
                    content=(
                        f"❌ Not enough render time.\n\n"
                        f"Remaining: **{remaining}s**\n"
                        f"Reset: **{format_reset(reset)}**"
                    ),
                    view=None
                )

            await self.cog.refresh_button(force=True, disabled=self.cog.is_global_busy())
            return

        await interaction.response.edit_message(
            content="📐 Choose aspect ratio:",
            view=AspectView(self.cog, self.user, self.prompt, seconds, self.model_key)
        )


# =====================================================
# ASPECT RATIO SELECTION
# =====================================================

class AspectView(ui.View):
    def __init__(self, cog, user, prompt, seconds, model_key):
        super().__init__(timeout=120)
        self.cog = cog
        self.user = user
        self.prompt = prompt
        self.seconds = seconds
        self.model_key = model_key

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "❌ This menu belongs to another user.",
                ephemeral=True
            )
            return False
        return True

    async def start(self, interaction, aspect):
        if self.cog.is_global_busy():
            await interaction.response.edit_message(
                content="⏳ A render is currently running. Please wait until it finishes.",
                view=None
            )
            return

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=f"⏳ Starting render with aspect `{aspect}`...",
            view=self
        )

        await self.cog.start_generation(
            interaction=interaction,
            user=self.user,
            prompt=self.prompt,
            seconds=self.seconds,
            aspect=aspect,
            model_key=self.model_key
        )

    @ui.button(label="🖥️ 16:9", style=discord.ButtonStyle.green)
    async def wide(self, interaction, button):
        await self.start(interaction, "16:9")

    @ui.button(label="📱 9:16", style=discord.ButtonStyle.blurple)
    async def vertical(self, interaction, button):
        await self.start(interaction, "9:16")

    @ui.button(label="⬜ 1:1", style=discord.ButtonStyle.gray)
    async def square(self, interaction, button):
        await self.start(interaction, "1:1")


# =====================================================
# VIDEO COG
# =====================================================

class VideoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = VideoDatabase()

        self.active_interactions = {}  # user_id -> interaction
        self.control_lock = asyncio.Lock()
        self.starting_lock = asyncio.Lock()
        self.starting_users = set()

        self.global_busy_lock = asyncio.Lock()
        self.global_busy = False

        self.http_lock = asyncio.Lock()
        self.http_session = None

        self.startup_task = None
        self.panel_guard_task = None

    # -------------------------------------------------
    # LIFECYCLE
    # -------------------------------------------------

    async def cog_load(self):
        self.bot.add_view(ModelPickerView(self, disabled=False))
        await self._ensure_http()

        if self.startup_task is None or self.startup_task.done():
            self.startup_task = asyncio.create_task(self.ensure_control_message_with_retry())

        if self.panel_guard_task is None or self.panel_guard_task.done():
            self.panel_guard_task = asyncio.create_task(self._panel_guard_loop())

    def cog_unload(self):
        if self.startup_task and not self.startup_task.done():
            self.startup_task.cancel()

        if self.panel_guard_task and not self.panel_guard_task.done():
            self.panel_guard_task.cancel()

        if self.http_session and not self.http_session.closed:
            self.bot.loop.create_task(self.http_session.close())

    async def _ensure_http(self):
        async with self.http_lock:
            if self.http_session is None or self.http_session.closed:
                self.http_session = aiohttp.ClientSession()

    async def _http(self):
        if self.http_session is None or self.http_session.closed:
            await self._ensure_http()
        return self.http_session

    @commands.Cog.listener()
    async def on_ready(self):
        print("VIDEO COG READY")
        if self.startup_task is None or self.startup_task.done():
            self.startup_task = asyncio.create_task(self.ensure_control_message_with_retry())
        if self.panel_guard_task is None or self.panel_guard_task.done():
            self.panel_guard_task = asyncio.create_task(self._panel_guard_loop())

    async def _panel_guard_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                # WICHTIG: während aktivem Render kein Auto-Repost
                if self.is_global_busy():
                    await asyncio.sleep(45)
                    continue

                await self.refresh_button(force=False, disabled=False)
            except Exception as e:
                print("PANEL GUARD ERROR:", repr(e))
            await asyncio.sleep(45)

    # -------------------------------------------------
    # BUSY GATE
    # -------------------------------------------------

    async def _try_begin_global_render(self) -> bool:
        async with self.global_busy_lock:
            if self.global_busy:
                return False
            self.global_busy = True
            return True

    async def _end_global_render(self):
        async with self.global_busy_lock:
            self.global_busy = False

    def is_global_busy(self) -> bool:
        return self.global_busy

    # -------------------------------------------------
    # USER STATE
    # -------------------------------------------------

    async def _mark_user_starting(self, user_id: int) -> bool:
        async with self.starting_lock:
            if user_id in self.starting_users:
                return False
            self.starting_users.add(user_id)
            return True

    async def _unmark_user_starting(self, user_id: int):
        async with self.starting_lock:
            self.starting_users.discard(user_id)

    async def _safe_followup_for_user(self, user, content: str):
        interaction = self.active_interactions.get(user.id)
        if interaction:
            with contextlib.suppress(Exception):
                await interaction.followup.send(content, ephemeral=True)
                return

        with contextlib.suppress(Exception):
            await user.send(content)

    async def _notify_failed_ephemeral(self, user, reason: str):
        quota = await self._quota_summary(user)
        text = f"❌ Render failed.\n**Reason:** {reason}\n\n{quota}"

        interaction = self.active_interactions.get(user.id)
        if interaction:
            with contextlib.suppress(Exception):
                await interaction.followup.send(text, ephemeral=True)
                return

        with contextlib.suppress(Exception):
            await user.send(text)

    async def _finalize_failed_render(self, user, reason: str, progress_message):
        await self._safe_delete_progress_message(progress_message)
        await self._notify_failed_ephemeral(user, reason)
        # Kein refresh hier -> passiert zentral in start_generation.finally

    # -------------------------------------------------
    # ROLE / LIMIT
    # -------------------------------------------------

    def get_user_limit(self, user):
        highest = 0
        for role in getattr(user, "roles", []):
            if role.id in ROLE_LIMITS:
                highest = max(highest, ROLE_LIMITS[role.id])
        return highest

    def get_user_tier(self, user):
        highest = 0
        name = "No Tier"
        for role in getattr(user, "roles", []):
            if role.id in ROLE_LIMITS and ROLE_LIMITS[role.id] > highest:
                highest = ROLE_LIMITS[role.id]
                name = role.name
        return name, highest

    async def _quota_summary(self, user):
        remaining, reset = await self.get_usage_info(user)
        tier_name, tier_limit = self.get_user_tier(user)
        return (
            f"Tier: **{tier_name}**\n"
            f"Daily limit: **{tier_limit}s**\n"
            f"Remaining: **{remaining}s**\n"
            f"Reset: **{format_reset(reset)}**"
        )

    # -------------------------------------------------
    # USAGE DB
    # -------------------------------------------------

    async def clean_usage(self):
        cutoff = (utc_now() - timedelta(hours=24)).isoformat()
        self.db.execute("DELETE FROM usage WHERE created < ?", (cutoff,))

    async def get_usage_info(self, user):
        await self.clean_usage()
        uid = str(user.id)

        rows = self.db.fetchall(
            """
            SELECT seconds, created
            FROM usage
            WHERE user_id=?
            ORDER BY created ASC
            """,
            (uid,)
        )

        used = sum(r[0] for r in rows)
        limit = self.get_user_limit(user)
        remaining = max(limit - used, 0)

        reset = None
        if rows:
            reset = datetime.fromisoformat(rows[0][1]) + timedelta(hours=24)

        return remaining, reset

    async def save_usage(self, user, seconds):
        self.db.execute(
            "INSERT INTO usage VALUES (?,?,?)",
            (str(user.id), seconds, utc_now().isoformat())
        )

    def add_active_job(self, user, queue_id):
        self.db.execute(
            "INSERT OR REPLACE INTO active_jobs VALUES (?,?)",
            (str(user.id), queue_id)
        )

    def remove_active_job(self, user):
        self.db.execute(
            "DELETE FROM active_jobs WHERE user_id=?",
            (str(user.id),)
        )

    # -------------------------------------------------
    # CONTROL PANEL
    # -------------------------------------------------

    async def _get_video_channel(self):
        channel = self.bot.get_channel(VIDEO_CHANNEL_ID)
        if channel is None:
            channel = await self.bot.fetch_channel(VIDEO_CHANNEL_ID)
        return channel

    def _message_custom_ids(self, msg: discord.Message):
        ids = []
        try:
            for row in (msg.components or []):
                children = getattr(row, "children", None)
                if children:
                    for child in children:
                        cid = getattr(child, "custom_id", None)
                        if cid:
                            ids.append(cid)
                else:
                    cid = getattr(row, "custom_id", None)
                    if cid:
                        ids.append(cid)
        except Exception:
            pass
        return ids

    def _panel_disabled_state(self, msg: discord.Message):
        try:
            for row in (msg.components or []):
                children = getattr(row, "children", None)
                if not children:
                    continue
                for child in children:
                    cid = getattr(child, "custom_id", None)
                    if cid == GENERATOR_SELECT_CUSTOM_ID:
                        return bool(getattr(child, "disabled", False))
        except Exception:
            pass
        return None

    def _looks_like_generator_message(self, msg: discord.Message):
        if msg.author != self.bot.user:
            return False
        content = (msg.content or "").strip()
        ids = self._message_custom_ids(msg)
        return content.startswith(CONTROL_PREFIX) or (GENERATOR_SELECT_CUSTOM_ID in ids)

    def _is_valid_generator_message(self, msg: discord.Message, disabled=None):
        if msg.author != self.bot.user:
            return False

        content = (msg.content or "").strip()
        ids = self._message_custom_ids(msg)

        basic_ok = content.startswith(CONTROL_PREFIX) and (GENERATOR_SELECT_CUSTOM_ID in ids)
        if not basic_ok:
            return False

        if disabled is None:
            return True

        msg_disabled = self._panel_disabled_state(msg)
        return (msg_disabled is not None and msg_disabled == bool(disabled))

    async def refresh_button(self, force=False, disabled=False):
        try:
            async with self.control_lock:
                channel = await self._get_video_channel()

                if hasattr(channel, "guild") and channel.guild:
                    me = channel.guild.get_member(self.bot.user.id)
                    if me:
                        perms = channel.permissions_for(me)
                        if not perms.send_messages:
                            print("BUTTON REFRESH ERROR: Missing 'Send Messages' permission.")
                            return False

                messages = [m async for m in channel.history(limit=CONTROL_LOOKBACK_LIMIT)]
                panel_messages = [m for m in messages if self._looks_like_generator_message(m)]

                # WICHTIG: gültiges Panel darf irgendwo in den letzten Nachrichten sein
                valid_panel = None
                for m in panel_messages:
                    if self._is_valid_generator_message(m, disabled=disabled):
                        valid_panel = m
                        break

                if valid_panel and not force:
                    for old_msg in panel_messages:
                        if old_msg.id != valid_panel.id:
                            with contextlib.suppress(Exception):
                                await old_msg.delete()
                    return True

                for msg in panel_messages:
                    with contextlib.suppress(Exception):
                        await msg.delete()

                await channel.send(CONTROL_MESSAGE_TEXT, view=ModelPickerView(self, disabled=disabled))
                print("GENERATOR PANEL POSTED")
                return True

        except Exception as e:
            print("BUTTON REFRESH ERROR:", repr(e))
            return False

    async def ensure_control_message_with_retry(self):
        await self.bot.wait_until_ready()
        for attempt in range(1, 9):
            ok = await self.refresh_button(force=False, disabled=self.is_global_busy())
            if ok:
                return True
            await asyncio.sleep(min(15, attempt * 2))
        print("FAILED TO ENSURE GENERATOR PANEL AFTER RETRIES")
        return False

    # -------------------------------------------------
    # PROGRESS SAFETY
    # -------------------------------------------------

    async def _safe_edit_progress(self, progress_message, embed):
        if not progress_message:
            return
        with contextlib.suppress(Exception):
            await progress_message.edit(embed=embed)

    async def _safe_delete_progress_message(self, progress_message):
        if not progress_message:
            return False

        for attempt in range(3):
            try:
                await progress_message.delete()
                return True
            except discord.NotFound:
                return True
            except discord.Forbidden as e:
                print("PROGRESS DELETE FORBIDDEN (direct):", repr(e))
                return False
            except discord.HTTPException as e:
                print("PROGRESS DELETE HTTP ERROR (direct):", repr(e))
                try:
                    msg = await progress_message.channel.fetch_message(progress_message.id)
                    await msg.delete()
                    return True
                except discord.NotFound:
                    return True
                except discord.Forbidden as e2:
                    print("PROGRESS DELETE FORBIDDEN (fetch):", repr(e2))
                    return False
                except discord.HTTPException as e2:
                    print("PROGRESS DELETE HTTP ERROR (fetch):", repr(e2))
                    await asyncio.sleep(0.6 * (attempt + 1))
                except Exception as e2:
                    print("PROGRESS DELETE ERROR (fetch):", repr(e2))
                    await asyncio.sleep(0.6 * (attempt + 1))
            except Exception as e:
                print("PROGRESS DELETE ERROR (direct):", repr(e))
                await asyncio.sleep(0.6 * (attempt + 1))

        return False

    # -------------------------------------------------
    # EMBEDS
    # -------------------------------------------------

    def _build_progress_embed(
        self,
        user,
        prompt,
        seconds,
        aspect,
        model,
        percent,
        elapsed_sec,
        eta_sec,
        stage_text="Rendering..."
    ):
        prompt_preview = codeblock_safe(trim_text(prompt, PROMPT_PREVIEW_PROGRESS))
        bar = progress_bar(percent, blocks=14)
        eta_text = f"{eta_sec}s" if eta_sec is not None else "calculating..."

        embed = discord.Embed(
            title="🎬 AI Video Render",
            description=f"{user.mention} • `{model['name']}`",
            color=discord.Color.blurple(),
            timestamp=utc_now()
        )
        embed.add_field(name="Prompt (copyable)", value=f"```{prompt_preview}```", inline=False)
        embed.add_field(name="Progress", value=f"`{bar} {percent}%`", inline=False)
        embed.add_field(
            name="Settings",
            value=f"• Aspect: `{aspect}`\n• Duration: `{seconds}s`\n• Resolution: `{model['resolution']}`",
            inline=False
        )
        embed.add_field(
            name="Timing",
            value=(
                f"• Elapsed: `{elapsed_sec}s`\n"
                f"• ETA: `{eta_text}`\n"
                f"• Status: {stage_text}\n"
                "• 🔒 Generator locked until this render is complete."
            ),
            inline=False
        )
        embed.set_footer(text="AI Video Generator")
        return embed

    def _build_error_embed(self, user, prompt, seconds, aspect, model, reason):
        prompt_preview = codeblock_safe(trim_text(prompt, PROMPT_PREVIEW_PROGRESS))
        reason_text = trim_text(reason or "Generation failed.", REASON_PREVIEW_LIMIT)

        embed = discord.Embed(
            title="❌ Render Failed",
            description=f"{user.mention} your request could not be completed.",
            color=discord.Color.red(),
            timestamp=utc_now()
        )
        embed.add_field(name="Reason", value=reason_text, inline=False)
        embed.add_field(name="Prompt (copyable)", value=f"```{prompt_preview}```", inline=False)
        embed.add_field(
            name="Settings",
            value=(
                f"• Model: `{model['name']}`\n"
                f"• Aspect: `{aspect}`\n"
                f"• Duration: `{seconds}s`\n"
                f"• Resolution: `{model['resolution']}`"
            ),
            inline=False
        )
        embed.set_footer(text="No time was deducted for failed renders.")
        return embed

    def _build_result_embed(self, user, prompt, seconds, aspect, model, is_video):
        prompt_preview = codeblock_safe(trim_text(prompt, PROMPT_PREVIEW_RESULT))
        title = "✅ Video Ready" if is_video else "✅ Image Ready"
        color = discord.Color.green() if is_video else discord.Color.teal()

        embed = discord.Embed(
            title=title,
            description=f"{user.mention} your render is complete.",
            color=color,
            timestamp=utc_now()
        )
        embed.add_field(name="Prompt (copyable)", value=f"```{prompt_preview}```", inline=False)
        embed.add_field(
            name="Settings",
            value=(
                f"• Model: `{model['name']}`\n"
                f"• Aspect: `{aspect}`\n"
                f"• Duration: `{seconds}s`\n"
                f"• Resolution: `{model['resolution']}`"
            ),
            inline=False
        )
        embed.set_footer(text="AI Video Generator")
        return embed

    # -------------------------------------------------
    # PROGRESS / ETA LOGIC
    # -------------------------------------------------

    def _estimate_target_time_ms(self, model_key, seconds, avg_ms):
        base = BASE_TARGET_MS.get(seconds, 160000)
        avg = safe_int(avg_ms, base)

        blended = int(base * 0.35 + avg * 0.65)
        blended = max(base, blended)

        duration_factor = DURATION_FACTOR.get(seconds, 1.0)
        model_factor = MODEL_TIME_FACTOR.get(model_key, 1.0)

        target = int(blended * duration_factor * model_factor)
        return max(target, 35000)

    def _calculate_percent(self, elapsed_ms, target_ms, last_percent):
        raw = elapsed_ms / max(target_ms, 1)

        if raw < 0.25:
            smoothed = 0.05 + raw * 0.45
        elif raw < 0.85:
            smoothed = 0.16 + (raw - 0.25) * 1.20
        else:
            smoothed = 0.88 + (raw - 0.85) * 0.50

        percent = int(min(max(smoothed * 100, 5), 97))

        if elapsed_ms > 12000 and percent < 7:
            percent = 7

        if percent < last_percent:
            percent = last_percent

        return percent

    async def _animate_queue_wait(self, progress_message, user, prompt, seconds, aspect, model):
        frames = [
            "Sending queue request.",
            "Sending queue request..",
            "Sending queue request..."
        ]
        i = 0
        while True:
            await self._safe_edit_progress(
                progress_message,
                self._build_progress_embed(
                    user=user,
                    prompt=prompt,
                    seconds=seconds,
                    aspect=aspect,
                    model=model,
                    percent=5,
                    elapsed_sec=0,
                    eta_sec=None,
                    stage_text=frames[i % len(frames)]
                )
            )
            i += 1
            await asyncio.sleep(1.2)

    # -------------------------------------------------
    # API HELPERS
    # -------------------------------------------------

    def _extract_queue_id(self, payload):
        if not isinstance(payload, dict):
            return None
        if payload.get("queue_id"):
            return payload.get("queue_id")
        if payload.get("id"):
            return payload.get("id")
        nested = payload.get("data")
        if isinstance(nested, dict) and nested.get("queue_id"):
            return nested.get("queue_id")
        return None

    def _extract_urls_from_payload(self, payload):
        urls = []
        if not isinstance(payload, (dict, list)):
            return urls

        interesting_keys = {
            "download_url", "url", "result_url", "video_url", "image_url", "file_url", "asset_url"
        }

        def walk(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    lk = str(k).lower()
                    if isinstance(v, str) and v.startswith("http"):
                        if lk in interesting_keys or "url" in lk or "download" in lk:
                            urls.append(v)
                    walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item)

        walk(payload)
        return list(dict.fromkeys(urls))

    def _parse_api_error(self, status_code: int, raw_text: str):
        msg = None
        err_type = None
        credits_refunded = False

        try:
            data = json.loads(raw_text) if raw_text else {}
            err = data.get("error")
            if isinstance(err, dict):
                msg = err.get("message") or data.get("message")
                err_type = err.get("type")
                credits_refunded = bool(err.get("credits_refunded"))
            elif isinstance(err, str):
                msg = err
            elif isinstance(data.get("message"), str):
                msg = data.get("message")
        except Exception:
            msg = (raw_text or "").strip()[:300] or None

        if status_code == 422 and err_type == "provider_content_policy":
            text = "Rendering aborted: Rejected by the model provider due to content policy."
            if credits_refunded:
                text += " Credits were refunded by the provider."
            return text, True

        if status_code in (401, 403):
            return "API authentication failed (401/403).", True

        if status_code == 429:
            return "Rate limited by provider (429). Retrying...", False

        if 500 <= status_code <= 599:
            return "Provider server error. Retrying...", False

        if 400 <= status_code <= 499:
            return f"Rendering aborted ({status_code}): {msg or 'Invalid request.'}", True

        return None, False

    async def _fetch_media_from_url(self, session, url, headers, visited=None):
        if not isinstance(url, str) or not url.startswith("http"):
            return None, None

        if visited is None:
            visited = set()
        if url in visited:
            return None, None
        visited.add(url)

        timeout = aiohttp.ClientTimeout(total=45, connect=12, sock_read=35)

        for use_auth in (True, False):
            try:
                req_headers = dict(headers) if use_auth else {}
                async with session.get(url, headers=req_headers, timeout=timeout) as resp:
                    body = await resp.read()
                    ctype = (resp.headers.get("content-type", "") or "").lower()

                    if not body or resp.status >= 400:
                        continue

                    if "video" in ctype:
                        return body, "video"
                    if "image" in ctype:
                        return body, "image"

                    guessed = detect_media_type(body)
                    if guessed:
                        return body, guessed

                    if "json" in ctype:
                        try:
                            nested_payload = json.loads(body.decode("utf-8", errors="ignore"))
                        except Exception:
                            nested_payload = None

                        if nested_payload:
                            nested_urls = self._extract_urls_from_payload(nested_payload)
                            for nested_url in nested_urls:
                                nested_data, nested_type = await self._fetch_media_from_url(
                                    session=session,
                                    url=nested_url,
                                    headers=headers,
                                    visited=visited
                                )
                                if nested_data:
                                    return nested_data, nested_type

            except Exception as e:
                print("DOWNLOAD URL FETCH ERROR:", repr(e))

        return None, None

    async def _queue_generation(self, payload, headers):
        timeout = aiohttp.ClientTimeout(total=35, connect=10, sock_read=30)
        last_error = None
        session = await self._http()

        for attempt in range(3):
            try:
                async with session.post(VIDEO_QUEUE_URL, headers=headers, json=payload, timeout=timeout) as response:
                    text = await response.text()
                    try:
                        data = json.loads(text)
                    except Exception:
                        data = {"raw": text}

                    if response.status >= 400:
                        user_error, hard_fail = self._parse_api_error(response.status, text)
                        last_error = user_error or f"Queue error ({response.status})."
                        print("QUEUE ERROR:", response.status, text[:300])

                        if hard_fail:
                            return None, data, last_error

                        await asyncio.sleep(1.4 * (attempt + 1))
                        continue

                    queue_id = self._extract_queue_id(data)
                    if queue_id:
                        return queue_id, data, None

                    last_error = "Queue response did not include queue_id."
                    print("QUEUE NO ID:", data)
                    await asyncio.sleep(1.2 * (attempt + 1))

            except Exception as e:
                last_error = f"Queue request failed: {repr(e)}"
                print("QUEUE REQUEST ERROR:", repr(e))
                await asyncio.sleep(1.2 * (attempt + 1))

        return None, None, (last_error or "Queue creation failed. Please try again.")

    # -------------------------------------------------
    # MAIN FLOW
    # -------------------------------------------------

    async def start_generation(self, interaction, user, prompt, seconds, aspect, model_key):
        if not MORDIEM_API:
            await interaction.followup.send("❌ MORDIEM_API is missing.", ephemeral=True)
            return

        if model_key not in VIDEO_MODELS:
            model_key = DEFAULT_MODEL
        model = VIDEO_MODELS[model_key]

        if not await self._mark_user_starting(user.id):
            await interaction.followup.send(
                "⏳ A generation for you is already starting. Please wait.",
                ephemeral=True
            )
            return

        acquired = await self._try_begin_global_render()
        if not acquired:
            await self._unmark_user_starting(user.id)
            await interaction.followup.send(
                "⏳ A render is currently running. Please wait until it finishes.",
                ephemeral=True
            )
            return

        self.active_interactions[user.id] = interaction
        progress_message = None
        queue_anim_task = None
        queue_id = None

        try:
            await self.refresh_button(force=True, disabled=True)

            if seconds > model["max_seconds"]:
                await interaction.followup.send(
                    f"❌ Model limit exceeded. Max is {model['max_seconds']}s.",
                    ephemeral=True
                )
                return

            remaining, reset = await self.get_usage_info(user)
            if remaining < seconds:
                allowed = [s for s in (5, 10, 15) if s <= model["max_seconds"] and s <= remaining]
                if allowed:
                    await interaction.followup.send(
                        f"❌ Not enough time for **{seconds}s**.\n"
                        f"You currently have **{remaining}s** left.\n\n"
                        f"Please choose one of: **{', '.join(f'{x}s' for x in allowed)}**",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        f"❌ Not enough render time.\n\n"
                        f"Remaining: **{remaining}s**\n"
                        f"Reset: **{format_reset(reset)}**",
                        ephemeral=True
                    )
                return

            payload = {
                "model": model_key,
                "prompt": prompt,
                "resolution": model["resolution"],
                "aspect_ratio": aspect
            }

            mode = str(model.get("mode", "")).lower()
            is_video_model = (mode == "video") or ("text-to-video" in model_key)
            if is_video_model:
                payload["duration"] = f"{seconds}s"

            headers = {
                "Authorization": f"Bearer {MORDIEM_API}",
                "Content-Type": "application/json"
            }

            channel = await self._get_video_channel()

            progress_message = await channel.send(
                embed=self._build_progress_embed(
                    user=user,
                    prompt=prompt,
                    seconds=seconds,
                    aspect=aspect,
                    model=model,
                    percent=5,
                    elapsed_sec=0,
                    eta_sec=None,
                    stage_text="Sending queue request..."
                )
            )

            queue_anim_task = asyncio.create_task(
                self._animate_queue_wait(
                    progress_message=progress_message,
                    user=user,
                    prompt=prompt,
                    seconds=seconds,
                    aspect=aspect,
                    model=model
                )
            )

            print("GEN REQUEST:", payload)
            queue_id, queue_response, queue_error = await self._queue_generation(payload, headers)
            print("GEN QUEUE RESPONSE:", queue_response)

            if queue_anim_task:
                queue_anim_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await queue_anim_task
                queue_anim_task = None

            if not queue_id:
                await self.post_result(
                    channel=channel,
                    user=user,
                    prompt=prompt,
                    seconds=seconds,
                    aspect=aspect,
                    model=model,
                    media_data=None,
                    media_type=None,
                    progress_message=progress_message,
                    error_message=queue_error or "Queue creation failed. Please try again."
                )
                return

            self.add_active_job(user, queue_id)

            await self._safe_edit_progress(
                progress_message,
                self._build_progress_embed(
                    user=user,
                    prompt=prompt,
                    seconds=seconds,
                    aspect=aspect,
                    model=model,
                    percent=8,
                    elapsed_sec=1,
                    eta_sec=None,
                    stage_text="Queue accepted. Rendering started."
                )
            )

            queue_download_url = None
            if isinstance(queue_response, dict):
                queue_download_url = queue_response.get("download_url")

            media_data, media_type, error_message = await self.wait_for_result(
                queue_id=queue_id,
                model_key=model_key,
                seconds=seconds,
                user=user,
                prompt=prompt,
                aspect=aspect,
                model=model,
                progress_message=progress_message,
                queue_download_url=queue_download_url
            )

            await self.post_result(
                channel=channel,
                user=user,
                prompt=prompt,
                seconds=seconds,
                aspect=aspect,
                model=model,
                media_data=media_data,
                media_type=media_type,
                progress_message=progress_message,
                error_message=error_message
            )

        finally:
            if queue_anim_task:
                queue_anim_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await queue_anim_task

            await self._safe_delete_progress_message(progress_message)

            if queue_id:
                self.remove_active_job(user)

            self.active_interactions.pop(user.id, None)
            await self._end_global_render()
            await self._unmark_user_starting(user.id)

            await self.refresh_button(force=True, disabled=False)

    # -------------------------------------------------
    # STATUS LOOP
    # -------------------------------------------------

    async def wait_for_result(
        self,
        queue_id,
        model_key,
        seconds,
        user,
        prompt,
        aspect,
        model,
        progress_message,
        queue_download_url=None
    ):
        headers = {
            "Authorization": f"Bearer {MORDIEM_API}",
            "Content-Type": "application/json"
        }

        started = utc_now()
        hard_deadline = started + timedelta(minutes=30)
        adaptive_deadline = started + timedelta(minutes=12)

        last_percent = 8
        finalize_attempts = 0

        # Neu: 500-Tracking
        consecutive_5xx = 0
        total_5xx = 0
        first_5xx_at = None

        timeout = aiohttp.ClientTimeout(total=90, connect=15, sock_read=70)
        session = await self._http()

        while True:
            now = utc_now()
            if now >= hard_deadline or now >= adaptive_deadline:
                break

            await asyncio.sleep(6)

            try:
                async with session.post(
                    VIDEO_RETRIEVE_URL,
                    headers=headers,
                    json={"model": model_key, "queue_id": queue_id},
                    timeout=timeout
                ) as response:
                    content_type = (response.headers.get("content-type", "") or "").lower()

                    if response.status >= 400:
                        body_text = await response.text()
                        user_error, hard_fail = self._parse_api_error(response.status, body_text)
                        print("RETRIEVE HTTP ERROR:", response.status, body_text[:300])

                        if response.status >= 500:
                            total_5xx += 1
                            consecutive_5xx += 1
                            if first_5xx_at is None:
                                first_5xx_at = utc_now()

                            await self._safe_edit_progress(
                                progress_message,
                                self._build_progress_embed(
                                    user=user,
                                    prompt=prompt,
                                    seconds=seconds,
                                    aspect=aspect,
                                    model=model,
                                    percent=max(last_percent, 12),
                                    elapsed_sec=max(int((utc_now() - started).total_seconds()), 0),
                                    eta_sec=None,
                                    stage_text=f"Provider error {response.status} (retry {total_5xx})..."
                                )
                            )

                            too_many = consecutive_5xx >= MAX_CONSECUTIVE_5XX
                            too_long = (utc_now() - first_5xx_at).total_seconds() >= MAX_5XX_WINDOW_SECONDS
                            if too_many or too_long:
                                return None, None, (
                                    "Provider currently unavailable (repeated 500 errors). "
                                    "Please try again in a few minutes."
                                )
                        else:
                            consecutive_5xx = 0
                            first_5xx_at = None

                        if hard_fail:
                            return None, None, (user_error or "Rendering failed.")
                        continue

                    # erfolgreicher HTTP-Call => reset 5xx streak
                    consecutive_5xx = 0
                    first_5xx_at = None

                    if "video" in content_type:
                        data = await response.read()
                        return data, "video", None

                    if "image" in content_type:
                        data = await response.read()
                        return data, "image", None

                    if "octet-stream" in content_type:
                        blob = await response.read()
                        guessed = detect_media_type(blob)
                        if guessed:
                            return blob, guessed, None

                    raw_text = await response.text()
                    try:
                        data = json.loads(raw_text)
                    except Exception:
                        print("STATUS NON-JSON:", raw_text[:300])
                        continue

                    print("GEN STATUS:", data)

                    if isinstance(data.get("error"), dict):
                        err = data["error"]
                        if err.get("type") == "provider_content_policy":
                            txt = "Rendering aborted: Rejected by the model provider due to content policy."
                            if bool(err.get("credits_refunded")):
                                txt += " Credits were refunded by the provider."
                            return None, None, txt

                    status = str(data.get("status", "")).lower()
                    avg = safe_int(data.get("average_execution_time", 180000), 180000)
                    elapsed = safe_int(data.get("execution_duration", 0), 0)
                    if elapsed <= 0:
                        elapsed = int((utc_now() - started).total_seconds() * 1000)

                    expected_total_sec = int((max(avg, 60000) / 1000) * 2.5) + 120
                    candidate_deadline = started + timedelta(seconds=expected_total_sec)
                    if candidate_deadline > adaptive_deadline:
                        adaptive_deadline = min(candidate_deadline, hard_deadline)

                    if status in {"failed", "error", "cancelled", "canceled"}:
                        err_msg = None
                        err = data.get("error")
                        if isinstance(err, dict):
                            err_msg = err.get("message")
                        elif isinstance(err, str):
                            err_msg = err
                        elif isinstance(data.get("message"), str):
                            err_msg = data.get("message")

                        if isinstance(err, dict) and err.get("type") == "provider_content_policy":
                            txt = "Rendering aborted: Rejected by the model provider due to content policy."
                            if bool(err.get("credits_refunded")):
                                txt += " Credits were refunded by the provider."
                            return None, None, txt

                        if err_msg:
                            return None, None, f"Rendering aborted: {err_msg}"
                        return None, None, "Rendering aborted."

                    if status == "completed":
                        candidate_urls = []
                        if isinstance(queue_download_url, str) and queue_download_url.startswith("http"):
                            candidate_urls.append(queue_download_url)

                        candidate_urls.extend(self._extract_urls_from_payload(data))
                        candidate_urls = list(dict.fromkeys(candidate_urls))

                        for media_url in candidate_urls:
                            media_data, media_type = await self._fetch_media_from_url(
                                session=session,
                                url=media_url,
                                headers=headers
                            )
                            if media_data:
                                return media_data, media_type, None

                        finalize_attempts += 1
                        await self._safe_edit_progress(
                            progress_message,
                            self._build_progress_embed(
                                user=user,
                                prompt=prompt,
                                seconds=seconds,
                                aspect=aspect,
                                model=model,
                                percent=max(last_percent, 98),
                                elapsed_sec=max(elapsed // 1000, 0),
                                eta_sec=None,
                                stage_text="Finalizing file delivery..."
                            )
                        )

                        if finalize_attempts >= 40:
                            return None, None, "Rendering finished, but the file could not be delivered."
                        continue

                    target_ms = self._estimate_target_time_ms(model_key, seconds, avg)
                    percent = self._calculate_percent(elapsed, target_ms, last_percent)

                    if percent != last_percent:
                        last_percent = percent
                        eta_sec = max((target_ms - elapsed) // 1000, 0)

                        await self._safe_edit_progress(
                            progress_message,
                            self._build_progress_embed(
                                user=user,
                                prompt=prompt,
                                seconds=seconds,
                                aspect=aspect,
                                model=model,
                                percent=percent,
                                elapsed_sec=max(elapsed // 1000, 0),
                                eta_sec=eta_sec,
                                stage_text="Rendering..."
                            )
                        )

            except asyncio.TimeoutError as e:
                print("STATUS LOOP TIMEOUT:", repr(e))
                continue
            except Exception as e:
                print("STATUS LOOP ERROR:", repr(e))
                continue

        return None, None, "Generation failed or timed out."

    # -------------------------------------------------
    # FINAL POST
    # -------------------------------------------------

    async def post_result(
        self,
        channel,
        user,
        prompt,
        seconds,
        aspect,
        model,
        media_data,
        media_type,
        progress_message,
        error_message=None
    ):
        interaction = self.active_interactions.get(user.id)

        if not media_data:
            reason = error_message or "Generation failed or timed out."
            await self._finalize_failed_render(user=user, reason=reason, progress_message=progress_message)
            return

        guild_limit = None
        if getattr(channel, "guild", None):
            guild_limit = getattr(channel.guild, "filesize_limit", None)

        if guild_limit and len(media_data) > guild_limit:
            size_mb = len(media_data) / (1024 * 1024)
            limit_mb = guild_limit / (1024 * 1024)
            reason = (
                "Render completed, but the output file is too large for this server's Discord upload limit. "
                f"File: {size_mb:.2f} MB • Limit: {limit_mb:.2f} MB"
            )
            await self._finalize_failed_render(user=user, reason=reason, progress_message=progress_message)
            return

        is_video = (media_type == "video")
        filename = "AI_video.mp4" if is_video else "AI_image.png"
        file = discord.File(io.BytesIO(media_data), filename=filename)

        result_embed = self._build_result_embed(
            user=user,
            prompt=prompt,
            seconds=seconds,
            aspect=aspect,
            model=model,
            is_video=is_video
        )

        if not is_video:
            result_embed.set_image(url=f"attachment://{filename}")

        try:
            await channel.send(embed=result_embed, file=file)
            await self.save_usage(user, seconds)

        except discord.HTTPException as e:
            print("RESULT SEND ERROR:", repr(e))
            if e.status == 413 or getattr(e, "code", None) == 40005:
                reason = "Render completed, but the output file is too large for this server's Discord upload limit."
            else:
                reason = "Render output could not be posted to Discord."
            await self._finalize_failed_render(user=user, reason=reason, progress_message=progress_message)
            return

        except Exception as e:
            print("RESULT SEND ERROR:", repr(e))
            reason = "Render output could not be posted to Discord."
            await self._finalize_failed_render(user=user, reason=reason, progress_message=progress_message)
            return

        await self._safe_delete_progress_message(progress_message)

        quota = await self._quota_summary(user)
        if interaction:
            with contextlib.suppress(Exception):
                await interaction.followup.send(f"✅ Completed!\n\n{quota}", ephemeral=True)
        else:
            await self._safe_followup_for_user(user, f"✅ Completed!\n\n{quota}")


# =====================================================
# SETUP
# =====================================================

async def setup(bot):
    await bot.add_cog(VideoCog(bot))