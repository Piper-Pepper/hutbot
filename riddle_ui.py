# riddle_ui.py
from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

import discord
from discord import Interaction
from discord.ui import View, Modal, TextInput, Select

from riddle_core import (
    RIDDLE_ROLE_ID, RIDDLE_MANAGER_ROLE_ID, MAX_RIDDLE_SLOTS, MAX_EXTRA_PING_ROLES,
    PANEL_SLOT_LIST_LIMIT, DEFAULT_IMAGE_URL,
    SUBMIT_BUTTON_ID, VOTE_UP_BUTTON_ID, VOTE_DOWN_BUTTON_ID,
    VOTE_CHANNEL_ID, UNSOLVED_ROTATION_HOURS, ROTATION_HARD_CAP_HOURS,
    UNSOLVED_ROTATION_XP_BONUS, MAX_RIDDLE_XP,
    SUBMIT_DELAY_MINUTES, PANEL_TIMEOUT_SECONDS, LEVEL_TIERS, MAX_XP_INPUT,
    DUPLICATE_PENDING, SUBMISSION_NOT_ACTIVE,
    logger, to_int, clean_value, is_http_url, truncate_text, parse_xp_input,
    clamp_embed_value, clamp_embed_description, extract_first_url, footer_text,
    parse_csv_role_ids, safe_defer, member_has_role, quiet_followup, quiet_respond,
    iso_in_future, hours_until, hours_since, discord_ts, duration_between_iso,
    format_duration_hours, format_clock_time, submit_unlock_iso, submit_is_locked,
)

if TYPE_CHECKING:
    from riddle import RiddleCog


_DISCORD_SELECT_MAX = 25
_ROLE_SELECT_MAX = max(1, min(_DISCORD_SELECT_MAX, MAX_EXTRA_PING_ROLES))

# Ideographic space. Discord collapses ordinary leading spaces, so this is the
# only reliable way to indent a continuation line. Needed because a single
# dense slot line wraps into 4-5 unreadable lines on a phone.
IND = "\u3000\u3000"


# =============================================================================
# INTERNAL HELPERS
# =============================================================================
def _spoiler_safe(text: Optional[str]) -> str:
    """Neutralise embedded || so user text cannot break out of a spoiler."""
    return (text or "").replace("||", "\u200b|\u200b|\u200b")


def _spoiler(text: Optional[str]) -> str:
    return f"||{_spoiler_safe((text or '').strip())}||"


def _first_line(text: Optional[str], max_len: int = 80) -> str:
    if not text:
        return "*no solution set*"
    body, _ = extract_first_url(text)
    base = (body or text).strip()
    line = base.split("\n", 1)[0].strip() if base else ""
    return truncate_text(line, max_len) if line else "*no solution set*"


def _add_answer_field(embed: discord.Embed, answer: Optional[str],
                      name: str = "🧠 Winning Answer") -> None:
    if answer and answer.strip():
        embed.add_field(name=name, value=clamp_embed_value(_spoiler(answer)), inline=False)


def _add_solution_field(embed: discord.Embed, riddle: dict) -> None:
    sol_text, more_url = extract_first_url((riddle.get("solution") or "").strip())
    parts: list[str] = []
    if sol_text:
        parts.append(_spoiler_safe(sol_text))
    if more_url:
        parts.append(f"[🔗 MORE]({more_url})")
    if not parts:
        return
    embed.add_field(name="✅ Solution",
                    value=clamp_embed_value(f"||{chr(10).join(parts)}||"), inline=False)


def _add_xp_level(embed: discord.Embed, xp: int, *, award_name: str = "🏆 Award",
                  suffix: str = " XP") -> None:
    """
    Two inline fields = half width each, which still reads fine on a phone.
    Three would drop to a third of the width and wrap mid-value.
    """
    x = max(0, to_int(xp, 0))
    embed.add_field(name=award_name, value=f"{x}{suffix}", inline=True)
    embed.add_field(name="🎚️ Level", value=level_badge(x), inline=True)


def _add_rotation_field(embed: discord.Embed, riddle: dict, *,
                        compact: bool = False) -> None:
    """
    Show the unsolved-rotation history. Only rendered when the riddle was
    actually AUTO-rotated at least once - which should be rare. Manual
    "Move to End" never increments the counter, so nothing shows up for it.
    """
    rot = max(0, to_int(riddle.get("rotation_count"), 0))
    if rot < 1:
        return

    times = "once" if rot == 1 else f"{rot}×"
    base_xp = to_int(riddle.get("base_xp"), None) if riddle.get("base_xp") is not None else None
    xp_now = max(0, to_int(riddle.get("xp"), 0))

    if compact:
        embed.add_field(name="🔁 Rotations", value=str(rot), inline=True)
        return

    lines = [f"🔁 Nobody solved this in time — rotated **{times}**."]
    if base_xp is not None and xp_now > base_xp:
        lines.append(f"💹 Reward raised: {base_xp} XP → **{xp_now} XP** "
                     f"(+{xp_now - base_xp})")
        if xp_now >= MAX_RIDDLE_XP:
            lines.append(f"🧱 XP ceiling reached ({MAX_RIDDLE_XP}) — no further increase.")
    embed.add_field(name="🔥 Unsolved Bonus", value=clamp_embed_value("\n".join(lines)),
                    inline=False)


def _add_timing_field(embed: discord.Embed, riddle: dict, *,
                      compact: bool = False, show_vote_time: bool = False) -> None:
    """
    IMPORTANT: `solved_at` here means "the winning answer was SUBMITTED", not
    "a manager pressed the button". Callers must pass the submission timestamp,
    otherwise moderator response time inflates the solve duration.

    Durations are rendered bold rather than in inline code: Discord does not
    wrap inside a code span, so a long value like `2d 2h 15m` either forces an
    early break or overflows on narrow screens.
    """
    first_posted = riddle.get("first_posted_at")
    solved_at = riddle.get("solved_at")
    voted_at = riddle.get("voted_at")

    posted_abs = discord_ts(first_posted, "f")
    posted_rel = discord_ts(first_posted, "R")
    solved_abs = discord_ts(solved_at, "f")
    solved_rel = discord_ts(solved_at, "R")
    took_h = duration_between_iso(first_posted, solved_at)

    if not posted_abs and not solved_abs:
        return

    # "Post Now" can reset first_posted_at while a submission is pending, which
    # would yield a negative duration. Don't print nonsense.
    duration_valid = took_h is not None and took_h >= 0

    if compact:
        if duration_valid:
            embed.add_field(name="⏱️ Time to solve",
                            value=f"**{format_duration_hours(took_h)}**", inline=True)
        elif posted_rel:
            embed.add_field(name="📌 Posted", value=posted_rel, inline=True)
        return

    lines: list[str] = []
    if posted_abs:
        lines.append(f"📌 **Posted:** {posted_abs}" + (f" · {posted_rel}" if posted_rel else ""))
    if solved_abs:
        lines.append(f"🏁 **Solved:** {solved_abs}" + (f" · {solved_rel}" if solved_rel else ""))
    if duration_valid:
        lines.append(f"⌛ **Took:** {format_duration_hours(took_h)}")

    if show_vote_time:
        voted_abs = discord_ts(voted_at, "f")
        review_h = duration_between_iso(solved_at, voted_at)
        if voted_abs:
            suffix = (f" · review took {format_duration_hours(review_h)}"
                      if review_h is not None and review_h >= 0 else "")
            lines.append(f"✅ **Approved:** {voted_abs}{suffix}")

    embed.add_field(name="⏱️ Timing", value=clamp_embed_value("\n".join(lines)), inline=False)


def _riddle_display_no(riddle: dict) -> int:
    """
    riddle_no is an identity assigned at creation - never recomputed from the
    solved total. The panel used to derive it positionally (solved + slot),
    which drifted from the number shown in the channel whenever an excluded
    user solved a riddle.
    """
    return to_int(riddle.get("riddle_no"), to_int(riddle.get("id"), 0))


# =============================================================================
# LEVEL / DIFFICULTY
# =============================================================================
def riddle_level(xp: int) -> tuple[str, str]:
    x = max(0, to_int(xp, 0))
    for bound, label, emoji in LEVEL_TIERS:
        if bound is None or x < bound:
            return label, emoji
    return LEVEL_TIERS[-1][1], LEVEL_TIERS[-1][2]


def level_badge(xp: int) -> str:
    label, emoji = riddle_level(xp)
    return f"{emoji} {label}"


