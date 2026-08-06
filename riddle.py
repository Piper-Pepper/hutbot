# riddle.py
from __future__ import annotations

import asyncio
import contextlib
from typing import Optional

import discord
from discord import app_commands, Interaction, Role
from discord.ext import commands

from riddle_core import (
    RIDDLE_GUILD_ID, RIDDLE_CHANNEL_ID, VOTE_CHANNEL_ID,
    RIDDLE_ROLE_ID, RIDDLE_MANAGER_ROLE_ID,
    EXCLUDED_COUNT_ROLE_ID, EXCLUDED_GAMEMASTER_ROLE_ID, EXTRA_EXCLUDED_ROLE_IDS_CSV,
    DEFAULT_IMAGE_URL, MAX_RIDDLE_SLOTS, MAX_EXTRA_PING_ROLES,
    SUBMIT_DELAY_MINUTES, UNSOLVED_ROTATION_HOURS, ROTATION_HARD_CAP_HOURS,
    UNSOLVED_ROTATION_XP_BONUS, MAX_RIDDLE_XP,
    SOLVED_HIATUS_HOURS, ROTATION_TICK_SECONDS, STATS_REBUILD_DEBOUNCE_SECONDS,
    SUBMIT_BUTTON_ID, VOTE_UP_BUTTON_ID, VOTE_DOWN_BUTTON_ID,
    UNKNOWN_MESSAGE, MessageLookup,
    logger, to_int, safe_int, is_http_url, unique_role_mentions, parse_csv_role_ids,
    now_iso_utc, iso_in_future, iso_utc_in_hours, hours_since, seconds_until,
    submit_unlock_iso, submit_is_locked,
    riddle_manager_required, riddle_guild_only, guild_is_served,
    send_access_denied, send_wrong_guild, MissingRiddleManagerRole, WrongGuild,
    validate_config, RiddleRepo,
)

from riddle_ui import (
    build_active_riddle_embed,
    build_fresh_solved_post_embed,
    build_solved_ping_post_embed,
    build_wrong_post_embed,
    build_xp_reminder_embed,
    build_vote_embed,
    build_rotation_warning_embed,
    build_rotation_bonus_embed,
    SubmitButtonView,
    VoteButtons,
    RiddleAdminPanelView,
    ChampionsView,
)


