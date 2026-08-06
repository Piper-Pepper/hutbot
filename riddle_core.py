# riddle_core.py
from __future__ import annotations

import os
import re
import asyncio
import contextlib
import datetime as dt
import logging
from pathlib import Path
from typing import Any, Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite
import discord
from discord import app_commands, Interaction
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("riddle_system")


# =============================================================================
# CONFIG
# =============================================================================
def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Env %s=%r is not an integer – falling back to %s", name, raw, default)
        return default


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


DB_PATH = _env_str("RIDDLE_DB_PATH", "data/riddle.sqlite3")

# ---------------------------------------------------------------------------
# SINGLE-GUILD OPERATION
# ---------------------------------------------------------------------------
# Channel and role IDs below are GLOBAL env vars, so the riddle system can only
# ever serve ONE guild correctly. If the bot is in several guilds, every guild
# would post into the same channel and the manager-role check would target the
# wrong server.
#
# RIDDLE_GUILD_ID pins the system to exactly one guild. Leave it at 0 only if
# the bot is guaranteed to be in a single guild.
# ---------------------------------------------------------------------------
RIDDLE_GUILD_ID = _env_int("RIDDLE_GUILD_ID", 0)

RIDDLE_CHANNEL_ID = _env_int("RIDDLE_CHANNEL_ID", 0)
VOTE_CHANNEL_ID = _env_int("RIDDLE_VOTE_CHANNEL_ID", 0)

RIDDLE_ROLE_ID = _env_int("RIDDLE_ROLE_ID", 0)
RIDDLE_MANAGER_ROLE_ID = _env_int("RIDDLE_MANAGER_ROLE_ID", 0)
EXCLUDED_COUNT_ROLE_ID = _env_int("RIDDLE_EXCLUDED_COUNT_ROLE_ID", 0)
EXCLUDED_GAMEMASTER_ROLE_ID = _env_int("RIDDLE_EXCLUDED_GAMEMASTER_ROLE_ID", 0)
EXTRA_EXCLUDED_ROLE_IDS_CSV = _env_str("RIDDLE_EXTRA_EXCLUDED_ROLE_IDS", "")

DEFAULT_IMAGE_URL = _env_str("DEFAULT_RIDDLE_IMAGE_URL", "")
ACCESS_DENIED_IMAGE_URL = _env_str("RIDDLE_ACCESS_DENIED_IMAGE_URL", "")

MAX_RIDDLE_SLOTS = _env_int("RIDDLE_MAX_SLOTS", 10)
MAX_EXTRA_PING_ROLES = _env_int("RIDDLE_MAX_EXTRA_PING_ROLES", 3)

# How many slots the admin panel prints in full. Everything beyond is collapsed
# into a single summary line – ten multi-part slot lines are unreadable on a
# phone.
PANEL_SLOT_LIST_LIMIT = _env_int("RIDDLE_PANEL_SLOT_LIST_LIMIT", 5)

# Timezone for FIXED text (button labels). Embeds use Discord timestamps and
# localise per viewer, but a button label is a static string baked into the
# message – it needs one defined zone. E.g. "UTC", "Europe/Berlin".
DISPLAY_TIMEZONE = _env_str("RIDDLE_DISPLAY_TIMEZONE", "UTC")

# Grace period after a fresh post before the Submit button works.
SUBMIT_DELAY_MINUTES = _env_int("RIDDLE_SUBMIT_DELAY_MINUTES", 5)

# Hours a riddle may sit in Slot 1 unsolved before it is auto-moved to the end.
# Countdown starts at the FIRST post; refreshes/edits do NOT reset it, and a
# bot restart no longer resets it either (see clear_all_open_post_refs).
UNSOLVED_ROTATION_HOURS = _env_int("RIDDLE_UNSOLVED_ROTATION_HOURS", 6)

# XP added every time a riddle is AUTO-rotated because nobody solved it.
# Manual "Move to End" from the admin panel does NOT bump XP.
UNSOLVED_ROTATION_XP_BONUS = _env_int("RIDDLE_ROTATION_XP_BONUS", 200)

# Hard ceiling so a permanently unsolved riddle cannot inflate forever.
MAX_RIDDLE_XP = _env_int("RIDDLE_MAX_XP", 50_000)

# Safety valve: rotate even with pending votes once this age is reached, so a
# single un-voted submission can never block the queue forever.
ROTATION_HARD_CAP_HOURS = _env_int(
    "RIDDLE_ROTATION_HARD_CAP_HOURS", max(1, UNSOLVED_ROTATION_HOURS * 2)
)

# After a solve the system stays ON, but no new Slot 1 riddle is posted during
# this hiatus. Bypassable via "Post Now".
SOLVED_HIATUS_HOURS = _env_int("RIDDLE_SOLVED_HIATUS_HOURS", 6)

ROTATION_TICK_SECONDS = _env_int("RIDDLE_ROTATION_TICK_SECONDS", 900)  # 15 min
STATS_REBUILD_DEBOUNCE_SECONDS = _env_int("RIDDLE_STATS_REBUILD_DEBOUNCE", 45)

SUBMIT_BUTTON_ID = "riddle_submit_solution_v2"
VOTE_UP_BUTTON_ID = "riddle_vote_up_v2"
VOTE_DOWN_BUTTON_ID = "riddle_vote_down_v2"

# Must stay below the 15 min interaction webhook token lifetime, otherwise
# on_timeout can no longer disable the panel buttons.
PANEL_TIMEOUT_SECONDS = 840

# (upper_bound_exclusive, label, emoji). Last entry catches all.
LEVEL_TIERS: tuple[tuple[Optional[int], str, str], ...] = (
    (1900, "EASY", "🟢"),
    (3000, "MEDIUM", "🟡"),
    (4000, "HARD", "🔴"),
    (None, "BRAIN-DEAD", "💀"),
)

# Upper bound for manual XP entry in the admin modal. MUST NOT exceed
# MAX_RIDDLE_XP: a manually set value above the rotation ceiling would be
# silently clipped DOWNWARDS on the first auto-rotation. validate_config()
# enforces this.
MAX_XP_INPUT = _env_int("RIDDLE_MAX_XP_INPUT", MAX_RIDDLE_XP)

ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
URL_RE = re.compile(r"(https?://\S+)")
_SQL_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_config() -> list[str]:
    """Return human-readable config problems. Empty list == OK."""
    problems: list[str] = []
    required = {
        "RIDDLE_CHANNEL_ID": RIDDLE_CHANNEL_ID,
        "RIDDLE_VOTE_CHANNEL_ID": VOTE_CHANNEL_ID,
        "RIDDLE_ROLE_ID": RIDDLE_ROLE_ID,
        "RIDDLE_MANAGER_ROLE_ID": RIDDLE_MANAGER_ROLE_ID,
    }
    for name, val in required.items():
        if val <= 0:
            problems.append(f"{name} is missing or 0 – the riddle system cannot function.")
    if not (1 <= MAX_RIDDLE_SLOTS <= 25):
        problems.append(f"RIDDLE_MAX_SLOTS={MAX_RIDDLE_SLOTS} must be 1..25 "
                        "(Discord select menus allow at most 25 options).")
    if not (1 <= MAX_EXTRA_PING_ROLES <= 25):
        problems.append(f"RIDDLE_MAX_EXTRA_PING_ROLES={MAX_EXTRA_PING_ROLES} must be 1..25.")
    if not (1 <= PANEL_SLOT_LIST_LIMIT <= MAX_RIDDLE_SLOTS):
        problems.append(f"RIDDLE_PANEL_SLOT_LIST_LIMIT={PANEL_SLOT_LIST_LIMIT} must be "
                        f"1..RIDDLE_MAX_SLOTS ({MAX_RIDDLE_SLOTS}).")
    if SUBMIT_DELAY_MINUTES < 0:
        problems.append("RIDDLE_SUBMIT_DELAY_MINUTES must be >= 0.")
    if UNSOLVED_ROTATION_HOURS <= 0:
        problems.append("RIDDLE_UNSOLVED_ROTATION_HOURS must be > 0.")
    if SUBMIT_DELAY_MINUTES >= UNSOLVED_ROTATION_HOURS * 60:
        problems.append(
            f"RIDDLE_SUBMIT_DELAY_MINUTES={SUBMIT_DELAY_MINUTES} is >= the rotation "
            f"window ({UNSOLVED_ROTATION_HOURS}h) – the riddle would rotate before "
            f"anyone could submit.")
    if UNSOLVED_ROTATION_XP_BONUS < 0:
        problems.append("RIDDLE_ROTATION_XP_BONUS must be >= 0.")
    if MAX_RIDDLE_XP <= 0:
        problems.append("RIDDLE_MAX_XP must be > 0.")
    if MAX_XP_INPUT <= 0:
        problems.append("RIDDLE_MAX_XP_INPUT must be > 0.")
    if MAX_XP_INPUT > MAX_RIDDLE_XP:
        problems.append(
            f"RIDDLE_MAX_XP_INPUT={MAX_XP_INPUT} exceeds RIDDLE_MAX_XP={MAX_RIDDLE_XP}. "
            f"A manager could set an XP value above the rotation ceiling, which the "
            f"first auto-rotation would clip DOWNWARDS – the riddle would silently "
            f"lose reward. Lower RIDDLE_MAX_XP_INPUT or raise RIDDLE_MAX_XP.")
    if ROTATION_HARD_CAP_HOURS < UNSOLVED_ROTATION_HOURS:
        problems.append(
            f"RIDDLE_ROTATION_HARD_CAP_HOURS={ROTATION_HARD_CAP_HOURS} must be >= "
            f"RIDDLE_UNSOLVED_ROTATION_HOURS={UNSOLVED_ROTATION_HOURS}.")
    if SOLVED_HIATUS_HOURS < 0:
        problems.append("RIDDLE_SOLVED_HIATUS_HOURS must be >= 0.")
    if RIDDLE_CHANNEL_ID > 0 and RIDDLE_CHANNEL_ID == VOTE_CHANNEL_ID:
        problems.append("RIDDLE_CHANNEL_ID equals RIDDLE_VOTE_CHANNEL_ID – "
                        "solutions would leak publicly.")
    return problems