# =============================================================================
# EMBED BUILDERS
# =============================================================================
def build_active_riddle_embed(guild: Optional[discord.Guild], riddle: dict, *,
                              posted_at_override: Optional[str] = None) -> discord.Embed:
    """
    posted_at_override is used for the very first post, where first_posted_at is
    not in the DB yet - so the "opens at" hint matches the stored anchor exactly.
    """
    r_no = _riddle_display_no(riddle)
    xp = max(0, to_int(riddle.get("xp"), 0))
    rot = max(0, to_int(riddle.get("rotation_count"), 0))

    title = f"🧩 Riddle No.{r_no}"
    if rot >= 1:
        title += "  🔥"  # visual cue that this one carries a bonus

    e = discord.Embed(
        title=title,
        description=clamp_embed_description(
            (riddle.get("text") or "*No riddle text set.*").strip()),
        color=discord.Color.blurple(),
    )
    if guild:
        e.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    _add_xp_level(e, xp)

    unlock = submit_unlock_iso(riddle, posted_at_override=posted_at_override)
    locked = submit_is_locked(riddle, posted_at_override=posted_at_override)

    if locked and unlock:
        # Fixed clock time (same as the button label) plus a Discord relative
        # timestamp, which counts down by itself - no per-minute edits needed.
        e.add_field(
            name="🔒 Riddle opens at",
            value=(f"**{format_clock_time(unlock)}** · {discord_ts(unlock, 'R')}\n"
                   f"*Submissions are locked for the first {SUBMIT_DELAY_MINUTES} "
                   f"minutes — everyone gets a fair chance to read it.*"),
            inline=False)
        e.color = discord.Color.dark_grey()
    else:
        posted_rel = discord_ts(riddle.get("first_posted_at") or posted_at_override, "R")
        if posted_rel:
            e.add_field(name="📌 Online since", value=posted_rel, inline=False)

    _add_rotation_field(e, riddle)

    img = riddle.get("image_url")
    if not is_http_url(img):
        img = DEFAULT_IMAGE_URL
    if is_http_url(img):
        e.set_image(url=img)
    e.set_footer(text=footer_text(guild))
    return e


def build_fresh_solved_post_embed(
    guild: Optional[discord.Guild], riddle: dict, solver_mention: str,
    solver_display_name: str, solver_avatar_url: Optional[str],
    submitted_answer: str,
) -> discord.Embed:
    r_no = _riddle_display_no(riddle)
    xp = max(0, to_int(riddle.get("xp"), 0))
    riddle_text = (riddle.get("text") or "*No text*").strip()

    e = discord.Embed(
        title=f"🎉 Riddle No.{r_no} — Solved!",
        description=clamp_embed_description(
            f"Congratulations {solver_mention}!\n\n**Riddle:**\n{riddle_text}"),
        color=discord.Color.gold(),
    )
    if solver_avatar_url:
        e.set_author(name=solver_display_name, icon_url=solver_avatar_url)
    else:
        e.set_author(name=solver_display_name)

    _add_answer_field(e, submitted_answer)
    _add_solution_field(e, riddle)
    _add_xp_level(e, xp)
    _add_rotation_field(e, riddle)
    _add_timing_field(e, riddle)

    if is_http_url(riddle.get("image_url")):
        e.set_thumbnail(url=riddle["image_url"])
    if is_http_url(riddle.get("solution_url")):
        e.set_image(url=riddle["solution_url"])
    e.set_footer(text=footer_text(guild))
    return e


def build_solved_ping_post_embed(
    guild: Optional[discord.Guild], riddle: dict, solver_mention: str,
    solver_avatar_url: Optional[str], submitted_answer: str = "",
) -> discord.Embed:
    r_no = _riddle_display_no(riddle)
    xp = max(0, to_int(riddle.get("xp"), 0))

    e = discord.Embed(
        title=f"🧩 Riddle No.{r_no} — Solved!",
        description=f"Congratulations {solver_mention}! 🎉",
        color=discord.Color.green(),
    )
    _add_answer_field(e, submitted_answer)
    _add_solution_field(e, riddle)
    _add_xp_level(e, xp, award_name="🏆 XP", suffix="")
    _add_timing_field(e, riddle, compact=True)
    _add_rotation_field(e, riddle, compact=True)

    if solver_avatar_url:
        e.set_thumbnail(url=solver_avatar_url)
    e.set_footer(text=footer_text(guild))
    return e


def build_wrong_post_embed(
    guild: Optional[discord.Guild], riddle: dict, submitter_mention: str,
    submitter_name: str, submitter_avatar_url: Optional[str], submitted_answer: str,
) -> discord.Embed:
    r_no = _riddle_display_no(riddle)
    xp = max(0, to_int(riddle.get("xp"), 0))
    e = discord.Embed(
        title=f"❌ Wrong Answer — Riddle No.{r_no} still open",
        description=clamp_embed_description(
            f"{submitter_mention}, your submitted solution was rejected.\n"
            f"The riddle is **still open** — keep trying!"),
        color=discord.Color.red(),
    )
    if submitter_avatar_url:
        e.set_author(name=submitter_name, icon_url=submitter_avatar_url)
    else:
        e.set_author(name=submitter_name)

    _add_answer_field(e, submitted_answer, name="🧠 Submitted Answer")
    _add_xp_level(e, xp, award_name="🏆 Still up for grabs")

    posted_rel = discord_ts(riddle.get("first_posted_at"), "R")
    if posted_rel:
        e.add_field(name="📌 Open since", value=posted_rel, inline=False)

    if is_http_url(riddle.get("image_url")):
        e.set_thumbnail(url=riddle["image_url"])
    e.set_footer(text=footer_text(guild))
    return e


def build_vote_embed(
    guild: Optional[discord.Guild], riddle: dict, submitter_id: int,
    submitter_name: str, submitter_avatar_url: Optional[str], submitted_answer: str,
) -> discord.Embed:
    xp = max(0, to_int(riddle.get("xp"), 0))
    e = discord.Embed(
        title="📜 New Solution Submitted",
        description=clamp_embed_description(riddle.get("text") or "*No riddle text*"),
        color=discord.Color.gold(),
    )
    if submitter_avatar_url:
        e.set_author(name=submitter_name, icon_url=submitter_avatar_url)
    else:
        e.set_author(name=submitter_name)

    e.add_field(name="🧠 User Answer",
                value=clamp_embed_value(submitted_answer or "*empty*"), inline=False)
    e.add_field(name="✅ Correct Solution",
                value=clamp_embed_value(riddle.get("solution") or "*Not set*"), inline=False)
    _add_xp_level(e, xp)

    rot = max(0, to_int(riddle.get("rotation_count"), 0))
    meta = [f"🆔 <@{submitter_id}> · `{submitter_id}`"]
    if rot >= 1:
        meta.append(f"🔁 Auto-rotated {rot}× (XP was raised)")
    posted_abs = discord_ts(riddle.get("first_posted_at"), "f")
    if posted_abs:
        age = hours_since(riddle.get("first_posted_at"))
        suffix = f" · open for **{format_duration_hours(age)}**" if age is not None else ""
        meta.append(f"📌 Posted {posted_abs}{suffix}")
    e.add_field(name="ℹ️ Details", value=clamp_embed_value("\n".join(meta)), inline=False)

    if is_http_url(riddle.get("image_url")):
        e.set_thumbnail(url=riddle["image_url"])
    e.set_footer(text=footer_text(guild))
    return e


def build_xp_reminder_embed(
    guild: Optional[discord.Guild], solver_mention: str, solver_name: str,
    solver_avatar_url: Optional[str], xp_amount: int, riddle_no: int,
    riddle: Optional[dict] = None,
) -> discord.Embed:
    xp = max(0, to_int(xp_amount, 0))
    safe_name = (solver_name or "UnknownUser").replace('"', "").strip() or "UnknownUser"
    e = discord.Embed(
        title="💰 XP Award — Reminder",
        description=clamp_embed_description(
            f"Riddle **No.{riddle_no}** was solved by {solver_mention}.\n"
            f"Please run one of these commands to grant the XP:"),
        color=discord.Color.gold(),
    )
    e.add_field(name="Command (by name)",
                value=f"`/xp add {xp} {safe_name}`", inline=False)
    e.add_field(name="Command (by mention)",
                value=f"`/xp add {xp} {solver_mention}`", inline=False)
    _add_xp_level(e, xp, award_name="Amount")
    if riddle:
        _add_rotation_field(e, riddle)
        _add_timing_field(e, riddle, show_vote_time=True)
    if solver_avatar_url:
        e.set_thumbnail(url=solver_avatar_url)
    e.set_footer(text=footer_text(guild))
    return e


