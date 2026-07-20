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

CONTROL_MESSAGE_TEXT = "🎬 **AI Video Generator**\nChoose your model:"


# =====================================================
# ROLES / DAILY LIMITS
# =====================================================

ROLE_LIMITS = {
    1377051179615522926: 10,    # Tier 1
    1375147276413964408: 20,    # Tier 2
    1376592697606930593: 30,    # Tier 3
    1381791848875430069: 40,    # Tier 4
    1375666588404940830: 50,    # Tier 5
    1375584380914896978: 60,    # Tier 6
    1346414581643219029: 200
}


# =====================================================
# MODELS
# =====================================================

VIDEO_MODELS = {
    "wan-2-7-enhanced-text-to-video": {
        "name": "WAN 2.7 Enhanced",
        "mode": "video",
        "resolution": "720p",
        "max_seconds": 15
    },
    "wan-2-7-text-to-image": {
        "name": "WAN 2.7 (Text-to-Image)",
        "mode": "image",
        "resolution": "1024x1024",
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
    }
}

DEFAULT_MODEL = "wan-2-7-enhanced-text-to-video"

# Progress-Feintuning
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
# MODEL SELECT (Dropdown statt Button)
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
            custom_id="video_model_select"
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
# LENGTH SELECTION
# =====================================================

