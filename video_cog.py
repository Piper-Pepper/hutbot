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

PROMPT_PREVIEW_LIMIT = 500


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
    1346414581643219029: 300
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

# Duration baseline scaling
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

# Per-model progress speed tuning (lower = visually faster)
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


def format_reset(dt):
    if not dt:
        return "unknown"
    return dt.strftime("%d.%m.%Y %H:%M UTC")


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def trim_prompt(text: str, limit: int = PROMPT_PREVIEW_LIMIT) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "[...]"


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


# =====================================================
# SQLITE
# =====================================================

class VideoDatabase:
    def __init__(self):
        self.lock = threading.Lock()
        self.db = sqlite3.connect("videos.sqlite", check_same_thread=False)
        self.cursor = self.db.cursor()

        with self.lock:
            self.cursor.execute("PRAGMA journal_mode=WAL")
            self.cursor.execute("PRAGMA synchronous=NORMAL")
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS usage (
                    user_id TEXT,
                    seconds INTEGER,
                    created TEXT
                )
                """
            )
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS active_jobs (
                    user_id TEXT PRIMARY KEY,
                    queue_id TEXT
                )
                """
            )
            self.cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_user_created ON usage (user_id, created)"
            )
            self.db.commit()

    def execute(self, query, params=()):
        with self.lock:
            self.cursor.execute(query, params)
            self.db.commit()

    def fetchall(self, query, params=()):
        with self.lock:
            self.cursor.execute(query, params)
            return self.cursor.fetchall()

    def fetchone(self, query, params=()):
        with self.lock:
            self.cursor.execute(query, params)
            return self.cursor.fetchone()


# =====================================================
# MODEL SELECT
# =====================================================