def build_rotation_warning_embed(guild: Optional[discord.Guild], riddle: dict,
                                 pending_count: int, age_hours: float) -> discord.Embed:
    r_no = _riddle_display_no(riddle)
    e = discord.Embed(
        title="⚠️ Riddle force-rotated with open votes",
        description=clamp_embed_description(
            f"Riddle **No.{r_no}** sat in Slot 1 for "
            f"**{format_duration_hours(age_hours)}** with **{pending_count}** "
            f"un-voted submission(s).\n\n"
            f"The hard cap of **{ROTATION_HARD_CAP_HOURS}h** was reached, so it was "
            f"moved to the end of the queue and those submissions were cancelled.\n"
            f"Nobody received XP for them."),
        color=discord.Color.orange(),
    )
    posted_abs = discord_ts(riddle.get("first_posted_at"), "f")
    if posted_abs:
        e.add_field(name="📌 Was posted", value=posted_abs, inline=False)
    e.set_footer(text=footer_text(guild))
    return e


def build_rotation_bonus_embed(guild: Optional[discord.Guild], riddle: dict,
                               bump: dict) -> discord.Embed:
    """Posted to the vote channel when an auto-rotation raised a riddle's XP."""
    r_no = _riddle_display_no(riddle)
    old_xp = to_int(bump.get("old_xp"), 0)
    new_xp = to_int(bump.get("new_xp"), 0)
    gained = to_int(bump.get("gained"), 0)
    rot = to_int(bump.get("rotation_count"), 0)
    times = "once" if rot == 1 else f"{rot}×"

    e = discord.Embed(
        title="🔥 Unsolved — reward increased",
        description=clamp_embed_description(
            f"Riddle **No.{r_no}** went unsolved for "
            f"**{UNSOLVED_ROTATION_HOURS}h** and was moved to the end of the queue.\n"
            f"It has now been auto-rotated **{times}**."),
        color=discord.Color.orange(),
    )
    if gained > 0:
        e.add_field(name="💹 XP", value=f"{old_xp} → **{new_xp}** (+{gained})", inline=True)
        e.add_field(name="🎚️ New Level", value=level_badge(new_xp), inline=True)
    else:
        e.add_field(name="🧱 XP",
                    value=f"**{new_xp}** (ceiling {MAX_RIDDLE_XP} reached)", inline=False)
    if bump.get("capped"):
        e.add_field(name="Note",
                    value=f"The bonus was clipped at the {MAX_RIDDLE_XP} XP ceiling.",
                    inline=False)
    e.set_footer(text=footer_text(guild))
    return e


async def edit_vote_result_message(msg: discord.Message, *, ok: bool,
                                   moderator_mention: str):
    try:
        if msg.embeds:
            d = msg.embeds[0].to_dict()
            d["fields"] = [f for f in d.get("fields", [])
                           if f.get("name") not in {"✅ Result", "❌ Result"}]
            e = discord.Embed.from_dict(d)
        else:
            e = discord.Embed(title="📜 Solution Vote")
        if ok:
            e.color = discord.Color.green()
            e.add_field(name="✅ Result",
                        value=clamp_embed_value(f"Approved by {moderator_mention}"),
                        inline=False)
        else:
            e.color = discord.Color.red()
            e.add_field(name="❌ Result",
                        value=clamp_embed_value(f"Rejected by {moderator_mention}"),
                        inline=False)
        await msg.edit(embed=e, view=None)
    except (discord.HTTPException, discord.NotFound):
        logger.debug("Could not annotate vote message %s", msg.id, exc_info=True)


class LoggedPersistentView(View):
    def __init__(self, timeout=None):
        super().__init__(timeout=timeout)

    async def on_error(self, interaction: Interaction, error: Exception, item):
        logger.exception("View error in %s (item=%r): %s",
                         self.__class__.__name__, item, error)
        await quiet_respond(interaction, "❌ Something broke internally. Check the logs.")


# =============================================================================
# MODALS
# =============================================================================
class SubmitSolutionModal(Modal):
    def __init__(self, cog: "RiddleCog", riddle_id: int):
        super().__init__(title="Submit your solution")
        self.cog = cog
        self.riddle_id = riddle_id
        self.answer = TextInput(label="Your Answer", style=discord.TextStyle.paragraph,
                                required=True, max_length=4000)
        self.add_item(self.answer)

    async def on_submit(self, interaction: Interaction):
        if interaction.guild is None:
            return
        if not await safe_defer(interaction, ephemeral=True):
            return

        # Must be the riddle that is CURRENTLY in slot 1, not merely an open one.
        # A user can sit in this modal while the worker rotates the riddle to
        # slot 10; it stays 'open', so a status-only check would let the answer
        # through and approving it would solve a riddle nobody could see.
        riddle = await self.cog.repo.get_open_slot1(interaction.guild.id)
        if not riddle or to_int(riddle.get("id"), 0) != self.riddle_id:
            await quiet_followup(
                interaction,
                "⚠️ This riddle is no longer the active one — it was solved, closed "
                "or moved while you were typing. Your answer was not submitted.")
            return

        # Authoritative grace-period gate. The disabled button can be bypassed
        # by a stale client, this cannot.
        if submit_is_locked(riddle):
            unlock = submit_unlock_iso(riddle)
            await quiet_followup(
                interaction,
                f"🔒 This riddle is not open yet. Submissions open at "
                f"**{format_clock_time(unlock)}** ({discord_ts(unlock, 'R')}).")
            return

        ans = clean_value(str(self.answer.value or ""))
        if not ans:
            await quiet_followup(interaction, "❌ Answer cannot be empty.")
            return

        rid = to_int(riddle.get("id"), 0)

        try:
            if await self.cog.repo.answer_already_rejected(rid, ans):
                await quiet_followup(
                    interaction,
                    "❌ That exact answer was already reviewed and rejected for this "
                    "riddle. Try something different.")
                return
        except Exception:
            logger.exception("answer_already_rejected check failed - continuing anyway")

        try:
            sid = await self.cog.repo.create_submission_pending(
                interaction.guild.id, rid, interaction.user.id, ans)
        except Exception:
            logger.exception("create_submission_pending failed")
            await quiet_followup(interaction, "❌ Could not save your submission (DB error).")
            return

        if sid == DUPLICATE_PENDING:
            await quiet_followup(
                interaction,
                "⏳ You already have a submission awaiting review for this riddle. "
                "Please wait for a manager to vote on it.")
            return
        if sid == SUBMISSION_NOT_ACTIVE:
            await quiet_followup(
                interaction,
                "⚠️ This riddle stopped being the active one a moment ago. "
                "Your answer was not submitted.")
            return
        if not sid:
            await quiet_followup(interaction, "❌ Could not save your submission.")
            return

        vote_channel = await self.cog.resolve_channel(VOTE_CHANNEL_ID)
        if vote_channel is None or not hasattr(vote_channel, "send"):
            await self.cog.repo.delete_submission(sid)
            await quiet_followup(interaction, "❌ Vote channel not available. Tell an admin.")
            return

        embed = build_vote_embed(interaction.guild, riddle, interaction.user.id,
                                 interaction.user.display_name,
                                 interaction.user.display_avatar.url, ans)
        try:
            vm = await vote_channel.send(embed=embed, view=VoteButtons(self.cog))
            await self.cog.repo.set_submission_vote_message(sid, vm.id)
        except Exception:
            logger.exception("Failed to post submission for voting")
            await self.cog.repo.delete_submission(sid)
            await quiet_followup(interaction, "❌ Failed to post your submission for voting.")
            return

        await quiet_followup(interaction, "✅ Your solution was submitted for review.")


class RiddleContentModal(Modal):
    def __init__(self, panel: "RiddleAdminPanelView", slot_no: int,
                 riddle_id: Optional[int], current: Optional[dict]):
        super().__init__(title=f"Slot {slot_no} Content")
        self.panel = panel
        self.slot_no = slot_no
        self.riddle_id = riddle_id
        cur = current or {}
        self.text = TextInput(label="Riddle Text", style=discord.TextStyle.paragraph,
                              default=cur.get("text") or "", required=True, max_length=4000)
        self.solution = TextInput(label="Solution", style=discord.TextStyle.paragraph,
                                  default=cur.get("solution") or "", required=True,
                                  max_length=4000)
        self.xp = TextInput(label=f"XP Reward (0 - {MAX_XP_INPUT})", placeholder="e.g. 1500",
                            default=str(max(0, to_int(cur.get("xp"), 0))),
                            required=True, max_length=12)
        self.add_item(self.text)
        self.add_item(self.solution)
        self.add_item(self.xp)

    async def on_submit(self, interaction: Interaction):
        if interaction.guild is None:
            return
        if not await safe_defer(interaction):
            return

        gid = interaction.guild.id
        raw_xp = str(self.xp.value or "")
        xp = parse_xp_input(raw_xp)
        if xp is None:
            self.panel.last_info = (
                f"❌ Invalid XP value `{truncate_text(raw_xp.strip(), 30)}` — use a whole "
                f"number between 0 and {MAX_XP_INPUT}. **Nothing was saved.**")
            await self.panel.safe_edit_panel()
            return

        had_rotations = max(0, to_int((self.panel.slot_map.get(self.slot_no) or {})
                                      .get("rotation_count"), 0))

        # Goes through the cog so the write happens under the SAME per-guild
        # lock the worker uses. Calling the repo directly would let the worker
        # publish a stale snapshot mid-save.
        ok, reason = await self.panel.cog.save_slot_content(
            gid, interaction.user.id, self.slot_no, self.riddle_id,
            str(self.text.value), str(self.solution.value), xp)

        if not ok:
            self.panel.last_info = {
                "conflict": ("⚠️ Riddle changed or was closed while you were editing. "
                             "Hit 🔄 and try again."),
                "empty": "❌ Save failed (text or solution empty?).",
                "error": "❌ Save failed — check the logs.",
            }.get(reason, "❌ Save failed.")
            await self.panel.safe_edit_panel()
            return

        note = ""
        if had_rotations:
            note = (f"\n🔁 Rotation counter reset (was {had_rotations}) — "
                    f"{xp} XP is the new baseline.")
        self.panel.last_info = f"✅ Saved. XP set to **{xp}**.{note}"
        await self.panel.safe_edit_panel()


