# riddle.py
from __future__ import annotations

import asyncio
from typing import Optional, Literal

import discord
from discord import app_commands, Interaction, Role
from discord.ext import commands

from riddle_core import (
    RIDDLE_CHANNEL_ID, VOTE_CHANNEL_ID,
    RIDDLE_ROLE_ID, RIDDLE_MANAGER_ROLE_ID,
    EXCLUDED_COUNT_ROLE_ID, EXCLUDED_GAMEMASTER_ROLE_ID, EXTRA_EXCLUDED_ROLE_IDS_CSV,
    DEFAULT_IMAGE_URL, MAX_RIDDLE_SLOTS, MAX_EXTRA_PING_ROLES,
    UNSOLVED_ROTATION_HOURS, SOLVED_HIATUS_HOURS, ROTATION_TICK_SECONDS,
    SUBMIT_BUTTON_ID, VOTE_UP_BUTTON_ID, VOTE_DOWN_BUTTON_ID,
    logger, to_int, safe_int, is_http_url, unique_role_mentions, parse_csv_role_ids,
    footer_text,
    iso_in_future, iso_utc_in_hours, hours_since,
    riddle_manager_required, send_access_denied, MissingRiddleManagerRole,
    RiddleRepo,
)

from riddle_ui import (
    build_active_riddle_embed,
    build_fresh_solved_post_embed,
    build_solved_ping_post_embed,
    build_wrong_post_embed,
    build_xp_reminder_embed,
    SubmitButtonView,
    VoteButtons,
    RiddleAdminPanelView,
    ChampionsView,
    ChampionsImportModal,
)


