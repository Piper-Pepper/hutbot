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

# Hours a riddle may sit in Slot 1 unsolved before it is auto-moved to the end.
# The countdown starts at the FIRST post; refreshes/edits do NOT reset it.
UNSOLVED_ROTATION_HOURS = _env_int("RIDDLE_UNSOLVED_ROTATION_HOURS", 6)

# Safety valve: rotate even if pending votes exist once this age is reached,
# so a single un-voted submission can never block the queue forever.
ROTATION_HARD_CAP_HOURS = _env_int(
    "RIDDLE_ROTATION_HARD_CAP_HOURS", max(1, UNSOLVED_ROTATION_HOURS * 2)
)

# After a solve the system stays ON, but no new Slot 1 riddle is posted
# during this hiatus. Bypassable via the "Post Now" button.
SOLVED_HIATUS_HOURS = _env_int("RIDDLE_SOLVED_HIATUS_HOURS", 6)

# How often the background worker reconciles Discord state with DB state.
ROTATION_TICK_SECONDS = _env_int("RIDDLE_ROTATION_TICK_SECONDS", 900)  # 15 min

# Debounce window for role-change triggered stats rebuilds.
STATS_REBUILD_DEBOUNCE_SECONDS = _env_int("RIDDLE_STATS_REBUILD_DEBOUNCE", 45)

SUBMIT_BUTTON_ID = "riddle_submit_solution_v2"
VOTE_UP_BUTTON_ID = "riddle_vote_up_v2"
VOTE_DOWN_BUTTON_ID = "riddle_vote_down_v2"

# Admin panel timeout. MUST stay below the 15 min interaction webhook token
# lifetime, otherwise on_timeout can no longer disable the buttons.
PANEL_TIMEOUT_SECONDS = 840

# Difficulty tiers: (upper_bound_exclusive, label, emoji). Last entry catches all.
LEVEL_TIERS: tuple[tuple[Optional[int], str, str], ...] = (
    (1500, "EASY", "🟢"),
    (3000, "MEDIUM", "🟡"),
    (4000, "HARD", "🔴"),
    (None, "BRAIN-DEAD", "💀"),
)

# Sanity guard for the XP field in the admin modal.
MAX_XP_INPUT = _env_int("RIDDLE_MAX_XP_INPUT", 1_000_000)

ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
URL_RE = re.compile(r"(https?://\S+)")


def validate_config() -> list[str]:
    """Return a list of human-readable config problems. Empty list == OK."""
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
        problems.append(
            f"RIDDLE_MAX_SLOTS={MAX_RIDDLE_SLOTS} must be 1..25 "
            "(Discord select menus allow at most 25 options)."
        )
    if not (1 <= MAX_EXTRA_PING_ROLES <= 25):
        problems.append(f"RIDDLE_MAX_EXTRA_PING_ROLES={MAX_EXTRA_PING_ROLES} must be 1..25.")
    if UNSOLVED_ROTATION_HOURS <= 0:
        problems.append("RIDDLE_UNSOLVED_ROTATION_HOURS must be > 0.")
    if ROTATION_HARD_CAP_HOURS < UNSOLVED_ROTATION_HOURS:
        problems.append(
            f"RIDDLE_ROTATION_HARD_CAP_HOURS={ROTATION_HARD_CAP_HOURS} must be >= "
            f"RIDDLE_UNSOLVED_ROTATION_HOURS={UNSOLVED_ROTATION_HOURS}."
        )
    if SOLVED_HIATUS_HOURS < 0:
        problems.append("RIDDLE_SOLVED_HIATUS_HOURS must be >= 0.")
    if RIDDLE_CHANNEL_ID > 0 and RIDDLE_CHANNEL_ID == VOTE_CHANNEL_ID:
        problems.append(
            "RIDDLE_CHANNEL_ID equals RIDDLE_VOTE_CHANNEL_ID – solutions would leak publicly."
        )
    return problems


# =============================================================================
# TIME UTILS   (everything stored is naive UTC ISO with a trailing 'Z')
# =============================================================================
def utcnow_naive() -> dt.datetime:
    """Timezone-naive UTC now. datetime.utcnow() is deprecated in Python 3.12+."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def now_iso_utc() -> str:
    return utcnow_naive().isoformat() + "Z"


def now_date_str() -> str:
    """Footer date – deliberately UTC so it matches every stored timestamp."""
    return utcnow_naive().strftime("%Y/%m/%d")


def parse_iso_utc(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).strip().rstrip("Z"))
    except Exception:
        return None


def iso_utc_in_hours(hours: float) -> str:
    ts = utcnow_naive() + dt.timedelta(hours=hours)
    return ts.replace(microsecond=0).isoformat() + "Z"


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
    Render a stored timestamp as a Discord dynamic timestamp tag so every
    viewer sees it in their own local timezone.
    Styles: t f F d D R  (R = relative, e.g. "3 hours ago").
    """
    unix = iso_to_unix(iso_ts)
    return f"<t:{unix}:{style}>" if unix is not None else None