def guild_is_served(guild_id: Optional[int]) -> bool:
    """
    True if the riddle system is responsible for this guild.

    Channel/role config is global, so exactly one guild can be served. With
    RIDDLE_GUILD_ID unset we fall back to "serve everything" for backwards
    compatibility – setup() logs a loud warning in that case.
    """
    if RIDDLE_GUILD_ID <= 0:
        return True
    return to_int(guild_id, 0) == RIDDLE_GUILD_ID


# =============================================================================
# TIME UTILS   (everything stored is naive UTC ISO with a trailing 'Z')
# =============================================================================
def utcnow_naive() -> dt.datetime:
    """Naive UTC now. datetime.utcnow() is deprecated in Python 3.12+."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def now_iso_utc() -> str:
    return utcnow_naive().isoformat() + "Z"


def now_date_str() -> str:
    """Footer date – UTC so it matches every stored timestamp."""
    return utcnow_naive().strftime("%Y/%m/%d")


def parse_iso_utc(s: Optional[str]) -> Optional[dt.datetime]:
    """
    Parse a stored timestamp into NAIVE UTC.

    Everything this module writes is naive-UTC + 'Z', but legacy rows or manual
    DB edits can contain an offset ("...+02:00"). fromisoformat would then hand
    back an AWARE datetime and every subtraction against utcnow_naive() would
    raise TypeError inside the worker tick, killing the whole guild loop.
    So: normalise to UTC and strip the tzinfo.
    """
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).strip().rstrip("Z"))
    except Exception:
        return None
    if t.tzinfo is not None:
        t = t.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t.replace(microsecond=0)


def iso_utc_in_hours(hours: float) -> str:
    ts = utcnow_naive() + dt.timedelta(hours=hours)
    return ts.replace(microsecond=0).isoformat() + "Z"


def iso_add_minutes(iso_ts: Optional[str], minutes: float) -> Optional[str]:
    t = parse_iso_utc(iso_ts)
    if t is None:
        return None
    return (t + dt.timedelta(minutes=minutes)).replace(microsecond=0).isoformat() + "Z"


def iso_to_unix(iso_ts: Optional[str]) -> Optional[int]:
    t = parse_iso_utc(iso_ts)
    if t is None:
        return None
    try:
        return int(t.replace(tzinfo=dt.timezone.utc).timestamp())
    except Exception:
        return None


def discord_ts(iso_ts: Optional[str], style: str = "f") -> Optional[str]:
    """
    Discord dynamic timestamp tag – every viewer sees their own local time and
    the client sizes it to the available width. Preferred over any hand-rolled
    time string, especially on mobile.
    Styles: t f F d D R (R = relative, updates by itself).
    """
    unix = iso_to_unix(iso_ts)
    return f"<t:{unix}:{style}>" if unix is not None else None


def _resolve_display_tz() -> dt.tzinfo:
    name = DISPLAY_TIMEZONE or "UTC"
    if name.upper() == "UTC":
        return dt.timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        logger.warning("RIDDLE_DISPLAY_TIMEZONE=%r is not a valid IANA zone – using UTC. "
                       "(On Windows you may need the 'tzdata' package.)", name)
        return dt.timezone.utc


_DISPLAY_TZ = _resolve_display_tz()


def format_clock_time(iso_ts: Optional[str], *, with_zone: bool = True) -> Optional[str]:
    """
    Stored UTC timestamp -> fixed wall clock, e.g. "16:35 (UTC)".

    Needed for button labels: Discord renders those once and never refreshes
    them, so "opens in 5m" is already wrong a minute later. A clock time stays
    correct until the message is edited.

    with_zone=False for button labels – Discord truncates long labels on narrow
    phone screens and "(UTC)" is the first thing to get cut, leaving "(U…".
    The zone is redundant there anyway because the embed above carries a
    per-viewer localised <t:...:R> timestamp.
    """
    t = parse_iso_utc(iso_ts)
    if t is None:
        return None
    local = t.replace(tzinfo=dt.timezone.utc).astimezone(_DISPLAY_TZ)
    out = local.strftime("%H:%M")
    if with_zone:
        out += f" ({local.strftime('%Z') or 'UTC'})"
    return out


def hours_since(iso_ts: Optional[str]) -> Optional[float]:
    t = parse_iso_utc(iso_ts)
    return None if t is None else (utcnow_naive() - t).total_seconds() / 3600.0


def hours_until(iso_ts: Optional[str]) -> Optional[float]:
    t = parse_iso_utc(iso_ts)
    return None if t is None else max(0.0, (t - utcnow_naive()).total_seconds() / 3600.0)


def seconds_until(iso_ts: Optional[str]) -> Optional[float]:
    t = parse_iso_utc(iso_ts)
    return None if t is None else max(0.0, (t - utcnow_naive()).total_seconds())


def iso_in_future(iso_ts: Optional[str]) -> bool:
    t = parse_iso_utc(iso_ts)
    return bool(t and t > utcnow_naive())


def duration_between_iso(start_iso: Optional[str], end_iso: Optional[str]) -> Optional[float]:
    a, b = parse_iso_utc(start_iso), parse_iso_utc(end_iso)
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600.0


def format_duration_hours(hours: Optional[float], *, short: bool = False) -> str:
    """
    3.7 -> '3h 42m' · 50.2 -> '2d 2h' · 0.05 -> '3m'
    short=True drops the spaces ('3h42m') for tight mobile layouts.

    NOTE: the sign is dropped on purpose – callers that can produce negative
    values (overdue countdowns) phrase them as "overdue by X" themselves.
    """
    if hours is None:
        return "unknown"
    total_minutes = int(round(abs(hours) * 60))
    if total_minutes <= 0:
        return "<1m" if short else "less than a minute"
    days, rem = divmod(total_minutes, 60 * 24)
    hrs, mins = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hrs:
        parts.append(f"{hrs}h")
    if mins and not days:
        parts.append(f"{mins}m")
    sep = "" if short else " "
    return sep.join(parts) or f"{total_minutes}m"


# =============================================================================
# SUBMIT GRACE PERIOD
# =============================================================================
def submit_unlock_iso(riddle: dict, *, posted_at_override: Optional[str] = None) -> Optional[str]:
    """
    When submissions open. None if the riddle was never posted.
    posted_at_override is for the very first post, where first_posted_at is not
    in the DB yet – caller passes the timestamp it is about to store, so hint
    and anchor cannot drift apart.
    """
    anchor = posted_at_override or riddle.get("first_posted_at")
    if not anchor:
        return None
    if SUBMIT_DELAY_MINUTES <= 0:
        return anchor
    return iso_add_minutes(anchor, SUBMIT_DELAY_MINUTES)


def submit_is_locked(riddle: dict, *, posted_at_override: Optional[str] = None) -> bool:
    """
    True while the grace period runs. A riddle that was never posted counts as
    NOT locked, so the button keeps working in edge cases instead of being dead
    with no way to unlock it.
    """
    if SUBMIT_DELAY_MINUTES <= 0:
        return False
    unlock = submit_unlock_iso(riddle, posted_at_override=posted_at_override)
    return bool(unlock and iso_in_future(unlock))


# =============================================================================
# VALUE UTILS
# =============================================================================
def footer_text(guild: Optional[discord.Guild]) -> str:
    return f"{guild.name if guild else 'Unknown Guild'} • {now_date_str()}"


def clean_value(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    vv = str(v).strip()
    return vv or None


def to_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def safe_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def normalize_answer(answer: Optional[str]) -> str:
    """
    Canonical form used for duplicate-answer detection. Stored in
    submissions.answer_norm so the lookup is an indexed equality check instead
    of loading every wrong answer and comparing in Python.
    """
    return " ".join((answer or "").lower().split())


def parse_xp_input(raw: Optional[str], max_xp: int = MAX_XP_INPUT) -> Optional[int]:
    """
    Strict XP parser for admin input. Returns None on anything invalid so the
    caller can show an error instead of silently storing 0 XP (which a plain
    int() fallback did with typos like "150O").
    """
    s = (raw or "").strip()
    if not s:
        return None
    for ch in (" ", "_", ".", ",", "'"):
        s = s.replace(ch, "")
    if not s.isdigit():
        return None
    val = int(s)
    return val if val <= max_xp else None


def is_http_url(url: Optional[str]) -> bool:
    return bool(url and isinstance(url, str) and url.startswith(("http://", "https://")))


def truncate_text(text: Optional[str], max_len: int = 180) -> str:
    t = text or ""
    return t[:max_len] + "…" if len(t) > max_len else t


def clamp_embed_value(text: Optional[str], limit: int = 1024) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def clamp_embed_description(text: Optional[str], limit: int = 4096) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def extract_first_url(text: Optional[str]) -> tuple[str, Optional[str]]:
    text = text or ""
    m = URL_RE.search(text)
    if not m:
        return text.strip(), None
    return URL_RE.sub("", text, count=1).strip(), m.group(1)


def parse_csv_role_ids(s: Optional[str]) -> list[int]:
    if not s:
        return []
    out: list[int] = []
    seen: set[int] = set()
    for p in str(s).split(","):
        p = p.strip()
        if not p:
            continue
        rid = safe_int(p, None)
        if rid is None or rid in seen:
            continue
        seen.add(rid)
        out.append(rid)
    return out


def unique_role_mentions(guild: Optional[discord.Guild], *role_ids: Optional[int]) -> list[str]:
    if guild is None:
        return []
    out: list[str] = []
    seen: set[int] = set()
    for rid in role_ids:
        rid_i = safe_int(rid, None)
        if not rid_i or rid_i in seen:
            continue
        role = guild.get_role(rid_i)
        if role:
            seen.add(rid_i)
            out.append(role.mention)
    return out


# =============================================================================
# INTERACTION UTILS
# =============================================================================
async def safe_defer(interaction: Interaction, *, ephemeral: bool = False,
                     thinking: bool = False) -> bool:
    if interaction.response.is_done():
        return True
    try:
        await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
        return True
    except (discord.NotFound, discord.HTTPException):
        return False


async def quiet_followup(interaction: Interaction, content: str, *, ephemeral: bool = True):
    with contextlib.suppress(discord.HTTPException, discord.NotFound):
        await interaction.followup.send(content, ephemeral=ephemeral)


async def quiet_respond(interaction: Interaction, content: str, *, ephemeral: bool = True):
    with contextlib.suppress(discord.HTTPException, discord.NotFound):
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)


def member_has_role(member: discord.abc.User, role_id: int) -> bool:
    if role_id <= 0:
        return False
    return isinstance(member, discord.Member) and any(r.id == role_id for r in member.roles)


class MissingRiddleManagerRole(app_commands.CheckFailure):
    pass


class WrongGuild(app_commands.CheckFailure):
    pass


def riddle_manager_required():
    async def predicate(interaction: Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            raise MissingRiddleManagerRole()
        if not guild_is_served(interaction.guild.id):
            raise WrongGuild()
        if not member_has_role(interaction.user, RIDDLE_MANAGER_ROLE_ID):
            raise MissingRiddleManagerRole()
        return True
    return app_commands.check(predicate)


def riddle_guild_only():
    """Guild gate without the manager-role requirement (public commands)."""
    async def predicate(interaction: Interaction) -> bool:
        if interaction.guild is None:
            raise WrongGuild()
        if not guild_is_served(interaction.guild.id):
            raise WrongGuild()
        return True
    return app_commands.check(predicate)


async def send_access_denied(interaction: Interaction):
    embed = discord.Embed(
        title="🔒 Access Restricted",
        description=(f"This command is restricted to <@&{RIDDLE_MANAGER_ROLE_ID}>."
                     if RIDDLE_MANAGER_ROLE_ID > 0 else
                     "This command is restricted – but no manager role is configured. "
                     "Check `RIDDLE_MANAGER_ROLE_ID` in your environment."),
        color=discord.Color.orange(),
    )
    if is_http_url(ACCESS_DENIED_IMAGE_URL):
        embed.set_image(url=ACCESS_DENIED_IMAGE_URL)
    embed.set_footer(text="Riddle Manager role required")
    with contextlib.suppress(discord.HTTPException, discord.NotFound):
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def send_wrong_guild(interaction: Interaction):
    await quiet_respond(
        interaction,
        "⚠️ The riddle system is configured for a different server and is not "
        "available here.")


# =============================================================================
# SENTINELS
# =============================================================================
class UnknownMessage:
    """
    "Could not determine whether the message exists" (permission error, 5xx,
    timeout). Callers MUST NOT treat this as "gone" – otherwise transient API
    errors cause duplicate posts.
    """
    __slots__ = ()

    def __repr__(self) -> str:
        return "<UNKNOWN_MESSAGE>"

    def __bool__(self) -> bool:
        return False


UNKNOWN_MESSAGE = UnknownMessage()
MessageLookup = Union[discord.Message, UnknownMessage, None]

# create_submission_pending result codes
DUPLICATE_PENDING = -1     # user already has an open submission for that riddle
SUBMISSION_NOT_ACTIVE = -2  # riddle is not open / no longer in slot 1


# =============================================================================
# DB REPO
# =============================================================================
class RiddleRepo:
    """
    Transaction contract
    --------------------
    * One aiosqlite connection, AUTOCOMMIT mode (isolation_level=None).
    * Every multi-statement mutation wraps itself in BEGIN IMMEDIATE.
    * `self.lock` serialises EVERYTHING – reads included.

    Why reads take the lock too: WAL gives you concurrent readers across
    SEPARATE connections. We have exactly one, driven by a single aiosqlite
    worker thread, so an unlocked read buys no parallelism whatsoever – it only
    buys dirty reads. An unlocked SELECT can land in the middle of an open
    BEGIN IMMEDIATE on the same connection and observe uncommitted rows; if
    that transaction then rolls back, the caller has already acted on data that
    never existed. count_pending_submissions_for_riddle() feeding the rotation
    decision is exactly that hazard.

    Re-entrancy: asyncio.Lock is NOT reentrant. Methods that hold the lock use
    `self.db.execute` directly and must never call `_one`/`_all`/`_exec`.
    """

    def __init__(self):
        self.db: Optional[aiosqlite.Connection] = None
        self.lock = asyncio.Lock()

    # ---------------------------------------------------------------- lifecycle
    async def start(self):
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(DB_PATH, isolation_level=None)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL;")
        await self.db.execute("PRAGMA foreign_keys=ON;")
        await self.db.execute("PRAGMA busy_timeout=5000;")
        await self.db.execute("PRAGMA synchronous=NORMAL;")
        await self._init_db()
        logger.info("RiddleRepo ready (db=%s)", DB_PATH)

    async def close(self):
        if self.db:
            with contextlib.suppress(Exception):
                await self.db.close()
            self.db = None

    # ------------------------------------------------------------ schema/migrate
    @staticmethod
    def _assert_ident(*names: str):
        """
        Guard for the few places that must interpolate an identifier into SQL
        (PRAGMA and ALTER TABLE take no parameters). Everything passed here is
        hard-coded today – this keeps it that way if someone later wires a
        variable through.
        """
        for n in names:
            if not _SQL_IDENT_RE.match(n or ""):
                raise ValueError(f"Refusing to interpolate unsafe SQL identifier: {n!r}")

    async def _column_names(self, table: str) -> list[str]:
        assert self.db is not None
        self._assert_ident(table)
        cur = await self.db.execute(f"PRAGMA table_info({table})")
        rows = await cur.fetchall()
        await cur.close()
        return [r["name"] for r in rows]

    async def _add_col_if_missing(self, table: str, col_name: str, col_def: str):
        assert self.db is not None
        self._assert_ident(table, col_name)
        if not col_def.startswith(col_name):
            raise ValueError(f"col_def {col_def!r} must start with column name {col_name!r}")
        if col_name not in await self._column_names(table):
            await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            logger.info("Migration: added %s.%s", table, col_name)

    async def _init_db(self):
        assert self.db is not None
        schema = """
        CREATE TABLE IF NOT EXISTS riddles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            riddle_no INTEGER,
            slot_no INTEGER,
            is_active INTEGER NOT NULL DEFAULT 0 CHECK(is_active IN (0,1)),
            text TEXT,
            solution TEXT,
            xp INTEGER NOT NULL DEFAULT 0,
            base_xp INTEGER,
            rotation_count INTEGER NOT NULL DEFAULT 0,
            mention_role_ids TEXT,
            image_url TEXT,
            solution_url TEXT,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','solved','closed')),
            created_by INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            posted_channel_id INTEGER,
            posted_message_id INTEGER,
            first_posted_at TEXT,
            solved_by INTEGER,
            solved_at TEXT,
            solved_post_channel_id INTEGER,
            solved_post_message_id INTEGER,
            closed_by INTEGER,
            closed_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_riddle_slot_open
            ON riddles(guild_id, slot_no) WHERE status='open' AND slot_no IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_riddle_active_one
            ON riddles(guild_id) WHERE status='open' AND is_active=1;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_riddle_posted_msg
            ON riddles(posted_message_id) WHERE posted_message_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_riddles_guild_status_slot
            ON riddles(guild_id, status, slot_no);
        CREATE INDEX IF NOT EXISTS idx_riddles_guild_status
            ON riddles(guild_id, status);

        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            riddle_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            answer TEXT NOT NULL,
            answer_norm TEXT,
            vote_message_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','correct','wrong','cancelled')),
            created_at TEXT NOT NULL,
            voted_by INTEGER,
            voted_at TEXT,
            FOREIGN KEY(riddle_id) REFERENCES riddles(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_sub_vote_msg
            ON submissions(vote_message_id) WHERE vote_message_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_sub_riddle_status ON submissions(riddle_id, status);
        CREATE INDEX IF NOT EXISTS idx_sub_guild_status ON submissions(guild_id, status);

        CREATE TABLE IF NOT EXISTS user_stats (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            solved_riddles INTEGER NOT NULL DEFAULT 0,
            xp INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(guild_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS guild_riddle_state (
            guild_id INTEGER PRIMARY KEY,
            is_enabled INTEGER NOT NULL DEFAULT 0 CHECK(is_enabled IN (0,1)),
            hiatus_until TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS guild_stats_cache (
            guild_id INTEGER PRIMARY KEY,
            solved_total_filtered INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS riddle_wrong_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            riddle_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(riddle_id) REFERENCES riddles(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_wrong_riddle ON riddle_wrong_posts(riddle_id);
        CREATE INDEX IF NOT EXISTS idx_wrong_guild ON riddle_wrong_posts(guild_id);
        """
        async with self.lock:
            await self.db.executescript(schema)

            await self._add_col_if_missing("riddles", "solved_post_channel_id",
                                           "solved_post_channel_id INTEGER")
            await self._add_col_if_missing("riddles", "solved_post_message_id",
                                           "solved_post_message_id INTEGER")
            await self._add_col_if_missing("riddles", "first_posted_at", "first_posted_at TEXT")
            # Rotation XP bonus tracking
            await self._add_col_if_missing("riddles", "rotation_count",
                                           "rotation_count INTEGER NOT NULL DEFAULT 0")
            await self._add_col_if_missing("riddles", "base_xp", "base_xp INTEGER")
            await self._add_col_if_missing("guild_riddle_state", "hiatus_until",
                                           "hiatus_until TEXT")
            # Indexed duplicate-answer lookup
            await self._add_col_if_missing("submissions", "answer_norm", "answer_norm TEXT")

            # Backfill base_xp for rows that existed before the bonus feature.
            await self.db.execute(
                "UPDATE riddles SET base_xp = xp WHERE base_xp IS NULL")

            # Backfill answer_norm. Done in Python because SQLite has no
            # "collapse inner whitespace" function; batched so a large table
            # does not build one giant statement.
            await self._backfill_answer_norm()
            await self.db.execute(
                "CREATE INDEX IF NOT EXISTS idx_sub_riddle_norm "
                "ON submissions(riddle_id, status, answer_norm)")

            # riddle_no used to be recomputed as (solved_total + slot_no) on
            # every tick, which produced duplicates whenever an excluded user
            # solved something (the total does not move then). It is an
            # identity now: assigned once at creation, never rewritten.
            # Repair any row that still has NULL.
            await self._backfill_riddle_no()

            # One pending submission per (riddle, user). Clean historical
            # duplicates first or the unique index cannot be created.
            await self.db.execute(
                """
                UPDATE submissions SET status='cancelled', voted_by=0, voted_at=?
                WHERE status='pending' AND id NOT IN (
                    SELECT MIN(id) FROM submissions WHERE status='pending'
                    GROUP BY riddle_id, user_id
                )
                """,
                (now_iso_utc(),),
            )
            try:
                await self.db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sub_one_pending_per_user "
                    "ON submissions(riddle_id, user_id) WHERE status='pending'")
            except Exception:
                logger.exception("Could not create idx_sub_one_pending_per_user – "
                                 "duplicates will only be blocked at app level.")

    async def _backfill_answer_norm(self, batch: int = 500):
        assert self.db is not None
        total = 0
        while True:
            cur = await self.db.execute(
                "SELECT id, answer FROM submissions WHERE answer_norm IS NULL LIMIT ?",
                (batch,))
            rows = await cur.fetchall()
            await cur.close()
            if not rows:
                break
            await self.db.executemany(
                "UPDATE submissions SET answer_norm=? WHERE id=?",
                [(normalize_answer(r["answer"]), to_int(r["id"], 0)) for r in rows])
            total += len(rows)
            if len(rows) < batch:
                break
        if total:
            logger.info("Migration: normalised %s submission answer(s)", total)

    async def _backfill_riddle_no(self):
        """Give every row lacking a riddle_no a stable per-guild number."""
        assert self.db is not None
        cur = await self.db.execute(
            "SELECT id, guild_id FROM riddles WHERE riddle_no IS NULL OR riddle_no <= 0 "
            "ORDER BY guild_id, id")
        rows = [dict(r) for r in await cur.fetchall()]
        await cur.close()
        if not rows:
            return
        counters: dict[int, int] = {}
        for r in rows:
            gid = to_int(r["guild_id"], 0)
            if gid not in counters:
                c2 = await self.db.execute(
                    "SELECT COALESCE(MAX(riddle_no), 0) AS n FROM riddles WHERE guild_id=?",
                    (gid,))
                nrow = await c2.fetchone()
                await c2.close()
                counters[gid] = to_int(nrow["n"] if nrow else 0, 0)
            counters[gid] += 1
            await self.db.execute("UPDATE riddles SET riddle_no=? WHERE id=?",
                                  (counters[gid], to_int(r["id"], 0)))
        logger.info("Migration: assigned riddle_no to %s row(s)", len(rows))

    # ------------------------------------------------------------------ helpers
    async def _one(self, query: str, params: tuple = ()) -> Optional[dict]:
        if self.db is None:
            return None
        async with self.lock:
            cur = await self.db.execute(query, params)
            row = await cur.fetchone()
            await cur.close()
        return dict(row) if row else None

    async def _all(self, query: str, params: tuple = ()) -> list[dict]:
        if self.db is None:
            return []
        async with self.lock:
            cur = await self.db.execute(query, params)
            rows = await cur.fetchall()
            await cur.close()
        return [dict(r) for r in rows]

    async def _exec(self, query: str, params: tuple = ()) -> tuple[int, int]:
        if self.db is None:
            return 0, 0
        async with self.lock:
            cur = await self.db.execute(query, params)
            rc = cur.rowcount
            lid = int(cur.lastrowid or 0)
            await cur.close()
        return rc, lid

    # ------------------------------------------------------- guild state / cache
    async def ensure_guild_state(self, guild_id: int):
        now = now_iso_utc()
        await self._exec("INSERT INTO guild_riddle_state (guild_id, is_enabled, updated_at) "
                         "VALUES (?, 0, ?) ON CONFLICT(guild_id) DO NOTHING", (guild_id, now))
        await self._exec("INSERT INTO guild_stats_cache (guild_id, solved_total_filtered, "
                         "updated_at) VALUES (?, 0, ?) ON CONFLICT(guild_id) DO NOTHING",
                         (guild_id, now))

    async def set_enabled(self, guild_id: int, enabled: bool):
        await self._exec(
            "INSERT INTO guild_riddle_state (guild_id, is_enabled, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET is_enabled=excluded.is_enabled, "
            "updated_at=excluded.updated_at",
            (guild_id, 1 if enabled else 0, now_iso_utc()))

    async def is_enabled(self, guild_id: int) -> bool:
        row = await self._one("SELECT is_enabled FROM guild_riddle_state WHERE guild_id=? LIMIT 1",
                              (guild_id,))
        return bool(to_int(row.get("is_enabled"), 0)) if row else False

    async def get_state_row(self, guild_id: int) -> dict:
        row = await self._one("SELECT * FROM guild_riddle_state WHERE guild_id=? LIMIT 1",
                              (guild_id,))
        return row or {"guild_id": guild_id, "is_enabled": 0,
                       "hiatus_until": None, "updated_at": now_iso_utc()}

    async def set_hiatus_until(self, guild_id: int, iso_ts: Optional[str]):
        await self.ensure_guild_state(guild_id)
        await self._exec("UPDATE guild_riddle_state SET hiatus_until=?, updated_at=? "
                         "WHERE guild_id=?", (iso_ts, now_iso_utc(), guild_id))

    async def get_hiatus_until(self, guild_id: int) -> Optional[str]:
        row = await self._one("SELECT hiatus_until FROM guild_riddle_state WHERE guild_id=? "
                              "LIMIT 1", (guild_id,))
        v = row.get("hiatus_until") if row else None
        return str(v) if v else None

    async def list_all_guild_ids(self) -> list[int]:
        """
        Every guild that has state anywhere. Filtered by RIDDLE_GUILD_ID when
        set, so a stray invite into a second server cannot make the worker post
        that guild's riddles into the configured channel.
        """
        rows = await self._all(
            """
            SELECT guild_id FROM guild_riddle_state
            UNION SELECT guild_id FROM guild_stats_cache
            UNION SELECT DISTINCT guild_id FROM riddles
            UNION SELECT DISTINCT guild_id FROM submissions
            UNION SELECT DISTINCT guild_id FROM user_stats
            """)
        return [gid for gid in (to_int(r.get("guild_id"), 0) for r in rows)
                if gid > 0 and guild_is_served(gid)]

    async def get_cached_solved_total(self, guild_id: int) -> int:
        row = await self._one("SELECT solved_total_filtered FROM guild_stats_cache "
                              "WHERE guild_id=? LIMIT 1", (guild_id,))
        return max(0, to_int(row.get("solved_total_filtered"), 0)) if row else 0

    async def set_cached_solved_total(self, guild_id: int, value: int):
        await self._exec(
            "INSERT INTO guild_stats_cache (guild_id, solved_total_filtered, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET "
            "solved_total_filtered=excluded.solved_total_filtered, updated_at=excluded.updated_at",
            (guild_id, max(0, to_int(value, 0)), now_iso_utc()))

    async def inc_cached_solved_total(self, guild_id: int, delta: int = 1):
        d = max(0, to_int(delta, 0))
        await self._exec(
            "INSERT INTO guild_stats_cache (guild_id, solved_total_filtered, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET "
            "solved_total_filtered = solved_total_filtered + ?, updated_at = excluded.updated_at",
            (guild_id, d, now_iso_utc(), d))

    # ----------------------------------------------------------- slot queries
    async def open_slot_map(self, guild_id: int) -> dict[int, dict]:
        rows = await self._all(
            "SELECT * FROM riddles WHERE guild_id=? AND status='open' "
            "AND slot_no BETWEEN 1 AND ? ORDER BY slot_no ASC", (guild_id, MAX_RIDDLE_SLOTS))
        out: dict[int, dict] = {}
        for r in rows:
            s = to_int(r.get("slot_no"), 0)
            if 1 <= s <= MAX_RIDDLE_SLOTS:
                out[s] = r
        return out

    async def get_open_slot1(self, guild_id: int) -> Optional[dict]:
        return await self._one("SELECT * FROM riddles WHERE guild_id=? AND status='open' "
                               "AND slot_no=1 LIMIT 1", (guild_id,))

    async def get_open_riddle_by_id(self, guild_id: int, riddle_id: int) -> Optional[dict]:
        return await self._one("SELECT * FROM riddles WHERE guild_id=? AND id=? "
                               "AND status='open' LIMIT 1", (guild_id, riddle_id))

    async def get_riddle_by_id(self, guild_id: int, riddle_id: int) -> Optional[dict]:
        return await self._one("SELECT * FROM riddles WHERE guild_id=? AND id=? LIMIT 1",
                               (guild_id, riddle_id))

    async def get_open_riddle_by_message(self, guild_id: int, message_id: int) -> Optional[dict]:
        return await self._one("SELECT * FROM riddles WHERE guild_id=? AND posted_message_id=? "
                               "AND status='open' LIMIT 1", (guild_id, message_id))

    # -------------------------------------------------------- content mutations
    async def upsert_slot_content(self, *, guild_id: int, user_id: int, slot_no: int,
                                  text: str, solution: str, xp: int) -> Optional[int]:
        if self.db is None or not (1 <= slot_no <= MAX_RIDDLE_SLOTS):
            return None
        text_c, solution_c = clean_value(text), clean_value(solution)
        if not text_c or not solution_c:
            return None
        xp = max(0, to_int(xp, 0))
        now = now_iso_utc()

        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute("SELECT id FROM riddles WHERE guild_id=? "
                                            "AND status='open' AND slot_no=? LIMIT 1",
                                            (guild_id, slot_no))
                row = await cur.fetchone()
                await cur.close()

                if row:
                    rid = to_int(row["id"], 0)
                    # A manual XP edit re-bases the riddle: the admin's value is
                    # the new baseline, rotation bonuses start over from there.
                    # riddle_no is NOT touched – it is the riddle's identity.
                    await self.db.execute(
                        "UPDATE riddles SET text=?, solution=?, xp=?, base_xp=?, "
                        "rotation_count=0, created_by=?, updated_at=? WHERE id=?",
                        (text_c, solution_c, xp, xp, user_id, now, rid))
                else:
                    cur = await self.db.execute("SELECT COALESCE(MAX(riddle_no), 0) + 1 AS n "
                                                "FROM riddles WHERE guild_id=?", (guild_id,))
                    nrow = await cur.fetchone()
                    await cur.close()
                    riddle_no = to_int(nrow["n"] if nrow else 1, 1)

                    cur = await self.db.execute(
                        """
                        INSERT INTO riddles (
                            guild_id, riddle_no, slot_no, is_active, text, solution, xp, base_xp,
                            rotation_count, mention_role_ids, status, created_by,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, 0, NULL, 'open', ?, ?, ?)
                        """,
                        (guild_id, riddle_no, slot_no, text_c, solution_c, xp, xp,
                         user_id, now, now))
                    rid = int(cur.lastrowid or 0)
                    await cur.close()

                await self.db.commit()
                return rid if rid > 0 else None
            except Exception:
                with contextlib.suppress(Exception):
                    await self.db.rollback()
                raise

    async def update_open_riddle_content_by_id(self, guild_id: int, riddle_id: int, user_id: int,
                                               text: str, solution: str, xp: int) -> bool:
        text_c, solution_c = clean_value(text), clean_value(solution)
        if not text_c or not solution_c:
            return False
        xp = max(0, to_int(xp, 0))
        # Manual edit re-bases XP and resets the rotation counter.
        rc, _ = await self._exec(
            "UPDATE riddles SET text=?, solution=?, xp=?, base_xp=?, rotation_count=0, "
            "created_by=?, updated_at=? WHERE guild_id=? AND id=? AND status='open'",
            (text_c, solution_c, xp, xp, user_id, now_iso_utc(), guild_id, riddle_id))
        return rc > 0

    async def set_riddle_images_by_id_open(self, guild_id: int, riddle_id: int,
                                           riddle_image_url: Optional[str],
                                           solution_image_url: Optional[str],
                                           user_id: int) -> bool:
        rc, _ = await self._exec(
            "UPDATE riddles SET image_url=?, solution_url=?, created_by=?, updated_at=? "
            "WHERE guild_id=? AND id=? AND status='open'",
            (clean_value(riddle_image_url), clean_value(solution_image_url),
             user_id, now_iso_utc(), guild_id, riddle_id))
        return rc > 0

    async def set_riddle_mentions_by_id_open(self, guild_id: int, riddle_id: int,
                                             mention_role_ids_csv: Optional[str],
                                             user_id: int) -> bool:
        rc, _ = await self._exec(
            "UPDATE riddles SET mention_role_ids=?, created_by=?, updated_at=? "
            "WHERE guild_id=? AND id=? AND status='open'",
            (clean_value(mention_role_ids_csv), user_id, now_iso_utc(), guild_id, riddle_id))
        return rc > 0

    # ------------------------------------------------------ rotation XP bonus
    async def bump_riddle_xp_on_rotation(self, guild_id: int, riddle_id: int, *,
                                         bonus: int, max_xp: int = MAX_RIDDLE_XP
                                         ) -> Optional[dict]:
        """
        Atomically raise a riddle's XP because it was auto-rotated unsolved, and
        increment its rotation counter. Only touches OPEN riddles.

        The ceiling can only ever HOLD the value, never lower it: a riddle whose
        XP already sits above max_xp (possible with legacy data or a raised
        ceiling that was later lowered) keeps what it has. Clipping downwards
        used to silently destroy reward, and both the bonus embed and the
        rotation field rendered it as "ceiling reached" so nobody noticed.

        Returns {"old_xp", "new_xp", "gained", "rotation_count", "capped"} or
        None if the riddle is gone / no longer open.
        """
        if self.db is None:
            return None
        bonus = max(0, to_int(bonus, 0))
        ceiling = max(0, to_int(max_xp, 0))

        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    "SELECT xp, base_xp, rotation_count FROM riddles "
                    "WHERE guild_id=? AND id=? AND status='open' LIMIT 1",
                    (guild_id, riddle_id))
                row = await cur.fetchone()
                await cur.close()
                if not row:
                    await self.db.rollback()
                    return None

                old_xp = max(0, to_int(row["xp"], 0))
                base_xp = to_int(row["base_xp"], old_xp)
                rot = max(0, to_int(row["rotation_count"], 0)) + 1

                # never below old_xp – see docstring
                new_xp = max(old_xp, min(old_xp + bonus, ceiling))
                capped = (old_xp + bonus) > new_xp

                await self.db.execute(
                    "UPDATE riddles SET xp=?, base_xp=COALESCE(base_xp, ?), rotation_count=?, "
                    "updated_at=? WHERE id=? AND status='open'",
                    (new_xp, base_xp, rot, now_iso_utc(), riddle_id))
                await self.db.commit()
                return {"old_xp": old_xp, "new_xp": new_xp, "gained": new_xp - old_xp,
                        "base_xp": base_xp, "rotation_count": rot, "capped": capped}
            except Exception:
                with contextlib.suppress(Exception):
                    await self.db.rollback()
                raise

    # ---------------------------------------------------------- slot geometry
    async def compact_open_slots(self, guild_id: int):
        """
        Compact open riddles into slots 1..MAX. Riddles that do not end up in
        slot 1 get first_posted_at + post refs cleared, so rotation timer and
        submit grace period restart when they cycle back. Overflow is closed.
        Exits early if the layout is already correct.

        NOTE: clearing a post ref only forgets the message, it does not delete
        it. Callers that can reach Discord should use pop_non_slot1_post_refs()
        BEFORE calling this and delete whatever comes back – otherwise a live
        Submit button is left behind with nothing pointing at it.
        """
        if self.db is None:
            return
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                now = now_iso_utc()
                cur = await self.db.execute(
                    "SELECT id, slot_no, is_active FROM riddles WHERE guild_id=? AND status='open' "
                    "ORDER BY CASE WHEN slot_no BETWEEN 1 AND ? THEN slot_no ELSE 9999 END, id",
                    (guild_id, MAX_RIDDLE_SLOTS))
                rows = [dict(r) for r in await cur.fetchall()]
                await cur.close()
                if not rows:
                    await self.db.commit()
                    return

                ids = [to_int(r["id"], 0) for r in rows if to_int(r["id"], 0) > 0]
                already_ok = len(rows) <= MAX_RIDDLE_SLOTS and all(
                    to_int(r.get("slot_no"), -1) == i
                    and to_int(r.get("is_active"), 0) == (1 if i == 1 else 0)
                    for i, r in enumerate(rows, start=1))
                if already_ok:
                    await self.db.commit()
                    return

                await self.db.execute(
                    "UPDATE riddles SET slot_no=NULL, is_active=0, updated_at=? "
                    "WHERE guild_id=? AND status='open'", (now, guild_id))
                for i, rid in enumerate(ids, start=1):
                    if i == 1:
                        await self.db.execute(
                            "UPDATE riddles SET slot_no=1, is_active=1, updated_at=? WHERE id=?",
                            (now, rid))
                    elif i <= MAX_RIDDLE_SLOTS:
                        await self.db.execute(
                            "UPDATE riddles SET slot_no=?, is_active=0, first_posted_at=NULL, "
                            "posted_channel_id=NULL, posted_message_id=NULL, updated_at=? "
                            "WHERE id=?", (i, now, rid))
                    else:
                        await self.db.execute(
                            "UPDATE riddles SET status='closed', closed_by=0, closed_at=?, "
                            "first_posted_at=NULL, posted_channel_id=NULL, "
                            "posted_message_id=NULL, updated_at=? WHERE id=?", (now, now, rid))
                        await self.db.execute(
                            "UPDATE submissions SET status='cancelled', voted_by=0, voted_at=? "
                            "WHERE riddle_id=? AND status='pending'", (now, rid))
                        logger.info("compact_open_slots: closed overflow riddle id=%s", rid)
                await self.db.commit()
            except Exception:
                with contextlib.suppress(Exception):
                    await self.db.rollback()
                raise

    async def move_open_riddle_to_end(self, guild_id: int, riddle_id: int) -> bool:
        if self.db is None:
            return False
        await self.compact_open_slots(guild_id)
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    "SELECT id FROM riddles WHERE guild_id=? AND status='open' "
                    "AND slot_no BETWEEN 1 AND ? ORDER BY slot_no ASC",
                    (guild_id, MAX_RIDDLE_SLOTS))
                rows = await cur.fetchall()
                await cur.close()
                ids = [to_int(r["id"], 0) for r in rows if to_int(r["id"], 0) > 0]
                if riddle_id not in ids:
                    await self.db.rollback()
                    return False

                now = now_iso_utc()
                if len(ids) <= 1:
                    # Nothing to reorder, but reset the timer so the single
                    # riddle gets a fresh countdown + grace period.
                    await self.db.execute(
                        "UPDATE riddles SET first_posted_at=NULL, posted_channel_id=NULL, "
                        "posted_message_id=NULL, updated_at=? WHERE id=?", (now, riddle_id))
                    await self.db.commit()
                    return True

                ids.remove(riddle_id)
                ids.append(riddle_id)
                await self.db.execute(
                    "UPDATE riddles SET slot_no=NULL, is_active=0, first_posted_at=NULL, "
                    "posted_channel_id=NULL, posted_message_id=NULL, updated_at=? "
                    "WHERE guild_id=? AND status='open'", (now, guild_id))
                for idx, rid in enumerate(ids, start=1):
                    await self.db.execute(
                        "UPDATE riddles SET slot_no=?, is_active=?, updated_at=? WHERE id=?",
                        (idx, 1 if idx == 1 else 0, now, rid))
                await self.db.commit()
                return True
            except Exception:
                with contextlib.suppress(Exception):
                    await self.db.rollback()
                raise

    async def close_open_riddle_by_id(self, guild_id: int, riddle_id: int,
                                      closed_by: int) -> Optional[dict]:
        if self.db is None:
            return None
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    "SELECT * FROM riddles WHERE guild_id=? AND id=? AND status='open' LIMIT 1",
                    (guild_id, riddle_id))
                row = await cur.fetchone()
                await cur.close()
                if not row:
                    await self.db.rollback()
                    return None
                snapshot = dict(row)
                now = now_iso_utc()
                await self.db.execute(
                    "UPDATE riddles SET status='closed', slot_no=NULL, is_active=0, "
                    "first_posted_at=NULL, posted_channel_id=NULL, posted_message_id=NULL, "
                    "closed_by=?, closed_at=?, updated_at=? WHERE id=? AND status='open'",
                    (closed_by, now, now, riddle_id))
                await self.db.execute(
                    "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? "
                    "WHERE riddle_id=? AND status='pending'", (closed_by, now, riddle_id))
                await self.db.commit()
                return snapshot
            except Exception:
                with contextlib.suppress(Exception):
                    await self.db.rollback()
                raise

    # --------------------------------------------------------------- post refs
    async def set_riddle_post_ref(self, riddle_id: int, channel_id: int, message_id: int,
                                  first_posted_at: Optional[str] = None):
        """
        first_posted_at is written only when currently NULL (COALESCE), so
        refreshes/edits reset neither the rotation countdown, the submit grace
        period, nor the timing display. The caller may pass the exact anchor it
        used for the "opens at" hint so both cannot drift apart.
        """
        now = now_iso_utc()
        await self._exec(
            "UPDATE riddles SET posted_channel_id=?, posted_message_id=?, "
            "first_posted_at=COALESCE(first_posted_at, ?), updated_at=? WHERE id=?",
            (channel_id, message_id, first_posted_at or now, now, riddle_id))

    async def reset_riddle_post_state(self, riddle_id: int):
        await self._exec(
            "UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL, "
            "first_posted_at=NULL, updated_at=? WHERE id=?", (now_iso_utc(), riddle_id))

    async def set_solved_post_ref(self, riddle_id: int, channel_id: int, message_id: int):
        await self._exec(
            "UPDATE riddles SET solved_post_channel_id=?, solved_post_message_id=?, "
            "updated_at=? WHERE id=?", (channel_id, message_id, now_iso_utc(), riddle_id))

    async def clear_all_open_post_refs(self, guild_id: Optional[int] = None, *,
                                       reset_timer: bool = True):
        """
        Forget the Discord message refs of all OPEN riddles.

        reset_timer=False keeps first_posted_at intact. Startup MUST use that:
        the old messages are deleted and re-posted, but the ROTATION CLOCK must
        survive. With the timer reset on every boot, a bot that restarts more
        often than RIDDLE_UNSOLVED_ROTATION_HOURS (deploys, OOM-restarts,
        flapping healthchecks) would never rotate a single riddle and the
        unsolved XP bonus would never fire – silently, with no error anywhere.
        Keeping the anchor also means the submit grace period is not re-armed
        on a riddle that has already been online for hours.
        """
        timer = ", first_posted_at=NULL" if reset_timer else ""
        if guild_id is None:
            await self._exec(
                f"UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL{timer}, "
                f"updated_at=? WHERE status='open'", (now_iso_utc(),))
        else:
            await self._exec(
                f"UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL{timer}, "
                f"updated_at=? WHERE guild_id=? AND status='open'",
                (now_iso_utc(), guild_id))

    async def clear_other_open_post_refs(self, guild_id: int,
                                         keep_riddle_id: int) -> list[dict]:
        """
        Clear post refs of every open riddle except the keeper AND return what
        was cleared, so the caller can delete those messages. Returning them is
        the point: a forgotten ref leaves a live Submit button in the channel
        that no DB row points at, and the button's fallback would then submit
        against whatever riddle happens to be in slot 1.
        """
        if self.db is None:
            return []
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    "SELECT id, posted_channel_id, posted_message_id FROM riddles "
                    "WHERE guild_id=? AND status='open' AND id<>? "
                    "AND posted_message_id IS NOT NULL", (guild_id, keep_riddle_id))
                rows = [dict(r) for r in await cur.fetchall()]
                await cur.close()
                if rows:
                    await self.db.execute(
                        "UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL, "
                        "first_posted_at=NULL, updated_at=? "
                        "WHERE guild_id=? AND status='open' AND id<>? "
                        "AND posted_message_id IS NOT NULL",
                        (now_iso_utc(), guild_id, keep_riddle_id))
                await self.db.commit()
                return rows
            except Exception:
                with contextlib.suppress(Exception):
                    await self.db.rollback()
                raise

    async def pop_non_slot1_post_refs(self, guild_id: int) -> list[dict]:
        """
        Return + clear post refs of open riddles that are NOT in slot 1.
        Call this before compact_open_slots() and delete the returned messages.
        """
        if self.db is None:
            return []
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    "SELECT id, posted_channel_id, posted_message_id FROM riddles "
                    "WHERE guild_id=? AND status='open' AND posted_message_id IS NOT NULL "
                    "AND (slot_no IS NULL OR slot_no<>1)", (guild_id,))
                rows = [dict(r) for r in await cur.fetchall()]
                await cur.close()
                if rows:
                    await self.db.execute(
                        "UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL, "
                        "updated_at=? WHERE guild_id=? AND status='open' "
                        "AND posted_message_id IS NOT NULL "
                        "AND (slot_no IS NULL OR slot_no<>1)", (now_iso_utc(), guild_id))
                await self.db.commit()
                return rows
            except Exception:
                with contextlib.suppress(Exception):
                    await self.db.rollback()
                raise

    async def list_open_post_refs(self, guild_id: int) -> list[dict]:
        return await self._all(
            "SELECT id, posted_channel_id, posted_message_id FROM riddles "
            "WHERE guild_id=? AND status='open' AND posted_message_id IS NOT NULL", (guild_id,))

    async def clear_stale_posted_refs(self, riddle_id: int):
        """Drop refs of a riddle whose message we just deleted (keeps the unique
        index on posted_message_id free of dead entries)."""
        await self._exec("UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL, "
                         "updated_at=? WHERE id=?", (now_iso_utc(), riddle_id))

    # ------------------------------------------------------------- wrong posts
    async def add_wrong_post(self, guild_id: int, riddle_id: int,
                             channel_id: int, message_id: int):
        await self._exec(
            "INSERT INTO riddle_wrong_posts (guild_id, riddle_id, channel_id, message_id, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (guild_id, riddle_id, channel_id, message_id, now_iso_utc()))

    async def list_wrong_posts_for_riddle(self, riddle_id: int) -> list[dict]:
        return await self._all("SELECT channel_id, message_id FROM riddle_wrong_posts "
                               "WHERE riddle_id=?", (riddle_id,))

    async def clear_wrong_posts_for_riddle(self, riddle_id: int):
        await self._exec("DELETE FROM riddle_wrong_posts WHERE riddle_id=?", (riddle_id,))

    # ------------------------------------------------------------- submissions
    async def create_submission_pending(self, guild_id: int, riddle_id: int,
                                        user_id: int, answer: str) -> Optional[int]:
        """
        Insert a pending submission, but ONLY while the riddle is open AND still
        in slot 1. Both conditions live inside the INSERT ... SELECT so the
        check and the write are one atomic step.

        Why slot_no matters: a user can open the modal, type for a minute, and
        submit after the worker rotated the riddle to slot 10. The riddle is
        still 'open', so a status-only check passes – and approving that
        submission would mark a riddle solved that was never on screen,
        triggering the full solved flow for the wrong riddle.

        Returns: new submission id
                 DUPLICATE_PENDING (-1)     – user already has one pending
                 SUBMISSION_NOT_ACTIVE (-2) – riddle closed or no longer slot 1
                 None                       – unexpected failure
        """
        try:
            rc, lid = await self._exec(
                """
                INSERT INTO submissions
                    (guild_id, riddle_id, user_id, answer, answer_norm, status, created_at)
                SELECT ?, r.id, ?, ?, ?, 'pending', ?
                FROM riddles r
                WHERE r.id=? AND r.guild_id=? AND r.status='open' AND r.slot_no=1
                """,
                (guild_id, user_id, answer, normalize_answer(answer), now_iso_utc(),
                 riddle_id, guild_id))
        except aiosqlite.IntegrityError as e:
            # Do NOT assume every integrity failure is the pending-duplicate
            # index. With PRAGMA foreign_keys=ON a vanished riddle_id raises
            # here too, and reporting that as "you already submitted" sends the
            # user chasing a submission that does not exist.
            msg = str(e).lower()
            if "idx_sub_one_pending_per_user" in msg or "unique constraint" in msg:
                return DUPLICATE_PENDING
            if "foreign key" in msg:
                logger.warning("Submission rejected – riddle %s vanished: %s", riddle_id, e)
                return SUBMISSION_NOT_ACTIVE
            logger.exception("Unexpected IntegrityError creating submission")
            return None
        if rc <= 0:
            return SUBMISSION_NOT_ACTIVE
        return lid if lid > 0 else None

    async def answer_already_rejected(self, riddle_id: int, answer: str) -> bool:
        """Indexed lookup via answer_norm (idx_sub_riddle_norm)."""
        norm = normalize_answer(answer)
        if not norm:
            return False
        row = await self._one(
            "SELECT 1 AS x FROM submissions WHERE riddle_id=? AND status='wrong' "
            "AND answer_norm=? LIMIT 1", (riddle_id, norm))
        return row is not None

    async def set_submission_vote_message(self, submission_id: int,
                                          vote_message_id: int) -> bool:
        rc, _ = await self._exec("UPDATE submissions SET vote_message_id=? WHERE id=?",
                                 (vote_message_id, submission_id))
        return rc > 0

    async def delete_submission(self, submission_id: int):
        await self._exec("DELETE FROM submissions WHERE id=?", (submission_id,))

    async def list_vote_messages_for_riddle(self, riddle_id: int, *,
                                            only_pending: bool = False) -> list[dict]:
        q = ("SELECT id, vote_message_id, status FROM submissions "
             "WHERE riddle_id=? AND vote_message_id IS NOT NULL")
        if only_pending:
            q += " AND status='pending'"
        return await self._all(q, (riddle_id,))

    async def reset_pending_vote_refs(self):
        await self._exec("UPDATE submissions SET vote_message_id=NULL WHERE status='pending'")

    async def cancel_pending_for_non_open(self):
        await self._exec(
            "UPDATE submissions SET status='cancelled', voted_by=0, voted_at=? "
            "WHERE status='pending' AND riddle_id IN "
            "(SELECT id FROM riddles WHERE status<>'open')", (now_iso_utc(),))

    async def cancel_pending_for_riddle(self, riddle_id: int, moderator_id: int = 0):
        await self._exec("UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? "
                         "WHERE riddle_id=? AND status='pending'",
                         (moderator_id, now_iso_utc(), riddle_id))

    async def has_pending_submissions_for_riddle(self, riddle_id: int) -> bool:
        row = await self._one("SELECT 1 AS x FROM submissions WHERE riddle_id=? "
                              "AND status='pending' LIMIT 1", (riddle_id,))
        return row is not None

    async def count_pending_submissions_for_riddle(self, riddle_id: int) -> int:
        row = await self._one("SELECT COUNT(*) AS c FROM submissions WHERE riddle_id=? "
                              "AND status='pending'", (riddle_id,))
        return to_int(row.get("c"), 0) if row else 0

    async def pending_open_submissions(self) -> list[dict]:
        return await self._all(
            """
            SELECT s.id AS submission_id, s.guild_id AS guild_id, s.user_id AS user_id,
                   s.answer AS answer, s.vote_message_id AS vote_message_id,
                   s.created_at AS submitted_at,
                   r.id AS riddle_id, r.text AS riddle_text, r.solution AS solution,
                   r.xp AS xp, r.mention_role_ids AS mention_role_ids,
                   r.image_url AS image_url, r.riddle_no AS riddle_no,
                   r.first_posted_at AS first_posted_at, r.rotation_count AS rotation_count
            FROM submissions s JOIN riddles r ON r.id = s.riddle_id
            WHERE s.status='pending' AND r.status='open'
            ORDER BY s.id ASC
            """)

    # -------------------------------------------------------------------- stats
    async def stats_entries(self, guild_id: int) -> list[tuple[int, int, int]]:
        rows = await self._all(
            "SELECT user_id, solved_riddles, xp FROM user_stats WHERE guild_id=? "
            "ORDER BY solved_riddles DESC, xp DESC", (guild_id,))
        out: list[tuple[int, int, int]] = []
        for r in rows:
            uid = to_int(r.get("user_id"), 0)
            if uid <= 0:
                continue
            out.append((uid, max(0, to_int(r.get("solved_riddles"), 0)),
                        max(0, to_int(r.get("xp"), 0))))
        return out

    async def apply_solve_xp(self, guild_id: int, user_id: int, xp_gain: int):
        await self._exec(
            """
            INSERT INTO user_stats (guild_id, user_id, solved_riddles, xp)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(guild_id, user_id)
            DO UPDATE SET solved_riddles = solved_riddles + 1, xp = xp + excluded.xp
            """, (guild_id, user_id, max(0, to_int(xp_gain, 0))))

    # ----------------------------------------------------- voting transactions
    async def approve_submission(self, vote_message_id: int,
                                 moderator_id: int) -> tuple[str, Optional[dict]]:
        """
        Atomic: mark submission correct, cancel siblings, mark riddle solved.
        Status: approved | not_found | already_done | riddle_closed.
        """
        if self.db is None:
            return "not_found", None
        vmid = to_int(vote_message_id, 0)
        if vmid <= 0:
            return "not_found", None

        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    """
                    SELECT s.id AS submission_id, s.guild_id AS guild_id,
                           s.riddle_id AS riddle_id, s.user_id AS user_id,
                           s.answer AS answer, s.status AS submission_status,
                           s.created_at AS submitted_at,
                           r.text AS riddle_text, r.solution AS solution, r.xp AS xp,
                           r.base_xp AS base_xp, r.rotation_count AS rotation_count,
                           r.status AS riddle_status, r.mention_role_ids AS mention_role_ids,
                           r.image_url AS image_url, r.solution_url AS solution_url,
                           r.riddle_no AS riddle_no, r.first_posted_at AS first_posted_at,
                           r.posted_channel_id AS posted_channel_id,
                           r.posted_message_id AS posted_message_id
                    FROM submissions s JOIN riddles r ON r.id = s.riddle_id
                    WHERE s.vote_message_id=? LIMIT 1
                    """, (vmid,))
                row = await cur.fetchone()
                await cur.close()
                if not row:
                    await self.db.rollback()
                    return "not_found", None

                data = dict(row)
                if str(data.get("submission_status") or "") != "pending":
                    await self.db.rollback()
                    return "already_done", data

                now = now_iso_utc()
                sid = to_int(data.get("submission_id"), 0)
                rid = to_int(data.get("riddle_id"), 0)
                solver_uid = to_int(data.get("user_id"), 0)

                if str(data.get("riddle_status") or "") != "open":
                    await self.db.execute(
                        "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? "
                        "WHERE id=? AND status='pending'", (moderator_id, now, sid))
                    await self.db.commit()
                    return "riddle_closed", data

                cur = await self.db.execute(
                    "UPDATE submissions SET status='correct', voted_by=?, voted_at=? "
                    "WHERE id=? AND status='pending'", (moderator_id, now, sid))
                sub_rc = cur.rowcount
                await cur.close()
                if sub_rc <= 0:
                    await self.db.rollback()
                    return "already_done", data

                await self.db.execute(
                    "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? "
                    "WHERE riddle_id=? AND status='pending' AND id<>?",
                    (moderator_id, now, rid, sid))

                cur = await self.db.execute(
                    "UPDATE riddles SET status='solved', slot_no=NULL, is_active=0, "
                    "solved_by=?, solved_at=?, updated_at=? WHERE id=? AND status='open'",
                    (solver_uid, now, now, rid))
                rid_rc = cur.rowcount
                await cur.close()
                if rid_rc <= 0:
                    await self.db.rollback()
                    return "riddle_closed", data

                await self.db.commit()

                xp = max(0, to_int(data.get("xp"), 0))
                ctx = {
                    "submission_id": sid,
                    "guild_id": to_int(data.get("guild_id"), 0),
                    "riddle_id": rid,
                    "solver_user_id": solver_uid,
                    "xp_gain": xp, "xp": xp,
                    "base_xp": to_int(data.get("base_xp"), xp),
                    "rotation_count": max(0, to_int(data.get("rotation_count"), 0)),
                    "answer": data.get("answer"),
                    "solution": data.get("solution"),
                    "text": data.get("riddle_text"),
                    "riddle_text": data.get("riddle_text"),
                    "mention_role_ids": data.get("mention_role_ids"),
                    "image_url": data.get("image_url"),
                    "solution_url": data.get("solution_url"),
                    "riddle_no": to_int(data.get("riddle_no"), 0),
                    "posted_channel_id": safe_int(data.get("posted_channel_id"), None),
                    "posted_message_id": safe_int(data.get("posted_message_id"), None),
                    # timing: submitted_at is the real solve moment,
                    # voted_at is only moderator latency
                    "first_posted_at": data.get("first_posted_at"),
                    "submitted_at": data.get("submitted_at"),
                    "voted_at": now,
                    "solved_at": now,  # DB audit value, NOT for display
                }
                return "approved", ctx
            except Exception:
                with contextlib.suppress(Exception):
                    await self.db.rollback()
                raise

    async def reject_submission(self, vote_message_id: int,
                                moderator_id: int) -> tuple[str, Optional[dict]]:
        if self.db is None:
            return "not_found", None
        vmid = to_int(vote_message_id, 0)
        if vmid <= 0:
            return "not_found", None

        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    """
                    SELECT s.id AS submission_id, s.guild_id AS guild_id,
                           s.riddle_id AS riddle_id, s.user_id AS user_id,
                           s.answer AS answer, s.status AS submission_status,
                           s.created_at AS submitted_at,
                           r.text AS riddle_text, r.xp AS xp, r.riddle_no AS riddle_no,
                           r.image_url AS image_url, r.status AS riddle_status,
                           r.mention_role_ids AS mention_role_ids,
                           r.first_posted_at AS first_posted_at,
                           r.rotation_count AS rotation_count
                    FROM submissions s JOIN riddles r ON r.id = s.riddle_id
                    WHERE s.vote_message_id=? LIMIT 1
                    """, (vmid,))
                row = await cur.fetchone()
                await cur.close()
                if not row:
                    await self.db.rollback()
                    return "not_found", None

                data = dict(row)
                if str(data.get("submission_status") or "") != "pending":
                    await self.db.rollback()
                    return "already_done", data

                now = now_iso_utc()
                sid = to_int(data.get("submission_id"), 0)

                if str(data.get("riddle_status") or "") != "open":
                    await self.db.execute(
                        "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? "
                        "WHERE id=? AND status='pending'", (moderator_id, now, sid))
                    await self.db.commit()
                    return "riddle_closed", data

                cur = await self.db.execute(
                    "UPDATE submissions SET status='wrong', voted_by=?, voted_at=? "
                    "WHERE id=? AND status='pending'", (moderator_id, now, sid))
                rc = cur.rowcount
                await cur.close()
                if rc <= 0:
                    await self.db.rollback()
                    return "already_done", data

                await self.db.commit()
                ctx = {
                    "submission_id": sid,
                    "guild_id": to_int(data.get("guild_id"), 0),
                    "riddle_id": to_int(data.get("riddle_id"), 0),
                    "solver_user_id": to_int(data.get("user_id"), 0),
                    "answer": data.get("answer"),
                    "text": data.get("riddle_text"),
                    "riddle_text": data.get("riddle_text"),
                    "riddle_no": to_int(data.get("riddle_no"), 0),
                    "xp": max(0, to_int(data.get("xp"), 0)),
                    "rotation_count": max(0, to_int(data.get("rotation_count"), 0)),
                    "image_url": data.get("image_url"),
                    "mention_role_ids": data.get("mention_role_ids"),
                    "first_posted_at": data.get("first_posted_at"),
                    "submitted_at": data.get("submitted_at"),
                    "voted_at": now,
                }
                return "rejected", ctx
            except Exception:
                with contextlib.suppress(Exception):
                    await self.db.rollback()
                raise