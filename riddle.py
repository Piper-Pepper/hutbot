# riddle.py
from __future__ import annotations

import asyncio
import contextlib
from typing import Optional

import discord
from discord import app_commands, Interaction, Role
from discord.ext import commands

from riddle_core import (
    RIDDLE_CHANNEL_ID, VOTE_CHANNEL_ID,
    RIDDLE_ROLE_ID, RIDDLE_MANAGER_ROLE_ID,
    EXCLUDED_COUNT_ROLE_ID, EXCLUDED_GAMEMASTER_ROLE_ID, EXTRA_EXCLUDED_ROLE_IDS_CSV,
    DEFAULT_IMAGE_URL, MAX_RIDDLE_SLOTS, MAX_EXTRA_PING_ROLES,
    UNSOLVED_ROTATION_HOURS, ROTATION_HARD_CAP_HOURS, SOLVED_HIATUS_HOURS,
    ROTATION_TICK_SECONDS, STATS_REBUILD_DEBOUNCE_SECONDS,
    SUBMIT_BUTTON_ID, VOTE_UP_BUTTON_ID, VOTE_DOWN_BUTTON_ID,
    UNKNOWN_MESSAGE, MessageLookup,
    logger, to_int, safe_int, is_http_url, unique_role_mentions, parse_csv_role_ids,
    iso_in_future, iso_utc_in_hours, hours_since,
    riddle_manager_required, send_access_denied, MissingRiddleManagerRole,
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
    rotating, closing, finalizing a vote) runs under a per-guild asyncio.Lock.
    Without it the background worker, on_ready reconnect recovery and admin
    panel clicks race each other and produce duplicate riddle posts with
    orphaned Submit buttons that nothing can clean up.

    asyncio.Lock is NOT reentrant, so every locked public method delegates to
    an `_*_unlocked` counterpart which internal callers use.
    """

    def __init__(self, bot: commands.Bot, repo: RiddleRepo):
        self.bot = bot
        self.repo = repo
        self._auto_task: Optional[asyncio.Task] = None
        self._startup_done = False
        self._guild_locks: dict[int, asyncio.Lock] = {}
        self._stats_rebuild_tasks: dict[int, asyncio.Task] = {}
        self._chunked_guilds: set[int] = set()

    # ==========================================================================
    # LOCKING
    # ==========================================================================
    def _lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._guild_locks.get(guild_id)
        if lock is None:
            lock = self._guild_locks[guild_id] = asyncio.Lock()
        return lock

    # ==========================================================================
    # EXCLUDED USERS / STATS FILTERING
    # ==========================================================================
    def excluded_role_ids(self) -> set[int]:
        s = {EXCLUDED_COUNT_ROLE_ID, EXCLUDED_GAMEMASTER_ROLE_ID, RIDDLE_MANAGER_ROLE_ID}
        for rid in parse_csv_role_ids(EXTRA_EXCLUDED_ROLE_IDS_CSV):
            if rid > 0:
                s.add(rid)
        s.discard(0)
        return s

    async def user_is_excluded(self, guild: discord.Guild, user_id: int, *,
                               allow_fetch: bool = True) -> bool:
        """
        allow_fetch=False avoids one REST call per user – mandatory inside loops.
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
        """Fill the member cache once so bulk filtering needs zero REST calls."""
        if guild.id in self._chunked_guilds:
            return
        self._chunked_guilds.add(guild.id)
        if not self.bot.intents.members:
            logger.warning(
                "Members intent is DISABLED – excluded-role filtering falls back to "
                "per-user REST lookups and stats rebuilds will be slow. Enable "
                "'Server Members Intent' in the developer portal.")
            return
        if guild.chunked:
            return
        try:
            await guild.chunk(cache=True)
            logger.info("Chunked guild %s (%s members)", guild.id, guild.member_count)
        except Exception:
            logger.exception("Failed to chunk guild %s", guild.id)

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
        Debounced rebuild. A role sync touching 500 members fires 500
        on_member_update events – we want exactly one rebuild, not 500.
        """
        existing = self._stats_rebuild_tasks.get(guild_id)
        if existing and not existing.done():
            return

        async def _run():
            try:
                await asyncio.sleep(delay)
                await self.rebuild_cached_solved_total_for_guild(guild_id)
                await self.sync_open_slot_numbers_for_guild(guild_id)
                logger.info("Debounced stats rebuild done for guild %s", guild_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Debounced stats rebuild failed for guild %s", guild_id)

        self._stats_rebuild_tasks[guild_id] = asyncio.create_task(
            _run(), name=f"riddle_stats_rebuild_{guild_id}")

    async def sync_open_slot_numbers_for_guild(self, guild_id: int):
        base = await self.repo.get_cached_solved_total(guild_id)
        await self.repo.sync_open_slot_numbers(guild_id, base)

    async def normalize_after_structure_change(self, guild_id: int):
        await self.repo.compact_open_slots(guild_id)
        await self.sync_open_slot_numbers_for_guild(guild_id)

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
          * discord.Message  – exists
          * None             – definitely gone (404)
          * UNKNOWN_MESSAGE  – undeterminable (permissions, 5xx, timeout)

        Callers MUST treat UNKNOWN_MESSAGE as "assume it still exists".
        The old blanket `except Exception: return None` turned every transient
        API hiccup into a duplicate post.
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
            logger.warning("No permission to fetch message %s/%s – assuming it EXISTS",
                           cid, mid)
            return UNKNOWN_MESSAGE
        except (discord.HTTPException, asyncio.TimeoutError):
            logger.warning("Transient error fetching message %s/%s – assuming it EXISTS",
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
            return True  # already gone – goal achieved
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
                return m.mention, str(m), m.display_avatar.url
        u = self.bot.get_user(uid)
        if u is None:
            with contextlib.suppress(discord.HTTPException, discord.NotFound):
                u = await self.bot.fetch_user(uid)
        if u:
            return u.mention, str(u), u.display_avatar.url
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
                                                custom_ids: set[str], limit: int = 400):
        """
        Legacy safety net for orphaned button messages created before per-guild
        locking existed. With locks + DB refs this should find nothing, so the
        history limit is kept modest instead of scanning thousands of messages
        on every boot.
        """
        ch = await self.resolve_channel(channel_id)
        me = self.bot.user
        if ch is None or not hasattr(ch, "history") or me is None:
            return
        removed = 0
        try:
            async for msg in ch.history(limit=limit):
                if msg.author.id != me.id:
                    continue
                if self._msg_has_custom_id(msg, custom_ids):
                    if await self.delete_message_ref(msg.channel.id, msg.id):
                        removed += 1
        except discord.Forbidden:
            logger.warning("No history permission in channel %s – skipping orphan sweep",
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
        await self.repo.clear_all_open_post_refs(guild_id)

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

    # ==========================================================================
    # POSTING  (locked public / unlocked internal)
    # ==========================================================================
    async def publish_slot1_post(self, guild_id: int, *, force_repost: bool,
                                 allow_role_ping: bool) -> str:
        async with self._lock(guild_id):
            return await self._publish_slot1_post_unlocked(
                guild_id, force_repost=force_repost, allow_role_ping=allow_role_ping)

    async def _publish_slot1_post_unlocked(self, guild_id: int, *, force_repost: bool,
                                           allow_role_ping: bool) -> str:
        """
        Publish or edit the Slot 1 riddle. Does NOT evaluate hiatus/rotation –
        that is enforce_enabled_state's job. Slot normalisation is the caller's
        responsibility (avoids doing it twice per tick).

        first_posted_at is preserved via COALESCE in set_riddle_post_ref, so a
        refresh/edit does not reset the countdown or the timing display.
        """
        slot1 = await self.repo.get_open_slot1(guild_id)
        if not slot1:
            return "no_slot1"
        guild = self.bot.get_guild(guild_id)
        ch = await self.resolve_channel(RIDDLE_CHANNEL_ID)
        if ch is None or not hasattr(ch, "send"):
            return "no_channel"

        rid = to_int(slot1.get("id"), 0)
        embed = build_active_riddle_embed(guild, slot1)
        content = self.build_ping_content(guild, slot1.get("mention_role_ids"))
        existing = await self.fetch_message_safe(slot1.get("posted_channel_id"),
                                                 slot1.get("posted_message_id"))

        # Undeterminable state: never post a second copy, retry next tick.
        if existing is UNKNOWN_MESSAGE and not force_repost:
            return "deferred_unknown_state"

        try:
            if force_repost and (isinstance(existing, discord.Message)
                                 or existing is UNKNOWN_MESSAGE):
                await self.delete_message_ref(slot1.get("posted_channel_id"),
                                              slot1.get("posted_message_id"))
                existing = None

            if isinstance(existing, discord.Message):
                await existing.edit(
                    content=content, embed=embed, view=SubmitButtonView(self),
                    allowed_mentions=discord.AllowedMentions(
                        roles=allow_role_ping, users=False, everyone=False))
                await self.repo.clear_other_open_post_refs(guild_id, rid)
                return "updated"

            msg = await ch.send(
                content=content, embed=embed, view=SubmitButtonView(self),
                allowed_mentions=discord.AllowedMentions(
                    roles=allow_role_ping, users=False, everyone=False))
            await self.repo.set_riddle_post_ref(rid, msg.channel.id, msg.id)
            await self.repo.clear_other_open_post_refs(guild_id, rid)
            return "posted"
        except discord.Forbidden:
            logger.error("Missing permissions to post in riddle channel %s",
                         RIDDLE_CHANNEL_ID)
            return "no_permission"
        except Exception:
            logger.exception("publish_slot1_post failed for guild %s", guild_id)
            return "error"

    async def force_repost_slot1_fresh(self, guild_id: int, *, allow_ping: bool) -> str:
        async with self._lock(guild_id):
            return await self._force_repost_slot1_fresh_unlocked(
                guild_id, allow_ping=allow_ping)

    async def _force_repost_slot1_fresh_unlocked(self, guild_id: int, *,
                                                 allow_ping: bool) -> str:
        """
        Delete any existing Slot 1 post, reset its timer + refs, then publish a
        brand-new post. Used by 'Post Now' and 'Turn ON' so the countdown really
        restarts.
        """
        await self.normalize_after_structure_change(guild_id)
        slot1 = await self.repo.get_open_slot1(guild_id)
        if not slot1:
            return "no_slot1"
        rid = to_int(slot1.get("id"), 0)
        await self.delete_message_ref(slot1.get("posted_channel_id"),
                                      slot1.get("posted_message_id"))
        await self.repo.reset_riddle_post_state(rid)
        return await self._publish_slot1_post_unlocked(
            guild_id, force_repost=False, allow_role_ping=allow_ping)

    async def enforce_enabled_state(self, guild_id: int, *, allow_ping: bool,
                                    force_repost: bool = False) -> str:
        async with self._lock(guild_id):
            return await self._enforce_enabled_state_unlocked(
                guild_id, allow_ping=allow_ping, force_repost=force_repost)

    async def _enforce_enabled_state_unlocked(self, guild_id: int, *, allow_ping: bool,
                                              force_repost: bool = False) -> str:
        """
        Reconcile Discord state with DB state:
          disabled              -> remove active posts
          hiatus active         -> remove active posts, do nothing
          hiatus expired        -> clear it, continue
          slot1 age >= rotation -> auto-rotate (hard cap overrides pending votes)
          otherwise             -> publish / refresh
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
                await self._rotate_riddle_to_end_unlocked(guild_id, rid_s1,
                                                           ping_new_slot1=True)
                logger.info("Auto-rotated riddle %s (age %.1fh, no pending votes)",
                            rid_s1, age_h)
                return "auto_rotated"
            if age_h >= ROTATION_HARD_CAP_HOURS:
                # Safety valve: an un-voted submission must not block the queue
                # forever.
                logger.warning(
                    "Hard-cap rotation: riddle %s age %.1fh with %s pending vote(s)",
                    rid_s1, age_h, pending)
                await self.post_rotation_warning(self.bot.get_guild(guild_id),
                                                  slot1, pending, age_h)
                await self._rotate_riddle_to_end_unlocked(guild_id, rid_s1,
                                                           ping_new_slot1=True)
                return "auto_rotated_forced"
            # else: within grace period – wait for managers to vote

        return await self._publish_slot1_post_unlocked(
            guild_id, force_repost=force_repost, allow_role_ping=allow_ping)

    # ==========================================================================
    # ROTATION / CLOSING
    # ==========================================================================
    async def rotate_riddle_to_end(self, guild_id: int, riddle_id: int, *,
                                   ping_new_slot1: bool) -> bool:
        async with self._lock(guild_id):
            return await self._rotate_riddle_to_end_unlocked(
                guild_id, riddle_id, ping_new_slot1=ping_new_slot1)

    async def _rotate_riddle_to_end_unlocked(self, guild_id: int, riddle_id: int, *,
                                             ping_new_slot1: bool) -> bool:
        """
        Move a riddle to the end of the open queue AND clean up all its Discord
        artifacts (posted message, wrong-answer posts, vote posts, pending
        submissions). Then publish the new Slot 1.
        """
        r = await self.repo.get_riddle_by_id(guild_id, riddle_id)
        if not r or str(r.get("status")) != "open":
            return False

        await self.delete_message_ref(r.get("posted_channel_id"), r.get("posted_message_id"))
        await self.cleanup_wrong_posts_for_riddle(riddle_id)
        await self.cleanup_vote_messages_for_riddle(riddle_id)
        await self.repo.cancel_pending_for_riddle(riddle_id)

        ok = await self.repo.move_open_riddle_to_end(guild_id, riddle_id)

        await self.normalize_after_structure_change(guild_id)
        if await self.repo.is_enabled(guild_id):
            # Slot 1 changed -> definitely a fresh post; first_posted_at is NULL
            # so the countdown and the timing display start over.
            await self._publish_slot1_post_unlocked(
                guild_id, force_repost=True, allow_role_ping=ping_new_slot1)
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
        await self.delete_message_ref(r.get("posted_channel_id"), r.get("posted_message_id"))
        await self.cleanup_wrong_posts_for_riddle(riddle_id)
        await self.cleanup_vote_messages_for_riddle(riddle_id)
        await self.repo.cancel_pending_for_riddle(riddle_id, closed_by)
        closed = await self.repo.close_open_riddle_by_id(guild_id, riddle_id, closed_by)
        if closed is not None:
            logger.info("Riddle %s closed by %s", riddle_id, closed_by)
        return closed is not None

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
            logger.warning("Vote channel unavailable – cannot restore pending votes")
            return

        restored = kept = 0
        for row in rows:
            existing_mid = safe_int(row.get("vote_message_id"), None)
            if existing_mid:
                found = await self.fetch_message_safe(VOTE_CHANNEL_ID, existing_mid)
                if found is not None:  # Message or UNKNOWN -> assume it lives
                    kept += 1
                    continue

            guild = self.bot.get_guild(to_int(row.get("guild_id"), 0))
            uid = to_int(row.get("user_id"), 0)
            _, uname, uavatar = await self.resolve_user_label(guild, uid)
            riddle_view = {
                "text": row.get("riddle_text"),
                "solution": row.get("solution"),
                "xp": row.get("xp"),
                "image_url": row.get("image_url"),
                "riddle_no": row.get("riddle_no"),
                "first_posted_at": row.get("first_posted_at"),
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
    # SOLVED FLOW  (👍 Correct)
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
        4) Post the FRESH BIG solved post (no pings) incl. timing
        5) Post the SMALL ping post (Base + Extras + Winner)
        6) Clean up the remaining vote messages
        7) XP reminder into the VOTE channel

        Step 1 comes first on purpose: if a Discord call later fails we lose a
        cosmetic post, not the reward – and the worker will not immediately
        re-post the next riddle, because the hiatus is already committed.
        The system deliberately stays ON after a solve.
        """
        gid = guild.id
        rid = to_int(ctx.get("riddle_id"), 0)
        sid = to_int(ctx.get("submission_id"), 0)
        solver_id = to_int(ctx.get("solver_user_id"), 0)
        xp_gain = max(0, to_int(ctx.get("xp_gain"), 0))
        submitted_answer = str(ctx.get("answer") or "")

        # Reload the riddle so we have the freshest fields; ctx fills the gaps.
        riddle_row = await self.repo.get_riddle_by_id(gid, rid) or {}
        for k in ("text", "solution", "xp", "image_url", "solution_url", "riddle_no",
                  "posted_channel_id", "posted_message_id", "mention_role_ids",
                  "first_posted_at", "solved_at"):
            if not riddle_row.get(k):
                riddle_row[k] = ctx.get(k)
        # Timing: first_posted_at survives the solve because approve_submission
        # only flips status to 'solved' and clear_all_open_post_refs touches
        # status='open' rows only.
        riddle_row["solved_at"] = riddle_row.get("solved_at") or ctx.get("solved_at")

        solver_mention, solver_name, solver_avatar = await self.resolve_user_label(
            guild, solver_id)

        # Single exclusion check instead of three separate lookups.
        is_excluded = (solver_id <= 0) or await self.user_is_excluded(guild, solver_id)

        logger.info(
            "Riddle %s (No.%s) solved by %s (%s) – %s XP – approved by %s (%s) – "
            "excluded=%s – posted=%s solved=%s",
            rid, riddle_row.get("riddle_no"), solver_id, solver_name, xp_gain,
            moderator.id, moderator, is_excluded,
            riddle_row.get("first_posted_at"), riddle_row.get("solved_at"))

        # ---- 1) DB STATE FIRST ----
        try:
            if not is_excluded:
                await self.repo.apply_solve_xp(gid, solver_id, xp_gain)
                await self.repo.inc_cached_solved_total(gid, 1)
            else:
                await self.rebuild_cached_solved_total_for_guild(gid)

            await self.normalize_after_structure_change(gid)
            await self.repo.clear_all_open_post_refs(gid)
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
        await self.cleanup_vote_messages_for_riddle(rid, exclude_submission_id=sid)

        # ---- 7) XP reminder ----
        if not is_excluded and xp_gain > 0:
            await self.post_xp_reminder_to_vote_channel(
                guild, solver_id, xp_gain,
                to_int(riddle_row.get("riddle_no"), 0), riddle_row)

    # ==========================================================================
    # WRONG FLOW  (👎 Wrong)
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
                  "first_posted_at"):
            if not riddle_row.get(k):
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
        gids.update(g.id for g in self.bot.guilds)

        for gid in gids:
            await self.repo.ensure_guild_state(gid)

        for g in self.bot.guilds:
            await self.ensure_guild_chunked(g)

        for gid in gids:
            await self.rebuild_cached_solved_total_for_guild(gid)
            await self.normalize_after_structure_change(gid)

        await self.delete_button_messages_in_channel(
            RIDDLE_CHANNEL_ID, {SUBMIT_BUTTON_ID}, limit=200)
        await self.delete_button_messages_in_channel(
            VOTE_CHANNEL_ID, {VOTE_UP_BUTTON_ID, VOTE_DOWN_BUTTON_ID}, limit=200)

        await self.repo.clear_all_open_post_refs(None)
        await self.repo.reset_pending_vote_refs()
        await self.repo.cancel_pending_for_non_open()

        for gid in gids:
            # enforce_enabled_state respects hiatus + rotation
            res = await self.enforce_enabled_state(gid, allow_ping=False, force_repost=True)
            logger.info("Startup reconcile guild %s -> %s", gid, res)

        await self.repost_pending_votes()
        logger.info("Riddle startup rebuild complete (%s guild(s))", len(gids))

    # ==========================================================================
    # BACKGROUND WORKER
    # ==========================================================================
    async def _auto_worker(self):
        await self.bot.wait_until_ready()
        if not self._startup_done:
            try:
                await self.startup_rebuild()
                self._startup_done = True
            except Exception:
                logger.exception("startup_rebuild failed – will retry next cycle")

        while not self.bot.is_closed():
            try:
                if not self._startup_done:
                    await self.startup_rebuild()
                    self._startup_done = True
                for gid in await self.repo.list_all_guild_ids():
                    await self.repo.ensure_guild_state(gid)
                    # handles: disabled, hiatus, rotation, hard cap, refresh
                    await self.enforce_enabled_state(gid, allow_ping=False,
                                                     force_repost=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("auto worker cycle error")
            await asyncio.sleep(max(60, ROTATION_TICK_SECONDS))

    # ==========================================================================
    # LISTENERS
    # ==========================================================================
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
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
        await self.repo.ensure_guild_state(guild.id)
        await self.ensure_guild_chunked(guild)

    @commands.Cog.listener()
    async def on_ready(self):
        """
        Fires on EVERY reconnect, not just the first start. Therefore:
          * no force_repost – otherwise the riddle jumps to the channel bottom
            on every connection hiccup
          * repost_pending_votes is idempotent
        """
        if not self._startup_done:
            return  # first startup is owned by _auto_worker -> startup_rebuild
        try:
            # Re-register persistent views defensively (idempotent)
            self.bot.add_view(SubmitButtonView(self))
            self.bot.add_view(VoteButtons(self))
            for gid in await self.repo.list_all_guild_ids():
                await self.enforce_enabled_state(gid, allow_ping=False, force_repost=False)
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
        for t in self._stats_rebuild_tasks.values():
            if not t.done():
                t.cancel()
                tasks.append(t)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._auto_task = None
        self._stats_rebuild_tasks.clear()
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
            embeds=await panel.build_embeds(interaction.guild),
            view=panel, ephemeral=True, wait=True,
            allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="riddle-champ",
                          description="Show riddle champions leaderboard.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_champ(self, interaction: Interaction,
                           visible: Optional[bool] = False,
                           image: Optional[str] = None,
                           mention: Optional[Role] = None):
        if interaction.guild is None:
            return
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
            interaction.user.id if not visible else None)
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
            "Riddle extension refuses to load – fix these config problems:\n  - "
            + "\n  - ".join(problems))

    if not bot.intents.members:
        logger.warning(
            "Members intent is disabled. Excluded-role filtering will use slow "
            "per-user REST lookups. Enable 'Server Members Intent'.")

    repo = RiddleRepo()
    await repo.start()
    try:
        await bot.add_cog(RiddleCog(bot, repo))
    except Exception:
        # Never leak an open DB connection if cog registration fails.
        await repo.close()
        raise
    _repo = repo
    logger.info("Riddle extension loaded (slots=%s, rotation=%sh, hard cap=%sh, "
                "hiatus=%sh, tick=%ss)",
                MAX_RIDDLE_SLOTS, UNSOLVED_ROTATION_HOURS, ROTATION_HARD_CAP_HOURS,
                SOLVED_HIATUS_HOURS, ROTATION_TICK_SECONDS)


async def teardown(bot: commands.Bot):
    global _repo
    if _repo is not None:
        await _repo.close()
        _repo = None
    logger.info("Riddle extension unloaded.")