def hours_since(iso_ts: Optional[str]) -> Optional[float]:
    t = parse_iso_utc(iso_ts)
    if t is None:
        return None
    return (utcnow_naive() - t).total_seconds() / 3600.0


def hours_until(iso_ts: Optional[str]) -> Optional[float]:
    t = parse_iso_utc(iso_ts)
    if t is None:
        return None
    return max(0.0, (t - utcnow_naive()).total_seconds() / 3600.0)


def iso_in_future(iso_ts: Optional[str]) -> bool:
    t = parse_iso_utc(iso_ts)
    return bool(t and t > utcnow_naive())


def duration_between_iso(start_iso: Optional[str], end_iso: Optional[str]) -> Optional[float]:
    """Hours between two stored timestamps. None if either is missing/unparseable."""
    a = parse_iso_utc(start_iso)
    b = parse_iso_utc(end_iso)
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600.0


def format_duration_hours(hours: Optional[float]) -> str:
    """3.7 -> '3h 42m' · 50.2 -> '2d 2h' · 0.05 -> '3m'."""
    if hours is None:
        return "unknown"
    total_minutes = int(round(abs(hours) * 60))
    if total_minutes <= 0:
        return "less than a minute"
    days, rem = divmod(total_minutes, 60 * 24)
    hrs, mins = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hrs:
        parts.append(f"{hrs}h")
    if mins and not days:  # minutes become noise once we talk in days
        parts.append(f"{mins}m")
    return " ".join(parts) or f"{total_minutes}m"


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