class RiddleImagesModal(Modal):
    def __init__(self, panel: "RiddleAdminPanelView", slot_no: int,
                 riddle_id: int, current: dict):
        super().__init__(title=f"Slot {slot_no} Images")
        self.panel = panel
        self.riddle_id = riddle_id
        self.riddle_image = TextInput(label="Riddle Image URL (blank = clear)",
                                      default=current.get("image_url") or "",
                                      required=False, max_length=2000)
        self.solution_image = TextInput(label="Solution Image URL (blank = clear)",
                                        default=current.get("solution_url") or "",
                                        required=False, max_length=2000)
        self.add_item(self.riddle_image)
        self.add_item(self.solution_image)

    async def on_submit(self, interaction: Interaction):
        if interaction.guild is None:
            return
        if not await safe_defer(interaction):
            return

        r_img = clean_value(self.riddle_image.value)
        s_img = clean_value(self.solution_image.value)
        if r_img and not is_http_url(r_img):
            self.panel.last_info = "❌ Invalid riddle image URL (must start with http/https)."
            await self.panel.safe_edit_panel()
            return
        if s_img and not is_http_url(s_img):
            self.panel.last_info = "❌ Invalid solution image URL (must start with http/https)."
            await self.panel.safe_edit_panel()
            return

        ok, reason = await self.panel.cog.save_slot_images(
            interaction.guild.id, self.riddle_id, interaction.user.id, r_img, s_img)
        self.panel.last_info = {
            True: "✅ Images updated.",
        }.get(ok, {"conflict": "⚠️ Riddle no longer open.",
                   "error": "❌ Could not update images (DB error)."}.get(reason, "❌ Failed."))
        await self.panel.safe_edit_panel()


# =============================================================================
# PING ROLES PICKER
# =============================================================================
class PingRolesPickerView(View):
    def __init__(self, panel: "RiddleAdminPanelView", slot_no: int,
                 riddle_id: int, current_ids: list[int]):
        super().__init__(timeout=180)
        self.panel = panel
        self.slot_no = slot_no
        self.riddle_id = riddle_id
        self.current_ids = current_ids
        self.picker_message: Optional[discord.Message] = None
        self._picked_ids: list[int] = list(current_ids[:MAX_EXTRA_PING_ROLES])

        self.role_select = discord.ui.RoleSelect(
            placeholder=f"Up to {_ROLE_SELECT_MAX} extra ping roles",
            min_values=0, max_values=_ROLE_SELECT_MAX, row=0)
        with contextlib.suppress(Exception):
            self.role_select.default_values = [
                discord.SelectDefaultValue(id=rid, type=discord.SelectDefaultValueType.role)
                for rid in current_ids[:_ROLE_SELECT_MAX]]
        self.role_select.callback = self.on_role_select
        self.add_item(self.role_select)

        save_btn = discord.ui.Button(label="💾 Save", style=discord.ButtonStyle.success, row=1)
        clear_btn = discord.ui.Button(label="🗑 Clear", style=discord.ButtonStyle.secondary,
                                      row=1)
        cancel_btn = discord.ui.Button(label="✖ Cancel", style=discord.ButtonStyle.danger,
                                       row=1)
        save_btn.callback = self.on_save
        clear_btn.callback = self.on_clear
        cancel_btn.callback = self.on_cancel
        self.add_item(save_btn)
        self.add_item(clear_btn)
        self.add_item(cancel_btn)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id == self.panel.owner_id:
            return True
        await quiet_respond(interaction,
                            "🔒 This picker belongs to someone else. Run `/riddle` yourself.")
        return False

    def _filter_ids(self, ids: list[int]) -> list[int]:
        out: list[int] = []
        seen: set[int] = set()
        for rid in ids:
            if not rid or rid == RIDDLE_ROLE_ID or rid in seen:
                continue
            seen.add(rid)
            out.append(rid)
            if len(out) >= MAX_EXTRA_PING_ROLES:
                break
        return out

    async def _close_picker(self, interaction: Interaction):
        with contextlib.suppress(discord.HTTPException, discord.NotFound):
            if self.picker_message:
                await self.picker_message.delete()
            else:
                await interaction.delete_original_response()
        self.stop()

    async def on_role_select(self, interaction: Interaction):
        self._picked_ids = self._filter_ids([r.id for r in self.role_select.values])
        if not await safe_defer(interaction, ephemeral=True):
            return
        preview = ", ".join(f"<@&{r}>" for r in self._picked_ids) or "*none*"
        with contextlib.suppress(discord.HTTPException, discord.NotFound):
            if self.picker_message:
                await self.picker_message.edit(
                    content=f"**Selected:** {preview}\nTap **💾 Save** to apply.",
                    view=self, allowed_mentions=discord.AllowedMentions.none())

    async def _persist(self, interaction: Interaction, csv: Optional[str], ok_msg: str):
        gid = interaction.guild.id if interaction.guild else 0
        ok, reason = await self.panel.cog.save_slot_mentions(
            gid, self.riddle_id, interaction.user.id, csv)
        self.panel.last_info = ok_msg if ok else {
            "conflict": "⚠️ Riddle no longer open.",
            "error": "❌ Could not update ping roles (DB error).",
        }.get(reason, "❌ Failed.")
        await self._close_picker(interaction)
        await self.panel.safe_edit_panel()

    async def on_save(self, interaction: Interaction):
        if interaction.guild is None:
            return
        if not await safe_defer(interaction, ephemeral=True):
            return
        ids = self._filter_ids(self._picked_ids)
        csv = ",".join(str(i) for i in ids) if ids else None
        await self._persist(interaction, csv, f"✅ Ping roles updated ({len(ids)} extra).")

    async def on_clear(self, interaction: Interaction):
        if interaction.guild is None:
            return
        if not await safe_defer(interaction, ephemeral=True):
            return
        await self._persist(interaction, None, "✅ Cleared all extra ping roles.")

    async def on_cancel(self, interaction: Interaction):
        if not await safe_defer(interaction, ephemeral=True):
            return
        self.panel.last_info = "Ping roles unchanged."
        await self._close_picker(interaction)
        await self.panel.safe_edit_panel()

    async def on_timeout(self):
        with contextlib.suppress(discord.HTTPException, discord.NotFound):
            if self.picker_message:
                await self.picker_message.delete()
        self.stop()


# =============================================================================
# PERSISTENT VIEWS
# =============================================================================
class SubmitButton(discord.ui.Button):
    """
    Two protection layers during the grace period:
      1. rendered `disabled` so Discord blocks the click client-side
      2. a DB check in the callback, because a stale client or a restart must
         not be able to bypass the lock
    """

    def __init__(self, cog: "RiddleCog", *, locked: bool = False,
                 unlock_at: Optional[str] = None):
        label = "🧠 Submit Solution"
        if locked:
            # Absolute clock time, NOT a countdown: the label is baked into the
            # message at render time and never refreshes, so "Opens in 5m" would
            # still say 5m when 30 seconds are left.
            # No timezone suffix - Discord truncates long labels on narrow phone
            # screens and "(UTC)" is the first thing to be cut, leaving "(U…".
            # The embed above carries a per-viewer localised timestamp anyway.
            clock = format_clock_time(unlock_at, with_zone=False)
            label = f"🔒 Opens {clock}" if clock else "🔒 Not open yet"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary if locked else discord.ButtonStyle.primary,
            custom_id=SUBMIT_BUTTON_ID, disabled=locked)
        self.cog = cog

    async def callback(self, interaction: Interaction):
        if interaction.guild is None:
            return
        # Slot 1 is the single source of truth. Looking the riddle up by message
        # id first and falling back to slot 1 meant a stale post (one whose ref
        # was cleared but whose message survived) silently submitted against a
        # different riddle.
        r = await self.cog.repo.get_open_slot1(interaction.guild.id)
        if not r:
            await quiet_respond(interaction, "⚠️ There is no active riddle right now.")
            return

        if interaction.message and to_int(r.get("posted_message_id"), 0) not in (
                0, interaction.message.id):
            await quiet_respond(
                interaction,
                "⚠️ This post is out of date — scroll down for the current riddle.")
            return

        if submit_is_locked(r):
            unlock = submit_unlock_iso(r)
            await quiet_respond(
                interaction,
                f"🔒 **Not open yet.** Submissions start at "
                f"**{format_clock_time(unlock)}** ({discord_ts(unlock, 'R')}).\n"
                f"Use the time to read it properly — everyone starts together.")
            return

        with contextlib.suppress(discord.HTTPException, discord.NotFound):
            await interaction.response.send_modal(
                SubmitSolutionModal(self.cog, to_int(r.get("id"), 0)))