class DurationView(ui.View):
    def __init__(self, cog, user, prompt, model_key):
        super().__init__(timeout=120)
        self.cog = cog
        self.user = user
        self.prompt = prompt
        self.model_key = model_key

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "❌ This menu belongs to another user.",
                ephemeral=True
            )
            return False
        return True

    async def choose(self, interaction, seconds):
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

    @ui.button(label="5 seconds", style=discord.ButtonStyle.green)
    async def five(self, interaction, button):
        await self.choose(interaction, 5)

    @ui.button(label="10 seconds", style=discord.ButtonStyle.blurple)
    async def ten(self, interaction, button):
        await self.choose(interaction, 10)

    @ui.button(label="15 seconds", style=discord.ButtonStyle.red)
    async def fifteen(self, interaction, button):
        await self.choose(interaction, 15)


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
        await interaction.response.defer(ephemeral=True)
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
        self.render_lock = asyncio.Lock()  # globale Einzel-Queue (stabil)

    async def cog_load(self):
        self.bot.add_view(ModelPickerView(self))

    # -------------------------------------------------
    # ROLE / LIMIT SYSTEM
    # -------------------------------------------------

    def get_user_limit(self, user):
        highest = 0
        roles = getattr(user, "roles", [])
        for role in roles:
            if role.id in ROLE_LIMITS:
                highest = max(highest, ROLE_LIMITS[role.id])
        return highest

    def get_user_tier(self, user):
        highest = 0
        name = "No Tier"
        roles = getattr(user, "roles", [])
        for role in roles:
            if role.id in ROLE_LIMITS and ROLE_LIMITS[role.id] > highest:
                highest = ROLE_LIMITS[role.id]
                name = role.name
        return name, highest

    # -------------------------------------------------
    # DB / USAGE
    # -------------------------------------------------

    async def clean_usage(self):
        cutoff = (utc_now() - timedelta(hours=24)).isoformat()
        self.db.execute(
            """
            DELETE FROM usage
            WHERE created < ?
            """,
            (cutoff,)
        )

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

        used = sum(row[0] for row in rows)
        limit = self.get_user_limit(user)
        remaining = max(limit - used, 0)

        reset = None
        if rows:
            reset = datetime.fromisoformat(rows[0][1]) + timedelta(hours=24)

        return remaining, reset

    async def save_usage(self, user, seconds):
        self.db.execute(
            """
            INSERT INTO usage
            VALUES (?,?,?)
            """,
            (str(user.id), seconds, utc_now().isoformat())
        )

    def add_active_job(self, user, queue_id):
        self.db.execute(
            """
            INSERT OR REPLACE INTO active_jobs
            VALUES (?,?)
            """,
            (str(user.id), queue_id)
        )

    def remove_active_job(self, user):
        self.db.execute(
            """
            DELETE FROM active_jobs
            WHERE user_id=?
            """,
            (str(user.id),)
        )

    # -------------------------------------------------
    # CONTROL MESSAGE
    # -------------------------------------------------

    def _is_generator_message(self, msg: discord.Message):
        if msg.author != self.bot.user:
            return False
        if not msg.components:
            return False
        content = (msg.content or "").strip()
        return content.startswith("🎬 **AI Video Generator**")

    async def _delete_generator_messages_in_last(self, channel, limit=20):
        async for msg in channel.history(limit=limit):
            if self._is_generator_message(msg):
                try:
                    await msg.delete()
                except Exception:
                    pass

    async def refresh_button(self, force=False):
        """
        Beim Start:
        - Wenn neueste Nachricht bereits der Generator ist -> nichts tun.
        - Sonst alte Generator-Nachrichten (letzte 20) löschen und frisch posten.
        """
        try:
            channel = await self.bot.fetch_channel(VIDEO_CHANNEL_ID)
            messages = [m async for m in channel.history(limit=20)]
            newest = messages[0] if messages else None

            if not force and newest and self._is_generator_message(newest):
                return

            for msg in messages:
                if self._is_generator_message(msg):
                    try:
                        await msg.delete()
                    except Exception:
                        pass

            await channel.send(
                CONTROL_MESSAGE_TEXT,
                view=ModelPickerView(self)
            )
        except Exception as e:
            print("BUTTON REFRESH ERROR:", e)

    async def remove_button(self):
        try:
            channel = await self.bot.fetch_channel(VIDEO_CHANNEL_ID)
            await self._delete_generator_messages_in_last(channel, limit=20)
        except Exception as e:
            print("REMOVE CONTROL ERROR:", e)

    # -------------------------------------------------
    # PROGRESS HELPERS
    # -------------------------------------------------

    def _estimate_target_time_ms(self, seconds, avg_ms):
        base = BASE_TARGET_MS.get(seconds, 160000)
        avg = safe_int(avg_ms, base)
        target = max(base, avg)
        target = int(target * DURATION_FACTOR.get(seconds, 1.0))
        return max(target, 45000)

    def _calculate_percent(self, elapsed_ms, target_ms, last_percent):
        # Rohfortschritt
        raw = elapsed_ms / max(target_ms, 1)

        # Smoothing:
        # - startet nicht zu hoch
        # - steigt sichtbar
        # - bremst vor 100%
        if raw < 0.25:
            smoothed = 0.05 + raw * 0.45
        elif raw < 0.85:
            smoothed = 0.16 + (raw - 0.25) * 1.20
        else:
            smoothed = 0.88 + (raw - 0.85) * 0.50

        percent = int(min(max(smoothed * 100, 5), 97))

        # Mindestbewegung nach kurzer Zeit
        if elapsed_ms > 12000 and percent < 7:
            percent = 7

        # nie rückwärts
        if percent < last_percent:
            percent = last_percent

        return percent

    def _build_progress_embed(self, user, prompt, seconds, aspect, model, percent, elapsed_sec, eta_sec):
        blocks = 20
        filled = int(blocks * percent / 100)
        bar = "█" * filled + "░" * (blocks - filled)

        short_prompt = prompt if len(prompt) <= 240 else (prompt[:237] + "...")
        eta_text = f"~{eta_sec}s" if eta_sec is not None else "calculating..."

        return discord.Embed(
            title="🎬 Rendering",
            description=(
                f"👤 {user.mention}\n"
                f"📝 {short_prompt}\n\n"
                f"```{bar} {percent}%```\n"
                f"⏱ Elapsed: {elapsed_sec}s • ETA: {eta_text}\n"
                f"⚙️ {aspect} • {seconds}s • {model['name']} • {model['resolution']}"
            ),
            timestamp=utc_now()
        )

    # -------------------------------------------------
    # API HELPERS
    # -------------------------------------------------

    def _extract_queue_id(self, payload):
        if not isinstance(payload, dict):
            return None

        # häufige Felder
        if payload.get("queue_id"):
            return payload.get("queue_id")
        if payload.get("id"):
            return payload.get("id")

        nested = payload.get("data")
        if isinstance(nested, dict) and nested.get("queue_id"):
            return nested.get("queue_id")

        return None

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

                        # JSON robust parsen
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
                print("QUEUE REQUEST ERROR:", e)
                await asyncio.sleep(1.2 * (attempt + 1))

        return None, None

    # -------------------------------------------------
    # MAIN FLOW
    # -------------------------------------------------

    async def start_generation(self, interaction, user, prompt, seconds, aspect, model_key):
        if model_key not in VIDEO_MODELS:
            model_key = DEFAULT_MODEL

        model = VIDEO_MODELS[model_key]

        # Nur ein Job gleichzeitig global (stabil + kein UI-Chaos)
        if self.render_lock.locked():
            await interaction.followup.send(
                "⏳ Aktuell läuft bereits eine Generierung. Bitte kurz warten.",
                ephemeral=True
            )
            return

        async with self.render_lock:
            self.active_interactions[user.id] = interaction
            progress_message = None

            try:
                if seconds > model["max_seconds"]:
                    await interaction.followup.send(
                        "❌ Model limit exceeded.",
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

                # Dauer nur bei Video-Modellen
                if model["mode"] == "video":
                    payload["duration"] = f"{seconds}s"

                headers = {
                    "Authorization": f"Bearer {MORDIEM_API}",
                    "Content-Type": "application/json"
                }

                print("GEN REQUEST:", payload)

                queue_id, queue_response = await self._queue_generation(payload, headers)
                print("GEN QUEUE RESPONSE:", queue_response)

                if not queue_id:
                    await interaction.followup.send(
                        "❌ Queue creation failed. Try again.",
                        ephemeral=True
                    )
                    return

                # Nutzung erst buchen, wenn queue_id wirklich da ist
                await self.save_usage(user, seconds)
                self.add_active_job(user, queue_id)

                # Generator-Control vorübergehend entfernen
                await self.remove_button()

                channel = await self.bot.fetch_channel(VIDEO_CHANNEL_ID)

                # EIN öffentlicher Fortschrittsbalken (Start bei 5%)
                progress_message = await channel.send(
                    embed=self._build_progress_embed(
                        user=user,
                        prompt=prompt,
                        seconds=seconds,
                        aspect=aspect,
                        model=model,
                        percent=5,
                        elapsed_sec=0,
                        eta_sec=None
                    )
                )

                await interaction.followup.send(
                    "✅ Rendering started. Der öffentliche Fortschritt läuft jetzt im Channel.",
                    ephemeral=True
                )

                media_data, media_type = await self.wait_for_result(
                    queue_id=queue_id,
                    model_key=model_key,
                    seconds=seconds,
                    user=user,
                    prompt=prompt,
                    aspect=aspect,
                    model=model,
                    progress_message=progress_message
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
                # cleanup
                self.remove_active_job(user)
                self.active_interactions.pop(user.id, None)
                await self.refresh_button(force=True)

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
        progress_message
    ):
        headers = {
            "Authorization": f"Bearer {MORDIEM_API}",
            "Content-Type": "application/json"
        }

        started = utc_now()
        timeout_at = started + timedelta(minutes=12)
        last_percent = 5

        timeout = aiohttp.ClientTimeout(total=25, connect=10, sock_read=20)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            while utc_now() < timeout_at:
                await asyncio.sleep(7)

                try:
                    async with session.post(
                        VIDEO_RETRIEVE_URL,
                        headers=headers,
                        json={
                            "model": model_key,
                            "queue_id": queue_id
                        }
                    ) as response:
                        content_type = (response.headers.get("content-type", "") or "").lower()

                        # Ergebnis direkt als Media
                        if "video" in content_type:
                            data = await response.read()
                            return data, "video"

                        if "image" in content_type:
                            data = await response.read()
                            return data, "image"

                        # sonst Status-JSON
                        raw_text = await response.text()
                        try:
                            data = json.loads(raw_text)
                        except Exception:
                            print("STATUS NON-JSON:", raw_text[:300])
                            continue

                        print("GEN STATUS:", data)

                        avg = safe_int(data.get("average_execution_time", 180000), 180000)
                        elapsed = safe_int(data.get("execution_duration", 0), 0)

                        # fallback wenn API elapsed=0 liefert
                        if elapsed <= 0:
                            elapsed = int((utc_now() - started).total_seconds() * 1000)

                        target_ms = self._estimate_target_time_ms(seconds, avg)
                        percent = self._calculate_percent(elapsed, target_ms, last_percent)

                        # nur editieren wenn sich was verändert hat
                        if percent != last_percent:
                            last_percent = percent
                            eta_sec = max((target_ms - elapsed) // 1000, 0)

                            try:
                                await progress_message.edit(
                                    embed=self._build_progress_embed(
                                        user=user,
                                        prompt=prompt,
                                        seconds=seconds,
                                        aspect=aspect,
                                        model=model,
                                        percent=percent,
                                        elapsed_sec=max(elapsed // 1000, 0),
                                        eta_sec=eta_sec
                                    )
                                )
                            except Exception as edit_err:
                                print("PROGRESS EDIT ERROR:", edit_err)

                except Exception as e:
                    print("STATUS LOOP ERROR:", e)
                    continue

        # Timeout
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
        # Bei Fehler: Progress löschen + Fehlermeldung
        if not media_data:
            try:
                await progress_message.delete()
            except Exception:
                pass

            await channel.send("❌ Generation failed or timed out.")
            return

        is_video = (media_type == "video")
        filename = "AI_video.mp4" if is_video else "AI_image.png"

        file = discord.File(io.BytesIO(media_data), filename=filename)

        compact_prompt = prompt if len(prompt) <= 1700 else (prompt[:1697] + "...")

        settings_line = f"`{aspect}` • `{seconds}s` • `{model['name']}` • `{model['resolution']}`"
        title_emoji = "🎬" if is_video else "🖼️"

        embed = discord.Embed(
            title=f"{title_emoji} {user.display_name}",
            description=(
                f"📝 **Prompt**\n{compact_prompt}\n\n"
                f"⚙️ **Settings**\n{settings_line}"
            ),
            timestamp=utc_now()
        )

        icon = channel.guild.icon.url if channel.guild and channel.guild.icon else None
        embed.set_footer(
            text=f"{model['resolution']} • AI Generator",
            icon_url=icon
        )

        # Bei Bildern direkt im Embed anzeigen
        if not is_video:
            embed.set_image(url=f"attachment://{filename}")

        await channel.send(embed=embed, file=file)

        # Wichtig: Fortschrittsbalken NACH Ergebnispost löschen
        try:
            await progress_message.delete()
        except Exception:
            pass

        # Private Rückmeldung an User
        remaining, reset = await self.get_usage_info(user)
        tier_name, tier_limit = self.get_user_tier(user)

        interaction = self.active_interactions.get(user.id)
        if interaction:
            try:
                await interaction.followup.send(
                    f"✅ Completed!\n\n"
                    f"🏆 Tier: **{tier_name}**\n"
                    f"⏳ Daily limit: **{tier_limit}s**\n\n"
                    f"🎬 Remaining: **{remaining}s**\n"
                    f"🔄 Reset: **{format_reset(reset)}**",
                    ephemeral=True
                )
            except Exception as e:
                print("PRIVATE FOLLOWUP ERROR:", e)

    # -------------------------------------------------
    # READY EVENT
    # -------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self):
        print("VIDEO COG READY")
        await self.refresh_button(force=False)


# =====================================================
# SETUP
# =====================================================

async def setup(bot):
    await bot.add_cog(VideoCog(bot))