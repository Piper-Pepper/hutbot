# video_cog.py
import asyncio
import contextlib
import json
import logging
import os
import re
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from venice_shared import (
    DEFAULT_VIDEO_MODEL,
    MAX_VIDEO_RENDER_SECONDS,
    SERVER_ANIM_ICON,
    TEXT_VIDEO_CHANNEL_ID,
    TEXT_VIDEO_MODEL_PROFILES,
    VIDEO_MODEL_PROFILES,
    T2VStarterView,
    add_rating_reactions,
    build_generation_success_text,
    build_progress_embed,
    build_t2v_starter_text,
    build_video_quota_text,
    bytes_to_data_url,
    channel_upload_limit_bytes,
    check_source_image_for_model,
    cleanup_temp_files,
    codeblock_safe,
    compress_video_file,
    estimate_render_seconds,
    extract_urls_from_payload,
    file_looks_like_image,
    file_looks_like_video,
    file_size,
    get_member_tier,
    get_model_durations,
    get_model_label,
    get_model_min_short_side,
    get_model_prompt_limit,
    get_model_resolution,
    get_quota_store,
    get_t2v_profile,
    get_video_budget_for_member,
    has_video_access,
    human_bytes,
    is_known_t2v_model,
    is_known_video_model,
    is_t2v_starter_message,
    log_memory_usage,
    looks_like_image,
    prepare_source_image_for_upload,
    purge_stale_temp_files,
    refresh_starter_message,
    register_starter_reposter,
    repost_starter_for_channel,
    resolve_t2v_aspect_ratio,
    resolve_video_aspect_ratio,
    safe_int,
    sanitize_error_text,
    send_ephemeral,
    send_video_role_locked,
    temp_path,
    trim,
    utc_now,
)

load_dotenv()
logger = logging.getLogger("venice_video_cog")

# ============ ENV ============
VENICE_API_KEY = os.getenv("VENICE_API_KEY")
VENICE_VIDEO_QUEUE_URL = os.getenv("VENICE_VIDEO_QUEUE_URL")
VENICE_VIDEO_RETRIEVE_URL = os.getenv("VENICE_VIDEO_RETRIEVE_URL")

VENICE_VIDEO_RESOLUTION_FALLBACK = os.getenv("VENICE_VIDEO_RESOLUTION", "720p")
VENICE_VIDEO_I2V_MODEL_DEFAULT = os.getenv("VENICE_VIDEO_I2V_MODEL", DEFAULT_VIDEO_MODEL)

VIDEO_QUOTA_FILE = os.getenv("VIDEO_QUOTA_FILE", "goonhut_video_quota.json")

# ============ SETTINGS ============
# Poll interval is derived per render. A flat 6s gives a fast model only a
# handful of bar updates before it is already finished.
VIDEO_POLL_MIN_SECONDS = 2.0
VIDEO_POLL_MAX_SECONDS = 6.0
VIDEO_POLL_TARGET_UPDATES = 25

VIDEO_HARD_TIMEOUT_SECONDS = 3000
VIDEO_ADAPTIVE_TIMEOUT_SECONDS = 900
VIDEO_ADAPTIVE_BASE_OVERHEAD = 180
VIDEO_MAX_CONSECUTIVE_5XX = 8
VIDEO_5XX_WINDOW_SECONDS = 180

# Streamed download chunk size (never buffer a whole clip in RAM).
DOWNLOAD_CHUNK = 256 * 1024
DOWNLOAD_MAX_BYTES = 600 * 1024 * 1024

PROGRESS_EMBED_TITLE = "🎬 VIDEO RENDER"


# ============ HELPERS ============
def _parse_retry_after_seconds(headers: Any, text: str) -> int:
    retry_after = 0
    try:
        raw = headers.get("Retry-After")
        if raw is not None:
            retry_after = int(str(raw).strip())
    except Exception:
        retry_after = 0
    if retry_after <= 0:
        m = re.search(r"retry(?:\s+after)?\s*[:=]?\s*(\d+)",
                      text or "", flags=re.IGNORECASE)
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


def _resolution_for_model(model_id: str) -> str:
    return get_model_resolution(model_id) or VENICE_VIDEO_RESOLUTION_FALLBACK