def parse_xp_input(raw: Optional[str], max_xp: int = MAX_XP_INPUT) -> Optional[int]:
    """
    Strict XP parser for admin input. Returns None on anything invalid so the
    caller can show an error instead of silently storing 0 XP (which is what a
    plain int() fallback did with typos like "150O" or "1.5k").
    Thousands separators are tolerated.
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
    link = m.group(1)
    return URL_RE.sub("", text, count=1).strip(), link


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
    """Best-effort followup for non-critical feedback. Never raises."""
    with contextlib.suppress(discord.HTTPException, discord.NotFound):
        await interaction.followup.send(content, ephemeral=ephemeral)


async def quiet_respond(interaction: Interaction, content: str, *, ephemeral: bool = True):
    """Respond or followup, whichever is currently valid. Never raises."""
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


def riddle_manager_required():
    async def predicate(interaction: Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            raise MissingRiddleManagerRole()
        if not member_has_role(interaction.user, RIDDLE_MANAGER_ROLE_ID):
            raise MissingRiddleManagerRole()
        return True
    return app_commands.check(predicate)


async def send_access_denied(interaction: Interaction):
    embed = discord.Embed(
        title="🔒 Access Restricted",
        description=(
            f"This command is restricted to <@&{RIDDLE_MANAGER_ROLE_ID}>."
            if RIDDLE_MANAGER_ROLE_ID > 0 else
            "This command is restricted – but no manager role is configured. "
            "Check `RIDDLE_MANAGER_ROLE_ID` in your environment."
        ),
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


# =============================================================================
# MESSAGE LOOKUP SENTINEL
# =============================================================================
class UnknownMessage:
    """
    Sentinel meaning "we could not determine whether the message exists"
    (missing permission, 5xx, timeout). Callers MUST NOT treat this as
    "message is gone" – otherwise transient API errors cause duplicate posts.
    """
    __slots__ = ()

    def __repr__(self) -> str:
        return "<UNKNOWN_MESSAGE>"

    def __bool__(self) -> bool:
        return False


UNKNOWN_MESSAGE = UnknownMessage()

# Return type of RiddleCog.fetch_message_safe
MessageLookup = Union[discord.Message, UnknownMessage, None]

# Sentinel returned by create_submission_pending when the user already has an
# open submission for that riddle.
DUPLICATE_PENDING = -1


# =============================================================================
# DB REPO
# =============================================================================
class RiddleRepo:
    """
    aiosqlite repository.

    Transaction contract
    --------------------
    * The connection runs in AUTOCOMMIT mode (isolation_level=None).
    * Every multi-statement mutation MUST wrap itself in
      `BEGIN IMMEDIATE` ... `commit()` / `rollback()`.
    * `self.lock` serialises all *writes*. Single-statement reads
      (`_one` / `_all`) intentionally do NOT take the lock: WAL allows
      concurrent readers, and locking there would deadlock any read that
      happens inside an open transaction.
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
    async def _column_names(self, table: str) -> list[str]:
        assert self.db is not None
        cur = await self.db.execute(f"PRAGMA table_info({table})")
        rows = await cur.fetchall()
        await cur.close()
        return [r["name"] for r in rows]

    async def _add_col_if_missing(self, table: str, col_name: str, col_def: str):
        assert self.db is not None
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
        -- Hot-path indexes: worker tick + panel render used to full-scan here.
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
        CREATE INDEX IF NOT EXISTS idx_sub_riddle_status
            ON submissions(riddle_id, status);
        CREATE INDEX IF NOT EXISTS idx_sub_guild_status
            ON submissions(guild_id, status);

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

            # --- column migrations for pre-existing databases ---
            await self._add_col_if_missing("riddles", "solved_post_channel_id",
                                           "solved_post_channel_id INTEGER")
            await self._add_col_if_missing("riddles", "solved_post_message_id",
                                           "solved_post_message_id INTEGER")
            await self._add_col_if_missing("riddles", "first_posted_at", "first_posted_at TEXT")
            await self._add_col_if_missing("guild_riddle_state", "hiatus_until",
                                           "hiatus_until TEXT")

            # --- enforce one pending submission per (riddle, user) --------------
            # Historical duplicates must be cleaned first or the index fails.
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
                    "ON submissions(riddle_id, user_id) WHERE status='pending'"
                )
            except Exception:
                logger.exception(
                    "Could not create idx_sub_one_pending_per_user – duplicate "
                    "submissions will only be blocked at application level."
                )

    # ------------------------------------------------------------------ helpers
    async def _one(self, query: str, params: tuple = ()) -> Optional[dict]:
        if self.db is None:
            return None
        cur = await self.db.execute(query, params)
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row else None

    async def _all(self, query: str, params: tuple = ()) -> list[dict]:
        if self.db is None:
            return []
        cur = await self.db.execute(query, params)
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def _exec(self, query: str, params: tuple = ()) -> tuple[int, int]:
        """Single-statement write. Autocommit, so no explicit commit needed."""
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
        await self._exec(
            "INSERT INTO guild_riddle_state (guild_id, is_enabled, updated_at) "
            "VALUES (?, 0, ?) ON CONFLICT(guild_id) DO NOTHING",
            (guild_id, now),
        )
        await self._exec(
            "INSERT INTO guild_stats_cache (guild_id, solved_total_filtered, updated_at) "
            "VALUES (?, 0, ?) ON CONFLICT(guild_id) DO NOTHING",
            (guild_id, now),
        )

    async def set_enabled(self, guild_id: int, enabled: bool):
        await self._exec(
            "INSERT INTO guild_riddle_state (guild_id, is_enabled, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET is_enabled=excluded.is_enabled, "
            "updated_at=excluded.updated_at",
            (guild_id, 1 if enabled else 0, now_iso_utc()),
        )

    async def is_enabled(self, guild_id: int) -> bool:
        row = await self._one(
            "SELECT is_enabled FROM guild_riddle_state WHERE guild_id=? LIMIT 1", (guild_id,)
        )
        return bool(to_int(row.get("is_enabled"), 0)) if row else False

    async def get_state_row(self, guild_id: int) -> dict:
        row = await self._one(
            "SELECT * FROM guild_riddle_state WHERE guild_id=? LIMIT 1", (guild_id,)
        )
        return row or {"guild_id": guild_id, "is_enabled": 0,
                       "hiatus_until": None, "updated_at": now_iso_utc()}

    async def set_hiatus_until(self, guild_id: int, iso_ts: Optional[str]):
        await self.ensure_guild_state(guild_id)
        await self._exec(
            "UPDATE guild_riddle_state SET hiatus_until=?, updated_at=? WHERE guild_id=?",
            (iso_ts, now_iso_utc(), guild_id),
        )

    async def get_hiatus_until(self, guild_id: int) -> Optional[str]:
        row = await self._one(
            "SELECT hiatus_until FROM guild_riddle_state WHERE guild_id=? LIMIT 1", (guild_id,)
        )
        v = row.get("hiatus_until") if row else None
        return str(v) if v else None

    async def list_all_guild_ids(self) -> list[int]:
        rows = await self._all(
            """
            SELECT guild_id FROM guild_riddle_state
            UNION SELECT guild_id FROM guild_stats_cache
            UNION SELECT DISTINCT guild_id FROM riddles
            UNION SELECT DISTINCT guild_id FROM submissions
            UNION SELECT DISTINCT guild_id FROM user_stats
            """
        )
        return [gid for gid in (to_int(r.get("guild_id"), 0) for r in rows) if gid > 0]

    async def get_cached_solved_total(self, guild_id: int) -> int:
        row = await self._one(
            "SELECT solved_total_filtered FROM guild_stats_cache WHERE guild_id=? LIMIT 1",
            (guild_id,),
        )
        return max(0, to_int(row.get("solved_total_filtered"), 0)) if row else 0

    async def set_cached_solved_total(self, guild_id: int, value: int):
        await self._exec(
            "INSERT INTO guild_stats_cache (guild_id, solved_total_filtered, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET "
            "solved_total_filtered=excluded.solved_total_filtered, updated_at=excluded.updated_at",
            (guild_id, max(0, to_int(value, 0)), now_iso_utc()),
        )

    async def inc_cached_solved_total(self, guild_id: int, delta: int = 1):
        d = max(0, to_int(delta, 0))
        await self._exec(
            "INSERT INTO guild_stats_cache (guild_id, solved_total_filtered, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET "
            "solved_total_filtered = solved_total_filtered + ?, updated_at = excluded.updated_at",
            (guild_id, d, now_iso_utc(), d),
        )

    # ----------------------------------------------------------- slot queries
    async def open_slot_map(self, guild_id: int) -> dict[int, dict]:
        rows = await self._all(
            "SELECT * FROM riddles WHERE guild_id=? AND status='open' "
            "AND slot_no BETWEEN 1 AND ? ORDER BY slot_no ASC",
            (guild_id, MAX_RIDDLE_SLOTS),
        )
        out: dict[int, dict] = {}
        for r in rows:
            s = to_int(r.get("slot_no"), 0)
            if 1 <= s <= MAX_RIDDLE_SLOTS:
                out[s] = r
        return out

    async def get_open_slot1(self, guild_id: int) -> Optional[dict]:
        return await self._one(
            "SELECT * FROM riddles WHERE guild_id=? AND status='open' AND slot_no=1 LIMIT 1",
            (guild_id,),
        )

    async def get_open_riddle_by_id(self, guild_id: int, riddle_id: int) -> Optional[dict]:
        return await self._one(
            "SELECT * FROM riddles WHERE guild_id=? AND id=? AND status='open' LIMIT 1",
            (guild_id, riddle_id),
        )

    async def get_riddle_by_id(self, guild_id: int, riddle_id: int) -> Optional[dict]:
        return await self._one(
            "SELECT * FROM riddles WHERE guild_id=? AND id=? LIMIT 1", (guild_id, riddle_id)
        )

    async def get_open_riddle_by_message(self, guild_id: int, message_id: int) -> Optional[dict]:
        return await self._one(
            "SELECT * FROM riddles WHERE guild_id=? AND posted_message_id=? "
            "AND status='open' LIMIT 1",
            (guild_id, message_id),
        )

    # -------------------------------------------------------- content mutations
    async def upsert_slot_content(self, *, guild_id: int, user_id: int, slot_no: int,
                                  text: str, solution: str, xp: int) -> Optional[int]:
        if self.db is None or not (1 <= slot_no <= MAX_RIDDLE_SLOTS):
            return None
        text_c = clean_value(text)
        solution_c = clean_value(solution)
        if not text_c or not solution_c:
            return None
        xp = max(0, to_int(xp, 0))
        now = now_iso_utc()

        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    "SELECT id FROM riddles WHERE guild_id=? AND status='open' "
                    "AND slot_no=? LIMIT 1",
                    (guild_id, slot_no),
                )
                row = await cur.fetchone()
                await cur.close()

                if row:
                    rid = to_int(row["id"], 0)
                    await self.db.execute(
                        "UPDATE riddles SET text=?, solution=?, xp=?, created_by=?, "
                        "updated_at=? WHERE id=?",
                        (text_c, solution_c, xp, user_id, now, rid),
                    )
                else:
                    cur = await self.db.execute(
                        "SELECT COALESCE(MAX(riddle_no), 0) + 1 AS n FROM riddles WHERE guild_id=?",
                        (guild_id,),
                    )
                    nrow = await cur.fetchone()
                    await cur.close()
                    riddle_no = to_int(nrow["n"] if nrow else 1, 1)

                    cur = await self.db.execute(
                        """
                        INSERT INTO riddles (
                            guild_id, riddle_no, slot_no, is_active, text, solution, xp,
                            mention_role_ids, status, created_by, created_at, updated_at
                        ) VALUES (?, ?, ?, 0, ?, ?, ?, NULL, 'open', ?, ?, ?)
                        """,
                        (guild_id, riddle_no, slot_no, text_c, solution_c, xp, user_id, now, now),
                    )
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
        text_c = clean_value(text)
        solution_c = clean_value(solution)
        if not text_c or not solution_c:
            return False
        rc, _ = await self._exec(
            "UPDATE riddles SET text=?, solution=?, xp=?, created_by=?, updated_at=? "
            "WHERE guild_id=? AND id=? AND status='open'",
            (text_c, solution_c, max(0, to_int(xp, 0)), user_id, now_iso_utc(),
             guild_id, riddle_id),
        )
        return rc > 0

    async def set_riddle_images_by_id_open(self, guild_id: int, riddle_id: int,
                                           riddle_image_url: Optional[str],
                                           solution_image_url: Optional[str],
                                           user_id: int) -> bool:
        rc, _ = await self._exec(
            "UPDATE riddles SET image_url=?, solution_url=?, created_by=?, updated_at=? "
            "WHERE guild_id=? AND id=? AND status='open'",
            (clean_value(riddle_image_url), clean_value(solution_image_url),
             user_id, now_iso_utc(), guild_id, riddle_id),
        )
        return rc > 0

    async def set_riddle_mentions_by_id_open(self, guild_id: int, riddle_id: int,
                                             mention_role_ids_csv: Optional[str],
                                             user_id: int) -> bool:
        rc, _ = await self._exec(
            "UPDATE riddles SET mention_role_ids=?, created_by=?, updated_at=? "
            "WHERE guild_id=? AND id=? AND status='open'",
            (clean_value(mention_role_ids_csv), user_id, now_iso_utc(), guild_id, riddle_id),
        )
        return rc > 0

    # ---------------------------------------------------------- slot geometry
    async def sync_open_slot_numbers(self, guild_id: int, solved_base: int):
        """
        Display numbers are positional: riddle_no = solved_total + slot_no.
        There is no UNIQUE constraint on riddle_no, so one UPDATE is enough
        (the old two-phase sentinel dance was unnecessary write amplification).
        """
        await self._exec(
            "UPDATE riddles SET riddle_no=(? + slot_no), updated_at=? "
            "WHERE guild_id=? AND status='open' AND slot_no BETWEEN 1 AND ?",
            (max(0, to_int(solved_base, 0)), now_iso_utc(), guild_id, MAX_RIDDLE_SLOTS),
        )

    async def compact_open_slots(self, guild_id: int):
        """
        Compact open riddles into slots 1..MAX in order. Riddles that do not end
        up in slot 1 get first_posted_at + post refs cleared, so their rotation
        timer restarts when they cycle back. Overflow is closed.
        Exits early if the layout is already correct (saves writes per tick).
        """
        if self.db is None:
            return
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                now = now_iso_utc()
                cur = await self.db.execute(
                    "SELECT id, slot_no, is_active FROM riddles WHERE guild_id=? "
                    "AND status='open' "
                    "ORDER BY CASE WHEN slot_no BETWEEN 1 AND ? THEN slot_no ELSE 9999 END, id",
                    (guild_id, MAX_RIDDLE_SLOTS),
                )
                rows = [dict(r) for r in await cur.fetchall()]
                await cur.close()

                if not rows:
                    await self.db.commit()
                    return

                ids = [to_int(r["id"], 0) for r in rows if to_int(r["id"], 0) > 0]

                # No-op detection: already compact, slot 1 already active.
                already_ok = len(rows) <= MAX_RIDDLE_SLOTS and all(
                    to_int(r.get("slot_no"), -1) == i
                    and to_int(r.get("is_active"), 0) == (1 if i == 1 else 0)
                    for i, r in enumerate(rows, start=1)
                )
                if already_ok:
                    await self.db.commit()
                    return

                # Release every slot first to avoid unique-index collisions.
                await self.db.execute(
                    "UPDATE riddles SET slot_no=NULL, is_active=0, updated_at=? "
                    "WHERE guild_id=? AND status='open'",
                    (now, guild_id),
                )
                for i, rid in enumerate(ids, start=1):
                    if i == 1:
                        # Slot 1 keeps first_posted_at (rotation timer) + post refs.
                        await self.db.execute(
                            "UPDATE riddles SET slot_no=1, is_active=1, updated_at=? WHERE id=?",
                            (now, rid),
                        )
                    elif i <= MAX_RIDDLE_SLOTS:
                        await self.db.execute(
                            "UPDATE riddles SET slot_no=?, is_active=0, first_posted_at=NULL, "
                            "posted_channel_id=NULL, posted_message_id=NULL, updated_at=? "
                            "WHERE id=?",
                            (i, now, rid),
                        )
                    else:
                        await self.db.execute(
                            "UPDATE riddles SET status='closed', closed_by=0, closed_at=?, "
                            "first_posted_at=NULL, posted_channel_id=NULL, "
                            "posted_message_id=NULL, updated_at=? WHERE id=?",
                            (now, now, rid),
                        )
                        await self.db.execute(
                            "UPDATE submissions SET status='cancelled', voted_by=0, voted_at=? "
                            "WHERE riddle_id=? AND status='pending'",
                            (now, rid),
                        )
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
                    (guild_id, MAX_RIDDLE_SLOTS),
                )
                rows = await cur.fetchall()
                await cur.close()
                ids = [to_int(r["id"], 0) for r in rows if to_int(r["id"], 0) > 0]

                if riddle_id not in ids:
                    await self.db.rollback()
                    return False

                now = now_iso_utc()
                if len(ids) <= 1:
                    # Nothing to reorder, but still reset the timer so the single
                    # riddle gets a fresh countdown after being re-posted.
                    await self.db.execute(
                        "UPDATE riddles SET first_posted_at=NULL, posted_channel_id=NULL, "
                        "posted_message_id=NULL, updated_at=? WHERE id=?",
                        (now, riddle_id),
                    )
                    await self.db.commit()
                    return True

                ids.remove(riddle_id)
                ids.append(riddle_id)
                await self.db.execute(
                    "UPDATE riddles SET slot_no=NULL, is_active=0, first_posted_at=NULL, "
                    "posted_channel_id=NULL, posted_message_id=NULL, updated_at=? "
                    "WHERE guild_id=? AND status='open'",
                    (now, guild_id),
                )
                for idx, rid in enumerate(ids, start=1):
                    await self.db.execute(
                        "UPDATE riddles SET slot_no=?, is_active=?, updated_at=? WHERE id=?",
                        (idx, 1 if idx == 1 else 0, now, rid),
                    )
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
                    (guild_id, riddle_id),
                )
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
                    (closed_by, now, now, riddle_id),
                )
                await self.db.execute(
                    "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? "
                    "WHERE riddle_id=? AND status='pending'",
                    (closed_by, now, riddle_id),
                )
                await self.db.commit()
                return snapshot
            except Exception:
                with contextlib.suppress(Exception):
                    await self.db.rollback()
                raise

    # --------------------------------------------------------------- post refs
    async def set_riddle_post_ref(self, riddle_id: int, channel_id: int, message_id: int):
        """
        Store post refs. first_posted_at is only written when NULL (COALESCE),
        so refreshes and edits never reset the rotation countdown – and the
        timing display keeps the true first-post time.
        """
        now = now_iso_utc()
        await self._exec(
            "UPDATE riddles SET posted_channel_id=?, posted_message_id=?, "
            "first_posted_at=COALESCE(first_posted_at, ?), updated_at=? WHERE id=?",
            (channel_id, message_id, now, now, riddle_id),
        )

    async def reset_riddle_post_state(self, riddle_id: int):
        """Clear post refs AND first_posted_at (used by 'Post Now' / 'Turn ON')."""
        await self._exec(
            "UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL, "
            "first_posted_at=NULL, updated_at=? WHERE id=?",
            (now_iso_utc(), riddle_id),
        )

    async def set_solved_post_ref(self, riddle_id: int, channel_id: int, message_id: int):
        await self._exec(
            "UPDATE riddles SET solved_post_channel_id=?, solved_post_message_id=?, "
            "updated_at=? WHERE id=?",
            (channel_id, message_id, now_iso_utc(), riddle_id),
        )

    async def clear_all_open_post_refs(self, guild_id: Optional[int] = None):
        """
        Only touches status='open' rows, so a solved riddle keeps its
        first_posted_at for the timing display.
        """
        if guild_id is None:
            await self._exec(
                "UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL, "
                "first_posted_at=NULL, updated_at=? WHERE status='open'",
                (now_iso_utc(),),
            )
        else:
            await self._exec(
                "UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL, "
                "first_posted_at=NULL, updated_at=? WHERE guild_id=? AND status='open'",
                (now_iso_utc(), guild_id),
            )

    async def clear_other_open_post_refs(self, guild_id: int, keep_riddle_id: int):
        await self._exec(
            "UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL, "
            "first_posted_at=NULL, updated_at=? "
            "WHERE guild_id=? AND status='open' AND id<>? AND posted_message_id IS NOT NULL",
            (now_iso_utc(), guild_id, keep_riddle_id),
        )

    async def list_open_post_refs(self, guild_id: int) -> list[dict]:
        return await self._all(
            "SELECT id, posted_channel_id, posted_message_id FROM riddles "
            "WHERE guild_id=? AND status='open' AND posted_message_id IS NOT NULL",
            (guild_id,),
        )

    async def clear_stale_posted_refs(self, riddle_id: int):
        """
        Drop posted refs of a riddle whose message we just deleted. Keeps the
        unique index on posted_message_id free of dead entries.
        """
        await self._exec(
            "UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL, "
            "updated_at=? WHERE id=?",
            (now_iso_utc(), riddle_id),
        )

    # ------------------------------------------------------------- wrong posts
    async def add_wrong_post(self, guild_id: int, riddle_id: int,
                             channel_id: int, message_id: int):
        await self._exec(
            "INSERT INTO riddle_wrong_posts (guild_id, riddle_id, channel_id, message_id, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (guild_id, riddle_id, channel_id, message_id, now_iso_utc()),
        )

    async def list_wrong_posts_for_riddle(self, riddle_id: int) -> list[dict]:
        return await self._all(
            "SELECT channel_id, message_id FROM riddle_wrong_posts WHERE riddle_id=?",
            (riddle_id,),
        )

    async def clear_wrong_posts_for_riddle(self, riddle_id: int):
        await self._exec("DELETE FROM riddle_wrong_posts WHERE riddle_id=?", (riddle_id,))

    # ------------------------------------------------------------- submissions
    async def create_submission_pending(self, guild_id: int, riddle_id: int,
                                        user_id: int, answer: str) -> Optional[int]:
        """
        Returns the new submission id, DUPLICATE_PENDING (-1) if the user
        already has an open submission for this riddle, or None on failure.
        """
        try:
            _, lid = await self._exec(
                "INSERT INTO submissions (guild_id, riddle_id, user_id, answer, status, "
                "created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
                (guild_id, riddle_id, user_id, answer, now_iso_utc()),
            )
        except aiosqlite.IntegrityError:
            return DUPLICATE_PENDING
        return lid if lid > 0 else None

    async def answer_already_rejected(self, riddle_id: int, answer: str) -> bool:
        """True if this exact answer (case/whitespace-insensitive) was voted wrong."""
        norm = " ".join((answer or "").lower().split())
        if not norm:
            return False
        rows = await self._all(
            "SELECT answer FROM submissions WHERE riddle_id=? AND status='wrong'",
            (riddle_id,),
        )
        return any(" ".join(str(r.get("answer") or "").lower().split()) == norm for r in rows)

    async def set_submission_vote_message(self, submission_id: int,
                                          vote_message_id: int) -> bool:
        rc, _ = await self._exec(
            "UPDATE submissions SET vote_message_id=? WHERE id=?",
            (vote_message_id, submission_id),
        )
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
            "(SELECT id FROM riddles WHERE status<>'open')",
            (now_iso_utc(),),
        )

    async def cancel_pending_for_riddle(self, riddle_id: int, moderator_id: int = 0):
        await self._exec(
            "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? "
            "WHERE riddle_id=? AND status='pending'",
            (moderator_id, now_iso_utc(), riddle_id),
        )

    async def has_pending_submissions_for_riddle(self, riddle_id: int) -> bool:
        row = await self._one(
            "SELECT 1 AS x FROM submissions WHERE riddle_id=? AND status='pending' LIMIT 1",
            (riddle_id,),
        )
        return row is not None

    async def count_pending_submissions_for_riddle(self, riddle_id: int) -> int:
        row = await self._one(
            "SELECT COUNT(*) AS c FROM submissions WHERE riddle_id=? AND status='pending'",
            (riddle_id,),
        )
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
                   r.first_posted_at AS first_posted_at
            FROM submissions s JOIN riddles r ON r.id = s.riddle_id
            WHERE s.status='pending' AND r.status='open'
            ORDER BY s.id ASC
            """
        )

    # -------------------------------------------------------------------- stats
    async def stats_entries(self, guild_id: int) -> list[tuple[int, int, int]]:
        rows = await self._all(
            "SELECT user_id, solved_riddles, xp FROM user_stats WHERE guild_id=? "
            "ORDER BY solved_riddles DESC, xp DESC",
            (guild_id,),
        )
        out: list[tuple[int, int, int]] = []
        for r in rows:
            uid = to_int(r.get("user_id"), 0)
            if uid <= 0:
                continue
            out.append((uid,
                        max(0, to_int(r.get("solved_riddles"), 0)),
                        max(0, to_int(r.get("xp"), 0))))
        return out

    async def apply_solve_xp(self, guild_id: int, user_id: int, xp_gain: int):
        await self._exec(
            """
            INSERT INTO user_stats (guild_id, user_id, solved_riddles, xp)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(guild_id, user_id)
            DO UPDATE SET solved_riddles = solved_riddles + 1, xp = xp + excluded.xp
            """,
            (guild_id, user_id, max(0, to_int(xp_gain, 0))),
        )

    # ----------------------------------------------------- voting transactions
    async def approve_submission(self, vote_message_id: int,
                                 moderator_id: int) -> tuple[str, Optional[dict]]:
        """
        Atomically: mark submission correct, cancel siblings, mark riddle solved.
        Returns (status, ctx) with status in
        approved | not_found | already_done | riddle_closed.
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
                           r.status AS riddle_status, r.mention_role_ids AS mention_role_ids,
                           r.image_url AS image_url, r.solution_url AS solution_url,
                           r.riddle_no AS riddle_no,
                           r.first_posted_at AS first_posted_at,
                           r.posted_channel_id AS posted_channel_id,
                           r.posted_message_id AS posted_message_id
                    FROM submissions s JOIN riddles r ON r.id = s.riddle_id
                    WHERE s.vote_message_id=? LIMIT 1
                    """,
                    (vmid,),
                )
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
                        "WHERE id=? AND status='pending'",
                        (moderator_id, now, sid),
                    )
                    await self.db.commit()
                    return "riddle_closed", data

                cur = await self.db.execute(
                    "UPDATE submissions SET status='correct', voted_by=?, voted_at=? "
                    "WHERE id=? AND status='pending'",
                    (moderator_id, now, sid),
                )
                sub_rc = cur.rowcount
                await cur.close()
                if sub_rc <= 0:
                    await self.db.rollback()
                    return "already_done", data

                await self.db.execute(
                    "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? "
                    "WHERE riddle_id=? AND status='pending' AND id<>?",
                    (moderator_id, now, rid, sid),
                )

                cur = await self.db.execute(
                    "UPDATE riddles SET status='solved', slot_no=NULL, is_active=0, "
                    "solved_by=?, solved_at=?, updated_at=? WHERE id=? AND status='open'",
                    (solver_uid, now, now, rid),
                )
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
                    "xp_gain": xp,
                    "xp": xp,
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
                    # --- timing ---
                    "first_posted_at": data.get("first_posted_at"),
                    "submitted_at": data.get("submitted_at"),
                    "solved_at": now,
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
                           r.first_posted_at AS first_posted_at
                    FROM submissions s JOIN riddles r ON r.id = s.riddle_id
                    WHERE s.vote_message_id=? LIMIT 1
                    """,
                    (vmid,),
                )
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
                        "WHERE id=? AND status='pending'",
                        (moderator_id, now, sid),
                    )
                    await self.db.commit()
                    return "riddle_closed", data

                cur = await self.db.execute(
                    "UPDATE submissions SET status='wrong', voted_by=?, voted_at=? "
                    "WHERE id=? AND status='pending'",
                    (moderator_id, now, sid),
                )
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
                    "image_url": data.get("image_url"),
                    "mention_role_ids": data.get("mention_role_ids"),
                    "first_posted_at": data.get("first_posted_at"),
                    "submitted_at": data.get("submitted_at"),
                }
                return "rejected", ctx
            except Exception:
                with contextlib.suppress(Exception):
                    await self.db.rollback()
                raise