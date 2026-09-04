# video_cog.py
import asyncio
import contextlib
import io
import json
import logging
import os
import re
import uuid
from datetime import timedelta
from typing import Any, Optional

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

from venice_shared import (
    DEFAULT_VIDEO_MODEL,
    MAX_VIDEO_RENDER_SECONDS,
    SERVER_ANIM_ICON,
    VENICE_VIDEO_I2V_MODEL_LTX25,
    VENICE_VIDEO_I2V_MODEL_MINIMAX,
    VENICE_VIDEO_I2V_MODEL_WAN3,
    VIDEO_MODEL_PROFILES,
    add_rating_reactions,
    build_generation_success_text,
    build_progress_embed,
    bytes_to_data_url,
    check_source_image_for_model,
    codeblock_safe,
    extract_urls_from_payload,
    format_reset_line,
    get_member_tier,
    get_model_durations,
    get_model_prompt_limit,
    get_model_resolution,
    get_quota_store,
    get_video_budget_for_member,
    get_video_profile,
    is_known_video_model,
    looks_like_image,
    looks_like_video,
    next_tier,
    repost_starter_for_channel,
    resolve_video_aspect_ratio,
    safe_int,
    sanitize_error_text,
    send_ephemeral,
    trim,
    utc_now,
    video_tier_line,
)

load_dotenv()
logger = logging.getLogger("venice_video_cog")

# =================================================
# ENV
# =================================================
VENICE_API_KEY = os.getenv("VENICE_API_KEY")
VENICE_VIDEO_QUEUE_URL = os.getenv("VENICE_VIDEO_QUEUE_URL")
VENICE_VIDEO_RETRIEVE_URL = os.getenv("VENICE_VIDEO_RETRIEVE_URL")

# Global fallback resolution for models not listed in VIDEO_MODEL_PROFILES.
VENICE_VIDEO_RESOLUTION_FALLBACK = os.getenv("VENICE_VIDEO_RESOLUTION", "720p")

# Legacy fallback when animate_image_to_video is called without model_id.
VENICE_VIDEO_I2V_MODEL_DEFAULT = os.getenv(
    "VENICE_VIDEO_I2V_MODEL", DEFAULT_VIDEO_MODEL
)

# =================================================
# SETTINGS
# =================================================
VIDEO_POLL_SECONDS = 6

# Absolute ceiling for a single poll loop.
VIDEO_HARD_TIMEOUT_SECONDS = 3000

# Baseline adaptive budget. Scaled per requested clip length at runtime,
# see _adaptive_budget_for(). A 25s WAN render needs far more wall time
# than a 5s clip, so a flat value would abort long jobs prematurely.
VIDEO_ADAPTIVE_TIMEOUT_SECONDS = 900
VIDEO_SECONDS_PER_OUTPUT_SECOND = 45
VIDEO_ADAPTIVE_BASE_OVERHEAD = 180

VIDEO_MAX_CONSECUTIVE_5XX = 8
VIDEO_5XX_WINDOW_SECONDS = 180

# Display renames for known model IDs.
VIDEO_MODEL_RENAMES = {
    VENICE_VIDEO_I2V_MODEL_WAN3: "WAN 3.0 🔞",
    VENICE_VIDEO_I2V_MODEL_LTX25: "LTX 2.5 Pro",
    VENICE_VIDEO_I2V_MODEL_MINIMAX: "MiniMax H3 Max",
}

VIDEO_QUOTA_FILE = os.getenv("VIDEO_QUOTA_FILE", "goonhut_video_quota.json")

PROGRESS_EMBED_TITLE = "🎬 VIDEO RENDER"


# =================================================
# HELPERS
# =================================================
def _parse_retry_after_seconds(headers: Any, text: str) -> int:
    retry_after = 0
    try:
        raw = headers.get("Retry-After")
        if raw is not None:
            retry_after = int(str(raw).strip())
    except Exception:
        retry_after = 0

    if retry_after <= 0:
        m = re.search(r"retry(?:\s+after)?\s*[:=]?\s*(\d+)", text or "", flags=re.IGNORECASE)
        if m:
            retry_after = int(m.group(1))

    return max(2, min(retry_after if retry_after > 0 else 20, 90))