class SubmitButtonView(LoggedPersistentView):
    def __init__(self, cog: "RiddleCog", *, locked: bool = False,
                 unlock_at: Optional[str] = None):
        super().__init__(timeout=None)
        self.add_item(SubmitButton(cog, locked=locked, unlock_at=unlock_at))


class _VoteBaseButton(discord.ui.Button):
    def __init__(self, cog: "RiddleCog", approve: bool, label: str,
                 style: discord.ButtonStyle, custom_id: str):
        super().__init__(label=label, style=style, custom_id=custom_id)
        self.cog = cog
        self.approve = approve

    async def callback(self, interaction: Interaction):
        if interaction.guild is None or interaction.message is None:
            return

        if not isinstance(interaction.user, discord.Member) or not member_has_role(
            interaction.user, RIDDLE_MANAGER_ROLE_ID
        ):
            await quiet_respond(interaction, "🔒 Only riddle managers may vote.")
            return

        if not await safe_defer(interaction, ephemeral=True):
            return

        try:
            if self.approve:
                status, ctx = await self.cog.repo.approve_submission(
                    interaction.message.id, interaction.user.id)
            else:
                status, ctx = await self.cog.repo.reject_submission(
                    interaction.message.id, interaction.user.id)
        except Exception:
            logger.exception("Vote button DB error")
            await quiet_followup(interaction,
                                 "❌ Internal DB error while processing your vote. Check logs.")
            return

        if status in {"not_found", "already_done", "riddle_closed"}:
            reason = {
                "not_found": ("This submission is no longer tracked. It was most likely "
                              "superseded after a bot restart — you can ignore this message."),
                "already_done": "This submission was already voted on.",
                "riddle_closed": "The related riddle is already closed or was rotated.",
            }[status]
            await quiet_followup(interaction, f"⚠️ {reason}")
            with contextlib.suppress(discord.HTTPException, discord.NotFound):
                await interaction.message.edit(view=None)
            return

        logger.info("Riddle vote %s: submission=%s riddle=%s moderator=%s (%s)",
                    "APPROVE" if self.approve else "REJECT",
                    (ctx or {}).get("submission_id"), (ctx or {}).get("riddle_id"),
                    interaction.user.id, interaction.user)

        try:
            if ctx and self.approve:
                await self.cog.finalize_correct(interaction.guild, ctx, interaction.user)
            elif ctx:
                await self.cog.finalize_wrong(interaction.guild, ctx, interaction.user)
        except Exception:
            logger.exception("Vote finalize failed")
            await quiet_followup(
                interaction,
                "⚠️ Vote was recorded (XP/stats applied) but some Discord posts failed. "
                "Check the logs.")
            return

        await edit_vote_result_message(interaction.message, ok=self.approve,
                                       moderator_mention=interaction.user.mention)
        await quiet_followup(
            interaction,
            "✅ Vote applied: **Correct** (solved flow triggered)." if self.approve else
            "✅ Vote applied: **Wrong** (public post sent, riddle stays open).")


class VoteSuccessButton(_VoteBaseButton):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(cog, True, "👍 Correct",
                         discord.ButtonStyle.success, VOTE_UP_BUTTON_ID)


class VoteFailButton(_VoteBaseButton):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(cog, False, "👎 Wrong",
                         discord.ButtonStyle.danger, VOTE_DOWN_BUTTON_ID)


class VoteButtons(LoggedPersistentView):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(timeout=None)
        self.add_item(VoteSuccessButton(cog))
        self.add_item(VoteFailButton(cog))


# =============================================================================
# ADMIN PANEL  ( /riddle )
# =============================================================================
class _BaseSlotSelect(Select):
    _placeholder = "Select slot"
    _kind_label = ""

    def __init__(self, panel: "RiddleAdminPanelView", *,
                 options: list[discord.SelectOption], row: int):
        self.panel = panel
        if not options:
            options = [discord.SelectOption(label=f"(no {self._kind_label} slots)",
                                            value="_none_", default=True)]
        super().__init__(placeholder=self._placeholder, min_values=1, max_values=1,
                         options=options[:_DISCORD_SELECT_MAX], row=row)

    async def callback(self, interaction: Interaction):
        val = self.values[0]
        if val == "_none_":
            await safe_defer(interaction)
            return
        self.panel.selected_slot = max(1, min(MAX_RIDDLE_SLOTS, to_int(val, 1)))
        if not await safe_defer(interaction):
            return
        self.panel.last_info = f"Selected {self._kind_label} slot {self.panel.selected_slot}."
        await self.panel.safe_edit_panel()


class FilledSlotsSelect(_BaseSlotSelect):
    _placeholder = "📋 Filled slots — pick to edit"
    _kind_label = "filled"

    def __init__(self, panel: "RiddleAdminPanelView", row: int):
        opts: list[discord.SelectOption] = []
        for slot in range(1, MAX_RIDDLE_SLOTS + 1):
            r = panel.slot_map.get(slot)
            if not r:
                continue
            rot = max(0, to_int(r.get("rotation_count"), 0))
            opts.append(discord.SelectOption(
                label=f"Slot {slot} · No.{_riddle_display_no(r)}"
                      + (f"  🔁{rot}" if rot else ""),
                value=str(slot),
                # Mobile shows select descriptions in a smaller font and cuts
                # them earlier than desktop - 90 chars was mostly invisible.
                description=_first_line(r.get("solution"), 40)[:100],
                default=(slot == panel.selected_slot), emoji="🧩"))
        super().__init__(panel, options=opts, row=row)


class EmptySlotsSelect(_BaseSlotSelect):
    _placeholder = "🈳 Empty slots — pick to fill"
    _kind_label = "empty"

    def __init__(self, panel: "RiddleAdminPanelView", row: int):
        opts: list[discord.SelectOption] = []
        for slot in range(1, MAX_RIDDLE_SLOTS + 1):
            if panel.slot_map.get(slot):
                continue
            opts.append(discord.SelectOption(
                label=f"Slot {slot}", value=str(slot),
                description="EMPTY — tap to create",
                default=(slot == panel.selected_slot), emoji="⬜"))
        super().__init__(panel, options=opts, row=row)


class PanelButton(discord.ui.Button):
    def __init__(self, panel: "RiddleAdminPanelView", label: str, action: str, row: int,
                 style: discord.ButtonStyle = discord.ButtonStyle.primary):
        super().__init__(label=label, style=style, row=row)
        self.panel = panel
        self.action = action

    async def callback(self, interaction: Interaction):
        try:
            await self.panel.handle_action(interaction, self.action)
        except discord.NotFound:
            return
        except discord.HTTPException as e:
            if getattr(e, "code", None) == 50027 or getattr(e, "status", None) == 401:
                await quiet_respond(interaction,
                                    "⌛ This admin panel session expired. Run `/riddle` again.")
                self.panel.stop()
                return
            logger.exception("Panel button '%s' failed", self.action)
        except Exception:
            logger.exception("Panel button '%s' failed", self.action)
            await quiet_respond(interaction,
                                f"❌ Action `{self.action}` crashed. Check the logs.")