class ModelSelect(ui.Select):
    def __init__(self, cog):
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
            custom_id=GENERATOR_SELECT_CUSTOM_ID
        )

    async def callback(self, interaction: discord.Interaction):
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
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.add_item(ModelSelect(cog))


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
        await interaction.response.send_message(
            "⏱ Choose video length:",
            view=DurationView(
                self.cog,
                interaction.user,
                self.prompt.value,
                self.model_key
            ),
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
    def __init__(self, cog, user, prompt, model_key):
        super().__init__(timeout=120)
        self.cog = cog
        self.user = user
        self.prompt = prompt
        self.model_key = model_key

        model = VIDEO_MODELS.get(model_key, VIDEO_MODELS[DEFAULT_MODEL])
        max_seconds = safe_int(model.get("max_seconds", 15), 15)
        allowed = [s for s in (5, 10, 15) if s <= max_seconds]

        if not allowed:
            self.add_item(ui.Button(label="No valid duration", disabled=True, style=discord.ButtonStyle.gray))
        else:
            for sec in allowed:
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
        model = VIDEO_MODELS.get(self.model_key, VIDEO_MODELS[DEFAULT_MODEL])

        if seconds > safe_int(model.get("max_seconds", 15), 15):
            await interaction.response.send_message(
                f"❌ This model supports max **{model.get('max_seconds', 15)}s**.",
                ephemeral=True
            )
            return

        remaining, reset = await self.cog.get_usage_info(self.user)
        if remaining < seconds:
            await interaction.response.send_message(
                f"❌ Not enough render time.\n\n"
                f"⏳ Remaining: **{remaining}s**\n"
                f"🔄 Reset: **{format_reset(reset)}**",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "📐 Choose aspect ratio:",
            view=AspectView(
                self.cog,
                self.user,
                self.prompt,
                seconds,
                self.model_key
            ),
            ephemeral=True
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
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=f"⏳ Starting with aspect `{aspect}`...",
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
        self.active_interactions = {}
        self.render_lock = asyncio.Lock()  # one global generation
        self.control_lock = asyncio.Lock()
        self.startup_task = None

        self.starting_users = set()
        self.starting_lock = asyncio.Lock()

    async def cog_load(self):
        self.bot.add_view(ModelPickerView(self))
        if self.startup_task is None or self.startup_task.done():
            self.startup_task = asyncio.create_task(self.ensure_control_message_with_retry())

    def cog_unload(self):
        if self.startup_task and not self.startup_task.done():
            self.startup_task.cancel()

    async def _mark_user_starting(self, user_id: int) -> bool:
        async with self.starting_lock:
            if user_id in self.starting_users:
                return False
            self.starting_users.add(user_id)
            return True

    async def _unmark_user_starting(self, user_id: int):
        async with self.starting_lock:
            self.starting_users.discard(user_id)

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

    def _looks_like_generator_message(self, msg: discord.Message):
        if msg.author != self.bot.user:
            return False
        content = (msg.content or "").strip()
        ids = self._message_custom_ids(msg)
        return content.startswith(CONTROL_PREFIX) or (GENERATOR_SELECT_CUSTOM_ID in ids)

    def _is_valid_generator_message(self, msg: discord.Message):
        if msg.author != self.bot.user:
            return False
        content = (msg.content or "").strip()
        ids = self._message_custom_ids(msg)
        return content.startswith(CONTROL_PREFIX) and (GENERATOR_SELECT_CUSTOM_ID in ids)

    async def refresh_button(self, force=False):
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
                newest = messages[0] if messages else None
                panel_messages = [m for m in messages if self._looks_like_generator_message(m)]
                newest_is_valid = newest is not None and self._is_valid_generator_message(newest)

                if newest_is_valid and not force:
                    # remove duplicate old panels if any
                    for old_msg in panel_messages[1:]:
                        with contextlib.suppress(Exception):
                            await old_msg.delete()
                    return True

                # remove old panels and post new one
                for msg in panel_messages:
                    with contextlib.suppress(Exception):
                        await msg.delete()

                await channel.send(CONTROL_MESSAGE_TEXT, view=ModelPickerView(self))
                print("GENERATOR PANEL POSTED")
                return True

        except Exception as e:
            print("BUTTON REFRESH ERROR:", repr(e))
            return False

    async def remove_button(self):
        try:
            async with self.control_lock:
                channel = await self._get_video_channel()
                messages = [m async for m in channel.history(limit=CONTROL_LOOKBACK_LIMIT)]
                for msg in messages:
                    if self._looks_like_generator_message(msg):
                        with contextlib.suppress(Exception):
                            await msg.delete()
            return True
        except Exception as e:
            print("REMOVE CONTROL ERROR:", repr(e))
            return False

    async def ensure_control_message_with_retry(self):
        await self.bot.wait_until_ready()
        for attempt in range(1, 9):
            ok = await self.refresh_button(force=False)
            if ok:
                return True
            await asyncio.sleep(min(15, attempt * 2))
        print("FAILED TO ENSURE GENERATOR PANEL AFTER RETRIES")
        return False

    # -------------------------------------------------
    # PROGRESS
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
        blocks = 20
        filled = int(blocks * percent / 100)
        bar = "█" * filled + "░" * (blocks - filled)

        prompt_preview = codeblock_safe(trim_prompt(prompt, PROMPT_PREVIEW_LIMIT))
        eta_text = f"~{eta_sec}s" if eta_sec is not None else "calculating..."

        return discord.Embed(
            title="🎬 Rendering",
            description=(
                f"👤 {user.mention}\n"
                f"📝 **Prompt**\n```{prompt_preview}```\n"
                f"```{bar} {percent}%```\n"
                f"⏱ Elapsed: {elapsed_sec}s • ETA: {eta_text}\n"
                f"📡 {stage_text}\n"
                f"⚙️ `{aspect}` • `{seconds}s` • `{model['name']}` • `{model['resolution']}`"
            ),
            timestamp=utc_now()
        )

    async def _animate_queue_wait(self, progress_message, user, prompt, seconds, aspect, model):
        frames = [
            "Sending queue request.",
            "Sending queue request..",
            "Sending queue request..."
        ]
        i = 0
        while True:
            await progress_message.edit(
                embed=self._build_progress_embed(
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

    async def _fetch_media_from_url(self, url, headers, visited=None):
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
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url, headers=req_headers) as resp:
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
                                        nested_url, headers, visited=visited
                                    )
                                    if nested_data:
                                        return nested_data, nested_type

            except Exception as e:
                print("DOWNLOAD URL FETCH ERROR:", repr(e))

        return None, None

    async def _queue_generation(self, payload, headers):
        timeout = aiohttp.ClientTimeout(total=35, connect=10, sock_read=30)

        for attempt in range(3):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        VIDEO_QUEUE_URL,
                        headers=headers,
                        json=payload
                    ) as response:
                        text = await response.text()
                        try:
                            data = json.loads(text)
                        except Exception:
                            data = {"raw": text}

                        if response.status >= 400:
                            print("QUEUE ERROR:", response.status, data)
                            await asyncio.sleep(1.5 * (attempt + 1))
                            continue

                        queue_id = self._extract_queue_id(data)
                        if queue_id:
                            return queue_id, data

                        print("QUEUE NO ID:", data)
                        await asyncio.sleep(1.2 * (attempt + 1))

            except Exception as e:
                print("QUEUE REQUEST ERROR:", repr(e))
                await asyncio.sleep(1.2 * (attempt + 1))

        return None, None

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

        if self.render_lock.locked():
            await self._unmark_user_starting(user.id)
            await interaction.followup.send(
                "⏳ A generation is currently running. Please wait.",
                ephemeral=True
            )
            return

        async with self.render_lock:
            self.active_interactions[user.id] = interaction
            progress_message = None
            queue_anim_task = None
            queue_id = None

            try:
                if seconds > model["max_seconds"]:
                    await interaction.followup.send(
                        f"❌ Model limit exceeded. Max is {model['max_seconds']}s.",
                        ephemeral=True
                    )
                    return

                remaining, reset = await self.get_usage_info(user)
                if remaining < seconds:
                    await interaction.followup.send(
                        f"❌ Not enough render time.\n\n"
                        f"⏳ Remaining: **{remaining}s**\n"
                        f"🔄 Reset: **{format_reset(reset)}**",
                        ephemeral=True
                    )
                    return

                payload = {
                    "model": model_key,
                    "prompt": prompt,
                    "resolution": model["resolution"],
                    "aspect_ratio": aspect
                }

                # Ensure duration is present for video models
                mode = str(model.get("mode", "")).lower()
                is_video_model = (mode == "video") or ("text-to-video" in model_key)
                if is_video_model:
                    payload["duration"] = f"{seconds}s"

                headers = {
                    "Authorization": f"Bearer {MORDIEM_API}",
                    "Content-Type": "application/json"
                }

                await self.remove_button()
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
                queue_id, queue_response = await self._queue_generation(payload, headers)
                print("GEN QUEUE RESPONSE:", queue_response)

                if queue_anim_task:
                    queue_anim_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await queue_anim_task
                    queue_anim_task = None

                if not queue_id:
                    if progress_message:
                        with contextlib.suppress(Exception):
                            await progress_message.delete()
                    await interaction.followup.send("❌ Queue creation failed. Try again.", ephemeral=True)
                    return

                await self.save_usage(user, seconds)
                self.add_active_job(user, queue_id)

                if progress_message:
                    with contextlib.suppress(Exception):
                        await progress_message.edit(
                            embed=self._build_progress_embed(
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

                media_data, media_type = await self.wait_for_result(
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
                    progress_message=progress_message
                )

            finally:
                if queue_anim_task:
                    queue_anim_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await queue_anim_task

                if queue_id:
                    self.remove_active_job(user)

                self.active_interactions.pop(user.id, None)
                await self.refresh_button(force=True)
                await self._unmark_user_starting(user.id)

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

        timeout = aiohttp.ClientTimeout(total=90, connect=15, sock_read=70)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            while utc_now() < hard_deadline and utc_now() < adaptive_deadline:
                await asyncio.sleep(6)

                try:
                    async with session.post(
                        VIDEO_RETRIEVE_URL,
                        headers=headers,
                        json={"model": model_key, "queue_id": queue_id}
                    ) as response:
                        content_type = (response.headers.get("content-type", "") or "").lower()

                        if response.status >= 400:
                            body_preview = (await response.text())[:300]
                            print("RETRIEVE HTTP ERROR:", response.status, body_preview)
                            continue

                        # Direct media body
                        if "video" in content_type:
                            data = await response.read()
                            return data, "video"

                        if "image" in content_type:
                            data = await response.read()
                            return data, "image"

                        if "octet-stream" in content_type:
                            blob = await response.read()
                            guessed = detect_media_type(blob)
                            if guessed:
                                return blob, guessed

                        raw_text = await response.text()
                        try:
                            data = json.loads(raw_text)
                        except Exception:
                            print("STATUS NON-JSON:", raw_text[:300])
                            continue

                        print("GEN STATUS:", data)

                        status = str(data.get("status", "")).lower()
                        avg = safe_int(data.get("average_execution_time", 180000), 180000)
                        elapsed = safe_int(data.get("execution_duration", 0), 0)
                        if elapsed <= 0:
                            elapsed = int((utc_now() - started).total_seconds() * 1000)

                        # Extend runtime window for slower jobs
                        expected_total_sec = int((max(avg, 60000) / 1000) * 2.5) + 120
                        candidate_deadline = started + timedelta(seconds=expected_total_sec)
                        if candidate_deadline > adaptive_deadline:
                            adaptive_deadline = min(candidate_deadline, hard_deadline)

                        if status in {"failed", "error", "cancelled", "canceled"}:
                            return None, None

                        if status == "completed":
                            candidate_urls = []
                            if isinstance(queue_download_url, str) and queue_download_url.startswith("http"):
                                candidate_urls.append(queue_download_url)

                            candidate_urls.extend(self._extract_urls_from_payload(data))
                            candidate_urls = list(dict.fromkeys(candidate_urls))

                            for media_url in candidate_urls:
                                media_data, media_type = await self._fetch_media_from_url(media_url, headers)
                                if media_data:
                                    return media_data, media_type

                            finalize_attempts += 1
                            if progress_message:
                                with contextlib.suppress(Exception):
                                    await progress_message.edit(
                                        embed=self._build_progress_embed(
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
                                return None, None
                            continue

                        # Normal processing progress update
                        target_ms = self._estimate_target_time_ms(model_key, seconds, avg)
                        percent = self._calculate_percent(elapsed, target_ms, last_percent)

                        if percent != last_percent:
                            last_percent = percent
                            eta_sec = max((target_ms - elapsed) // 1000, 0)

                            if progress_message:
                                with contextlib.suppress(Exception):
                                    await progress_message.edit(
                                        embed=self._build_progress_embed(
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

        return None, None

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
        progress_message
    ):
        if not media_data:
            if progress_message:
                with contextlib.suppress(Exception):
                    await progress_message.delete()
            await channel.send("❌ Generation failed or timed out.")
            return

        is_video = (media_type == "video")
        filename = "AI_video.mp4" if is_video else "AI_image.png"
        file = discord.File(io.BytesIO(media_data), filename=filename)

        prompt_preview = codeblock_safe(trim_prompt(prompt, PROMPT_PREVIEW_LIMIT))
        settings_line = f"`{aspect}` • `{seconds}s` • `{model['name']}` • `{model['resolution']}`"
        title_emoji = "🎬" if is_video else "🖼️"

        embed = discord.Embed(
            title=f"{title_emoji} {user.display_name}",
            description=(
                f"📝 **Prompt**\n```{prompt_preview}```\n"
                f"⚙️ **Settings**\n{settings_line}"
            ),
            timestamp=utc_now()
        )

        icon = channel.guild.icon.url if channel.guild and channel.guild.icon else None
        embed.set_footer(
            text=f"{model['resolution']} • AI Generator",
            icon_url=icon
        )

        if not is_video:
            embed.set_image(url=f"attachment://{filename}")

        await channel.send(embed=embed, file=file)

        if progress_message:
            with contextlib.suppress(Exception):
                await progress_message.delete()

        remaining, reset = await self.get_usage_info(user)
        tier_name, tier_limit = self.get_user_tier(user)

        interaction = self.active_interactions.get(user.id)
        if interaction:
            with contextlib.suppress(Exception):
                await interaction.followup.send(
                    f"✅ Completed!\n\n"
                    f"🏆 Tier: **{tier_name}**\n"
                    f"⏳ Daily limit: **{tier_limit}s**\n\n"
                    f"🎬 Remaining: **{remaining}s**\n"
                    f"🔄 Reset: **{format_reset(reset)}**",
                    ephemeral=True
                )

    # -------------------------------------------------
    # READY
    # -------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self):
        print("VIDEO COG READY")
        if self.startup_task is None or self.startup_task.done():
            self.startup_task = asyncio.create_task(self.ensure_control_message_with_retry())


# =====================================================
# SETUP
# =====================================================

async def setup(bot):
    await bot.add_cog(VideoCog(bot))