def _extract_queue_id(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    for key in ("queue_id", "id"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            return v
    nested = payload.get("data")
    if isinstance(nested, dict):
        for key in ("queue_id", "id"):
            v = nested.get(key)
            if isinstance(v, str) and v:
                return v
    return None


def _video_model_label(model_name: str) -> str:
    key = (model_name or "").strip()
    if key in VIDEO_MODEL_RENAMES:
        return VIDEO_MODEL_RENAMES[key]
    profile = VIDEO_MODEL_PROFILES.get(key)
    if profile:
        return str(profile.get("button_label") or key)
    return key


def _resolution_for_model(model_id: str) -> str:
    """Per-model resolution from the shared profile table, with env fallback."""
    return get_model_resolution(model_id) or VENICE_VIDEO_RESOLUTION_FALLBACK


def _adaptive_budget_for(seconds: int) -> int:
    """Wall-clock budget for a render of `seconds` output length."""
    scaled = int(max(1, seconds) * VIDEO_SECONDS_PER_OUTPUT_SECOND)
    return min(
        VIDEO_HARD_TIMEOUT_SECONDS,
        max(VIDEO_ADAPTIVE_TIMEOUT_SECONDS, scaled + VIDEO_ADAPTIVE_BASE_OVERHEAD),
    )


# =================================================
# COG
# =================================================
class VeniceVideoCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self.session_lock = asyncio.Lock()

        self.global_busy = False
        self.global_busy_lock = asyncio.Lock()

        self._active_users: set[int] = set()
        self._active_users_lock = asyncio.Lock()

        self.video_quota = get_quota_store(VIDEO_QUOTA_FILE)

    # ---------- lifecycle ----------
    async def _ensure_session(self):
        async with self.session_lock:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=120),
                    connector=aiohttp.TCPConnector(limit=40, ttl_dns_cache=300),
                )

    async def cog_load(self):
        await self._ensure_session()

    def cog_unload(self):
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    # ---------- locks ----------
    async def _try_begin_global(self) -> bool:
        async with self.global_busy_lock:
            if self.global_busy:
                return False
            self.global_busy = True
            return True

    async def _end_global(self):
        async with self.global_busy_lock:
            self.global_busy = False

    async def _try_lock_user(self, user_id: int) -> bool:
        async with self._active_users_lock:
            if user_id in self._active_users:
                return False
            self._active_users.add(user_id)
            return True

    async def _unlock_user(self, user_id: int):
        async with self._active_users_lock:
            self._active_users.discard(user_id)

    # ---------- quota ----------
    async def get_remaining_info(self, guild_id: int, member: discord.Member) -> dict[str, int]:
        tier = get_member_tier(member)
        budget = get_video_budget_for_member(member)
        state = await self.video_quota.peek(guild_id, member.id, budget)
        return {
            "tier": tier,
            "used": int(state["used"]),
            "limit": int(state["limit"]),
            "remaining": int(state["remaining"]),
            "reset_in": int(state["reset_in"]),
            "reset_at": int(state.get("reset_at", 0) or 0),
        }

    # ---------- embeds ----------
    def _progress_embed(
        self,
        user: discord.abc.User,
        prompt: str,
        percent: int,
        elapsed_sec: int,
        stage_text: str,
        quota: dict[str, int],
        model_id: str,
    ) -> discord.Embed:
        return build_progress_embed(
            title=PROGRESS_EMBED_TITLE,
            color=discord.Color.purple(),
            user=user,
            prompt=prompt,
            percent=percent,
            status_lines=[stage_text, f"Elapsed: `{elapsed_sec}s`"],
            quota_name="Quota (24h)",
            quota_state=quota,
            quota_unit="s",
            footer=(
                f"🎞️ {_video_model_label(model_id)} "
                f"• 📺 {_resolution_for_model(model_id)}"
            ),
        )

    def _result_embed(
        self, prompt: str, seconds: int, model_id: str, guild_icon_url: Optional[str]
    ) -> discord.Embed:
        embed = discord.Embed(color=discord.Color.dark_magenta(), timestamp=utc_now())
        embed.add_field(
            name="Prompt",
            value=f"```{codeblock_safe(trim(prompt, 1500))}```",
            inline=False,
        )
        embed.set_footer(
            text=(
                f"🎞️ {_video_model_label(model_id)} "
                f"• 📺 {_resolution_for_model(model_id)} • ⏱️ {seconds}s"
            ),
            icon_url=guild_icon_url,
        )
        return embed

    # ---------- cleanup ----------
    def _is_progress_leak_post(self, msg: discord.Message) -> bool:
        if not self.bot.user or msg.author.id != self.bot.user.id:
            return False
        if not msg.embeds:
            return False
        return (msg.embeds[0].title or "").strip() == PROGRESS_EMBED_TITLE

    async def _cleanup_progress_leaks(
        self,
        channel: discord.abc.Messageable,
        keep_ids: Optional[set[int]] = None,
        limit: int = 20,
    ):
        keep_ids = keep_ids or set()
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        async for msg in channel.history(limit=limit):
            if msg.id in keep_ids:
                continue
            if self._is_progress_leak_post(msg):
                with contextlib.suppress(Exception):
                    await msg.delete()

    async def _safe_edit_progress(
        self, message: Optional[discord.Message], embed: discord.Embed
    ):
        if message:
            with contextlib.suppress(Exception):
                await message.edit(embed=embed)

    async def _safe_delete_message(self, message: Optional[discord.Message]):
        if message:
            with contextlib.suppress(Exception):
                await message.delete()

    # ---------- media fetch ----------
    async def _fetch_media_from_url(
        self, url: str, headers: dict[str, str], visited: Optional[set[str]] = None
    ):
        if not isinstance(url, str) or not url.startswith("http"):
            return None, None
        visited = visited or set()
        if url in visited or len(visited) > 12:
            return None, None
        visited.add(url)

        await self._ensure_session()
        assert self.session is not None

        timeout = aiohttp.ClientTimeout(total=90, connect=12, sock_read=75)
        for use_auth in (True, False):
            try:
                req_headers = dict(headers) if use_auth else {}
                async with self.session.get(url, headers=req_headers, timeout=timeout) as resp:
                    body = await resp.read()
                    ctype = (resp.headers.get("content-type") or "").lower()
                    if resp.status >= 400 or not body:
                        continue

                    if "video" in ctype or looks_like_video(body):
                        return body, "video"
                    if "image" in ctype or looks_like_image(body):
                        return body, "image"

                    if "json" in ctype:
                        try:
                            nested = json.loads(body.decode("utf-8", errors="ignore"))
                        except Exception:
                            nested = None
                        if nested:
                            for nested_url in extract_urls_from_payload(nested):
                                data, kind = await self._fetch_media_from_url(
                                    nested_url, headers, visited
                                )
                                if data:
                                    return data, kind
            except Exception:
                continue

        return None, None

    # ---------- provider: queue ----------
    async def _queue_i2v(
        self,
        model_id: str,
        image_url: str,
        image_bytes: Optional[bytes],
        prompt: str,
        seconds: int,
        aspect: str,
    ) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[str], str]:
        if not VENICE_VIDEO_QUEUE_URL:
            return None, None, "VENICE_VIDEO_QUEUE_URL is missing.", "noid"
        if not VENICE_API_KEY:
            return None, None, "VENICE_API_KEY is missing.", "noid"

        # Canonical field per Venice docs is 'image_url', which accepts both
        # http URLs and data URLs. Strict validators reject the legacy
        # 'image' key, so we drop it entirely.
        image_variants: list[dict[str, Any]] = []
        if image_url and image_url.startswith("http"):
            image_variants.append({"image_url": image_url})
        if image_bytes and looks_like_image(image_bytes):
            image_variants.append({"image_url": bytes_to_data_url(image_bytes)})

        if not image_variants:
            return None, None, "No usable source image (neither URL nor bytes).", "noid"

        await self._ensure_session()
        assert self.session is not None

        headers = {
            "Authorization": f"Bearer {VENICE_API_KEY}",
            "Content-Type": "application/json",
        }
        request_id = uuid.uuid4().hex[:8]
        resolution = _resolution_for_model(model_id)
        prompt_limit = get_model_prompt_limit(model_id)

        base_payload: dict[str, Any] = {
            "model": model_id,
            "prompt": trim(prompt, prompt_limit),
            "resolution": resolution,
            "duration": f"{seconds}s",
        }

        # WAN 3.0 -> "adaptive", LTX 2.5 Pro -> "auto",
        # MiniMax H3 Max -> field omitted entirely (aspect_ratios: []).
        aspect_for_payload = resolve_video_aspect_ratio(model_id, aspect)
        if aspect_for_payload:
            base_payload["aspect_ratio"] = aspect_for_payload

        timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=50)
        last_error = "Queue request failed."

        for attempt in range(2):
            for variant_idx, variant in enumerate(image_variants):
                payload = {**base_payload, **variant}
                try:
                    async with self.session.post(
                        VENICE_VIDEO_QUEUE_URL, headers=headers, json=payload, timeout=timeout
                    ) as resp:
                        text = await resp.text()
                        logger.info(
                            "[VID %s] queue status=%s attempt=%s variant=%s(%s) "
                            "model=%s res=%s dur=%ss ar=%s",
                            request_id, resp.status, attempt + 1,
                            variant_idx, next(iter(variant)),
                            model_id, resolution, seconds,
                            base_payload.get("aspect_ratio", "-"),
                        )

                        if resp.status in (400, 415, 422):
                            last_error = (
                                f"Queue error ({resp.status}): {sanitize_error_text(text)}"
                            )
                            if variant_idx < len(image_variants) - 1:
                                continue
                            return None, {"raw": text}, last_error, request_id

                        if resp.status in (401, 403, 404):
                            return (
                                None, {"raw": text},
                                f"Queue error ({resp.status}): {sanitize_error_text(text)}",
                                request_id,
                            )

                        if resp.status == 429:
                            if "too many failed attempts" in (text or "").lower():
                                return (
                                    None, {"raw": text},
                                    f"Provider rate limit: {sanitize_error_text(text)}",
                                    request_id,
                                )
                            await asyncio.sleep(_parse_retry_after_seconds(resp.headers, text))
                            continue

                        if resp.status >= 500:
                            last_error = f"Provider error ({resp.status})"
                            await asyncio.sleep(2 + attempt * 2)
                            continue

                        try:
                            data = json.loads(text) if text else {}
                        except Exception:
                            data = {"raw": text}

                        queue_id = _extract_queue_id(data)
                        if queue_id:
                            return queue_id, data, None, request_id

                        last_error = "Queue response did not include queue_id."
                except asyncio.TimeoutError:
                    last_error = "Queue request timed out."
                except Exception as e:
                    last_error = f"Queue request error: {e}"

            await asyncio.sleep(1.2 + attempt)

        return None, None, last_error, request_id

    # ---------- provider: poll ----------
    async def _wait_for_result(
        self,
        model_id: str,
        queue_id: str,
        progress_message: Optional[discord.Message],
        user: discord.abc.User,
        prompt: str,
        quota: dict[str, int],
        queue_download_url: Optional[str] = None,
        request_id: str = "unknown",
        requested_seconds: int = 5,
    ) -> tuple[Optional[bytes], Optional[str], Optional[str]]:
        if not VENICE_VIDEO_RETRIEVE_URL:
            return None, None, "VENICE_VIDEO_RETRIEVE_URL is missing."
        if not VENICE_API_KEY:
            return None, None, "VENICE_API_KEY is missing."

        await self._ensure_session()
        assert self.session is not None

        headers = {
            "Authorization": f"Bearer {VENICE_API_KEY}",
            "Content-Type": "application/json",
        }
        started = utc_now()
        hard_deadline = started + timedelta(seconds=VIDEO_HARD_TIMEOUT_SECONDS)
        adaptive_deadline = started + timedelta(
            seconds=_adaptive_budget_for(requested_seconds)
        )

        consecutive_5xx = 0
        total_5xx = 0
        first_5xx_at = None
        finalize_attempts = 0
        last_percent = 8

        timeout = aiohttp.ClientTimeout(total=90, connect=15, sock_read=70)

        while True:
            if utc_now() >= hard_deadline or utc_now() >= adaptive_deadline:
                break

            await asyncio.sleep(VIDEO_POLL_SECONDS)
            elapsed_sec = int((utc_now() - started).total_seconds())

            try:
                async with self.session.post(
                    VENICE_VIDEO_RETRIEVE_URL,
                    headers=headers,
                    json={"model": model_id, "queue_id": queue_id},
                    timeout=timeout,
                ) as response:
                    ctype = (response.headers.get("content-type") or "").lower()

                    if response.status == 429:
                        t429 = await response.text()
                        await asyncio.sleep(_parse_retry_after_seconds(response.headers, t429))
                        continue

                    if response.status >= 400:
                        _ = await response.text()

                        if response.status >= 500:
                            total_5xx += 1
                            consecutive_5xx += 1
                            if first_5xx_at is None:
                                first_5xx_at = utc_now()

                            p = max(last_percent, 12)
                            await self._safe_edit_progress(
                                progress_message,
                                self._progress_embed(
                                    user, prompt, p, elapsed_sec,
                                    f"Provider error {response.status} (retry {total_5xx})...",
                                    quota, model_id,
                                ),
                            )
                            last_percent = p

                            too_many = consecutive_5xx >= VIDEO_MAX_CONSECUTIVE_5XX
                            too_long = first_5xx_at and (
                                (utc_now() - first_5xx_at).total_seconds()
                                >= VIDEO_5XX_WINDOW_SECONDS
                            )
                            if too_many or too_long:
                                return None, None, "Provider unavailable (repeated 5xx errors)."
                            continue

                        consecutive_5xx = 0
                        first_5xx_at = None

                        if response.status in (401, 403):
                            return None, None, "API authentication failed (401/403)."
                        if response.status == 404:
                            return None, None, "Retrieve endpoint not found (404)."
                        if response.status == 422:
                            return None, None, "Retrieve request rejected by provider (422)."
                        continue

                    consecutive_5xx = 0
                    first_5xx_at = None

                    if "video" in ctype:
                        blob = await response.read()
                        if looks_like_video(blob):
                            return blob, "video", None

                    if "image" in ctype:
                        blob = await response.read()
                        if looks_like_image(blob):
                            return blob, "image", None

                    raw = await response.text()
                    try:
                        data = json.loads(raw) if raw else {}
                    except Exception:
                        continue

                    status = str(data.get("status", "")).lower()

                    avg_ms = safe_int(data.get("average_execution_time", 180000), 180000)
                    exec_ms = safe_int(data.get("execution_duration", 0), 0)
                    if exec_ms <= 0:
                        exec_ms = elapsed_sec * 1000

                    expected_total_sec = int((max(avg_ms, 60000) / 1000) * 2.5) + 120
                    candidate = started + timedelta(seconds=expected_total_sec)
                    if candidate > adaptive_deadline:
                        adaptive_deadline = min(candidate, hard_deadline)

                    if status in {"failed", "error", "cancelled", "canceled"}:
                        err = data.get("error")
                        msg = (
                            err.get("message") if isinstance(err, dict)
                            else err if isinstance(err, str)
                            else data.get("message")
                        )
                        return None, None, (
                            f"Rendering aborted: {sanitize_error_text(str(msg or 'unknown'))}"
                        )

                    if status == "completed":
                        candidate_urls: list[str] = []
                        if isinstance(queue_download_url, str) and queue_download_url.startswith("http"):
                            candidate_urls.append(queue_download_url)
                        candidate_urls.extend(extract_urls_from_payload(data))
                        candidate_urls = list(dict.fromkeys(candidate_urls))

                        for media_url in candidate_urls:
                            media_data, media_type = await self._fetch_media_from_url(
                                media_url, headers
                            )
                            if media_data:
                                return media_data, media_type, None

                        finalize_attempts += 1
                        p = max(last_percent, 98)
                        await self._safe_edit_progress(
                            progress_message,
                            self._progress_embed(
                                user, prompt, p, elapsed_sec,
                                "Finalizing file delivery...", quota, model_id,
                            ),
                        )
                        last_percent = p

                        if finalize_attempts >= 25:
                            return None, None, (
                                "Rendering finished, but no deliverable file was returned."
                            )
                        continue

                    target_ms = max(avg_ms, 120000)
                    percent = min(97, max(8, int((exec_ms / max(target_ms, 1)) * 100)))
                    percent = max(percent, last_percent)

                    if percent != last_percent:
                        await self._safe_edit_progress(
                            progress_message,
                            self._progress_embed(
                                user, prompt, percent, elapsed_sec, "Rendering...", quota, model_id,
                            ),
                        )
                        last_percent = percent

            except asyncio.TimeoutError:
                continue
            except Exception:
                continue

        return None, None, "Generation timed out."

    # ---------- public api (called by image / face cogs + shared animate UI) ----------
    async def animate_image_to_video(
        self,
        interaction: discord.Interaction,
        image_url: str,
        image_bytes: Optional[bytes],
        prompt: str,
        aspect: str,
        seconds: int,
        target_channel: discord.abc.Messageable,
        model_id: Optional[str] = None,
    ) -> bool:
        if not VENICE_API_KEY:
            await send_ephemeral(interaction, "❌ VENICE_API_KEY is missing.")
            return False
        if not VENICE_VIDEO_QUEUE_URL or not VENICE_VIDEO_RETRIEVE_URL:
            await send_ephemeral(interaction, "❌ Video API endpoints are missing in .env.")
            return False
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await send_ephemeral(interaction, "❌ This action is server-only.")
            return False
        if seconds <= 0:
            await send_ephemeral(interaction, "❌ Invalid duration.")
            return False
        if seconds > MAX_VIDEO_RENDER_SECONDS:
            await send_ephemeral(
                interaction,
                f"❌ Max duration per render is {MAX_VIDEO_RENDER_SECONDS} seconds.",
            )
            return False

        # Resolve effective model. Unknown IDs are rejected outright so that
        # stale buttons from retired models cannot queue dead requests.
        effective_model_id = (model_id or VENICE_VIDEO_I2V_MODEL_DEFAULT).strip()
        if not effective_model_id:
            await send_ephemeral(interaction, "❌ No video model configured.")
            return False

        if not is_known_video_model(effective_model_id):
            available = ", ".join(
                _video_model_label(m) for m in VIDEO_MODEL_PROFILES
            )
            await send_ephemeral(
                interaction,
                f"❌ Unknown video model `{effective_model_id}`.\nAvailable: {available}",
            )
            return False

        model_durations = get_model_durations(effective_model_id)
        if seconds not in model_durations:
            allowed = ", ".join(f"{s}s" for s in model_durations)
            await send_ephemeral(
                interaction,
                f"❌ Allowed durations for this model are {allowed}.",
            )
            return False

        has_url = bool(image_url and image_url.startswith("http"))
        has_bytes = bool(image_bytes and looks_like_image(image_bytes))
        if not has_url and not has_bytes:
            await send_ephemeral(
                interaction, "❌ No valid source image for video generation."
            )
            return False

        # Per-model source constraints (WAN 3.0 requires a 240px short edge).
        size_error = check_source_image_for_model(effective_model_id, image_bytes)
        if size_error:
            await send_ephemeral(interaction, f"❌ {size_error}")
            return False

        if not await self._try_lock_user(interaction.user.id):
            await send_ephemeral(
                interaction, "⏳ You already have a video render running. Please wait."
            )
            return False

        tier = get_member_tier(interaction.user)
        budget = get_video_budget_for_member(interaction.user)

        if budget <= 0:
            await send_ephemeral(
                interaction, "🎬 Video rendering is locked for members without a Tier role."
            )
            await self._unlock_user(interaction.user.id)
            return False

        ok_q, state_q, token = await self.video_quota.reserve(
            interaction.guild.id, interaction.user.id, budget, seconds
        )
        if not ok_q:
            msg = (
                f"⛔ Not enough video seconds left in your 24h window.\n"
                f"Used: **{state_q['used']}/{state_q['limit']}s** "
                f"• Remaining: **{state_q['remaining']}s**\n"
                f"⏳ {format_reset_line(state_q)}\n"
                f"Current tier: **T{tier}**."
            )
            nxt = next_tier(tier)
            if nxt:
                nt, cfg = nxt
                msg += (
                    f"\n🚀 Next unlock: **Tier {nt}** "
                    f"(<@&{cfg['role_id']}>, Level {cfg['level']}) "
                    f"→ **{cfg['video_budget_sec']}s/day**."
                )
            msg += f"\nTier budgets: `{video_tier_line()}`"
            await send_ephemeral(interaction, msg)
            await self._unlock_user(interaction.user.id)
            return False

        if not await self._try_begin_global():
            await self.video_quota.rollback(token)
            await send_ephemeral(
                interaction, "⏳ Another video render is currently running. Please wait."
            )
            await self._unlock_user(interaction.user.id)
            return False

        progress_message: Optional[discord.Message] = None
        quota_success = False
        keep_ids: set[int] = set()

        if isinstance(target_channel, (discord.TextChannel, discord.Thread)):
            await self._cleanup_progress_leaks(target_channel, keep_ids=set(), limit=20)

        try:
            progress_message = await target_channel.send(
                embed=self._progress_embed(
                    interaction.user, prompt, 5, 0, "Sending queue request...",
                    state_q, effective_model_id,
                )
            )
            keep_ids.add(progress_message.id)

            queue_id, queue_response, queue_error, request_id = await self._queue_i2v(
                model_id=effective_model_id,
                image_url=image_url,
                image_bytes=image_bytes,
                prompt=prompt,
                seconds=seconds,
                aspect=aspect,
            )
            if not queue_id:
                await send_ephemeral(
                    interaction,
                    f"❌ Animation failed: "
                    f"{sanitize_error_text(queue_error or 'Queue failed.')}",
                )
                return False

            queue_download_url = None
            if isinstance(queue_response, dict):
                qdu = queue_response.get("download_url")
                if isinstance(qdu, str):
                    queue_download_url = qdu

            await self._safe_edit_progress(
                progress_message,
                self._progress_embed(
                    interaction.user, prompt, 8, 1,
                    "Queue accepted. Rendering started.", state_q, effective_model_id,
                ),
            )

            media_data, media_type, error_message = await self._wait_for_result(
                model_id=effective_model_id,
                queue_id=queue_id,
                progress_message=progress_message,
                user=interaction.user,
                prompt=prompt,
                quota=state_q,
                queue_download_url=queue_download_url,
                request_id=request_id,
                requested_seconds=seconds,
            )

            if not media_data:
                await send_ephemeral(
                    interaction,
                    f"❌ Animation failed: "
                    f"{sanitize_error_text(error_message or 'Unknown error')}",
                )
                return False
            if media_type != "video":
                await send_ephemeral(interaction, "❌ Provider returned non-video output.")
                return False

            guild_limit = None
            guild_icon_url = None
            guild = getattr(target_channel, "guild", None)
            if guild:
                guild_limit = getattr(guild, "filesize_limit", None)
                if guild.icon:
                    guild_icon_url = guild.icon.url

            if guild_limit and len(media_data) > guild_limit:
                await send_ephemeral(
                    interaction,
                    f"❌ Video too large for Discord upload limit "
                    f"({len(media_data) // (1024 * 1024)}MB > "
                    f"{guild_limit // (1024 * 1024)}MB).\n"
                    f"Try a shorter duration.",
                )
                return False

            video_post = await target_channel.send(
                content=(
                    f"{SERVER_ANIM_ICON} 🎬 **Video** • {interaction.user.mention} "
                    f"• ▶ **CLICK TO PLAY**"
                ),
                embed=self._result_embed(prompt, seconds, effective_model_id, guild_icon_url),
                file=discord.File(io.BytesIO(media_data), filename="AI_video.mp4"),
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False
                ),
            )
            keep_ids.add(video_post.id)

            await add_rating_reactions(video_post)

            quota_success = True
            info = await self.get_remaining_info(interaction.guild.id, interaction.user)
            await send_ephemeral(
                interaction,
                build_generation_success_text(
                    info,
                    kind="video",
                    unit="s",
                    quota_label="Remaining today",
                ),
            )
            return True

        except discord.Forbidden:
            await send_ephemeral(
                interaction, "❌ Missing Discord permissions to post video."
            )
            return False
        except Exception as e:
            logger.exception("animate_image_to_video failed: %s", e)
            await send_ephemeral(
                interaction, f"❌ Animation failed: {sanitize_error_text(str(e))}"
            )
            return False
        finally:
            if not quota_success:
                await self.video_quota.rollback(token)

            await self._safe_delete_message(progress_message)
            await self._end_global()
            await self._unlock_user(interaction.user.id)

            if isinstance(target_channel, (discord.TextChannel, discord.Thread)):
                await self._cleanup_progress_leaks(
                    target_channel, keep_ids=keep_ids, limit=25
                )
                with contextlib.suppress(Exception):
                    await repost_starter_for_channel(target_channel)

            # NOTE: cleanup_user_ephemerals wipes tracked ephemerals only.
            # AnimateEphemeralView is declared persistent_ephemeral=True in
            # venice_shared, so its animate buttons stay clickable and the
            # user can queue further animations of the same source image.
            asyncio.create_task(self._cleanup_user_ephemerals_delayed(interaction))

    async def _cleanup_user_ephemerals_delayed(
        self, interaction: discord.Interaction, delay: float = 8.0
    ):
        from venice_shared import cleanup_user_ephemerals
        await cleanup_user_ephemerals(interaction, delay=delay)

    # ---------- admin ----------
    @commands.command(name="video_quota_prune")
    @commands.has_permissions(administrator=True)
    async def video_quota_prune(self, ctx: commands.Context):
        pruned = await self.video_quota.prune()
        await ctx.send(f"✅ Pruned {pruned} expired video quota entr(ies).")

    @commands.command(name="video_profiles")
    @commands.has_permissions(administrator=True)
    async def video_profiles(self, ctx: commands.Context):
        """Show the current animate button configuration."""
        lines = ["🎞️ **Animate button profiles**"]
        for model_id, profile in VIDEO_MODEL_PROFILES.items():
            durations = ", ".join(f"{d}s" for d in profile["durations"])
            if profile.get("require_aspect_ratio"):
                auto = profile.get("aspect_ratio_auto") or "-"
                allowed = profile.get("allowed_aspect_ratios") or []
                aspect_info = f" • AR: {auto} ({'/'.join(allowed)})"
            else:
                aspect_info = " • AR: none"
            min_side = profile.get("min_short_side") or 0
            min_info = f" • min short side: {min_side}px" if min_side else ""
            lines.append(
                f"• `{model_id}` -> {profile['button_label']}\n"
                f"  {profile['resolution']} • {durations}{aspect_info}{min_info}"
            )
        lines.append(f"\nMax per render: **{MAX_VIDEO_RENDER_SECONDS}s**")
        await ctx.send("\n".join(lines))

    @commands.command(name="video_test_model")
    @commands.has_permissions(administrator=True)
    async def video_test_model(self, ctx: commands.Context):
        """Ping the models endpoint and list live video model IDs."""
        base = (VENICE_VIDEO_QUEUE_URL or "").split("/api/")[0]
        if not base or not VENICE_API_KEY:
            await ctx.send("❌ Queue URL or API key missing.")
            return

        await self._ensure_session()
        assert self.session is not None
        url = f"{base}/api/v1/models?type=video"
        headers = {"Authorization": f"Bearer {VENICE_API_KEY}"}

        try:
            async with self.session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    await ctx.send(f"❌ Models endpoint returned {resp.status}.")
                    return
                data = await resp.json()
        except Exception as e:
            await ctx.send(f"❌ Request failed: {sanitize_error_text(str(e))}")
            return

        live_ids = {
            m.get("id") for m in data.get("data", []) if isinstance(m, dict)
        }
        lines = ["🔍 **Configured vs. live**"]
        for model_id in VIDEO_MODEL_PROFILES:
            mark = "✅" if model_id in live_ids else "❌ NOT FOUND"
            lines.append(f"{mark} `{model_id}`")
        await ctx.send("\n".join(lines)[:1900])


async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceVideoCog(bot))