class RiddleCog(commands.Cog):
    """
    Concurrency model
    -----------------
    Every operation that mutates the *published* state of a guild (posting,
    rotating, closing, saving slot content, finalizing a vote) runs under a
    per-guild asyncio.Lock. Without it the background worker, on_ready
    reconnect recovery and admin panel clicks race each other and produce
    duplicate riddle posts with orphaned Submit buttons that nothing can clean
    up.

    The repo's own lock only protects a single SQL operation. It does NOT
    protect a read-modify-publish sequence, which is why the UI must go through
    the save_* methods on this cog instead of calling repo methods directly.

    asyncio.Lock is NOT reentrant, so every locked public method delegates to
    an `_*_unlocked` counterpart which internal callers use.

    Role pings
    ----------
    A riddle pings its roles exactly once: on its FIRST post. That decision is
    derived inside _publish_slot1_post_unlocked from first_posted_at being
    NULL, not from the caller. Callers can only SUPPRESS it
    (ping_on_first_post=False) - used by startup and reconnect recovery so a
    restart never spams the server.

    Unsolved rotation XP bonus
    --------------------------
    `xp_bonus` defaults to 0 everywhere. ONLY the two automatic rotation paths
    in _enforce_enabled_state_unlocked pass UNSOLVED_ROTATION_XP_BONUS. A manual
    "Move to End" from the admin panel therefore never raises the reward and
    never touches rotation_count.
    """

    def __init__(self, bot: commands.Bot, repo: RiddleRepo):
        self.bot = bot
        self.repo = repo
        self._auto_task: Optional[asyncio.Task] = None
        self._startup_done = False
        self._guild_locks: dict[int, asyncio.Lock] = {}
        self._stats_rebuild_tasks: dict[int, asyncio.Task] = {}
        self._unlock_tasks: dict[int, asyncio.Task] = {}
        self._chunked_guilds: set[int] = set()
        self._excluded_ids_cache: Optional[frozenset[int]] = None
        # guild_id -> signature of what is currently rendered in the channel.
        # Lets the worker skip a pointless message edit every single tick.
        self._render_sig: dict[int, tuple] = {}

    # ==========================================================================
    # LOCKING
    # ==========================================================================
    def _lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._guild_locks.get(guild_id)
        if lock is None:
            lock = self._guild_locks[guild_id] = asyncio.Lock()
        return lock

    # ==========================================================================
    # SUBMIT UNLOCK SCHEDULING
    # ==========================================================================
    def schedule_submit_unlock(self, guild_id: int, unlock_iso: Optional[str]):
        """
        Re-render the active riddle post once the grace period expires, so the
        Submit button becomes clickable without waiting for the next worker
        tick. Purely cosmetic - the authoritative gate is the DB check in the
        button callback, so a lost task only makes the button look late.
        """
        old = self._unlock_tasks.get(guild_id)
        if old and not old.done():
            old.cancel()
        if not unlock_iso:
            return
        delay = seconds_until(unlock_iso)
        if delay is None:
            return

        async def _run():
            try:
                await asyncio.sleep(delay + 2.0)  # small buffer against clock skew
                res = await self.enforce_enabled_state(guild_id, force_repost=False,
                                                       ping_on_first_post=False)
                logger.info("Submit unlock re-render for guild %s -> %s", guild_id, res)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Submit unlock re-render failed for guild %s", guild_id)

        self._unlock_tasks[guild_id] = asyncio.create_task(
            _run(), name=f"riddle_submit_unlock_{guild_id}")

    def cancel_submit_unlock(self, guild_id: int):
        task = self._unlock_tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()

    # ==========================================================================
    # EXCLUDED USERS / STATS FILTERING
    # ==========================================================================
    def excluded_role_ids(self) -> frozenset[int]:
        """
        Cached: this is called once per member during a stats rebuild, and
        re-parsing the CSV 500 times per rebuild is pure waste. The values come
        from env vars and cannot change at runtime.
        """
        if self._excluded_ids_cache is None:
            s = {EXCLUDED_COUNT_ROLE_ID, EXCLUDED_GAMEMASTER_ROLE_ID, RIDDLE_MANAGER_ROLE_ID}
            for rid in parse_csv_role_ids(EXTRA_EXCLUDED_ROLE_IDS_CSV):
                if rid > 0:
                    s.add(rid)
            s.discard(0)
            self._excluded_ids_cache = frozenset(s)
        return self._excluded_ids_cache

    async def user_is_excluded(self, guild: discord.Guild, user_id: int, *,
                               allow_fetch: bool = True) -> bool:
        """
        allow_fetch=False avoids one REST call per user - mandatory inside loops.
        With the members intent + guild chunking the cache is authoritative.
        """
        m = guild.get_member(user_id)
        if m is None and allow_fetch:
            with contextlib.suppress(discord.HTTPException, discord.NotFound):
                m = await guild.fetch_member(user_id)
        if m is None:
            return False
        ex = self.excluded_role_ids()
        return any(r.id in ex for r in m.roles)

    async def ensure_guild_chunked(self, guild: discord.Guild):
        """
        Fill the member cache once so bulk filtering needs zero REST calls.

        The guild is only marked as done AFTER a successful chunk. Marking it
        upfront meant a single chunk timeout (common on large guilds) silently
        disabled excluded-role filtering for the rest of the process lifetime:
        get_member() returns None, user_is_excluded(allow_fetch=False) returns
        False, and every excluded user starts counting towards the leaderboard
        and the solved total.
        """
        if guild.id in self._chunked_guilds:
            return
        if not self.bot.intents.members:
            # Not a transient failure - no point retrying every rebuild.
            self._chunked_guilds.add(guild.id)
            logger.warning(
                "Members intent is DISABLED - excluded-role filtering falls back to "
                "per-user REST lookups and stats rebuilds will be slow. Enable "
                "'Server Members Intent' in the developer portal.")
            return
        if guild.chunked:
            self._chunked_guilds.add(guild.id)
            return
        try:
            await guild.chunk(cache=True)
        except Exception:
            logger.exception("Failed to chunk guild %s - excluded-role filtering may be "
                             "incomplete, will retry on next rebuild", guild.id)
            return
        self._chunked_guilds.add(guild.id)
        logger.info("Chunked guild %s (%s members)", guild.id, guild.member_count)

    async def rebuild_cached_solved_total_for_guild(self, guild_id: int) -> int:
        rows = await self.repo.stats_entries(guild_id)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            total = sum(s for _, s, _ in rows)
            await self.repo.set_cached_solved_total(guild_id, total)
            return total
        await self.ensure_guild_chunked(guild)
        total = 0
        for uid, solved, _xp in rows:
            if await self.user_is_excluded(guild, uid, allow_fetch=False):
                continue
            total += solved
        await self.repo.set_cached_solved_total(guild_id, total)
        return total

    async def filtered_stats_entries_for_guild(
        self, guild: discord.Guild
    ) -> list[tuple[int, int, int]]:
        await self.ensure_guild_chunked(guild)
        raw = await self.repo.stats_entries(guild.id)
        out: list[tuple[int, int, int]] = []
        for uid, solved, xp in raw:
            if await self.user_is_excluded(guild, uid, allow_fetch=False):
                continue
            out.append((uid, solved, xp))
        return out

    def schedule_stats_rebuild(self, guild_id: int,
                               delay: float = STATS_REBUILD_DEBOUNCE_SECONDS):
        """
        True trailing-edge debounce: a new request CANCELS the pending one and
        restarts the timer, so the rebuild runs once, after the last change.

        The previous leading-edge throttle dropped every request that arrived
        while a task was sleeping - a role sync lasting longer than the delay
        would therefore rebuild from a half-finished state and never again.
        """
        existing = self._stats_rebuild_tasks.get(guild_id)
        if existing and not existing.done():
            existing.cancel()

        async def _run():
            try:
                await asyncio.sleep(delay)
                await self.rebuild_cached_solved_total_for_guild(guild_id)
                logger.info("Debounced stats rebuild done for guild %s", guild_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Debounced stats rebuild failed for guild %s", guild_id)

        self._stats_rebuild_tasks[guild_id] = asyncio.create_task(
            _run(), name=f"riddle_stats_rebuild_{guild_id}")

    async def normalize_after_structure_change(self, guild_id: int):
        """
        Compact open riddles into slots 1..N and delete any riddle post that is
        no longer slot 1.

        riddle_no is NOT recomputed here any more. It used to be
        (solved_total + slot_no), rewritten on every tick, which produced
        duplicate numbers in the history whenever an excluded user solved a
        riddle (the total does not advance then) and made updated_at
        meaningless. It is now assigned once at creation and treated as the
        riddle's identity.
        """
        orphans = await self.repo.pop_non_slot1_post_refs(guild_id)
        await self.repo.compact_open_slots(guild_id)
        if orphans:
            await self._delete_post_refs(orphans)
            self._render_sig.pop(guild_id, None)

    async def _delete_post_refs(self, rows: list[dict]):
        """Delete Discord messages for rows returned by the repo's pop_* calls."""
        for row in rows:
            cid = row.get("posted_channel_id")
            mid = row.get("posted_message_id")
            if await self.delete_message_ref(cid, mid):
                logger.info("Removed orphaned riddle post %s/%s (riddle %s)",
                            cid, mid, row.get("id"))

    # ==========================================================================
    # DISCORD RESOLVERS
    # ==========================================================================
    async def resolve_channel(self, channel_id: int):
        if to_int(channel_id, 0) <= 0:
            return None
        ch = self.bot.get_channel(channel_id)
        if ch is not None:
            return ch
        try:
            return await self.bot.fetch_channel(channel_id)
        except Exception:
            logger.warning("Could not resolve channel %s", channel_id)
            return None

    async def fetch_message_safe(self, channel_id: Optional[int],
                                 message_id: Optional[int]) -> MessageLookup:
        """
        Returns:
          * discord.Message  - exists
          * None             - definitely gone (404)
          * UNKNOWN_MESSAGE  - undeterminable (permissions, 5xx, timeout)

        Callers MUST treat UNKNOWN_MESSAGE as "assume it still exists". The old
        blanket `except Exception: return None` turned every transient API
        hiccup into a duplicate post.
        """
        cid = safe_int(channel_id, None)
        mid = safe_int(message_id, None)
        if not cid or not mid:
            return None
        ch = await self.resolve_channel(cid)
        if ch is None or not hasattr(ch, "fetch_message"):
            return UNKNOWN_MESSAGE
        try:
            return await ch.fetch_message(mid)
        except discord.NotFound:
            return None
        except discord.Forbidden:
            logger.warning("No permission to fetch message %s/%s - assuming it EXISTS",
                           cid, mid)
            return UNKNOWN_MESSAGE
        except (discord.HTTPException, asyncio.TimeoutError):
            logger.warning("Transient error fetching message %s/%s - assuming it EXISTS",
                           cid, mid, exc_info=True)
            return UNKNOWN_MESSAGE

    async def delete_message_ref(self, channel_id: Optional[int],
                                 message_id: Optional[int]) -> bool:
        """
        Delete via PartialMessage: no fetch needed, so it works without the
        message cache and without read_message_history.
        """
        cid = safe_int(channel_id, None)
        mid = safe_int(message_id, None)
        if not cid or not mid:
            return False
        ch = await self.resolve_channel(cid)
        if ch is None or not hasattr(ch, "get_partial_message"):
            return False
        try:
            await ch.get_partial_message(mid).delete()
            return True
        except discord.NotFound:
            return True  # already gone - goal achieved
        except discord.Forbidden:
            logger.warning("Missing permission to delete message %s/%s", cid, mid)
            return False
        except discord.HTTPException:
            logger.warning("Failed to delete message %s/%s", cid, mid, exc_info=True)
            return False

    async def resolve_user_label(
        self, guild: Optional[discord.Guild], uid: int
    ) -> tuple[str, str, Optional[str]]:
        if guild:
            m = guild.get_member(uid)
            if m is None:
                with contextlib.suppress(discord.HTTPException, discord.NotFound):
                    m = await guild.fetch_member(uid)
            if m:
                return m.mention, m.display_name, m.display_avatar.url
        u = self.bot.get_user(uid)
        if u is None:
            with contextlib.suppress(discord.HTTPException, discord.NotFound):
                u = await self.bot.fetch_user(uid)
        if u:
            return u.mention, u.display_name, u.display_avatar.url
        return f"<@{uid}>", f"User {uid}", None

    # ==========================================================================
    # PING CONTENT
    # ==========================================================================
    def build_ping_content(self, guild: Optional[discord.Guild],
                           mention_role_ids_csv: Optional[str]) -> Optional[str]:
        """Base role + up to MAX_EXTRA_PING_ROLES extra roles as a mention string."""
        extra: list[int] = []
        for rid in parse_csv_role_ids(mention_role_ids_csv):
            if rid == RIDDLE_ROLE_ID or rid in extra:
                continue
            extra.append(rid)
            if len(extra) >= MAX_EXTRA_PING_ROLES:
                break
        mentions = unique_role_mentions(guild, RIDDLE_ROLE_ID, *extra)
        return " ".join(dict.fromkeys(m for m in mentions if m)) or None

    # ==========================================================================
    # CLEANUP HELPERS
    # ==========================================================================
    def _msg_has_custom_id(self, msg: discord.Message, custom_ids: set[str]) -> bool:
        try:
            for row in (msg.components or []):
                for child in getattr(row, "children", []):
                    if getattr(child, "custom_id", None) in custom_ids:
                        return True
        except Exception:
            logger.debug("Component inspection failed for message %s", msg.id, exc_info=True)
        return False

    async def delete_button_messages_in_channel(self, channel_id: int,
                                                custom_ids: set[str],
                                                keep_message_ids: Optional[set[int]] = None,
                                                limit: int = 200):
        """
        Legacy safety net for orphaned button messages. keep_message_ids
        protects posts we are about to reuse, so this can run without racing
        the reconcile pass.
        """
        ch = await self.resolve_channel(channel_id)
        me = self.bot.user
        if ch is None or not hasattr(ch, "history") or me is None:
            return
        keep = keep_message_ids or set()
        removed = 0
        try:
            async for msg in ch.history(limit=limit):
                if msg.author.id != me.id or msg.id in keep:
                    continue
                if self._msg_has_custom_id(msg, custom_ids):
                    if await self.delete_message_ref(msg.channel.id, msg.id):
                        removed += 1
        except discord.Forbidden:
            logger.warning("No history permission in channel %s - skipping orphan sweep",
                           channel_id)
        except Exception:
            logger.exception("Orphan sweep failed in channel %s", channel_id)
        if removed:
            logger.info("Orphan sweep removed %s stale button message(s) in %s",
                        removed, channel_id)

    async def cleanup_vote_messages_for_riddle(self, riddle_id: int,
                                               exclude_submission_id: Optional[int] = None):
        rows = await self.repo.list_vote_messages_for_riddle(riddle_id)
        for row in rows:
            sid = to_int(row.get("id"), 0)
            if exclude_submission_id is not None and sid == exclude_submission_id:
                continue
            mid = to_int(row.get("vote_message_id"), 0)
            if mid > 0:
                await self.delete_message_ref(VOTE_CHANNEL_ID, mid)

    async def cleanup_wrong_posts_for_riddle(self, riddle_id: int):
        for row in await self.repo.list_wrong_posts_for_riddle(riddle_id):
            await self.delete_message_ref(row.get("channel_id"), row.get("message_id"))
        await self.repo.clear_wrong_posts_for_riddle(riddle_id)

    async def remove_active_riddle_posts(self, guild_id: int):
        for row in await self.repo.list_open_post_refs(guild_id):
            await self.delete_message_ref(row.get("posted_channel_id"),
                                          row.get("posted_message_id"))
        # Timer is preserved: removing the post (system OFF / hiatus) is not the
        # same as starting the riddle over.
        await self.repo.clear_all_open_post_refs(guild_id, reset_timer=False)
        self._render_sig.pop(guild_id, None)
        self.cancel_submit_unlock(guild_id)

    async def post_xp_reminder_to_vote_channel(self, guild: Optional[discord.Guild],
                                               user_id: int, xp_gain: int, riddle_no: int,
                                               riddle: Optional[dict] = None):
        """Post an XP-add reminder into the VOTE channel after a solve."""
        if VOTE_CHANNEL_ID <= 0 or xp_gain <= 0:
            return
        ch = await self.resolve_channel(VOTE_CHANNEL_ID)
        if ch is None or not hasattr(ch, "send"):
            return
        mention, name, avatar = await self.resolve_user_label(guild, user_id)
        embed = build_xp_reminder_embed(guild, mention, name, avatar,
                                        xp_gain, riddle_no, riddle)
        try:
            await ch.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.exception("Failed to post XP reminder to vote channel")

    async def post_rotation_warning(self, guild: Optional[discord.Guild], riddle: dict,
                                    pending_count: int, age_hours: float):
        if VOTE_CHANNEL_ID <= 0:
            return
        ch = await self.resolve_channel(VOTE_CHANNEL_ID)
        if ch is None or not hasattr(ch, "send"):
            return
        try:
            await ch.send(
                embed=build_rotation_warning_embed(guild, riddle, pending_count, age_hours),
                allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.exception("Failed to post rotation warning")

    async def post_rotation_bonus(self, guild: Optional[discord.Guild],
                                  riddle: dict, bump: dict):
        """Announce an XP increase caused by an automatic unsolved rotation."""
        if VOTE_CHANNEL_ID <= 0 or not bump:
            return
        ch = await self.resolve_channel(VOTE_CHANNEL_ID)
        if ch is None or not hasattr(ch, "send"):
            return
        try:
            await ch.send(embed=build_rotation_bonus_embed(guild, riddle, bump),
                          allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.exception("Failed to post rotation bonus notice")

    # ==========================================================================
    # POSTING  (locked public / unlocked internal)
    # ==========================================================================
    @staticmethod
    def _render_signature(riddle: dict, locked: bool, content: Optional[str]) -> tuple:
        """
        Everything that can change the rendered message. The relative timestamps
        inside the embed update client-side, so they are deliberately absent -
        that is what makes skipping the edit safe.
        """
        return (
            to_int(riddle.get("id"), 0),
            str(riddle.get("updated_at") or ""),
            str(riddle.get("first_posted_at") or ""),
            to_int(riddle.get("xp"), 0),
            to_int(riddle.get("rotation_count"), 0),
            bool(locked),
            content or "",
        )

    async def publish_slot1_post(self, guild_id: int, *, force_repost: bool,
                                 ping_on_first_post: bool = True) -> str:
        async with self._lock(guild_id):
            return await self._publish_slot1_post_unlocked(
                guild_id, force_repost=force_repost,
                ping_on_first_post=ping_on_first_post)

    async def _publish_slot1_post_unlocked(self, guild_id: int, *, force_repost: bool,
                                           ping_on_first_post: bool = True,
                                           force_ping: bool = False) -> str:
        """
        Publish or edit the Slot 1 riddle. Does NOT evaluate hiatus/rotation -
        that is enforce_enabled_state's job. Slot normalisation is the caller's
        responsibility (avoids doing it twice per tick).

        PING RULE
        ---------
        The role ping fires when this riddle has never been posted before
        (first_posted_at IS NULL). That is derived here, not passed in, because
        it is a property of the RIDDLE, not of the caller.

        This used to be an `allow_role_ping` flag that the worker always passed
        as False, with the result that the single most important notification -
        "a new riddle is up after the hiatus" - never actually notified anyone.
        The mention was in the message, it just never pinged.

          ping_on_first_post=False -> suppress even on a first post
                                      (startup / reconnect: the riddle is being
                                      re-posted, it is not new)
          force_ping=True          -> ping regardless
                                      ("Post Now" / "Turn ON" are explicit
                                      manager actions)

        first_posted_at is preserved via COALESCE in set_riddle_post_ref, so a
        refresh/edit resets neither the rotation countdown, the submit grace
        period, nor the timing display.
        """
        slot1 = await self.repo.get_open_slot1(guild_id)
        if not slot1:
            return "no_slot1"
        guild = self.bot.get_guild(guild_id)
        ch = await self.resolve_channel(RIDDLE_CHANNEL_ID)
        if ch is None or not hasattr(ch, "send"):
            return "no_channel"

        rid = to_int(slot1.get("id"), 0)
        existing = await self.fetch_message_safe(slot1.get("posted_channel_id"),
                                                 slot1.get("posted_message_id"))

        # Undeterminable state: never post a second copy, retry next tick.
        if existing is UNKNOWN_MESSAGE and not force_repost:
            return "deferred_unknown_state"

        content = self.build_ping_content(guild, slot1.get("mention_role_ids"))

        try:
            if force_repost and (isinstance(existing, discord.Message)
                                 or existing is UNKNOWN_MESSAGE):
                await self.delete_message_ref(slot1.get("posted_channel_id"),
                                              slot1.get("posted_message_id"))
                existing = None

            # ---- EDIT path ----
            if isinstance(existing, discord.Message):
                locked = submit_is_locked(slot1)
                unlock = submit_unlock_iso(slot1)

                sig = self._render_signature(slot1, locked, content)
                if self._render_sig.get(guild_id) == sig:
                    # Nothing that affects rendering changed. Relative
                    # timestamps refresh client-side, so re-editing every tick
                    # is ~96 pointless API calls per guild per day.
                    if locked:
                        self.schedule_submit_unlock(guild_id, unlock)
                    return "unchanged"

                await existing.edit(
                    content=content,
                    embed=build_active_riddle_embed(guild, slot1),
                    view=SubmitButtonView(self, locked=locked, unlock_at=unlock),
                    # An edit never re-pings: Discord only notifies on the
                    # message that introduces a mention.
                    allowed_mentions=discord.AllowedMentions.none())
                self._render_sig[guild_id] = sig
                orphans = await self.repo.clear_other_open_post_refs(guild_id, rid)
                await self._delete_post_refs(orphans)
                if locked:
                    self.schedule_submit_unlock(guild_id, unlock)
                return "updated"

            # ---- FRESH POST path ----
            is_first_post = not slot1.get("first_posted_at")
            do_ping = force_ping or (ping_on_first_post and is_first_post)

            # first_posted_at may not be in the DB yet, so we mint the anchor
            # here and hand the SAME value to embed, button and DB write. That
            # keeps the "opens at" hint and the stored anchor perfectly in sync.
            post_ts = slot1.get("first_posted_at") or now_iso_utc()
            unlock = submit_unlock_iso(slot1, posted_at_override=post_ts)
            locked = submit_is_locked(slot1, posted_at_override=post_ts)

            msg = await ch.send(
                content=content,
                embed=build_active_riddle_embed(guild, slot1,
                                                posted_at_override=post_ts),
                view=SubmitButtonView(self, locked=locked, unlock_at=unlock),
                allowed_mentions=discord.AllowedMentions(
                    roles=do_ping, users=False, everyone=False))
            await self.repo.set_riddle_post_ref(rid, msg.channel.id, msg.id,
                                                first_posted_at=post_ts)
            fresh = await self.repo.get_open_slot1(guild_id) or slot1
            self._render_sig[guild_id] = self._render_signature(fresh, locked, content)
            orphans = await self.repo.clear_other_open_post_refs(guild_id, rid)
            await self._delete_post_refs(orphans)
            if locked:
                self.schedule_submit_unlock(guild_id, unlock)
            logger.info("Riddle %s posted (ping=%s, first_post=%s, locked_until=%s)",
                        rid, do_ping, is_first_post, unlock if locked else "-")
            return "posted"
        except discord.Forbidden:
            logger.error("Missing permissions to post in riddle channel %s",
                         RIDDLE_CHANNEL_ID)
            return "no_permission"
        except Exception:
            logger.exception("publish_slot1_post failed for guild %s", guild_id)
            return "error"

    async def force_repost_slot1_fresh(self, guild_id: int, *, ping: bool) -> str:
        async with self._lock(guild_id):
            return await self._force_repost_slot1_fresh_unlocked(guild_id, ping=ping)

    async def _force_repost_slot1_fresh_unlocked(self, guild_id: int, *, ping: bool) -> str:
        """
        Delete any existing Slot 1 post, reset its timer + refs, then publish a
        brand-new post. Used by 'Post Now' and 'Turn ON', so both the rotation
        countdown and the submit grace period really restart.
        """
        await self.normalize_after_structure_change(guild_id)
        slot1 = await self.repo.get_open_slot1(guild_id)
        if not slot1:
            return "no_slot1"
        rid = to_int(slot1.get("id"), 0)
        await self.delete_message_ref(slot1.get("posted_channel_id"),
                                      slot1.get("posted_message_id"))
        await self.repo.reset_riddle_post_state(rid)
        self._render_sig.pop(guild_id, None)
        return await self._publish_slot1_post_unlocked(
            guild_id, force_repost=False, force_ping=ping)

    async def enforce_enabled_state(self, guild_id: int, *, force_repost: bool = False,
                                    ping_on_first_post: bool = True) -> str:
        async with self._lock(guild_id):
            return await self._enforce_enabled_state_unlocked(
                guild_id, force_repost=force_repost,
                ping_on_first_post=ping_on_first_post)

    async def _enforce_enabled_state_unlocked(self, guild_id: int, *,
                                              force_repost: bool = False,
                                              ping_on_first_post: bool = True) -> str:
        """
        Reconcile Discord state with DB state:
          disabled              -> remove active posts
          hiatus active         -> remove active posts, do nothing
          hiatus expired        -> clear it, continue
          slot1 too old         -> AUTO-rotate (+XP bonus, rotation_count += 1)
          otherwise             -> publish / refresh

        The two rotate calls below are the ONLY places that pass an xp_bonus.
        """
        await self.normalize_after_structure_change(guild_id)

        if not await self.repo.is_enabled(guild_id):
            await self.remove_active_riddle_posts(guild_id)
            return "disabled"

        hiatus = await self.repo.get_hiatus_until(guild_id)
        if hiatus:
            if iso_in_future(hiatus):
                await self.remove_active_riddle_posts(guild_id)
                return "hiatus"
            await self.repo.set_hiatus_until(guild_id, None)
            logger.info("Hiatus expired for guild %s", guild_id)

        slot1 = await self.repo.get_open_slot1(guild_id)
        if not slot1:
            await self.repo.set_enabled(guild_id, False)
            await self.remove_active_riddle_posts(guild_id)
            logger.info("Guild %s auto-disabled: no open riddles left", guild_id)
            return "enabled_but_no_slot1"

        age_h = hours_since(slot1.get("first_posted_at"))
        if age_h is not None and age_h >= UNSOLVED_ROTATION_HOURS:
            rid_s1 = to_int(slot1.get("id"), 0)
            pending = await self.repo.count_pending_submissions_for_riddle(rid_s1)

            if pending == 0:
                logger.info("Auto-rotating riddle %s (age %.1fh, no pending votes)",
                            rid_s1, age_h)
                await self._rotate_riddle_to_end_unlocked(
                    guild_id, rid_s1, ping_new_slot1=True,
                    xp_bonus=UNSOLVED_ROTATION_XP_BONUS)
                return "auto_rotated"

            if age_h >= ROTATION_HARD_CAP_HOURS:
                # Safety valve: an un-voted submission must not block the queue
                # forever. Still counts as "unsolved", so the bonus applies.
                logger.warning(
                    "Hard-cap rotation: riddle %s age %.1fh with %s pending vote(s)",
                    rid_s1, age_h, pending)
                await self.post_rotation_warning(self.bot.get_guild(guild_id),
                                                 slot1, pending, age_h)
                await self._rotate_riddle_to_end_unlocked(
                    guild_id, rid_s1, ping_new_slot1=True,
                    xp_bonus=UNSOLVED_ROTATION_XP_BONUS)
                return "auto_rotated_forced"
            # else: within grace period - wait for managers to vote

        return await self._publish_slot1_post_unlocked(
            guild_id, force_repost=force_repost,
            ping_on_first_post=ping_on_first_post)

    # ==========================================================================
    # ROTATION / CLOSING
    # ==========================================================================
    async def rotate_riddle_to_end(self, guild_id: int, riddle_id: int, *,
                                   ping_new_slot1: bool, xp_bonus: int = 0) -> bool:
        """
        Public entry point. xp_bonus defaults to 0 - callers that represent a
        MANUAL move (admin panel) simply omit it and nothing is bumped.
        """
        async with self._lock(guild_id):
            return await self._rotate_riddle_to_end_unlocked(
                guild_id, riddle_id, ping_new_slot1=ping_new_slot1, xp_bonus=xp_bonus)

    async def _rotate_riddle_to_end_unlocked(self, guild_id: int, riddle_id: int, *,
                                             ping_new_slot1: bool,
                                             xp_bonus: int = 0) -> bool:
        """
        Move a riddle to the end of the open queue AND clean up all its Discord
        artifacts (posted message, wrong-answer posts, vote posts, pending
        submissions). Then publish the new Slot 1.

        xp_bonus > 0 marks this as an AUTOMATIC unsolved rotation:
        the riddle's XP is raised (capped at MAX_RIDDLE_XP) and rotation_count
        is incremented. The bump happens BEFORE the DB move, while the riddle
        is still status='open' - bump_riddle_xp_on_rotation only touches open
        rows, and it is atomic, so a crash mid-rotation cannot double-count.
        """
        r = await self.repo.get_riddle_by_id(guild_id, riddle_id)
        if not r or str(r.get("status")) != "open":
            return False

        guild = self.bot.get_guild(guild_id)

        # ---- XP bonus for going unsolved (automatic rotations only) ----
        bump: Optional[dict] = None
        if xp_bonus > 0:
            try:
                bump = await self.repo.bump_riddle_xp_on_rotation(
                    guild_id, riddle_id, bonus=xp_bonus, max_xp=MAX_RIDDLE_XP)
            except Exception:
                # A failed bump must never abort the rotation itself, otherwise
                # the queue would stall on a DB hiccup.
                logger.exception("XP bonus bump failed for riddle %s - rotating anyway",
                                 riddle_id)
            if bump:
                logger.info(
                    "Unsolved rotation bonus: riddle %s  xp %s -> %s (+%s)  "
                    "rotation_count=%s%s",
                    riddle_id, bump["old_xp"], bump["new_xp"], bump["gained"],
                    bump["rotation_count"], "  [CAPPED]" if bump.get("capped") else "")
                # Refresh the local snapshot so the announcement shows new values.
                r = await self.repo.get_riddle_by_id(guild_id, riddle_id) or r

        self.cancel_submit_unlock(guild_id)
        self._render_sig.pop(guild_id, None)
        await self.delete_message_ref(r.get("posted_channel_id"), r.get("posted_message_id"))
        await self.cleanup_wrong_posts_for_riddle(riddle_id)
        await self.cleanup_vote_messages_for_riddle(riddle_id)
        await self.repo.cancel_pending_for_riddle(riddle_id)

        ok = await self.repo.move_open_riddle_to_end(guild_id, riddle_id)

        if bump and to_int(bump.get("rotation_count"), 0) >= 1:
            await self.post_rotation_bonus(guild, r, bump)

        await self.normalize_after_structure_change(guild_id)
        if await self.repo.is_enabled(guild_id):
            # Slot 1 changed -> fresh post; first_posted_at is NULL, so
            # countdown, grace period and timing display all start over, and
            # the new riddle pings because it is its first post.
            await self._publish_slot1_post_unlocked(
                guild_id, force_repost=True, ping_on_first_post=ping_new_slot1)
        return ok

    async def close_and_cleanup_riddle(self, guild_id: int, riddle_id: int,
                                       closed_by: int) -> bool:
        async with self._lock(guild_id):
            return await self._close_and_cleanup_riddle_unlocked(
                guild_id, riddle_id, closed_by)

    async def _close_and_cleanup_riddle_unlocked(self, guild_id: int, riddle_id: int,
                                                 closed_by: int) -> bool:
        r = await self.repo.get_riddle_by_id(guild_id, riddle_id)
        if not r or str(r.get("status")) != "open":
            return False
        self.cancel_submit_unlock(guild_id)
        self._render_sig.pop(guild_id, None)
        await self.delete_message_ref(r.get("posted_channel_id"), r.get("posted_message_id"))
        await self.cleanup_wrong_posts_for_riddle(riddle_id)
        await self.cleanup_vote_messages_for_riddle(riddle_id)
        await self.repo.cancel_pending_for_riddle(riddle_id, closed_by)
        closed = await self.repo.close_open_riddle_by_id(guild_id, riddle_id, closed_by)
        if closed is not None:
            logger.info("Riddle %s closed by %s", riddle_id, closed_by)
        return closed is not None

    async def close_active_and_disable(self, guild_id: int, riddle_id: int,
                                       closed_by: int) -> bool:
        """
        Close Slot 1 AND turn the system off, in that order, under ONE lock.

        Doing it as two separate locked calls left a window in which the system
        was still enabled but slot 1 had just been freed: the worker could grab
        the lock in between, see an enabled guild with a fresh slot 1, and post
        the next riddle - with a role ping - only for the caller to delete it a
        moment later. A ghost post plus a ping nobody wanted.
        """
        async with self._lock(guild_id):
            await self.repo.set_enabled(guild_id, False)
            await self.repo.set_hiatus_until(guild_id, None)
            ok = await self._close_and_cleanup_riddle_unlocked(guild_id, riddle_id, closed_by)
            for row in await self.repo.list_open_post_refs(guild_id):
                await self.delete_message_ref(row.get("posted_channel_id"),
                                              row.get("posted_message_id"))
            await self.repo.clear_all_open_post_refs(guild_id, reset_timer=False)
            self._render_sig.pop(guild_id, None)
            return ok

    async def disable_and_clear(self, guild_id: int):
        """Turn the system OFF and remove the active post, atomically."""
        async with self._lock(guild_id):
            await self.repo.set_enabled(guild_id, False)
            await self.repo.set_hiatus_until(guild_id, None)
            await self.remove_active_riddle_posts(guild_id)

    async def enable_and_post(self, guild_id: int) -> str:
        """Turn ON + fresh post + explicit ping, atomically."""
        async with self._lock(guild_id):
            if not await self.repo.get_open_slot1(guild_id):
                return "no_slot1"
            await self.repo.set_enabled(guild_id, True)
            await self.repo.set_hiatus_until(guild_id, None)
            return await self._force_repost_slot1_fresh_unlocked(guild_id, ping=True)

    # ==========================================================================
    # SLOT CONTENT MUTATIONS  (locked - used by the admin panel)
    # ==========================================================================
    # The repo lock protects one SQL statement. It does NOT protect the
    # read-modify-publish sequence the worker runs. If the panel writes while
    # the worker sits between "read slot1" and "send message", the worker
    # publishes stale content and then clears post refs by an id that has since
    # moved to a different slot. So: same guild lock as everything else.

    async def save_slot_content(self, guild_id: int, user_id: int, slot_no: int,
                                riddle_id: Optional[int], text: str, solution: str,
                                xp: int) -> tuple[bool, str]:
        """Returns (ok, reason). reason in: saved | conflict | empty | error."""
        async with self._lock(guild_id):
            try:
                if riddle_id:
                    changed = await self.repo.update_open_riddle_content_by_id(
                        guild_id, riddle_id, user_id, text, solution, xp)
                    if not changed:
                        return False, "conflict"
                else:
                    rid = await self.repo.upsert_slot_content(
                        guild_id=guild_id, user_id=user_id, slot_no=slot_no,
                        text=text, solution=solution, xp=xp)
                    if not rid:
                        return False, "empty"
            except Exception:
                logger.exception("save_slot_content failed (guild=%s slot=%s)",
                                 guild_id, slot_no)
                return False, "error"

            self._render_sig.pop(guild_id, None)
            await self.normalize_after_structure_change(guild_id)
            if await self.repo.is_enabled(guild_id):
                await self._enforce_enabled_state_unlocked(
                    guild_id, force_repost=False, ping_on_first_post=True)
            return True, "saved"

    async def save_slot_images(self, guild_id: int, riddle_id: int, user_id: int,
                               image_url: Optional[str],
                               solution_url: Optional[str]) -> tuple[bool, str]:
        async with self._lock(guild_id):
            try:
                good = await self.repo.set_riddle_images_by_id_open(
                    guild_id, riddle_id, image_url, solution_url, user_id)
            except Exception:
                logger.exception("save_slot_images failed (riddle=%s)", riddle_id)
                return False, "error"
            if not good:
                return False, "conflict"
            self._render_sig.pop(guild_id, None)
            if await self.repo.is_enabled(guild_id):
                await self._enforce_enabled_state_unlocked(
                    guild_id, force_repost=False, ping_on_first_post=True)
            return True, "saved"

    async def save_slot_mentions(self, guild_id: int, riddle_id: int, user_id: int,
                                 csv: Optional[str]) -> tuple[bool, str]:
        async with self._lock(guild_id):
            try:
                good = await self.repo.set_riddle_mentions_by_id_open(
                    guild_id, riddle_id, csv, user_id)
            except Exception:
                logger.exception("save_slot_mentions failed (riddle=%s)", riddle_id)
                return False, "error"
            if not good:
                return False, "conflict"
            self._render_sig.pop(guild_id, None)
            if await self.repo.is_enabled(guild_id):
                await self._enforce_enabled_state_unlocked(
                    guild_id, force_repost=False, ping_on_first_post=True)
            return True, "saved"

    async def disable_for_structure_change(self, guild_id: int) -> bool:
        """
        Auto-OFF before a structural edit, so no post churn happens while an
        admin reorganises. Returns True if the system WAS on.
        NOTE: caller must not hold the guild lock.
        """
        async with self._lock(guild_id):
            if not await self.repo.is_enabled(guild_id):
                return False
            await self.repo.set_enabled(guild_id, False)
            await self.repo.set_hiatus_until(guild_id, None)
            await self.remove_active_riddle_posts(guild_id)
            return True

    # ==========================================================================
    # PENDING VOTE RECOVERY
    # ==========================================================================
    async def repost_pending_votes(self):
        """
        Idempotent: a vote message that still exists is left alone. Without this
        check every reconnect multiplied the vote messages, and the stale copies
        produced confusing "no longer tracked" errors for managers.
        """
        rows = await self.repo.pending_open_submissions()
        if not rows:
            return
        vote_channel = await self.resolve_channel(VOTE_CHANNEL_ID)
        if vote_channel is None or not hasattr(vote_channel, "send"):
            logger.warning("Vote channel unavailable - cannot restore pending votes")
            return

        restored = kept = 0
        for row in rows:
            gid = to_int(row.get("guild_id"), 0)
            if not guild_is_served(gid):
                continue
            existing_mid = safe_int(row.get("vote_message_id"), None)
            if existing_mid:
                found = await self.fetch_message_safe(VOTE_CHANNEL_ID, existing_mid)
                if found is not None:  # Message or UNKNOWN -> assume it lives
                    kept += 1
                    continue

            guild = self.bot.get_guild(gid)
            uid = to_int(row.get("user_id"), 0)
            _, uname, uavatar = await self.resolve_user_label(guild, uid)
            riddle_view = {
                "text": row.get("riddle_text"),
                "solution": row.get("solution"),
                "xp": row.get("xp"),
                "image_url": row.get("image_url"),
                "riddle_no": row.get("riddle_no"),
                "first_posted_at": row.get("first_posted_at"),
                "rotation_count": row.get("rotation_count"),
            }
            embed = build_vote_embed(guild, riddle_view, uid, uname, uavatar,
                                     row.get("answer") or "")
            try:
                vm = await vote_channel.send(embed=embed, view=VoteButtons(self))
                await self.repo.set_submission_vote_message(
                    to_int(row["submission_id"], 0), vm.id)
                restored += 1
            except Exception:
                logger.exception("Failed to restore vote message for submission %s",
                                 row.get("submission_id"))
        if restored or kept:
            logger.info("Pending votes: %s kept, %s reposted", kept, restored)

    # ==========================================================================
    # SOLVED FLOW  (Correct)
    # ==========================================================================
    async def finalize_correct(self, guild: discord.Guild, ctx: dict,
                               moderator: discord.Member):
        async with self._lock(guild.id):
            await self._finalize_correct_unlocked(guild, ctx, moderator)

    async def _finalize_correct_unlocked(self, guild: discord.Guild, ctx: dict,
                                         moderator: discord.Member):
        """
        1) DB state first: XP, solved counter, slot geometry, solved hiatus
        2) Delete the original riddle post
        3) Delete all wrong-answer posts for this riddle
        4) Post the FRESH BIG solved post (no pings) incl. timing + rotations
        5) Post the SMALL ping post (Base + Extras + Winner)
        6) Clean up the remaining vote messages
        7) XP reminder into the VOTE channel

        Step 1 comes first on purpose: if a Discord call later fails we lose a
        cosmetic post, not the reward - and the worker will not immediately
        re-post the next riddle, because the hiatus is already committed.
        The system deliberately stays ON after a solve.
        """
        gid = guild.id
        rid = to_int(ctx.get("riddle_id"), 0)
        sid = to_int(ctx.get("submission_id"), 0)
        solver_id = to_int(ctx.get("solver_user_id"), 0)
        xp_gain = max(0, to_int(ctx.get("xp_gain"), 0))
        submitted_answer = str(ctx.get("answer") or "")

        self.cancel_submit_unlock(gid)
        self._render_sig.pop(gid, None)

        # Reload the riddle for the freshest fields; ctx fills the gaps.
        riddle_row = await self.repo.get_riddle_by_id(gid, rid) or {}
        for k in ("text", "solution", "xp", "base_xp", "rotation_count", "image_url",
                  "solution_url", "riddle_no", "posted_channel_id", "posted_message_id",
                  "mention_role_ids", "first_posted_at"):
            if riddle_row.get(k) in (None, "", 0) and ctx.get(k) is not None:
                riddle_row[k] = ctx.get(k)

        # --- TIMING ---
        # "Solved" is the moment the winning answer was SUBMITTED, not the moment
        # a manager pressed the button. Otherwise moderator response time would
        # show up as riddle difficulty. riddles.solved_at keeps the vote time for
        # audits, which is why solved_at must NOT be filled from the DB row here.
        riddle_row["solved_at"] = ctx.get("submitted_at") or ctx.get("solved_at")
        riddle_row["voted_at"] = ctx.get("voted_at") or ctx.get("solved_at")

        solver_mention, solver_name, solver_avatar = await self.resolve_user_label(
            guild, solver_id)

        # Single exclusion check instead of three separate lookups.
        is_excluded = (solver_id <= 0) or await self.user_is_excluded(guild, solver_id)

        rot = max(0, to_int(riddle_row.get("rotation_count"), 0))
        logger.info(
            "Riddle %s (No.%s) solved by %s (%s) - %s XP - rotations=%s - approved by "
            "%s (%s) - excluded=%s - posted=%s submitted=%s voted=%s",
            rid, riddle_row.get("riddle_no"), solver_id, solver_name, xp_gain, rot,
            moderator.id, moderator, is_excluded,
            riddle_row.get("first_posted_at"), ctx.get("submitted_at"), ctx.get("voted_at"))

        # ---- 1) DB STATE FIRST ----
        try:
            if not is_excluded:
                await self.repo.apply_solve_xp(gid, solver_id, xp_gain)
                await self.repo.inc_cached_solved_total(gid, 1)
            else:
                await self.rebuild_cached_solved_total_for_guild(gid)

            await self.normalize_after_structure_change(gid)
            await self.repo.clear_all_open_post_refs(gid, reset_timer=False)
            if await self.repo.is_enabled(gid) and SOLVED_HIATUS_HOURS > 0:
                await self.repo.set_hiatus_until(gid, iso_utc_in_hours(SOLVED_HIATUS_HOURS))
        except Exception:
            logger.exception("CRITICAL: DB state update failed after approving "
                             "submission %s (riddle %s)", sid, rid)
            raise

        # ---- 2) delete the original riddle post ----
        await self.delete_message_ref(riddle_row.get("posted_channel_id"),
                                      riddle_row.get("posted_message_id"))
        await self.repo.clear_stale_posted_refs(rid)

        # ---- 3) delete all wrong-answer posts ----
        await self.cleanup_wrong_posts_for_riddle(rid)

        ch = await self.resolve_channel(RIDDLE_CHANNEL_ID)
        if ch is not None and hasattr(ch, "send"):
            # ---- 4) fresh big solved post (no pings) ----
            try:
                fresh_msg = await ch.send(
                    embed=build_fresh_solved_post_embed(
                        guild, riddle_row, solver_mention, solver_name,
                        solver_avatar, submitted_answer),
                    allowed_mentions=discord.AllowedMentions.none())
                await self.repo.set_solved_post_ref(rid, fresh_msg.channel.id, fresh_msg.id)
            except Exception:
                logger.exception("Failed to post fresh solved message")

            # ---- 5) small ping post (Base + Extras + Winner) ----
            try:
                role_ping = self.build_ping_content(guild,
                                                    riddle_row.get("mention_role_ids"))
                ping_content = " ".join(p for p in (role_ping, solver_mention) if p) or None
                await ch.send(
                    content=ping_content,
                    embed=build_solved_ping_post_embed(
                        guild, riddle_row, solver_mention, solver_avatar,
                        submitted_answer=submitted_answer),
                    allowed_mentions=discord.AllowedMentions(
                        users=True, roles=True, everyone=False))
            except Exception:
                logger.exception("Failed to post solved ping message")

        # ---- 6) clean up remaining vote messages ----
        # The winning submission's vote post is kept and then annotated with
        # "Approved by ..." by the button handler.
        await self.cleanup_vote_messages_for_riddle(rid, exclude_submission_id=sid)

        # ---- 7) XP reminder ----
        if not is_excluded and xp_gain > 0:
            await self.post_xp_reminder_to_vote_channel(
                guild, solver_id, xp_gain,
                to_int(riddle_row.get("riddle_no"), 0), riddle_row)

    # ==========================================================================
    # WRONG FLOW
    # ==========================================================================
    async def finalize_wrong(self, guild: discord.Guild, ctx: dict,
                             moderator: discord.Member):
        async with self._lock(guild.id):
            await self._finalize_wrong_unlocked(guild, ctx, moderator)

    async def _finalize_wrong_unlocked(self, guild: discord.Guild, ctx: dict,
                                       moderator: discord.Member):
        """
        Riddle stays OPEN. Public wrong-answer post in the riddle channel, only
        the submitter is pinged. The message id is stored so it can be removed
        at solve time or on rotation.
        """
        gid = guild.id
        rid = to_int(ctx.get("riddle_id"), 0)
        submitter_id = to_int(ctx.get("solver_user_id"), 0)

        riddle_row = await self.repo.get_riddle_by_id(gid, rid) or {}
        for k in ("text", "xp", "image_url", "riddle_no", "mention_role_ids",
                  "first_posted_at", "rotation_count"):
            if riddle_row.get(k) in (None, "", 0) and ctx.get(k) is not None:
                riddle_row[k] = ctx.get(k)

        logger.info("Riddle %s: answer by %s rejected by %s (%s)",
                    rid, submitter_id, moderator.id, moderator)

        mention, name, avatar = await self.resolve_user_label(guild, submitter_id)
        ch = await self.resolve_channel(RIDDLE_CHANNEL_ID)
        if ch is None or not hasattr(ch, "send"):
            return

        try:
            sent = await ch.send(
                content=mention or None,
                embed=build_wrong_post_embed(guild, riddle_row, mention, name, avatar,
                                             str(ctx.get("answer") or "")),
                allowed_mentions=discord.AllowedMentions(
                    users=True, roles=False, everyone=False))
            await self.repo.add_wrong_post(gid, rid, sent.channel.id, sent.id)
        except Exception:
            logger.exception("Failed to post wrong-answer message")

    # ==========================================================================
    # STARTUP
    # ==========================================================================
    async def startup_rebuild(self):
        gids: set[int] = set(await self.repo.list_all_guild_ids())
        gids.update(g.id for g in self.bot.guilds if guild_is_served(g.id))

        for gid in gids:
            await self.repo.ensure_guild_state(gid)

        for g in self.bot.guilds:
            if guild_is_served(g.id):
                await self.ensure_guild_chunked(g)

        for gid in gids:
            await self.rebuild_cached_solved_total_for_guild(gid)
            await self.normalize_after_structure_change(gid)

        await self.delete_button_messages_in_channel(
            RIDDLE_CHANNEL_ID, {SUBMIT_BUTTON_ID}, limit=200)
        await self.delete_button_messages_in_channel(
            VOTE_CHANNEL_ID, {VOTE_UP_BUTTON_ID, VOTE_DOWN_BUTTON_ID}, limit=200)

        # reset_timer=False is the important bit. The messages are gone and get
        # re-posted below, but first_posted_at must SURVIVE: it is the anchor
        # for the unsolved-rotation countdown. Wiping it on every boot meant a
        # bot restarting more often than RIDDLE_UNSOLVED_ROTATION_HOURS would
        # never rotate anything and never pay the unsolved bonus - silently.
        await self.repo.clear_all_open_post_refs(None, reset_timer=False)
        self._render_sig.clear()
        await self.repo.reset_pending_vote_refs()
        await self.repo.cancel_pending_for_non_open()

        for gid in gids:
            # ping_on_first_post=False: a restart re-posts an existing riddle,
            # that is not news worth pinging the whole role for.
            res = await self.enforce_enabled_state(gid, force_repost=True,
                                                   ping_on_first_post=False)
            logger.info("Startup reconcile guild %s -> %s", gid, res)

        await self.repost_pending_votes()
        logger.info("Riddle startup rebuild complete (%s guild(s))", len(gids))

    # ==========================================================================
    # BACKGROUND WORKER
    # ==========================================================================
    async def _auto_worker(self):
        await self.bot.wait_until_ready()
        self._warn_if_multi_guild()
        if not self._startup_done:
            try:
                await self.startup_rebuild()
                self._startup_done = True
            except Exception:
                logger.exception("startup_rebuild failed - will retry next cycle")

        while not self.bot.is_closed():
            try:
                if not self._startup_done:
                    await self.startup_rebuild()
                    self._startup_done = True
                for gid in await self.repo.list_all_guild_ids():
                    await self.repo.ensure_guild_state(gid)
                    # handles: disabled, hiatus, rotation (+XP bonus),
                    # hard cap, refresh, and the post-hiatus ping
                    await self.enforce_enabled_state(gid, force_repost=False,
                                                     ping_on_first_post=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("auto worker cycle error")
            await asyncio.sleep(max(60, ROTATION_TICK_SECONDS))

    def _warn_if_multi_guild(self):
        """
        Channel and role IDs are global env vars, so only one guild can be
        served correctly. Without RIDDLE_GUILD_ID a second guild would post its
        riddles into the configured channel and the manager-role check would
        target the wrong server.
        """
        if RIDDLE_GUILD_ID > 0:
            if not any(g.id == RIDDLE_GUILD_ID for g in self.bot.guilds):
                logger.error(
                    "RIDDLE_GUILD_ID=%s but the bot is not a member of that guild. "
                    "The riddle system will do nothing.", RIDDLE_GUILD_ID)
            return
        if len(self.bot.guilds) > 1:
            logger.error(
                "Bot is in %s guilds but RIDDLE_GUILD_ID is not set. Channel/role "
                "config is global, so EVERY guild would post into channel %s and "
                "vote permissions would be checked against role %s. Set "
                "RIDDLE_GUILD_ID to pin the system to one server.",
                len(self.bot.guilds), RIDDLE_CHANNEL_ID, RIDDLE_MANAGER_ROLE_ID)

    # ==========================================================================
    # LISTENERS
    # ==========================================================================
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not guild_is_served(after.guild.id):
            return
        b = {r.id for r in before.roles}
        a = {r.id for r in after.roles}
        if b == a:
            return
        ex = self.excluded_role_ids()
        if (b & ex) != (a & ex):
            # Debounced so a mass role sync does not trigger N full rebuilds.
            self.schedule_stats_rebuild(after.guild.id)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        if not guild_is_served(guild.id):
            logger.warning("Joined guild %s (%s) - riddle system is pinned to %s and "
                           "will ignore it.", guild.id, guild.name, RIDDLE_GUILD_ID)
            return
        await self.repo.ensure_guild_state(guild.id)
        await self.ensure_guild_chunked(guild)

    @commands.Cog.listener()
    async def on_ready(self):
        """
        Fires on EVERY reconnect, not just the first start. Therefore:
          * no force_repost - otherwise the riddle jumps to the channel bottom
            on every hiccup (and its grace period would restart)
          * no ping - a reconnect is not a new riddle
          * repost_pending_votes is idempotent
        """
        if not self._startup_done:
            return  # first startup is owned by _auto_worker -> startup_rebuild
        try:
            # Re-register persistent views defensively (idempotent). The
            # unlocked variant is registered - Discord routes interactions by
            # custom_id, and the callback re-checks the lock against the DB.
            self.bot.add_view(SubmitButtonView(self))
            self.bot.add_view(VoteButtons(self))
            for gid in await self.repo.list_all_guild_ids():
                await self.enforce_enabled_state(gid, force_repost=False,
                                                 ping_on_first_post=False)
            await self.repost_pending_votes()
            logger.info("on_ready: reconciled active riddles + pending votes")
        except Exception:
            logger.exception("on_ready reconnect recovery failed")

    # ==========================================================================
    # LIFECYCLE
    # ==========================================================================
    async def cog_load(self):
        self.bot.add_view(SubmitButtonView(self))
        self.bot.add_view(VoteButtons(self))
        if self._auto_task is None or self._auto_task.done():
            self._auto_task = asyncio.create_task(self._auto_worker(),
                                                  name="riddle_auto_worker")

    async def cog_unload(self):
        """Await cancellation so no task touches the repo after it was closed."""
        tasks: list[asyncio.Task] = []
        if self._auto_task and not self._auto_task.done():
            self._auto_task.cancel()
            tasks.append(self._auto_task)
        for bucket in (self._stats_rebuild_tasks, self._unlock_tasks):
            for t in bucket.values():
                if not t.done():
                    t.cancel()
                    tasks.append(t)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._auto_task = None
        self._stats_rebuild_tasks.clear()
        self._unlock_tasks.clear()
        self._render_sig.clear()
        logger.info("RiddleCog unloaded, background tasks stopped.")

    # ==========================================================================
    # SLASH COMMANDS
    # ==========================================================================
    @app_commands.command(name="riddle", description="Open the main riddle admin panel.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle(self, interaction: Interaction):
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        gid = interaction.guild.id
        await self.repo.ensure_guild_state(gid)
        await self.normalize_after_structure_change(gid)

        panel = RiddleAdminPanelView(self, interaction.user.id, gid)
        await panel.refresh_data()
        await panel.rebuild_items()
        panel.message = await interaction.followup.send(
            embeds=await panel.build_embeds(),
            view=panel, ephemeral=True, wait=True,
            allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="riddle-champ",
                          description="Show the riddle champions leaderboard.")
    @app_commands.guild_only()
    @riddle_guild_only()
    async def riddle_champ(self, interaction: Interaction,
                           visible: Optional[bool] = False,
                           image: Optional[str] = None,
                           mention: Optional[Role] = None):
        """
        Public on purpose: players want to look up their own rank. Only the
        options that affect OTHER people stay manager-only.
        """
        if interaction.guild is None:
            return

        is_manager = (isinstance(interaction.user, discord.Member)
                      and any(r.id == RIDDLE_MANAGER_ROLE_ID
                              for r in interaction.user.roles))
        # Posting publicly / pinging a role is a manager action.
        if not is_manager:
            visible = False
            mention = None
            image = None

        await interaction.response.defer(ephemeral=not visible, thinking=True)

        entries_raw = await self.filtered_stats_entries_for_guild(interaction.guild)
        total_solved = await self.repo.get_cached_solved_total(interaction.guild.id)

        # Percentages must use the SAME population as the entries, otherwise a
        # stale cache lets a single player exceed 100%.
        sum_filtered = sum(s for _, s, _ in entries_raw)
        if sum_filtered > total_solved:
            total_solved = sum_filtered
            await self.repo.set_cached_solved_total(interaction.guild.id, total_solved)
        denom = sum_filtered or total_solved

        entries = [
            (uid, solved, (solved / denom * 100.0 if denom else 0.0), xp)
            for uid, solved, xp in entries_raw
        ]

        async def resolver(uid: int):
            return await self.resolve_user_label(interaction.guild, uid)

        view = ChampionsView(
            entries, total_solved, resolver,
            image if is_http_url(image) else DEFAULT_IMAGE_URL,
            interaction.user.id if not visible else None,
            highlight_user_id=interaction.user.id)
        view.message = await interaction.followup.send(
            content=mention.mention if (visible and mention) else None,
            embed=await view.build_embed(), view=view,
            ephemeral=not visible, wait=True,
            allowed_mentions=discord.AllowedMentions(
                roles=bool(visible and mention), users=False, everyone=False))

    # ==========================================================================
    # ERROR HANDLER
    # ==========================================================================
    async def cog_app_command_error(self, interaction: Interaction,
                                    error: app_commands.AppCommandError):
        if isinstance(error, MissingRiddleManagerRole):
            await send_access_denied(interaction)
            return
        if isinstance(error, WrongGuild):
            await send_wrong_guild(interaction)
            return
        logger.exception("Riddle command error: %s", error)
        with contextlib.suppress(discord.HTTPException, discord.NotFound):
            msg = "❌ Something went wrong running that command. Check the logs."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


# =============================================================================
# EXTENSION ENTRY POINTS
# =============================================================================
_repo: Optional[RiddleRepo] = None


async def setup(bot: commands.Bot):
    global _repo

    problems = validate_config()
    if problems:
        for p in problems:
            logger.error("RIDDLE CONFIG: %s", p)
        raise RuntimeError(
            "Riddle extension refuses to load - fix these config problems:\n  - "
            + "\n  - ".join(problems))

    if not bot.intents.members:
        logger.warning(
            "Members intent is disabled. Excluded-role filtering will use slow "
            "per-user REST lookups. Enable 'Server Members Intent'.")

    if RIDDLE_GUILD_ID <= 0:
        logger.warning(
            "RIDDLE_GUILD_ID is not set. Channel and role IDs are global, so the "
            "riddle system can only serve ONE guild correctly. Set RIDDLE_GUILD_ID "
            "if this bot is (or might be) in more than one server.")

    repo = RiddleRepo()
    await repo.start()
    try:
        await bot.add_cog(RiddleCog(bot, repo))
    except Exception:
        # Never leak an open DB connection if cog registration fails.
        await repo.close()
        raise
    _repo = repo
    logger.info(
        "Riddle extension loaded (guild=%s, slots=%s, submit delay=%smin, "
        "rotation=%sh, hard cap=%sh, rotation bonus=+%s XP up to %s, hiatus=%sh, "
        "tick=%ss)",
        RIDDLE_GUILD_ID or "ANY", MAX_RIDDLE_SLOTS, SUBMIT_DELAY_MINUTES,
        UNSOLVED_ROTATION_HOURS, ROTATION_HARD_CAP_HOURS, UNSOLVED_ROTATION_XP_BONUS,
        MAX_RIDDLE_XP, SOLVED_HIATUS_HOURS, ROTATION_TICK_SECONDS)


async def teardown(bot: commands.Bot):
    global _repo
    if _repo is not None:
        await _repo.close()
        _repo = None
    logger.info("Riddle extension unloaded.")