class RiddleAdminPanelView(View):
    """
    Mobile note: Discord splits a button row evenly across the available width.
    With four buttons per row a phone shows ~8 characters each, which turned
    "✏️ Edit Content" and "🖼️ Edit Images" into two identical-looking "✏️ Edit…"
    / "🖼️ Edit…" buttons. Labels are therefore short and carry a distinct
    emoji, and rows hold at most three action buttons.
    """

    def __init__(self, cog: "RiddleCog", owner_id: int, guild_id: int):
        # Below the 15 min webhook-token lifetime, so on_timeout can still
        # disable the buttons instead of failing with error 50027.
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.selected_slot: int = 1
        self.slot_map: dict[int, dict] = {}
        self.state: dict = {}
        self.last_info: str = "Ready."
        self.message: Optional[discord.Message] = None
        self.show_full_preview: bool = False

    @property
    def guild(self) -> Optional[discord.Guild]:
        """
        self.message is a WebhookMessage from an ephemeral followup; its
        `.guild` is backed by _WebhookState and is effectively always None.
        Resolve through the bot instead.
        """
        return self.cog.bot.get_guild(self.guild_id)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await quiet_respond(
            interaction,
            "🔒 This panel belongs to another manager. Run `/riddle` to get your own.")
        return False

    # ------------------------------------------------------------------- data
    async def refresh_data(self):
        self.slot_map = await self.cog.repo.open_slot_map(self.guild_id)
        self.state = await self.cog.repo.get_state_row(self.guild_id)

    async def rebuild_items(self):
        self.clear_items()
        enabled = bool(to_int(self.state.get("is_enabled"), 0))

        self.add_item(FilledSlotsSelect(self, row=0))
        self.add_item(EmptySlotsSelect(self, row=1))

        # Row 2 – edit the selected slot
        self.add_item(PanelButton(self, "✏️ Text", "edit_content", 2))
        self.add_item(PanelButton(self, "🖼️ Images", "edit_images", 2))
        self.add_item(PanelButton(self, "🎯 Pings", "edit_mentions", 2))

        # Row 3 – move / remove the selected slot
        self.add_item(PanelButton(self, "↘ To End", "move_to_end", 3,
                                  discord.ButtonStyle.secondary))
        self.add_item(PanelButton(self, "🗑️ Delete", "delete_slot", 3,
                                  discord.ButtonStyle.danger))
        self.add_item(PanelButton(self, "🔒 Close", "close_active", 3,
                                  discord.ButtonStyle.danger))

        # Row 4 – system
        self.add_item(PanelButton(
            self, "🔴 OFF" if enabled else "🟢 ON", "toggle", 4,
            discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success))
        self.add_item(PanelButton(self, "📢 Post", "post_now", 4))
        self.add_item(PanelButton(self, "🔎 Detail" if not self.show_full_preview
                                  else "📇 Kompakt", "toggle_preview", 4,
                                  discord.ButtonStyle.secondary))
        self.add_item(PanelButton(self, "🔄", "refresh", 4, discord.ButtonStyle.secondary))

    # ----------------------------------------------------------------- status
    def _system_status_display(self) -> tuple[str, discord.Color]:
        enabled = bool(to_int(self.state.get("is_enabled"), 0))
        hiatus_until = self.state.get("hiatus_until") or None
        if enabled and hiatus_until and iso_in_future(hiatus_until):
            remaining = hours_until(hiatus_until) or 0.0
            return (f"🟡 ON · hiatus {format_duration_hours(remaining, short=True)}",
                    discord.Color.gold())
        if enabled:
            return "🟢 ON", discord.Color.green()
        return "🟠 OFF", discord.Color.orange()

    def _submit_lock_display(self) -> Optional[str]:
        if SUBMIT_DELAY_MINUTES <= 0:
            return None
        slot1 = self.slot_map.get(1)
        if not slot1 or not slot1.get("first_posted_at"):
            return None
        unlock = submit_unlock_iso(slot1)
        if submit_is_locked(slot1):
            return (f"🔒 **LOCKED** — opens **{format_clock_time(unlock)}** "
                    f"({discord_ts(unlock, 'R')})")
        return f"🔓 open — unlocked {discord_ts(unlock, 'R')}"

    async def _slot1_auto_move_status(self) -> Optional[str]:
        slot1_row = self.slot_map.get(1)
        if not slot1_row:
            return None

        enabled = bool(to_int(self.state.get("is_enabled"), 0))
        hiatus_until = self.state.get("hiatus_until") or None
        if not enabled:
            return "⏸ paused (system OFF)"
        if hiatus_until and iso_in_future(hiatus_until):
            return "⏸ paused (solved hiatus)"

        age_h = hours_since(slot1_row.get("first_posted_at"))
        if age_h is None:
            return "⏳ waiting for first post"

        rid_s1 = to_int(slot1_row.get("id"), 0)
        pending = await self.cog.repo.count_pending_submissions_for_riddle(rid_s1)
        remaining_h = UNSOLVED_ROTATION_HOURS - age_h
        cap_remaining_h = ROTATION_HARD_CAP_HOURS - age_h
        bonus_note = (f" · **+{UNSOLVED_ROTATION_XP_BONUS} XP**"
                      if UNSOLVED_ROTATION_XP_BONUS > 0 else "")

        if remaining_h > 0:
            base = f"🕒 in **{format_duration_hours(remaining_h)}**{bonus_note}"
            if pending:
                return (f"{base}\n⚠️ {pending} pending vote(s) delay it — "
                        f"hard cap in **{format_duration_hours(cap_remaining_h)}**")
            return base

        if not pending:
            return (f"🚨 next tick (overdue **{format_duration_hours(remaining_h)}**)"
                    f"{bonus_note}")
        if cap_remaining_h > 0:
            return (f"⛔ blocked by {pending} pending vote(s)\n"
                    f"forced in **{format_duration_hours(cap_remaining_h)}** "
                    f"(cap {ROTATION_HARD_CAP_HOURS}h)")
        return (f"🚨 hard cap reached — forced next tick, "
                f"{pending} pending vote(s) will be cancelled")

    # ----------------------------------------------------------------- embeds
    def _slot_lines(self) -> list[str]:
        """
        One slot renders as up to three SHORT stacked lines instead of a single
        long "·"-separated one. A dense line wraps into 4-5 ragged lines on a
        phone and the slot boundaries become impossible to see.
        Only PANEL_SLOT_LIST_LIMIT slots are listed in full.
        """
        lines: list[str] = []
        shown = 0
        hidden_filled = 0

        for slot in range(1, MAX_RIDDLE_SLOTS + 1):
            row = self.slot_map.get(slot)
            is_selected = slot == self.selected_slot

            if shown >= PANEL_SLOT_LIST_LIMIT and not is_selected:
                if row:
                    hidden_filled += 1
                continue

            marker = "🟩" if is_selected else ("⬛" if row else "⬜")
            if not row:
                lines.append(f"{marker} **Slot {slot}** — `EMPTY`")
                shown += 1
                continue

            xp = to_int(row.get("xp"), 0)
            rot = max(0, to_int(row.get("rotation_count"), 0))
            extras = parse_csv_role_ids(row.get("mention_role_ids"))

            head = f"{marker} **Slot {slot}** · No.{_riddle_display_no(row)}"
            if slot == 1:
                age = hours_since(row.get("first_posted_at"))
                if age is None:
                    head += " · 👉 not posted"
                elif submit_is_locked(row):
                    head += f" · 👉 {format_duration_hours(age, short=True)} 🔒"
                else:
                    head += f" · 👉 {format_duration_hours(age, short=True)}"
            lines.append(head)

            meta = f"{IND}{xp} XP · {level_badge(xp)}"
            if rot >= 1:
                meta += f" · 🔁{rot}"
            if extras:
                meta += f" · +{len(extras)} roles"
            lines.append(meta)
            lines.append(f"{IND}_{_first_line(row.get('solution'), 40)}_")
            shown += 1

        if hidden_filled or shown < MAX_RIDDLE_SLOTS:
            rest = MAX_RIDDLE_SLOTS - shown
            if rest > 0:
                lines.append(f"⋯ *+{rest} more slot(s), {hidden_filled} filled — "
                             f"use the dropdowns*")
        return lines

    async def build_embeds(self) -> list[discord.Embed]:
        guild = self.guild
        solved_cached = await self.cog.repo.get_cached_solved_total(self.guild_id)
        sys_val, sys_color = self._system_status_display()
        occupied = sum(1 for s in range(1, MAX_RIDDLE_SLOTS + 1) if self.slot_map.get(s))

        main = discord.Embed(title="🗂️ Riddle Control Center", color=sys_color)
        # Two inline fields (half width each) survive a phone screen; three drop
        # to a third of the width and wrap mid-value.
        main.add_field(name="🧩 System", value=sys_val, inline=True)
        main.add_field(name="⁉️ Solved", value=str(solved_cached), inline=True)
        main.add_field(name="📦 Slots",
                       value=f"**{occupied}/{MAX_RIDDLE_SLOTS}** filled · "
                             f"selected: **{self.selected_slot}**",
                       inline=False)

        lock_line = self._submit_lock_display()
        if lock_line:
            main.add_field(name=f"🧠 Submit ({SUBMIT_DELAY_MINUTES} min grace)",
                           value=clamp_embed_value(lock_line), inline=False)

        auto_move_line = await self._slot1_auto_move_status()
        if auto_move_line:
            main.add_field(name=f"⏭ Auto-Move (after {UNSOLVED_ROTATION_HOURS}h)",
                           value=clamp_embed_value(auto_move_line), inline=False)

        lines = self._slot_lines()
        main.add_field(name="Slots",
                       value=clamp_embed_value("\n".join(lines)) if lines else "*none*",
                       inline=False)

        if self.last_info:
            main.add_field(name="ℹ️ Info", value=clamp_embed_value(self.last_info),
                           inline=False)

        embeds: list[discord.Embed] = [main]

        row = self.slot_map.get(self.selected_slot)
        if row:
            shown_no = _riddle_display_no(row)
            r_url = clean_value(row.get("image_url"))
            s_url = clean_value(row.get("solution_url"))
            xp_val = to_int(row.get("xp"), 0)
            rot = max(0, to_int(row.get("rotation_count"), 0))
            base_xp = to_int(row.get("base_xp"), xp_val)

            if self.show_full_preview:
                preview = discord.Embed(
                    title=f"🔍 Slot {self.selected_slot} · Riddle No.{shown_no}",
                    description=clamp_embed_description(row.get("text") or "*No text*"),
                    color=discord.Color.blurple())
                extra_ids = parse_csv_role_ids(
                    row.get("mention_role_ids"))[:MAX_EXTRA_PING_ROLES]
                extra_mentions = (", ".join(f"<@&{rid}>" for rid in extra_ids)
                                  if extra_ids else "*none*")
                _add_xp_level(preview, xp_val)
                preview.add_field(
                    name="🔔 Ping Roles",
                    value=clamp_embed_value(
                        f"**Base:** <@&{RIDDLE_ROLE_ID}>\n**Extra:** {extra_mentions}"),
                    inline=False)
                if rot >= 1:
                    preview.add_field(
                        name="🔁 Auto-Rotations",
                        value=f"**{rot}×** · base was {base_xp} XP → now {xp_val} XP "
                              f"(+{max(0, xp_val - base_xp)})",
                        inline=False)
                sol = row.get("solution")
                preview.add_field(name="✅ Solution (stored)",
                                  value=clamp_embed_value(_spoiler(sol) if sol else "*not set*"),
                                  inline=False)
                # URLs as compact links instead of raw strings - a 2000-char URL
                # is unreadable on any screen and murderous on a phone.
                img_lines = [
                    f"🖼️ Riddle: {'[link](' + r_url + ')' if is_http_url(r_url) else '*not set*'}",
                    f"🧩 Solution: {'[link](' + s_url + ')' if is_http_url(s_url) else '*not set*'}",
                ]
                preview.add_field(name="Images", value="\n".join(img_lines), inline=False)
                posted_abs = discord_ts(row.get("first_posted_at"), "f")
                if posted_abs:
                    unlock = submit_unlock_iso(row)
                    extra = (f"\n🔓 Opens **{format_clock_time(unlock)}** "
                             f"({discord_ts(unlock, 'R')})") if unlock else ""
                    preview.add_field(
                        name="📌 First posted",
                        value=f"{posted_abs} · "
                              f"{discord_ts(row.get('first_posted_at'), 'R')}{extra}",
                        inline=False)
                # The riddle image goes here as a full-width image instead of
                # two extra embeds carrying a single thumbnail each - those
                # turned the panel into five screen-heights on mobile and
                # pushed the buttons far below the fold.
                if is_http_url(r_url):
                    preview.set_image(url=r_url)
                elif is_http_url(s_url):
                    preview.set_image(url=s_url)
                preview.set_footer(text=f"Full preview · id={to_int(row.get('id'), 0)}")
                embeds.append(preview)
            else:
                desc = f"**Solution (1st line):**\n{_first_line(row.get('solution'), 120)}"
                if rot >= 1:
                    desc += f"\n\n🔁 Auto-rotated **{rot}×** · base {base_xp} XP"
                preview = discord.Embed(
                    title=f"🔍 Slot {self.selected_slot} · Riddle No.{shown_no}",
                    description=clamp_embed_description(desc),
                    color=discord.Color.blurple())
                _add_xp_level(preview, xp_val)
                if is_http_url(s_url):
                    preview.set_thumbnail(url=s_url)
                preview.set_footer(text=f"Compact · id={to_int(row.get('id'), 0)}")
                embeds.append(preview)

        return self._guard_embed_budget(embeds)

    @staticmethod
    def _guard_embed_budget(embeds: list[discord.Embed]) -> list[discord.Embed]:
        """Discord allows 10 embeds and 6000 total characters per message."""
        embeds = embeds[:10]
        total = 0
        out: list[discord.Embed] = []
        for e in embeds:
            size = len(e)
            if total + size > 5900:
                logger.debug("Panel embed budget exceeded - dropping trailing embeds")
                break
            total += size
            out.append(e)
        return out or [discord.Embed(title="🗂️ Riddle Control Center",
                                     description="*(panel too large to render)*")]

    # -------------------------------------------------------------- rendering
    async def safe_edit_panel(self):
        if not self.message:
            return
        await self.refresh_data()
        await self.rebuild_items()
        try:
            await self.message.edit(
                embeds=await self.build_embeds(), view=self,
                allowed_mentions=discord.AllowedMentions.none())
        except discord.NotFound:
            self.message = None
        except discord.HTTPException as e:
            if getattr(e, "code", None) == 50027 or getattr(e, "status", None) == 401:
                self.message = None
                self.stop()
                logger.info("Admin panel session expired - user must rerun /riddle.")
                return
            logger.exception("Panel edit failed")
        except Exception:
            logger.exception("Panel edit failed")

    async def on_timeout(self):
        if self.message:
            with contextlib.suppress(discord.HTTPException, discord.NotFound):
                for child in self.children:
                    if hasattr(child, "disabled"):
                        child.disabled = True
                # Drop the embeds too - a timeout notice sitting on top of a
                # full panel reads like the panel is still usable.
                await self.message.edit(
                    content="⌛ Panel timed out — run `/riddle` again.",
                    embeds=[], view=self)
        self.stop()

    # ---------------------------------------------------------------- actions
    async def handle_action(self, interaction: Interaction, action: str):
        if interaction.guild is None:
            return
        gid = interaction.guild.id

        # ---- MODAL actions must send the modal BEFORE any defer ----
        if action == "edit_content":
            row = self.slot_map.get(self.selected_slot)
            rid = to_int(row.get("id"), 0) if row else None
            with contextlib.suppress(discord.HTTPException, discord.NotFound):
                await interaction.response.send_modal(
                    RiddleContentModal(self, self.selected_slot, rid, row))
            return

        if action == "edit_images":
            row = self.slot_map.get(self.selected_slot)
            if not row:
                if await safe_defer(interaction):
                    self.last_info = "⚠️ Slot empty — fill it first."
                    await self.safe_edit_panel()
                return
            with contextlib.suppress(discord.HTTPException, discord.NotFound):
                await interaction.response.send_modal(
                    RiddleImagesModal(self, self.selected_slot,
                                      to_int(row.get("id"), 0), row))
            return

        if action == "edit_mentions":
            row = self.slot_map.get(self.selected_slot)
            if not row:
                if await safe_defer(interaction):
                    self.last_info = "⚠️ Slot empty — fill it first."
                    await self.safe_edit_panel()
                return
            if not await safe_defer(interaction, ephemeral=True):
                return
            current_ids = parse_csv_role_ids(
                row.get("mention_role_ids"))[:MAX_EXTRA_PING_ROLES]
            picker = PingRolesPickerView(self, self.selected_slot,
                                         to_int(row.get("id"), 0), current_ids)
            preview = ", ".join(f"<@&{r}>" for r in current_ids) or "*none*"
            picker.picker_message = await interaction.followup.send(
                content=(f"🎯 **Ping roles · Slot {self.selected_slot}**\n"
                         f"Current: {preview}\n"
                         f"Base <@&{RIDDLE_ROLE_ID}> is always pinged."),
                view=picker, ephemeral=True, wait=True,
                allowed_mentions=discord.AllowedMentions.none())
            return

        # ---- generic actions ----
        if not await safe_defer(interaction):
            return

        handler = getattr(self, f"_act_{action}", None)
        if handler is None:
            logger.warning("Unknown panel action %r", action)
            self.last_info = f"⚠️ Unknown action `{action}`."
            await self.safe_edit_panel()
            return
        await handler(interaction, gid)

    async def _act_toggle_preview(self, interaction: Interaction, gid: int):
        self.show_full_preview = not self.show_full_preview
        self.last_info = ("🔎 Preview: FULL." if self.show_full_preview
                          else "📇 Preview: COMPACT.")
        await self.safe_edit_panel()

    async def _act_refresh(self, interaction: Interaction, gid: int):
        res = await self.cog.enforce_enabled_state(gid, force_repost=False,
                                                   ping_on_first_post=False)
        self.last_info = f"✅ Refreshed (`{res}`)."
        await self.safe_edit_panel()

    async def _act_move_to_end(self, interaction: Interaction, gid: int):
        # Re-read: slot_map may be minutes old and another manager may have
        # rotated meanwhile - acting on a stale riddle_id would hit the wrong row.
        await self.refresh_data()
        row = self.slot_map.get(self.selected_slot)
        if not row:
            self.last_info = "⚠️ Slot is empty (it changed since the panel was drawn)."
            await self.safe_edit_panel()
            return
        was_enabled = await self.cog.disable_for_structure_change(gid)
        # xp_bonus omitted on purpose: a MANUAL move is just reordering, it must
        # not raise the reward or touch rotation_count.
        moved = await self.cog.rotate_riddle_to_end(gid, to_int(row.get("id"), 0),
                                                    ping_new_slot1=False)
        if not moved:
            self.last_info = "⚠️ Move failed — riddle no longer open."
        elif was_enabled:
            self.last_info = ("✅ Moved to end (no XP bonus — manual). "
                              "System set to 🟠 OFF — use 🟢 ON or 📢 Post to resume.")
        else:
            self.last_info = "✅ Moved to end (no XP bonus — manual)."
        await self.safe_edit_panel()

    async def _act_delete_slot(self, interaction: Interaction, gid: int):
        await self.refresh_data()
        row = self.slot_map.get(self.selected_slot)
        if not row:
            self.last_info = "⚠️ Slot already empty (it changed since the panel was drawn)."
            await self.safe_edit_panel()
            return
        was_enabled = await self.cog.disable_for_structure_change(gid)
        ok = await self.cog.close_and_cleanup_riddle(gid, to_int(row.get("id"), 0),
                                                     interaction.user.id)
        if not ok:
            self.last_info = "⚠️ Already closed."
        elif was_enabled:
            self.last_info = ("✅ Deleted. System set to 🟠 OFF — "
                              "use 🟢 ON or 📢 Post to resume.")
        else:
            self.last_info = "✅ Deleted."
        await self.safe_edit_panel()

    async def _act_toggle(self, interaction: Interaction, gid: int):
        if await self.cog.repo.is_enabled(gid):
            await self.cog.disable_and_clear(gid)
            self.last_info = "✅ System turned OFF (active post removed)."
        else:
            res = await self.cog.enable_and_post(gid)
            if res == "no_slot1":
                self.last_info = "⚠️ Cannot turn ON — Slot 1 is empty."
            else:
                self.last_info = (f"✅ System ON (`{res}`). Timer restarted, "
                                  f"submissions locked for {SUBMIT_DELAY_MINUTES} min.")
        await self.safe_edit_panel()

    async def _act_post_now(self, interaction: Interaction, gid: int):
        # Same atomic path as Turn ON: enable + clear hiatus + fresh post + ping,
        # all under one guild lock.
        res = await self.cog.enable_and_post(gid)
        if res == "no_slot1":
            self.last_info = "⚠️ Slot 1 is empty."
        else:
            self.last_info = (f"✅ Posted (`{res}`). Hiatus cleared, "
                              f"submissions locked for {SUBMIT_DELAY_MINUTES} min.")
        await self.safe_edit_panel()

    async def _act_close_active(self, interaction: Interaction, gid: int):
        s1 = await self.cog.repo.get_open_slot1(gid)
        if not s1:
            self.last_info = "⚠️ No active riddle in Slot 1."
            await self.safe_edit_panel()
            return
        # One locked operation: closing first and disabling afterwards left a
        # window where the worker saw an enabled guild with a freshly vacated
        # slot 1 and posted (and pinged) the next riddle, which was then
        # deleted again a moment later.
        ok = await self.cog.close_active_and_disable(gid, to_int(s1.get("id"), 0),
                                                     interaction.user.id)
        self.last_info = ("✅ Active riddle closed. System OFF." if ok
                          else "⚠️ Already closed. System OFF.")
        await self.safe_edit_panel()