def _adaptive_budget_for(model_id: str, seconds: int) -> int:
    """Wall-clock budget for the poll loop, based on the model's own speed."""
    scaled = estimate_render_seconds(model_id, seconds)
    return min(
        VIDEO_HARD_TIMEOUT_SECONDS,
        max(VIDEO_ADAPTIVE_TIMEOUT_SECONDS, scaled + VIDEO_ADAPTIVE_BASE_OVERHEAD),
    )


def _poll_interval_for(model_id: str, seconds: int) -> float:
    """Aim for ~25 bar updates across the expected runtime."""
    expected = estimate_render_seconds(model_id, seconds)
    return max(VIDEO_POLL_MIN_SECONDS,
               min(VIDEO_POLL_MAX_SECONDS, expected / VIDEO_POLL_TARGET_UPDATES))


# ============ COG ============
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
        self._starter_ready = False

    # ---------- lifecycle ----------
    async def _ensure_session(self):
        async with self.session_lock:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=None, connect=15),
                    connector=aiohttp.TCPConnector(limit=40, ttl_dns_cache=300),
                )

    async def cog_load(self):
        await self._ensure_session()
        if TEXT_VIDEO_CHANNEL_ID > 0:
            self.bot.add_view(T2VStarterView(TEXT_VIDEO_CHANNEL_ID))
            register_starter_reposter(TEXT_VIDEO_CHANNEL_ID, self._repost_t2v_starter)
        else:
            logger.warning("TEXT_VIDEO_CHANNEL_ID is not set - text-to-video disabled.")
        self.temp_janitor.start()

    def cog_unload(self):
        with contextlib.suppress(Exception):
            self.temp_janitor.cancel()
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    @tasks.loop(minutes=30)
    async def temp_janitor(self):
        with contextlib.suppress(Exception):
            await asyncio.to_thread(purge_stale_temp_files, 3600)

    @temp_janitor.before_loop
    async def _before_janitor(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._starter_ready or TEXT_VIDEO_CHANNEL_ID <= 0:
            return
        self._starter_ready = True
        channel = self.bot.get_channel(TEXT_VIDEO_CHANNEL_ID)
        if isinstance(channel, discord.TextChannel):
            await self._repost_t2v_starter(channel)
        else:
            logger.warning("T2V channel %s not found.", TEXT_VIDEO_CHANNEL_ID)

    async def _repost_t2v_starter(self, channel: discord.TextChannel):
        await refresh_starter_message(
            channel=channel,
            bot_user_id=self.bot.user.id if self.bot.user else None,
            content=build_t2v_starter_text(),
            view_factory=lambda: T2VStarterView(channel.id),
            matcher=is_t2v_starter_message,
            scan_limit=15,
        )

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
    async def get_remaining_info(
        self, guild_id: int, member: discord.Member
    ) -> dict[str, int]:
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
        self, user: discord.abc.User, prompt: str, percent: int,
        elapsed_sec: int, stage_text: str, quota: dict[str, int], model_id: str,
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
            footer=(f"🎞️ {get_model_label(model_id)} "
                    f"• 📺 {_resolution_for_model(model_id)}"),
        )

    def _result_embed(
        self, prompt: str, seconds: int, model_id: str,
        guild_icon_url: Optional[str], note: str = "",
    ) -> discord.Embed:
        embed = discord.Embed(color=discord.Color.dark_magenta(), timestamp=utc_now())
        embed.add_field(name="Prompt",
                        value=f"```{codeblock_safe(trim(prompt, 1500))}```",
                        inline=False)
        if note:
            embed.add_field(name="Note", value=note, inline=False)
        embed.set_footer(
            text=(f"🎞️ {get_model_label(model_id)} "
                  f"• 📺 {_resolution_for_model(model_id)} • ⏱️ {seconds}s"),
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
        self, channel: discord.abc.Messageable,
        keep_ids: Optional[set[int]] = None, limit: int = 20,
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

    # ---------- streamed media download ----------
    async def _download_to_file(
        self, url: str, headers: dict[str, str], visited: Optional[set[str]] = None
    ) -> tuple[Optional[Path], Optional[str]]:
        """
        Stream a URL to a temp file. Returns (path, kind) with kind in
        {'video','image'}. Never holds more than one chunk in RAM.
        """
        if not isinstance(url, str) or not url.startswith("http"):
            return None, None
        visited = visited or set()
        if url in visited or len(visited) > 12:
            return None, None
        visited.add(url)

        await self._ensure_session()
        assert self.session is not None
        timeout = aiohttp.ClientTimeout(total=1200, connect=15, sock_read=120)

        for use_auth in (True, False):
            out_path = temp_path("vdl", "bin")
            try:
                req_headers = dict(headers) if use_auth else {}
                async with self.session.get(
                    url, headers=req_headers, timeout=timeout
                ) as resp:
                    if resp.status >= 400:
                        cleanup_temp_files(out_path)
                        continue

                    ctype = (resp.headers.get("content-type") or "").lower()

                    # JSON indirection: small, safe to buffer.
                    if "json" in ctype:
                        body = await resp.read()
                        cleanup_temp_files(out_path)
                        try:
                            nested = json.loads(body.decode("utf-8", errors="ignore"))
                        except Exception:
                            nested = None
                        if nested:
                            for nested_url in extract_urls_from_payload(nested):
                                p, k = await self._download_to_file(
                                    nested_url, headers, visited)
                                if p:
                                    return p, k
                        continue

                    total = 0
                    with open(out_path, "wb") as fh:
                        async for chunk in resp.content.iter_chunked(DOWNLOAD_CHUNK):
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > DOWNLOAD_MAX_BYTES:
                                raise ValueError("download exceeded size cap")
                            fh.write(chunk)

                    if total == 0:
                        cleanup_temp_files(out_path)
                        continue

                    if "video" in ctype or file_looks_like_video(out_path):
                        return out_path, "video"
                    if "image" in ctype or file_looks_like_image(out_path):
                        return out_path, "image"

                    cleanup_temp_files(out_path)
            except Exception as e:
                logger.debug("download failed (%s): %s", url[:80], e)
                cleanup_temp_files(out_path)
                continue

        return None, None

    # ---------- provider: queue ----------
    async def _queue_render(
        self,
        model_id: str,
        prompt: str,
        seconds: int,
        aspect_value: Optional[str],
        image_variants: Optional[list[dict[str, Any]]] = None,
    ) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[str], str]:
        """
        Unified queue call. image_variants is None for text-to-video.
        Returns (queue_id, raw_response, error, request_id).
        """
        if not VENICE_VIDEO_QUEUE_URL:
            return None, None, "VENICE_VIDEO_QUEUE_URL is missing.", "noid"
        if not VENICE_API_KEY:
            return None, None, "VENICE_API_KEY is missing.", "noid"

        await self._ensure_session()
        assert self.session is not None

        headers = {
            "Authorization": f"Bearer {VENICE_API_KEY}",
            "Content-Type": "application/json",
        }
        request_id = uuid.uuid4().hex[:8]
        resolution = _resolution_for_model(model_id)

        base_payload: dict[str, Any] = {
            "model": model_id,
            "prompt": trim(prompt, get_model_prompt_limit(model_id)),
            "resolution": resolution,
            "duration": f"{seconds}s",
        }
        if aspect_value:
            base_payload["aspect_ratio"] = aspect_value

        variants = image_variants if image_variants else [{}]
        timeout = aiohttp.ClientTimeout(total=90, connect=10, sock_read=70)
        last_error = "Queue request failed."

        for attempt in range(2):
            for variant_idx, variant in enumerate(variants):
                payload = {**base_payload, **variant}
                try:
                    async with self.session.post(
                        VENICE_VIDEO_QUEUE_URL, headers=headers,
                        json=payload, timeout=timeout,
                    ) as resp:
                        text = await resp.text()
                        logger.info(
                            "[VID %s] queue status=%s try=%s variant=%s model=%s "
                            "res=%s dur=%ss ar=%s",
                            request_id, resp.status, attempt + 1,
                            (next(iter(variant)) if variant else "text"),
                            model_id, resolution, seconds,
                            base_payload.get("aspect_ratio", "-"),
                        )

                        if resp.status in (400, 415, 422):
                            last_error = (f"Queue error ({resp.status}): "
                                          f"{sanitize_error_text(text)}")
                            if variant_idx < len(variants) - 1:
                                continue
                            return None, {"raw": text}, last_error, request_id

                        if resp.status in (401, 403, 404):
                            return (None, {"raw": text},
                                    f"Queue error ({resp.status}): "
                                    f"{sanitize_error_text(text)}", request_id)

                        if resp.status == 429:
                            if "too many failed attempts" in (text or "").lower():
                                return (None, {"raw": text},
                                        f"Provider rate limit: "
                                        f"{sanitize_error_text(text)}", request_id)
                            await asyncio.sleep(
                                _parse_retry_after_seconds(resp.headers, text))
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
    ) -> tuple[Optional[Path], Optional[str], Optional[str]]:
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
            seconds=_adaptive_budget_for(model_id, requested_seconds))

        # Our own estimate is the baseline for the progress bar. The provider
        # average is only blended in when it looks plausible.
        own_estimate_ms = estimate_render_seconds(model_id, requested_seconds) * 1000
        poll_interval = _poll_interval_for(model_id, requested_seconds)

        logger.info("[VID %s] polling every %.1fs, estimate %.0fs",
                    request_id, poll_interval, own_estimate_ms / 1000)

        consecutive_5xx = 0
        total_5xx = 0
        first_5xx_at = None
        finalize_attempts = 0
        last_percent = 8

        timeout = aiohttp.ClientTimeout(total=180, connect=15, sock_read=120)

        while True:
            if utc_now() >= hard_deadline or utc_now() >= adaptive_deadline:
                break

            await asyncio.sleep(poll_interval)
            elapsed_sec = int((utc_now() - started).total_seconds())

            try:
                async with self.session.post(
                    VENICE_VIDEO_RETRIEVE_URL, headers=headers,
                    json={"model": model_id, "queue_id": queue_id}, timeout=timeout,
                ) as response:
                    ctype = (response.headers.get("content-type") or "").lower()

                    if response.status == 429:
                        t429 = await response.text()
                        await asyncio.sleep(
                            _parse_retry_after_seconds(response.headers, t429))
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
                                    f"Provider error {response.status} "
                                    f"(retry {total_5xx})...",
                                    quota, model_id),
                            )
                            last_percent = p

                            too_many = consecutive_5xx >= VIDEO_MAX_CONSECUTIVE_5XX
                            too_long = first_5xx_at and (
                                (utc_now() - first_5xx_at).total_seconds()
                                >= VIDEO_5XX_WINDOW_SECONDS)
                            if too_many or too_long:
                                return None, None, "Provider unavailable (repeated 5xx)."
                            continue

                        consecutive_5xx, first_5xx_at = 0, None
                        if response.status in (401, 403):
                            return None, None, "API authentication failed (401/403)."
                        if response.status == 404:
                            return None, None, "Retrieve endpoint not found (404)."
                        if response.status == 422:
                            return None, None, "Retrieve request rejected (422)."
                        continue

                    consecutive_5xx, first_5xx_at = 0, None

                    # Direct binary body -> stream straight to disk.
                    if "video" in ctype or "image" in ctype:
                        out_path = temp_path("vdl", "bin")
                        total = 0
                        try:
                            with open(out_path, "wb") as fh:
                                async for chunk in response.content.iter_chunked(
                                        DOWNLOAD_CHUNK):
                                    if not chunk:
                                        continue
                                    total += len(chunk)
                                    if total > DOWNLOAD_MAX_BYTES:
                                        raise ValueError("stream exceeded cap")
                                    fh.write(chunk)
                        except Exception:
                            cleanup_temp_files(out_path)
                            continue

                        if file_looks_like_video(out_path):
                            return out_path, "video", None
                        if file_looks_like_image(out_path):
                            return out_path, "image", None
                        cleanup_temp_files(out_path)
                        continue

                    raw = await response.text()
                    try:
                        data = json.loads(raw) if raw else {}
                    except Exception:
                        continue

                    status = str(data.get("status", "")).lower()
                    avg_ms = safe_int(data.get("average_execution_time", 0), 0)
                    exec_ms = safe_int(data.get("execution_duration", 0), 0)
                    if exec_ms <= 0:
                        exec_ms = elapsed_sec * 1000

                    if status in {"failed", "error", "cancelled", "canceled"}:
                        err = data.get("error")
                        msg = (err.get("message") if isinstance(err, dict)
                               else err if isinstance(err, str)
                               else data.get("message"))
                        return None, None, (
                            f"Rendering aborted: "
                            f"{sanitize_error_text(str(msg or 'unknown'))}")

                    if status == "completed":
                        candidate_urls: list[str] = []
                        if (isinstance(queue_download_url, str)
                                and queue_download_url.startswith("http")):
                            candidate_urls.append(queue_download_url)
                        candidate_urls.extend(extract_urls_from_payload(data))
                        candidate_urls = list(dict.fromkeys(candidate_urls))

                        for media_url in candidate_urls:
                            path, kind = await self._download_to_file(media_url, headers)
                            if path:
                                return path, kind, None

                        finalize_attempts += 1
                        p = max(last_percent, 98)
                        await self._safe_edit_progress(
                            progress_message,
                            self._progress_embed(
                                user, prompt, p, elapsed_sec,
                                "Finalizing file delivery...", quota, model_id),
                        )
                        last_percent = p

                        if finalize_attempts >= 25:
                            return None, None, (
                                "Rendering finished, but no file was returned.")
                        continue

                    # Progress. The old code used max(avg_ms, 120000) as the
                    # denominator, which pinned fast models near 20%: a 25s
                    # MiniMax render divided by 120s can never climb higher.
                    if 5000 < avg_ms < own_estimate_ms * 3:
                        target_ms = (avg_ms + own_estimate_ms) // 2
                    else:
                        target_ms = own_estimate_ms
                    target_ms = max(target_ms, 15000)

                    # Stretch the estimate if the render outlives it, so the
                    # bar keeps creeping instead of sticking at the cap.
                    if exec_ms > target_ms:
                        own_estimate_ms = int(exec_ms * 1.25)
                        target_ms = own_estimate_ms

                        # A slower-than-expected render also needs more budget.
                        stretched = started + timedelta(
                            seconds=int(own_estimate_ms / 1000) + 120)
                        if stretched > adaptive_deadline:
                            adaptive_deadline = min(stretched, hard_deadline)

                    percent = min(97, max(8, int((exec_ms / target_ms) * 100)))
                    percent = max(percent, last_percent)

                    if percent != last_percent:
                        await self._safe_edit_progress(
                            progress_message,
                            self._progress_embed(
                                user, prompt, percent, elapsed_sec,
                                "Rendering...", quota, model_id),
                        )
                        last_percent = percent

            except asyncio.TimeoutError:
                continue
            except Exception:
                continue

        return None, None, "Generation timed out."

    # ---------- shared render core ----------
    async def _run_render(
        self,
        interaction: discord.Interaction,
        model_id: str,
        prompt: str,
        seconds: int,
        aspect_value: Optional[str],
        target_channel: discord.abc.Messageable,
        image_variants: Optional[list[dict[str, Any]]],
        kind: str,
    ) -> bool:
        """Locking, quota, queue, poll, compress and post. Used by both modes."""
        if not await self._try_lock_user(interaction.user.id):
            await send_ephemeral(
                interaction, "⏳ You already have a render running. Please wait.")
            return False

        member = interaction.user
        tier = get_member_tier(member)
        budget = get_video_budget_for_member(member)

        if budget <= 0:
            await send_video_role_locked(interaction)
            await self._unlock_user(interaction.user.id)
            return False

        ok_q, state_q, token = await self.video_quota.reserve(
            interaction.guild.id, interaction.user.id, budget, seconds)
        if not ok_q:
            await send_ephemeral(interaction, build_video_quota_text(tier, state_q))
            await self._unlock_user(interaction.user.id)
            return False

        if not await self._try_begin_global():
            await self.video_quota.rollback(token)
            await send_ephemeral(
                interaction, "⏳ Another render is currently running. Please wait.")
            await self._unlock_user(interaction.user.id)
            return False

        progress_message: Optional[discord.Message] = None
        quota_success = False
        keep_ids: set[int] = set()
        media_path: Optional[Path] = None
        upload_path: Optional[Path] = None

        if isinstance(target_channel, (discord.TextChannel, discord.Thread)):
            await self._cleanup_progress_leaks(target_channel, keep_ids=set(), limit=20)

        try:
            log_memory_usage("render-start")

            progress_message = await target_channel.send(
                embed=self._progress_embed(
                    interaction.user, prompt, 5, 0,
                    "Sending queue request...", state_q, model_id)
            )
            keep_ids.add(progress_message.id)

            queue_id, queue_response, queue_error, request_id = await self._queue_render(
                model_id=model_id, prompt=prompt, seconds=seconds,
                aspect_value=aspect_value, image_variants=image_variants,
            )
            if not queue_id:
                await send_ephemeral(
                    interaction,
                    f"❌ Render failed: "
                    f"{sanitize_error_text(queue_error or 'Queue failed.')}")
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
                    "Queue accepted. Rendering started.", state_q, model_id),
            )

            media_path, media_type, error_message = await self._wait_for_result(
                model_id=model_id, queue_id=queue_id,
                progress_message=progress_message, user=interaction.user,
                prompt=prompt, quota=state_q,
                queue_download_url=queue_download_url,
                request_id=request_id, requested_seconds=seconds,
            )

            if not media_path:
                await send_ephemeral(
                    interaction,
                    f"❌ Render failed: "
                    f"{sanitize_error_text(error_message or 'Unknown error')}")
                return False
            if media_type != "video":
                await send_ephemeral(interaction, "❌ Provider returned non-video output.")
                return False

            log_memory_usage("download-done")

            guild = getattr(target_channel, "guild", None)
            guild_icon_url = guild.icon.url if (guild and guild.icon) else None
            upload_limit = channel_upload_limit_bytes(target_channel)
            raw_size = file_size(media_path)
            note = ""
            upload_path = media_path

            if raw_size > upload_limit:
                target = max(1024 * 1024, upload_limit - 512 * 1024)

                async def _cb(text: str):
                    await self._safe_edit_progress(
                        progress_message,
                        self._progress_embed(
                            interaction.user, prompt, 99, 0,
                            text, state_q, model_id),
                    )

                compressed, comp_note = await compress_video_file(
                    media_path, target, float(seconds), progress_cb=_cb)

                if compressed is None:
                    await send_ephemeral(
                        interaction,
                        f"❌ Video too large ({human_bytes(raw_size)} > "
                        f"{human_bytes(upload_limit)}) and compression failed: "
                        f"{comp_note}.\nTry a shorter duration.")
                    return False

                upload_path = compressed
                if comp_note != "no compression needed":
                    note = f"🗜️ {comp_note}"

            with open(upload_path, "rb") as fh:
                video_post = await target_channel.send(
                    content=(f"{SERVER_ANIM_ICON} 🎬 **Video** • "
                             f"{interaction.user.mention} • ▶ **CLICK TO PLAY**"),
                    embed=self._result_embed(
                        prompt, seconds, model_id, guild_icon_url, note),
                    file=discord.File(fh, filename="AI_video.mp4"),
                    allowed_mentions=discord.AllowedMentions(
                        users=True, roles=False, everyone=False),
                )
            keep_ids.add(video_post.id)
            await add_rating_reactions(video_post)

            quota_success = True
            info = await self.get_remaining_info(interaction.guild.id, interaction.user)
            await send_ephemeral(
                interaction,
                build_generation_success_text(
                    info, kind=kind, unit="s", quota_label="Remaining today"),
            )
            log_memory_usage("render-done")
            return True

        except discord.Forbidden:
            await send_ephemeral(
                interaction, "❌ Missing Discord permissions to post video.")
            return False
        except Exception as e:
            logger.exception("render failed: %s", e)
            await send_ephemeral(
                interaction, f"❌ Render failed: {sanitize_error_text(str(e))}")
            return False
        finally:
            if not quota_success:
                await self.video_quota.rollback(token)

            cleanup_temp_files(media_path)
            if upload_path and upload_path != media_path:
                cleanup_temp_files(upload_path)

            await self._safe_delete_message(progress_message)
            await self._end_global()
            await self._unlock_user(interaction.user.id)

            if isinstance(target_channel, (discord.TextChannel, discord.Thread)):
                await self._cleanup_progress_leaks(
                    target_channel, keep_ids=keep_ids, limit=25)
                with contextlib.suppress(Exception):
                    await repost_starter_for_channel(target_channel)

            asyncio.create_task(self._cleanup_user_ephemerals_delayed(interaction))

    # ---------- public: image to video ----------
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
            await send_ephemeral(interaction, "❌ Video API endpoints missing in .env.")
            return False
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await send_ephemeral(interaction, "❌ This action is server-only.")
            return False
        if not has_video_access(interaction.user):
            await send_video_role_locked(interaction)
            return False
        if seconds <= 0:
            await send_ephemeral(interaction, "❌ Invalid duration.")
            return False
        if seconds > MAX_VIDEO_RENDER_SECONDS:
            await send_ephemeral(
                interaction,
                f"❌ Max duration per render is {MAX_VIDEO_RENDER_SECONDS} seconds.")
            return False

        effective_model_id = (model_id or VENICE_VIDEO_I2V_MODEL_DEFAULT).strip()
        if not effective_model_id:
            await send_ephemeral(interaction, "❌ No video model configured.")
            return False

        if not is_known_video_model(effective_model_id):
            available = ", ".join(get_model_label(m) for m in VIDEO_MODEL_PROFILES)
            await send_ephemeral(
                interaction,
                f"❌ Unknown video model `{effective_model_id}`.\nAvailable: {available}")
            return False

        model_durations = get_model_durations(effective_model_id)
        if seconds not in model_durations:
            allowed = ", ".join(f"{s}s" for s in model_durations)
            await send_ephemeral(
                interaction, f"❌ Allowed durations for this model: {allowed}.")
            return False

        has_url = bool(image_url and image_url.startswith("http"))
        has_bytes = bool(image_bytes and looks_like_image(image_bytes))
        if not has_url and not has_bytes:
            await send_ephemeral(interaction, "❌ No valid source image.")
            return False

        size_error = check_source_image_for_model(effective_model_id, image_bytes)
        if size_error:
            await send_ephemeral(interaction, f"❌ {size_error}")
            return False

        # Canonical field is 'image_url' (accepts http and data URLs).
        image_variants: list[dict[str, Any]] = []
        if has_url:
            image_variants.append({"image_url": image_url})
        if has_bytes:
            prepared = prepare_source_image_for_upload(
                image_bytes,
                min_short_side=get_model_min_short_side(effective_model_id),
            )
            if prepared and looks_like_image(prepared):
                image_variants.append({"image_url": bytes_to_data_url(prepared)})

        if not image_variants:
            await send_ephemeral(interaction, "❌ No usable source image.")
            return False

        return await self._run_render(
            interaction=interaction,
            model_id=effective_model_id,
            prompt=prompt,
            seconds=seconds,
            aspect_value=resolve_video_aspect_ratio(effective_model_id, aspect),
            target_channel=target_channel,
            image_variants=image_variants,
            kind="video",
        )

    # ---------- public: text to video ----------
    async def text_to_video(
        self,
        interaction: discord.Interaction,
        prompt: str,
        aspect: str,
        seconds: int,
        target_channel: discord.abc.Messageable,
        model_id: str,
    ) -> bool:
        if not VENICE_API_KEY:
            await send_ephemeral(interaction, "❌ VENICE_API_KEY is missing.")
            return False
        if not VENICE_VIDEO_QUEUE_URL or not VENICE_VIDEO_RETRIEVE_URL:
            await send_ephemeral(interaction, "❌ Video API endpoints missing in .env.")
            return False
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await send_ephemeral(interaction, "❌ This action is server-only.")
            return False
        if not has_video_access(interaction.user):
            await send_video_role_locked(interaction)
            return False

        model_id = (model_id or "").strip()
        if not is_known_t2v_model(model_id):
            available = ", ".join(
                p["button_label"] for p in TEXT_VIDEO_MODEL_PROFILES.values())
            await send_ephemeral(
                interaction,
                f"❌ Unknown text-to-video model.\nAvailable: {available}")
            return False

        profile = get_t2v_profile(model_id)
        if seconds <= 0 or seconds > MAX_VIDEO_RENDER_SECONDS:
            await send_ephemeral(
                interaction,
                f"❌ Max duration per render is {MAX_VIDEO_RENDER_SECONDS} seconds.")
            return False
        if seconds not in profile["durations"]:
            allowed = ", ".join(f"{s}s" for s in profile["durations"])
            await send_ephemeral(
                interaction, f"❌ Allowed durations for this model: {allowed}.")
            return False

        prompt = (prompt or "").strip()
        if not prompt:
            await send_ephemeral(interaction, "❌ Prompt is empty.")
            return False

        return await self._run_render(
            interaction=interaction,
            model_id=model_id,
            prompt=prompt,
            seconds=seconds,
            aspect_value=resolve_t2v_aspect_ratio(model_id, aspect),
            target_channel=target_channel,
            image_variants=None,
            kind="text_video",
        )

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

    @commands.command(name="video_tmp_purge")
    @commands.has_permissions(administrator=True)
    async def video_tmp_purge(self, ctx: commands.Context):
        removed = await asyncio.to_thread(purge_stale_temp_files, 0)
        await ctx.send(f"🧹 Removed {removed} temp file(s).")

    @commands.command(name="video_starter")
    @commands.has_permissions(administrator=True)
    async def video_starter(self, ctx: commands.Context):
        if TEXT_VIDEO_CHANNEL_ID <= 0:
            await ctx.send("❌ TEXT_VIDEO_CHANNEL_ID is not set in .env.")
            return
        channel = self.bot.get_channel(TEXT_VIDEO_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            await ctx.send("❌ Text-to-video channel not found.")
            return
        await self._repost_t2v_starter(channel)
        await ctx.send(f"✅ Starter refreshed in {channel.mention}.")

    @commands.command(name="video_profiles")
    @commands.has_permissions(administrator=True)
    async def video_profiles(self, ctx: commands.Context):
        lines = ["🎞️ **Image → Video**"]
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
            speed = profile.get("est_seconds_per_second")
            lines.append(f"• `{model_id}` → {profile['button_label']}\n"
                         f"  {profile['resolution']} • {durations}"
                         f"{aspect_info}{min_info} • ~{speed}x realtime")

        lines.append("\n🎬 **Text → Video**")
        for model_id, profile in TEXT_VIDEO_MODEL_PROFILES.items():
            durations = ", ".join(f"{d}s" for d in profile["durations"])
            ratios = "/".join(profile["aspect_ratios"])
            speed = profile.get("est_seconds_per_second")
            lines.append(f"• `{model_id}` → {profile['button_label']}\n"
                         f"  {profile['resolution']} • {durations} • AR: {ratios}"
                         f" • ~{speed}x realtime")

        channel_info = (f"<#{TEXT_VIDEO_CHANNEL_ID}>" if TEXT_VIDEO_CHANNEL_ID > 0
                        else "`not configured`")
        lines.append(f"\nMax per render: **{MAX_VIDEO_RENDER_SECONDS}s** "
                     f"• T2V channel: {channel_info}")
        await ctx.send("\n".join(lines)[:1950])

    @commands.command(name="video_timing")
    @commands.has_permissions(administrator=True)
    async def video_timing(self, ctx: commands.Context):
        """Show the estimates that drive the progress bar and poll interval."""
        lines = ["⏱️ **Render estimates**"]
        seen: set[str] = set()
        for table in (VIDEO_MODEL_PROFILES, TEXT_VIDEO_MODEL_PROFILES):
            for model_id, profile in table.items():
                if model_id in seen:
                    continue
                seen.add(model_id)
                parts = []
                for d in profile["durations"][:3]:
                    est = estimate_render_seconds(model_id, d)
                    parts.append(f"{d}s→~{est}s")
                poll = _poll_interval_for(model_id, profile["durations"][0])
                lines.append(f"`{model_id}`\n  {' • '.join(parts)} "
                             f"• poll {poll:.1f}s")
        await ctx.send("\n".join(lines)[:1950])

    @commands.command(name="video_test_model")
    @commands.has_permissions(administrator=True)
    async def video_test_model(self, ctx: commands.Context):
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

        live_ids = {m.get("id") for m in data.get("data", []) if isinstance(m, dict)}
        lines = ["🔍 **Configured vs. live**", "", "**I2V**"]
        for model_id in VIDEO_MODEL_PROFILES:
            lines.append(f"{'✅' if model_id in live_ids else '❌'} `{model_id}`")
        lines += ["", "**T2V**"]
        for model_id in TEXT_VIDEO_MODEL_PROFILES:
            lines.append(f"{'✅' if model_id in live_ids else '❌'} `{model_id}`")
        await ctx.send("\n".join(lines)[:1900])


async def setup(bot: commands.Bot):
    await bot.add_cog(VeniceVideoCog(bot))