class RiddleCog(commands.Cog):
    def __init__(self, bot: commands.Bot, repo: RiddleRepo):
        self.bot = bot
        self.repo = repo
        self._auto_task: Optional[asyncio.Task] = None
        self._startup_done = False

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

    async def user_is_excluded(self, guild: discord.Guild, user_id: int) -> bool:
        m = guild.get_member(user_id)
        if m is None:
            try:
                m = await guild.fetch_member(user_id)
            except Exception:
                m = None
        if m is None:
            return False
        ex = self.excluded_role_ids()
        return any(r.id in ex for r in m.roles)

    async def rebuild_cached_solved_total_for_guild(self, guild_id: int) -> int:
        rows = await self.repo.stats_entries(guild_id)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            total = sum(s for _, s, _ in rows)
            await self.repo.set_cached_solved_total(guild_id, total)
            return total
        total = 0
        for uid, solved, _xp in rows:
            if await self.user_is_excluded(guild, uid):
                continue
            total += solved
        await self.repo.set_cached_solved_total(guild_id, total)
        return total

    async def filtered_stats_entries_for_guild(self, guild: discord.Guild) -> list[tuple[int, int, int]]:
        raw = await self.repo.stats_entries(guild.id)
        out = []
        for uid, solved, xp in raw:
            if await self.user_is_excluded(guild, uid):
                continue
            out.append((uid, solved, xp))
        return out

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
        ch = self.bot.get_channel(channel_id)
        if ch is not None:
            return ch
        try:
            return await self.bot.fetch_channel(channel_id)
        except Exception:
            return None

    async def fetch_message_safe(self, channel_id: Optional[int], message_id: Optional[int]) -> Optional[discord.Message]:
        cid = safe_int(channel_id, None)
        mid = safe_int(message_id, None)
        if not cid or not mid:
            return None
        ch = await self.resolve_channel(cid)
        if ch is None or not hasattr(ch, "fetch_message"):
            return None
        try:
            return await ch.fetch_message(mid)
        except Exception:
            return None

    async def resolve_user_label(self, guild: Optional[discord.Guild], uid: int) -> tuple[str, str, Optional[str]]:
        mention = f"<@{uid}>"
        if guild:
            m = guild.get_member(uid)
            if m is None:
                try:
                    m = await guild.fetch_member(uid)
                except Exception:
                    m = None
            if m:
                return m.mention, str(m), m.display_avatar.url
        u = self.bot.get_user(uid)
        if u is None:
            try:
                u = await self.bot.fetch_user(uid)
            except Exception:
                u = None
        if u:
            return u.mention, str(u), u.display_avatar.url
        return mention, f"User {uid}", None

    # ==========================================================================
    # PING CONTENT HELPER
    # ==========================================================================
    def build_ping_content(self, guild: Optional[discord.Guild], mention_role_ids_csv: Optional[str]) -> Optional[str]:
        """Base role + up to MAX_EXTRA_PING_ROLES extra roles as a mention string."""
        extra: list[int] = []
        for rid in parse_csv_role_ids(mention_role_ids_csv):
            if rid == RIDDLE_ROLE_ID:
                continue
            if rid not in extra:
                extra.append(rid)
            if len(extra) >= MAX_EXTRA_PING_ROLES:
                break
        mentions = unique_role_mentions(guild, RIDDLE_ROLE_ID, *extra)
        return " ".join(dict.fromkeys(m for m in mentions if m)) or None

    # ==========================================================================
    # CHANNEL CLEANUP HELPERS
    # ==========================================================================
    def _msg_has_custom_id(self, msg: discord.Message, custom_ids: set[str]) -> bool:
        try:
            for row in (msg.components or []):
                for child in getattr(row, "children", []):
                    if getattr(child, "custom_id", None) in custom_ids:
                        return True
        except Exception:
            pass
        return False

    async def delete_button_messages_in_channel(self, channel_id: int, custom_ids: set[str], limit: int = 3000):
        ch = await self.resolve_channel(channel_id)
        me = self.bot.user
        if ch is None or not hasattr(ch, "history") or me is None:
            return
        try:
            async for msg in ch.history(limit=limit):
                if msg.author.id != me.id:
                    continue
                if self._msg_has_custom_id(msg, custom_ids):
                    try:
                        await msg.delete()
                    except Exception:
                        pass
        except Exception:
            pass

    async def cleanup_vote_messages_for_riddle(self, riddle_id: int, exclude_submission_id: Optional[int] = None):
        rows = await self.repo.list_vote_messages_for_riddle(riddle_id)
        if not rows:
            return
        vote_channel = await self.resolve_channel(VOTE_CHANNEL_ID)
        if vote_channel is None or not hasattr(vote_channel, "fetch_message"):
            return
        for row in rows:
            sid = to_int(row.get("id"), 0)
            if exclude_submission_id is not None and sid == exclude_submission_id:
                continue
            mid = to_int(row.get("vote_message_id"), 0)
            if mid <= 0:
                continue
            try:
                msg = await vote_channel.fetch_message(mid)
                await msg.delete()
            except Exception:
                pass

    async def cleanup_wrong_posts_for_riddle(self, riddle_id: int):
        rows = await self.repo.list_wrong_posts_for_riddle(riddle_id)
        for row in rows:
            cid = safe_int(row.get("channel_id"), None)
            mid = safe_int(row.get("message_id"), None)
            if not cid or not mid:
                continue
            ch = await self.resolve_channel(cid)
            if ch is None or not hasattr(ch, "fetch_message"):
                continue
            try:
                msg = await ch.fetch_message(mid)
                await msg.delete()
            except Exception:
                pass
        await self.repo.clear_wrong_posts_for_riddle(riddle_id)

    async def remove_active_riddle_posts(self, guild_id: int):
        rows = await self.repo.list_open_post_refs(guild_id)
        for row in rows:
            cid = safe_int(row.get("posted_channel_id"), None)
            mid = safe_int(row.get("posted_message_id"), None)
            if not cid or not mid:
                continue
            ch = await self.resolve_channel(cid)
            if ch is None or not hasattr(ch, "fetch_message"):
                continue
            try:
                msg = await ch.fetch_message(mid)
                await msg.delete()
            except Exception:
                pass
        await self.repo.clear_all_open_post_refs(guild_id)

    async def post_xp_reminder_to_vote_channel(self, guild: Optional[discord.Guild],
                                               user_id: int, xp_gain: int, riddle_no: int):
        """Post an XP-add reminder into the VOTE channel after a solve."""
        if VOTE_CHANNEL_ID <= 0 or xp_gain <= 0:
            return
        ch = await self.resolve_channel(VOTE_CHANNEL_ID)
        if ch is None or not hasattr(ch, "send"):
            return
        mention, name, avatar = await self.resolve_user_label(guild, user_id)
        embed = build_xp_reminder_embed(guild, mention, name, avatar, xp_gain, riddle_no)
        try:
            await ch.send(embed=embed,
                          allowed_mentions=discord.AllowedMentions(roles=False, users=False, everyone=False))
        except Exception:
            logger.exception("Failed to post XP reminder to vote channel")

    # ==========================================================================
    # POSTING: active riddle in Slot 1
    # ==========================================================================
    async def publish_slot1_post(self, guild_id: int, *, force_repost: bool, allow_role_ping: bool) -> str:
        """
        Publish (or edit) the current Slot 1 riddle.
        Does NOT check hiatus / rotation — call enforce_enabled_state for that.
        first_posted_at is preserved via COALESCE in set_riddle_post_ref, so
        refresh/edit does NOT reset the 6h countdown.
        """
        await self.normalize_after_structure_change(guild_id)
        slot1 = await self.repo.get_open_slot1(guild_id)
        if not slot1:
            return "no_slot1"
        guild = self.bot.get_guild(guild_id)
        ch = await self.resolve_channel(RIDDLE_CHANNEL_ID)
        if ch is None or not hasattr(ch, "send"):
            return "no_channel"

        embed = build_active_riddle_embed(guild, slot1)
        content = self.build_ping_content(guild, slot1.get("mention_role_ids"))
        existing = await self.fetch_message_safe(slot1.get("posted_channel_id"), slot1.get("posted_message_id"))

        try:
            if existing and force_repost:
                try:
                    await existing.delete()
                except Exception:
                    pass
                existing = None
            if existing:
                await existing.edit(
                    content=content, embed=embed, view=SubmitButtonView(self),
                    allowed_mentions=discord.AllowedMentions(
                        roles=allow_role_ping, users=False, everyone=False,
                    ),
                )
                await self.repo.clear_other_open_post_refs(guild_id, to_int(slot1["id"], 0))
                return "updated"
            msg = await ch.send(
                content=content, embed=embed, view=SubmitButtonView(self),
                allowed_mentions=discord.AllowedMentions(
                    roles=allow_role_ping, users=False, everyone=False,
                ),
            )
            await self.repo.set_riddle_post_ref(to_int(slot1["id"], 0), msg.channel.id, msg.id)
            await self.repo.clear_other_open_post_refs(guild_id, to_int(slot1["id"], 0))
            return "posted"
        except Exception:
            logger.exception("publish_slot1_post failed")
            return "error"

    async def force_repost_slot1_fresh(self, guild_id: int, *, allow_ping: bool) -> str:
        """
        Delete any existing Slot 1 post, reset its timer + refs, then publish
        a brand-new post. Used by 'Post Now' and 'Turn ON' so the 6h countdown
        actually restarts.
        """
        slot1 = await self.repo.get_open_slot1(guild_id)
        if not slot1:
            return "no_slot1"
        rid = to_int(slot1.get("id"), 0)
        old_msg = await self.fetch_message_safe(
            slot1.get("posted_channel_id"), slot1.get("posted_message_id"),
        )
        if old_msg:
            try:
                await old_msg.delete()
            except Exception:
                pass
        await self.repo.reset_riddle_post_state(rid)
        return await self.publish_slot1_post(
            guild_id, force_repost=False, allow_role_ping=allow_ping,
        )

    async def enforce_enabled_state(self, guild_id: int, *, allow_ping: bool, force_repost: bool = False) -> str:
        """
        Reconcile Discord state with DB state. Respects hiatus + auto-rotation.
          - if disabled            -> remove active posts
          - if in hiatus           -> remove active posts, do nothing
          - if hiatus expired      -> clear it, continue
          - if slot1 age >= 6h and no pending vote posts -> auto-rotate
          - otherwise              -> publish / refresh
        """
        await self.normalize_after_structure_change(guild_id)

        if not await self.repo.is_enabled(guild_id):
            await self.remove_active_riddle_posts(guild_id)
            return "disabled"

        # Hiatus check
        hiatus = await self.repo.get_hiatus_until(guild_id)
        if hiatus:
            if iso_in_future(hiatus):
                await self.remove_active_riddle_posts(guild_id)
                return "hiatus"
            # expired
            await self.repo.set_hiatus_until(guild_id, None)

        slot1 = await self.repo.get_open_slot1(guild_id)
        if not slot1:
            await self.repo.set_enabled(guild_id, False)
            await self.remove_active_riddle_posts(guild_id)
            return "enabled_but_no_slot1"

        # Auto-rotation check: 6h since FIRST post AND no pending vote posts.
        fpa = slot1.get("first_posted_at")
        age_h = hours_since(fpa) if fpa else None
        if age_h is not None and age_h >= UNSOLVED_ROTATION_HOURS:
            rid_s1 = to_int(slot1.get("id"), 0)
            if not await self.repo.has_pending_submissions_for_riddle(rid_s1):
                await self.auto_rotate_slot1_unsolved(guild_id)
                return "auto_rotated"
            # else: pending vote posts still exist -> wait, don't rotate yet.

        return await self.publish_slot1_post(
            guild_id, force_repost=force_repost, allow_role_ping=allow_ping,
        )

    # ==========================================================================
    # ROTATION / CLEANUP HELPERS
    # ==========================================================================
    async def rotate_riddle_to_end(self, guild_id: int, riddle_id: int, *, ping_new_slot1: bool) -> bool:
        """
        Move a riddle to the end of the open queue AND clean up all its Discord
        artifacts (posted riddle message, wrong-answer posts, pending vote posts,
        pending submissions). Then publish the new Slot 1.
        """
        r = await self.repo.get_riddle_by_id(guild_id, riddle_id)
        if not r or str(r.get("status")) != "open":
            return False

        # 1) delete posted riddle message
        orig = await self.fetch_message_safe(r.get("posted_channel_id"), r.get("posted_message_id"))
        if orig:
            try:
                await orig.delete()
            except Exception:
                logger.exception("Failed to delete original riddle post during rotation")

        # 2) delete wrong-answer public posts
        await self.cleanup_wrong_posts_for_riddle(riddle_id)

        # 3) delete pending vote messages + cancel pending submissions
        await self.cleanup_vote_messages_for_riddle(riddle_id)
        await self.repo.cancel_pending_for_riddle(riddle_id)

        # 4) DB move
        ok = await self.repo.move_open_riddle_to_end(guild_id, riddle_id)

        # 5) normalize + publish fresh Slot 1
        await self.normalize_after_structure_change(guild_id)
        if await self.repo.is_enabled(guild_id):
            # Slot 1 changed -> definitely a fresh post; publish_slot1_post will
            # set first_posted_at from NULL (via COALESCE).
            await self.publish_slot1_post(
                guild_id, force_repost=True, allow_role_ping=ping_new_slot1,
            )
        return ok

    async def auto_rotate_slot1_unsolved(self, guild_id: int):
        """Auto-rotation entry point (6h unsolved + no pending vote posts)."""
        slot1 = await self.repo.get_open_slot1(guild_id)
        if not slot1:
            return
        await self.rotate_riddle_to_end(
            guild_id, to_int(slot1.get("id"), 0), ping_new_slot1=True,
        )

    async def close_and_cleanup_riddle(self, guild_id: int, riddle_id: int, closed_by: int) -> bool:
        """
        Close a riddle AND clean up all its Discord artifacts. Used by the
        'Delete Slot' and 'Close Active' panel buttons.
        """
        r = await self.repo.get_riddle_by_id(guild_id, riddle_id)
        if not r or str(r.get("status")) != "open":
            return False
        orig = await self.fetch_message_safe(r.get("posted_channel_id"), r.get("posted_message_id"))
        if orig:
            try:
                await orig.delete()
            except Exception:
                pass
        await self.cleanup_wrong_posts_for_riddle(riddle_id)
        await self.cleanup_vote_messages_for_riddle(riddle_id)
        await self.repo.cancel_pending_for_riddle(riddle_id, closed_by)
        closed = await self.repo.close_open_riddle_by_id(guild_id, riddle_id, closed_by)
        return closed is not None

    # ==========================================================================
    # REPOST PENDING VOTES (on startup/reconnect)
    # ==========================================================================
    async def repost_pending_votes(self):
        rows = await self.repo.pending_open_submissions()
        if not rows:
            return
        vote_channel = await self.resolve_channel(VOTE_CHANNEL_ID)
        if vote_channel is None or not hasattr(vote_channel, "send"):
            return
        from riddle_ui import build_vote_embed
        for row in rows:
            guild = self.bot.get_guild(to_int(row.get("guild_id"), 0))
            uid = to_int(row.get("user_id"), 0)
            _, uname, uavatar = await self.resolve_user_label(guild, uid)
            riddle_view = {
                "text": row.get("riddle_text"),
                "solution": row.get("solution"),
                "xp": row.get("xp"),
                "image_url": None,
            }
            embed = build_vote_embed(guild, riddle_view, uid, uname, uavatar, row.get("answer") or "")
            try:
                vm = await vote_channel.send(embed=embed, view=VoteButtons(self))
                await self.repo.set_submission_vote_message(to_int(row["submission_id"], 0), vm.id)
            except Exception:
                pass

    # ==========================================================================
    # SOLVED FLOW  (👍 Correct)
    # ==========================================================================
    async def finalize_correct(self, guild: discord.Guild, ctx: dict, moderator: discord.Member):
        """
        1)  Delete the original riddle post
        2)  Delete all wrong-answer posts for this riddle
        3)  Post FRESH BIG solved post (no pings)
        4)  Post SMALL ping post (Base + Extras + Winner)
        5)  Cleanup other pending vote messages
        6)  Apply XP + solved-count if solver not excluded
        7)  System STAYS ON, but a solved hiatus of SOLVED_HIATUS_HOURS is set
        8)  XP reminder posted to VOTE channel
        """
        gid = guild.id
        rid = to_int(ctx.get("riddle_id"), 0)
        sid = to_int(ctx.get("submission_id"), 0)
        solver_id = to_int(ctx.get("solver_user_id"), 0)
        xp_gain = max(0, to_int(ctx.get("xp_gain"), 0))
        submitted_answer = str(ctx.get("answer") or "")

        # Reload the riddle so we have the freshest fields.
        riddle_row = await self.repo.get_riddle_by_id(gid, rid) or {}
        for k in ("text", "solution", "xp", "image_url", "solution_url",
                  "riddle_no", "posted_channel_id", "posted_message_id",
                  "mention_role_ids"):
            if not riddle_row.get(k):
                riddle_row[k] = ctx.get(k)

        solver_mention, solver_name, solver_avatar = await self.resolve_user_label(guild, solver_id)

        # ---- 1) DELETE the original riddle post ----
        original = await self.fetch_message_safe(
            riddle_row.get("posted_channel_id"), riddle_row.get("posted_message_id"),
        )
        if original:
            try:
                await original.delete()
            except Exception:
                logger.exception("Failed to delete original riddle post")

        # ---- 2) DELETE all wrong-answer posts for this riddle ----
        await self.cleanup_wrong_posts_for_riddle(rid)

        # ---- 3) Post the FRESH BIG solved post (no pings on this post) ----
        ch = await self.resolve_channel(RIDDLE_CHANNEL_ID)
        if ch is not None and hasattr(ch, "send"):
            try:
                fresh_embed = build_fresh_solved_post_embed(
                    guild, riddle_row,
                    solver_mention, solver_name, solver_avatar,
                    submitted_answer,
                )
                fresh_msg = await ch.send(
                    embed=fresh_embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await self.repo.set_solved_post_ref(rid, fresh_msg.channel.id, fresh_msg.id)
            except Exception:
                logger.exception("Failed to post fresh solved message")

            # ---- 4) Post the SMALL ping/announcement (Base + Extras + Winner) ----
            try:
                role_ping_str = self.build_ping_content(guild, riddle_row.get("mention_role_ids"))
                content_parts = [p for p in (role_ping_str, solver_mention) if p]
                ping_content = " ".join(content_parts) if content_parts else None

                ping_embed = build_solved_ping_post_embed(
                    guild, riddle_row, solver_mention, solver_avatar,
                    submitted_answer=submitted_answer,
                )
                await ch.send(
                    content=ping_content,
                    embed=ping_embed,
                    allowed_mentions=discord.AllowedMentions(
                        users=True, roles=True, everyone=False,
                    ),
                )
            except Exception:
                logger.exception("Failed to post ping message")

        # ---- 5) Cleanup other pending vote messages for this riddle ----
        await self.cleanup_vote_messages_for_riddle(rid, exclude_submission_id=sid)

        # ---- 6) XP + solved-count only if solver is NOT excluded ----
        if solver_id > 0 and not await self.user_is_excluded(guild, solver_id):
            await self.repo.apply_solve_xp(gid, solver_id, xp_gain)
            await self.repo.inc_cached_solved_total(gid, 1)
        else:
            await self.rebuild_cached_solved_total_for_guild(gid)

        # ---- 7) Normalize + start SOLVED HIATUS (system stays ON) ----
        await self.normalize_after_structure_change(gid)
        await self.repo.clear_all_open_post_refs(gid)
        if await self.repo.is_enabled(gid):
            await self.repo.set_hiatus_until(gid, iso_utc_in_hours(SOLVED_HIATUS_HOURS))
        # System is NOT turned off here — spec: after a solve the system
        # stays ON, only the next post is delayed by the hiatus.

        # ---- 8) XP reminder in the VOTE channel ----
        if solver_id > 0 and xp_gain > 0 and not await self.user_is_excluded(guild, solver_id):
            await self.post_xp_reminder_to_vote_channel(
                guild, solver_id, xp_gain, to_int(riddle_row.get("riddle_no"), 0),
            )

    # ==========================================================================
    # WRONG FLOW  (👎 Wrong)
    # ==========================================================================
    async def finalize_wrong(self, guild: discord.Guild, ctx: dict, moderator: discord.Member):
        """
        Riddle stays OPEN. Post a public wrong-answer message in the riddle channel.
        Only the submitter is pinged. Message-ID stored for cleanup at solve time
        or at auto-rotation.
        """
        gid = guild.id
        rid = to_int(ctx.get("riddle_id"), 0)
        submitter_id = to_int(ctx.get("solver_user_id"), 0)

        riddle_row = await self.repo.get_riddle_by_id(gid, rid) or {}
        for k in ("text", "xp", "image_url", "riddle_no", "mention_role_ids"):
            if not riddle_row.get(k):
                riddle_row[k] = ctx.get(k)

        submitter_mention, submitter_name, submitter_avatar = await self.resolve_user_label(guild, submitter_id)

        ch = await self.resolve_channel(RIDDLE_CHANNEL_ID)
        if ch is None or not hasattr(ch, "send"):
            return

        content = submitter_mention if submitter_mention else None
        embed = build_wrong_post_embed(
            guild, riddle_row,
            submitter_mention, submitter_name, submitter_avatar,
            str(ctx.get("answer") or ""),
        )

        try:
            sent = await ch.send(
                content=content, embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
            await self.repo.add_wrong_post(gid, rid, sent.channel.id, sent.id)
        except Exception:
            logger.exception("Failed to post wrong-answer message")

    # ==========================================================================
    # STARTUP REBUILD
    # ==========================================================================
    async def startup_rebuild(self):
        gids: set[int] = set(await self.repo.list_all_guild_ids())
        gids.update(g.id for g in self.bot.guilds)

        for gid in gids:
            await self.repo.ensure_guild_state(gid)

        for gid in gids:
            await self.rebuild_cached_solved_total_for_guild(gid)
            await self.normalize_after_structure_change(gid)

        await self.delete_button_messages_in_channel(RIDDLE_CHANNEL_ID, {SUBMIT_BUTTON_ID}, limit=3000)
        await self.delete_button_messages_in_channel(
            VOTE_CHANNEL_ID, {VOTE_UP_BUTTON_ID, VOTE_DOWN_BUTTON_ID}, limit=4000,
        )

        await self.repo.clear_all_open_post_refs(None)
        await self.repo.reset_pending_vote_refs()
        await self.repo.cancel_pending_for_non_open()

        for gid in gids:
            # enforce_enabled_state respects hiatus + rotation
            await self.enforce_enabled_state(gid, allow_ping=False, force_repost=True)

        await self.repost_pending_votes()

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
                for gid in await self.repo.list_all_guild_ids():
                    await self.repo.ensure_guild_state(gid)
                    # enforce_enabled_state handles: disabled, hiatus,
                    # auto-rotation (6h + no pending), and normal refresh.
                    await self.enforce_enabled_state(gid, allow_ping=False, force_repost=False)
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
            await self.rebuild_cached_solved_total_for_guild(after.guild.id)
            await self.sync_open_slot_numbers_for_guild(after.guild.id)

    @commands.Cog.listener()
    async def on_ready(self):
        """Reconcile after every (re)connect so buttons + hiatus work again."""
        if not self._startup_done:
            return  # first startup is handled by _auto_worker -> startup_rebuild
        try:
            # Re-register persistent views defensively (idempotent)
            self.bot.add_view(SubmitButtonView(self))
            self.bot.add_view(VoteButtons(self))
            for gid in await self.repo.list_all_guild_ids():
                await self.enforce_enabled_state(gid, allow_ping=False, force_repost=True)
            await self.repost_pending_votes()
            logger.info("on_ready: reconciled active riddles + pending votes after reconnect")
        except Exception:
            logger.exception("on_ready reconnect recovery failed")

    # ==========================================================================
    # LIFECYCLE
    # ==========================================================================
    async def cog_load(self):
        self.bot.add_view(SubmitButtonView(self))
        self.bot.add_view(VoteButtons(self))
        if self._auto_task is None or self._auto_task.done():
            self._auto_task = asyncio.create_task(self._auto_worker(), name="riddle_auto_worker")

    def cog_unload(self):
        if self._auto_task and not self._auto_task.done():
            self._auto_task.cancel()

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
        msg = await interaction.followup.send(
            embeds=await panel.build_embeds(interaction.guild),
            view=panel, ephemeral=True, wait=True,
        )
        panel.message = msg

    @app_commands.command(name="riddle-champ", description="Show riddle champions leaderboard.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_champ(
        self, interaction: Interaction,
        visible: Optional[bool] = False,
        image: Optional[str] = None,
        mention: Optional[Role] = None,
    ):
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=not visible, thinking=True)
        entries_raw = await self.filtered_stats_entries_for_guild(interaction.guild)
        total_solved = await self.repo.get_cached_solved_total(interaction.guild.id)
        if total_solved <= 0 and entries_raw:
            total_solved = sum(s for _, s, _ in entries_raw)
            await self.repo.set_cached_solved_total(interaction.guild.id, total_solved)
        entries = [
            (uid, solved, (solved / total_solved * 100.0 if total_solved else 0.0), xp)
            for uid, solved, xp in entries_raw
        ]
        name_cache: dict[int, str] = {}
        avatar_cache: dict[int, str] = {}
        for uid, _, _ in entries_raw:
            _, name, avatar = await self.resolve_user_label(interaction.guild, uid)
            name_cache[uid] = name
            if avatar:
                avatar_cache[uid] = avatar
        view = ChampionsView(
            entries, total_solved, name_cache, avatar_cache,
            image if is_http_url(image) else DEFAULT_IMAGE_URL,
            interaction.user.id if not visible else None,
        )
        sent = await interaction.followup.send(
            content=mention.mention if (visible and mention) else None,
            embed=view.build_embed(), view=view,
            ephemeral=not visible, wait=True,
            allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
        )
        view.message = sent

    @app_commands.command(
        name="champions-import-json",
        description="Import legacy champions data from JSON (merge or replace).",
    )
    @app_commands.guild_only()
    @riddle_manager_required()
    async def champions_import_json(self, interaction: Interaction,
                                    mode: Literal["merge", "replace"] = "merge"):
        await interaction.response.send_modal(ChampionsImportModal(self, mode))

    # ==========================================================================
    # APP-COMMAND ERROR HANDLER
    # ==========================================================================
    async def cog_app_command_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, MissingRiddleManagerRole):
            await send_access_denied(interaction)
            return
        logger.exception("Riddle command error: %s", error)


# =============================================================================
# EXTENSION ENTRY POINTS
# =============================================================================
_repo: Optional[RiddleRepo] = None


async def setup(bot: commands.Bot):
    global _repo
    _repo = RiddleRepo()
    await _repo.start()
    await bot.add_cog(RiddleCog(bot, _repo))
    logger.info("Riddle extension loaded.")


async def teardown(bot: commands.Bot):
    global _repo
    if _repo is not None:
        await _repo.close()
        _repo = None
    logger.info("Riddle extension unloaded.")