# =============================================================================
# CHAMPIONS VIEW  ( /riddle-champ )
# =============================================================================
UserResolver = Callable[[int], Awaitable[tuple[str, str, Optional[str]]]]


class ChampionsView(View):
    """
    Names/avatars are resolved LAZILY, one page at a time, so a 200-entry
    leaderboard no longer triggers 200 member lookups to display 5 rows.

    Rows live in the embed DESCRIPTION rather than in one field each: fields
    stack with a lot of vertical padding on mobile, a compact list does not.
    """

    def __init__(self, entries: list[tuple[int, int, float, int]], total_solved: int,
                 resolver: UserResolver, image_url: Optional[str],
                 owner_id: Optional[int], per_page: int = 5,
                 highlight_user_id: Optional[int] = None):
        super().__init__(timeout=300)
        self.entries = entries
        self.total_solved = total_solved
        self.resolver = resolver
        self.name_cache: dict[int, str] = {}
        self.avatar_cache: dict[int, str] = {}
        self.page = 0
        self.per_page = max(1, per_page)
        self.max_page = max((len(entries) - 1) // self.per_page, 0)
        self.page1_image_url = image_url if is_http_url(image_url) else DEFAULT_IMAGE_URL
        self.default_image_url = DEFAULT_IMAGE_URL
        self.owner_id = owner_id
        self.highlight_user_id = highlight_user_id
        self.message: Optional[discord.Message] = None
        self._own_rank = next(
            (i for i, (uid, *_) in enumerate(entries, start=1) if uid == highlight_user_id),
            None)
        self._sync()

    def _sync(self):
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.max_page
        self.mine_btn.disabled = self._own_rank is None

    def _page_rows(self) -> list[tuple[int, int, float, int]]:
        start = self.page * self.per_page
        return self.entries[start:start + self.per_page]

    async def _resolve_page(self):
        for uid, *_ in self._page_rows():
            if uid in self.name_cache:
                continue
            try:
                _, name, avatar = await self.resolver(uid)
            except Exception:
                logger.debug("Resolver failed for user %s", uid, exc_info=True)
                name, avatar = f"User {uid}", None
            self.name_cache[uid] = name
            if avatar:
                self.avatar_cache[uid] = avatar

    def _name(self, uid: int) -> str:
        return truncate_text(self.name_cache.get(uid, f"User {uid}"), 24)

    async def build_embed(self) -> discord.Embed:
        await self._resolve_page()
        rows = self._page_rows()
        start = self.page * self.per_page

        e = discord.Embed(
            title=f"🏆 Riddle Champions — {self.total_solved} solved",
            color=discord.Color.gold())

        body: list[str] = []
        for i, (uid, solved, percent, xp) in enumerate(rows, start=start + 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"`#{i}`"
            me = " ⬅️ **you**" if uid == self.highlight_user_id else ""
            body.append(f"{medal} **{self._name(uid)}**{me}")
            body.append(f"{IND}🧩 {solved} · 📊 {percent:.1f}% · 🧠 {xp} XP")
        e.description = clamp_embed_description(
            "\n".join(body) if body else "*No entries yet.*")

        footer_bits = [f"Page {self.page + 1}/{self.max_page + 1}",
                       f"{len(self.entries)} ranked"]
        if self._own_rank:
            footer_bits.append(f"your rank: #{self._own_rank}")
        e.set_footer(text=" · ".join(footer_bits))

        if self.page == 0 and rows:
            av = self.avatar_cache.get(rows[0][0])
            if av:
                e.set_thumbnail(url=av)
        img = self.page1_image_url if self.page == 0 else self.default_image_url
        if is_http_url(img):
            e.set_image(url=img)
        return e

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.owner_id is None or interaction.user.id == self.owner_id:
            return True
        await quiet_respond(interaction,
                            "🔒 Use `/riddle-champ` yourself to browse the leaderboard.")
        return False

    async def _render(self, interaction: Interaction):
        self._sync()
        embed = await self.build_embed()
        with contextlib.suppress(discord.HTTPException, discord.NotFound):
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: Interaction, _: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await self._render(interaction)

    @discord.ui.button(label="📍 Me", style=discord.ButtonStyle.primary)
    async def mine_btn(self, interaction: Interaction, _: discord.ui.Button):
        if self._own_rank:
            self.page = min(self.max_page, (self._own_rank - 1) // self.per_page)
        await self._render(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: Interaction, _: discord.ui.Button):
        self.page = min(self.max_page, self.page + 1)
        await self._render(interaction)

    async def on_timeout(self):
        if self.message:
            with contextlib.suppress(discord.HTTPException, discord.NotFound):
                for child in self.children:
                    if hasattr(child, "disabled"):
                        child.disabled = True
                await self.message.edit(view=self)
        self.stop()