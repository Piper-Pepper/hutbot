# =========================
# riddle.py (Part 1/3)
# =========================
from __future__ import annotations

import os
import re
import json
import asyncio
import logging
import datetime as dt
from pathlib import Path
from typing import Optional, Any, Literal

import aiosqlite
import discord
from discord import app_commands, Interaction, Role
from discord.ext import commands
from discord.ui import View, Modal, TextInput, Select
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("riddle_system")


def env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


DB_PATH = (os.getenv("RIDDLE_DB_PATH") or "data/riddle.sqlite3").strip()

RIDDLE_CHANNEL_ID = env_int("RIDDLE_CHANNEL_ID", 0)
VOTE_CHANNEL_ID = env_int("RIDDLE_VOTE_CHANNEL_ID", 0)

RIDDLE_ROLE_ID = env_int("RIDDLE_ROLE_ID", 0)
RIDDLE_MANAGER_ROLE_ID = env_int("RIDDLE_MANAGER_ROLE_ID", 0)

EXCLUDED_COUNT_ROLE_ID = env_int("RIDDLE_EXCLUDED_COUNT_ROLE_ID", 0)
EXCLUDED_GAMEMASTER_ROLE_ID = env_int("RIDDLE_EXCLUDED_GAMEMASTER_ROLE_ID", 0)
EXTRA_EXCLUDED_ROLE_IDS_CSV = (os.getenv("RIDDLE_EXTRA_EXCLUDED_ROLE_IDS") or "").strip()

XP_NOTIFY_CHANNEL_ID = env_int("RIDDLE_XP_NOTIFY_CHANNEL_ID", 0)

DEFAULT_IMAGE_URL = (os.getenv("DEFAULT_RIDDLE_IMAGE_URL") or "").strip()
ACCESS_DENIED_IMAGE_URL = (os.getenv("RIDDLE_ACCESS_DENIED_IMAGE_URL") or "").strip()

MAX_RIDDLE_SLOTS = env_int("RIDDLE_MAX_SLOTS", 10)
MAX_EXTRA_PING_ROLES = env_int("RIDDLE_MAX_EXTRA_PING_ROLES", 3)
AUTO_SCAN_SECONDS = env_int("RIDDLE_AUTO_SCAN_SECONDS", 43200)

SUBMIT_BUTTON_ID = "riddle_submit_solution"
VOTE_UP_BUTTON_ID = "riddle_vote_up"
VOTE_DOWN_BUTTON_ID = "riddle_vote_down"

ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
ID_RE = re.compile(r"\b(\d{17,22})\b")


def now_iso_utc() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def now_date_str() -> str:
    return dt.datetime.now().strftime("%Y/%m/%d")


def clean_value(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    vv = v.strip()
    return vv if vv else None


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


def is_http_url(url: Optional[str]) -> bool:
    return bool(url and isinstance(url, str) and url.startswith(("http://", "https://")))


def truncate_text(text: str, max_len: int = 180) -> str:
    if text and len(text) > max_len:
        return text[:max_len] + "[...]"
    return text or ""


def clamp_embed_value(text: Optional[str], limit: int = 1024) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def clamp_embed_description(text: Optional[str], limit: int = 4096) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def extract_link(text: str) -> tuple[str, Optional[str]]:
    text = text or ""
    m = re.search(r"(https?://\S+)", text)
    if not m:
        return text.strip(), None
    link = m.group(1)
    return text.replace(link, "").strip(), link


def footer_text(guild: Optional[discord.Guild]) -> str:
    return f"{guild.name if guild else 'Unknown Guild'} • {now_date_str()}"


def member_has_role(member: discord.abc.User, role_id: int) -> bool:
    return isinstance(member, discord.Member) and any(r.id == role_id for r in member.roles)


def parse_csv_role_ids(s: Optional[str]) -> list[int]:
    if not s:
        return []
    out: list[int] = []
    seen = set()
    for p in str(s).split(","):
        p = p.strip()
        if not p:
            continue
        try:
            rid = int(p)
        except ValueError:
            continue
        if rid in seen:
            continue
        seen.add(rid)
        out.append(rid)
    return out


def parse_role_input(raw: str, guild: discord.Guild, max_roles: int = MAX_EXTRA_PING_ROLES) -> list[int]:
    if not raw:
        return []
    candidates: list[int] = []

    for m in ROLE_MENTION_RE.findall(raw):
        try:
            candidates.append(int(m))
        except ValueError:
            pass

    for m in ID_RE.findall(raw):
        try:
            candidates.append(int(m))
        except ValueError:
            pass

    tokens = [t.strip().lstrip("@") for t in re.split(r"[,;\n]+", raw) if t.strip()]
    for token in tokens:
        if re.fullmatch(r"\d{17,22}", token):
            continue
        token_l = token.lower()

        exact = [r for r in guild.roles if not r.is_default() and r.name.lower() == token_l]
        if exact:
            candidates.append(exact[0].id)
            continue

        starts = [r for r in guild.roles if not r.is_default() and r.name.lower().startswith(token_l)]
        if starts:
            candidates.append(starts[0].id)
            continue

        contains = [r for r in guild.roles if not r.is_default() and token_l in r.name.lower()]
        if contains:
            candidates.append(contains[0].id)

    out: list[int] = []
    seen = set()
    for rid in candidates:
        if rid == RIDDLE_ROLE_ID:
            continue
        if rid in seen:
            continue
        role = guild.get_role(rid)
        if role is None or role.is_default():
            continue
        seen.add(rid)
        out.append(rid)
        if len(out) >= max_roles:
            break
    return out


def unique_role_mentions(guild: Optional[discord.Guild], *role_ids: Optional[int]) -> list[str]:
    if guild is None:
        return []
    out: list[str] = []
    seen = set()
    for rid in role_ids:
        rid_i = safe_int(rid, None)
        if not rid_i or rid_i in seen:
            continue
        role = guild.get_role(rid_i)
        if role:
            seen.add(rid_i)
            out.append(role.mention)
    return out


def build_xpadd_commands(member_mention: str, member_name: str, xp_amount: int) -> tuple[str, str]:
    xp = max(0, to_int(xp_amount, 0))
    safe_name = (member_name or "UnknownUser").replace('"', "").strip() or "UnknownUser"
    return f'/xpadd "{safe_name}" {xp}', f"/xpadd {member_mention} {xp}"


async def safe_defer(
    interaction: Interaction,
    *,
    ephemeral: bool = False,
    thinking: bool = False,
) -> bool:
    if interaction.response.is_done():
        return True
    try:
        await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
        return True
    except (discord.NotFound, discord.HTTPException):
        return False


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
        description=f"This command is restricted to <@&{RIDDLE_MANAGER_ROLE_ID}>.",
        color=discord.Color.orange(),
    )
    if is_http_url(ACCESS_DENIED_IMAGE_URL):
        embed.set_image(url=ACCESS_DENIED_IMAGE_URL)
    embed.set_footer(text="Riddle Manager role required")
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


class LoggedPersistentView(View):
    def __init__(self, timeout=None):
        super().__init__(timeout=timeout)

    async def on_error(self, interaction: Interaction, error: Exception, item):
        logger.exception("View error in %s: %s", self.__class__.__name__, error)


class RiddleRepo:
    def __init__(self):
        self.db: Optional[aiosqlite.Connection] = None
        self.lock = asyncio.Lock()

    @staticmethod
    def _qident(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    async def start(self):
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(DB_PATH)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL;")
        await self.db.execute("PRAGMA foreign_keys=ON;")
        await self.db.execute("PRAGMA busy_timeout=5000;")
        await self.db.commit()
        await self._init_db()

    async def close(self):
        if self.db:
            await self.db.close()
            self.db = None

    async def _column_names(self, table: str) -> list[str]:
        assert self.db is not None
        cur = await self.db.execute(f"PRAGMA table_info({table})")
        rows = await cur.fetchall()
        await cur.close()
        return [r["name"] for r in rows]

    async def _add_col_if_missing(self, table: str, col_name: str, col_def: str):
        assert self.db is not None
        cols = await self._column_names(table)
        if col_name not in cols:
            await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")

    async def _index_columns(self, index_name: str) -> list[str]:
        assert self.db is not None
        escaped = index_name.replace("'", "''")
        cur = await self.db.execute(f"PRAGMA index_info('{escaped}')")
        rows = await cur.fetchall()
        await cur.close()
        rows_sorted = sorted(rows, key=lambda r: to_int(r["seqno"], 0))
        return [str(r["name"]) for r in rows_sorted if r["name"]]

    async def _recreate_riddle_indexes(self):
        assert self.db is not None
        await self.db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_riddle_slot_open
            ON riddles(guild_id, slot_no)
            WHERE status='open' AND slot_no IS NOT NULL
            """
        )
        await self.db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_riddle_active_one
            ON riddles(guild_id)
            WHERE status='open' AND is_active=1
            """
        )
        await self.db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_riddle_posted_msg
            ON riddles(posted_message_id)
            WHERE posted_message_id IS NOT NULL
            """
        )

    async def _rebuild_riddles_table_without_legacy_unique(self):
        assert self.db is not None

        await self.db.execute("PRAGMA foreign_keys=OFF;")
        await self.db.execute("BEGIN IMMEDIATE")
        try:
            await self.db.execute(
                """
                CREATE TABLE riddles_new (
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
                    solved_by INTEGER,
                    solved_at TEXT,
                    closed_by INTEGER,
                    closed_at TEXT
                )
                """
            )

            await self.db.execute(
                """
                INSERT INTO riddles_new (
                    id, guild_id, riddle_no, slot_no, is_active, text, solution, xp,
                    mention_role_ids, image_url, solution_url, status, created_by,
                    created_at, updated_at, posted_channel_id, posted_message_id,
                    solved_by, solved_at, closed_by, closed_at
                )
                SELECT
                    id, guild_id, riddle_no, slot_no, is_active, text, solution, xp,
                    mention_role_ids, image_url, solution_url, status, created_by,
                    created_at, updated_at, posted_channel_id, posted_message_id,
                    solved_by, solved_at, closed_by, closed_at
                FROM riddles
                """
            )

            await self.db.execute("DROP TABLE riddles")
            await self.db.execute("ALTER TABLE riddles_new RENAME TO riddles")
            await self._recreate_riddle_indexes()
            await self.db.commit()
            logger.info("Rebuilt riddles table without legacy unique(guild_id, riddle_no).")
        except Exception:
            await self.db.rollback()
            raise
        finally:
            await self.db.execute("PRAGMA foreign_keys=ON;")

    async def _drop_legacy_riddle_no_unique_constraints(self):
        """
        Detect and remove old UNIQUE constraints/indexes on (guild_id, riddle_no).
        If an autoindex exists (from table-level UNIQUE), rebuild the table.
        Must be called while holding self.lock.
        """
        assert self.db is not None

        cur = await self.db.execute("PRAGMA index_list(riddles)")
        idx_rows = await cur.fetchall()
        await cur.close()

        autoindex_found = False
        to_drop: list[str] = []

        for row in idx_rows:
            idx_name = str(row["name"])
            is_unique = to_int(row["unique"], 0) == 1
            if not is_unique:
                continue

            cols = await self._index_columns(idx_name)
            if cols == ["guild_id", "riddle_no"]:
                if idx_name.startswith("sqlite_autoindex"):
                    autoindex_found = True
                else:
                    to_drop.append(idx_name)

        for idx_name in to_drop:
            await self.db.execute(f"DROP INDEX IF EXISTS {self._qident(idx_name)}")
            logger.info("Dropped legacy unique index: %s", idx_name)

        if autoindex_found:
            logger.warning("Legacy autoindex UNIQUE(guild_id, riddle_no) found. Rebuilding table.")
            await self._rebuild_riddles_table_without_legacy_unique()

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
            solved_by INTEGER,
            solved_at TEXT,
            closed_by INTEGER,
            closed_at TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_riddle_slot_open
        ON riddles(guild_id, slot_no)
        WHERE status='open' AND slot_no IS NOT NULL;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_riddle_active_one
        ON riddles(guild_id)
        WHERE status='open' AND is_active=1;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_riddle_posted_msg
        ON riddles(posted_message_id)
        WHERE posted_message_id IS NOT NULL;

        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            riddle_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            answer TEXT NOT NULL,
            vote_message_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','correct','wrong','cancelled')),
            created_at TEXT NOT NULL,
            voted_by INTEGER,
            voted_at TEXT,
            FOREIGN KEY(riddle_id) REFERENCES riddles(id) ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_sub_vote_msg
        ON submissions(vote_message_id)
        WHERE vote_message_id IS NOT NULL;

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
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS guild_stats_cache (
            guild_id INTEGER PRIMARY KEY,
            solved_total_filtered INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        """
        async with self.lock:
            await self.db.executescript(schema)

            await self._add_col_if_missing("riddles", "riddle_no", "riddle_no INTEGER")
            await self._add_col_if_missing("riddles", "slot_no", "slot_no INTEGER")
            await self._add_col_if_missing("riddles", "is_active", "is_active INTEGER NOT NULL DEFAULT 0")
            await self._add_col_if_missing("riddles", "xp", "xp INTEGER NOT NULL DEFAULT 0")
            await self._add_col_if_missing("riddles", "mention_role_ids", "mention_role_ids TEXT")
            await self._add_col_if_missing("riddles", "image_url", "image_url TEXT")
            await self._add_col_if_missing("riddles", "solution_url", "solution_url TEXT")
            await self._add_col_if_missing("riddles", "posted_channel_id", "posted_channel_id INTEGER")
            await self._add_col_if_missing("riddles", "posted_message_id", "posted_message_id INTEGER")
            await self._add_col_if_missing("riddles", "solved_by", "solved_by INTEGER")
            await self._add_col_if_missing("riddles", "solved_at", "solved_at TEXT")
            await self._add_col_if_missing("riddles", "closed_by", "closed_by INTEGER")
            await self._add_col_if_missing("riddles", "closed_at", "closed_at TEXT")

            await self._add_col_if_missing("submissions", "vote_message_id", "vote_message_id INTEGER")
            await self._add_col_if_missing("submissions", "status", "status TEXT NOT NULL DEFAULT 'pending'")
            await self._add_col_if_missing("submissions", "voted_by", "voted_by INTEGER")
            await self._add_col_if_missing("submissions", "voted_at", "voted_at TEXT")

            await self._drop_legacy_riddle_no_unique_constraints()

            await self.db.execute(
                """
                UPDATE riddles AS r
                SET riddle_no = (
                    SELECT COUNT(*)
                    FROM riddles r2
                    WHERE r2.guild_id = r.guild_id AND r2.id <= r.id
                )
                WHERE r.riddle_no IS NULL
                """
            )
            await self.db.commit()

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
            await self.db.commit()
            rc = cur.rowcount
            lid = int(cur.lastrowid or 0)
            await cur.close()
        return rc, lid

    async def ensure_guild_state(self, guild_id: int):
        await self._exec(
            """
            INSERT INTO guild_riddle_state (guild_id, is_enabled, updated_at)
            VALUES (?, 0, ?)
            ON CONFLICT(guild_id) DO NOTHING
            """,
            (guild_id, now_iso_utc()),
        )
        await self._exec(
            """
            INSERT INTO guild_stats_cache (guild_id, solved_total_filtered, updated_at)
            VALUES (?, 0, ?)
            ON CONFLICT(guild_id) DO NOTHING
            """,
            (guild_id, now_iso_utc()),
        )

    async def set_enabled(self, guild_id: int, enabled: bool):
        await self._exec(
            """
            INSERT INTO guild_riddle_state (guild_id, is_enabled, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
              is_enabled=excluded.is_enabled,
              updated_at=excluded.updated_at
            """,
            (guild_id, 1 if enabled else 0, now_iso_utc()),
        )

    async def is_enabled(self, guild_id: int) -> bool:
        row = await self._one("SELECT is_enabled FROM guild_riddle_state WHERE guild_id=? LIMIT 1", (guild_id,))
        return bool(to_int(row.get("is_enabled"), 0)) if row else False

    async def get_state_row(self, guild_id: int) -> dict:
        row = await self._one("SELECT * FROM guild_riddle_state WHERE guild_id=? LIMIT 1", (guild_id,))
        return row or {"guild_id": guild_id, "is_enabled": 0, "updated_at": now_iso_utc()}

    async def list_all_guild_ids(self) -> list[int]:
        rows = await self._all(
            """
            SELECT guild_id FROM guild_riddle_state
            UNION
            SELECT guild_id FROM guild_stats_cache
            UNION
            SELECT DISTINCT guild_id FROM riddles
            UNION
            SELECT DISTINCT guild_id FROM submissions
            UNION
            SELECT DISTINCT guild_id FROM user_stats
            """
        )
        return [to_int(r.get("guild_id"), 0) for r in rows if to_int(r.get("guild_id"), 0) > 0]

    async def get_cached_solved_total(self, guild_id: int) -> int:
        row = await self._one("SELECT solved_total_filtered FROM guild_stats_cache WHERE guild_id=? LIMIT 1", (guild_id,))
        return max(0, to_int(row.get("solved_total_filtered"), 0)) if row else 0

    async def set_cached_solved_total(self, guild_id: int, value: int):
        v = max(0, to_int(value, 0))
        await self._exec(
            """
            INSERT INTO guild_stats_cache (guild_id, solved_total_filtered, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
              solved_total_filtered=excluded.solved_total_filtered,
              updated_at=excluded.updated_at
            """,
            (guild_id, v, now_iso_utc()),
        )

    async def inc_cached_solved_total(self, guild_id: int, delta: int = 1):
        d = max(0, to_int(delta, 0))
        await self._exec(
            """
            INSERT INTO guild_stats_cache (guild_id, solved_total_filtered, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
              solved_total_filtered = solved_total_filtered + ?,
              updated_at = excluded.updated_at
            """,
            (guild_id, d, now_iso_utc(), d),
        )

    async def sync_open_slot_numbers(self, guild_id: int, solved_base: int):
        """
        Two-phase renumber to avoid transient unique collisions.
        """
        if self.db is None:
            return

        base = max(0, to_int(solved_base, 0))
        now = now_iso_utc()

        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                await self.db.execute(
                    """
                    UPDATE riddles
                    SET riddle_no = -(id + 1000000000), updated_at=?
                    WHERE guild_id=?
                      AND status='open'
                      AND slot_no BETWEEN 1 AND ?
                    """,
                    (now, guild_id, MAX_RIDDLE_SLOTS),
                )
                await self.db.execute(
                    """
                    UPDATE riddles
                    SET riddle_no=(? + slot_no), updated_at=?
                    WHERE guild_id=?
                      AND status='open'
                      AND slot_no BETWEEN 1 AND ?
                    """,
                    (base, now, guild_id, MAX_RIDDLE_SLOTS),
                )
                await self.db.commit()
            except Exception:
                await self.db.rollback()
                raise

    async def open_slot_map(self, guild_id: int) -> dict[int, dict]:
        rows = await self._all(
            """
            SELECT * FROM riddles
            WHERE guild_id=? AND status='open' AND slot_no BETWEEN 1 AND ?
            ORDER BY slot_no ASC
            """,
            (guild_id, MAX_RIDDLE_SLOTS),
        )
        out: dict[int, dict] = {}
        for r in rows:
            s = to_int(r.get("slot_no"), 0)
            if 1 <= s <= MAX_RIDDLE_SLOTS:
                out[s] = r
        return out

    async def get_open_slot_riddle(self, guild_id: int, slot_no: int) -> Optional[dict]:
        return await self._one(
            "SELECT * FROM riddles WHERE guild_id=? AND status='open' AND slot_no=? LIMIT 1",
            (guild_id, slot_no),
        )

    async def get_open_slot1(self, guild_id: int) -> Optional[dict]:
        return await self.get_open_slot_riddle(guild_id, 1)

    async def get_open_riddle_by_id(self, guild_id: int, riddle_id: int) -> Optional[dict]:
        return await self._one(
            "SELECT * FROM riddles WHERE guild_id=? AND id=? AND status='open' LIMIT 1",
            (guild_id, riddle_id),
        )

    async def get_open_riddle_by_message(self, guild_id: int, message_id: int) -> Optional[dict]:
        return await self._one(
            "SELECT * FROM riddles WHERE guild_id=? AND posted_message_id=? AND status='open' LIMIT 1",
            (guild_id, message_id),
        )

    async def upsert_slot_content(
        self, *, guild_id: int, user_id: int, slot_no: int, text: str, solution: str, xp: int
    ) -> Optional[int]:
        if self.db is None or not (1 <= slot_no <= MAX_RIDDLE_SLOTS):
            return None
        text = clean_value(text)
        solution = clean_value(solution)
        if not text or not solution:
            return None
        xp = max(0, to_int(xp, 0))
        now = now_iso_utc()

        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    "SELECT * FROM riddles WHERE guild_id=? AND status='open' AND slot_no=? LIMIT 1",
                    (guild_id, slot_no),
                )
                row = await cur.fetchone()
                await cur.close()

                if row:
                    old = dict(row)
                    await self.db.execute(
                        """
                        UPDATE riddles
                        SET text=?, solution=?, xp=?, created_by=?, updated_at=?
                        WHERE id=?
                        """,
                        (text, solution, xp, user_id, now, old["id"]),
                    )
                    rid = to_int(old["id"], 0)
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
                        )
                        VALUES (?, ?, ?, 0, ?, ?, ?, NULL, 'open', ?, ?, ?)
                        """,
                        (guild_id, riddle_no, slot_no, text, solution, xp, user_id, now, now),
                    )
                    rid = int(cur.lastrowid or 0)
                    await cur.close()

                await self.db.commit()
                return rid if rid > 0 else None
            except Exception:
                await self.db.rollback()
                raise

    async def update_open_riddle_content_by_id(
        self, guild_id: int, riddle_id: int, user_id: int, text: str, solution: str, xp: int
    ) -> bool:
        text = clean_value(text)
        solution = clean_value(solution)
        if not text or not solution:
            return False
        xp = max(0, to_int(xp, 0))
        rc, _ = await self._exec(
            """
            UPDATE riddles
            SET text=?, solution=?, xp=?, created_by=?, updated_at=?
            WHERE guild_id=? AND id=? AND status='open'
            """,
            (text, solution, xp, user_id, now_iso_utc(), guild_id, riddle_id),
        )
        return rc > 0

    async def set_riddle_images_by_id_open(
        self, guild_id: int, riddle_id: int, riddle_image_url: Optional[str], solution_image_url: Optional[str], user_id: int
    ) -> bool:
        rc, _ = await self._exec(
            """
            UPDATE riddles
            SET image_url=?, solution_url=?, created_by=?, updated_at=?
            WHERE guild_id=? AND id=? AND status='open'
            """,
            (clean_value(riddle_image_url), clean_value(solution_image_url), user_id, now_iso_utc(), guild_id, riddle_id),
        )
        return rc > 0

    async def set_riddle_mentions_by_id_open(
        self, guild_id: int, riddle_id: int, mention_role_ids_csv: Optional[str], user_id: int
    ) -> bool:
        rc, _ = await self._exec(
            """
            UPDATE riddles
            SET mention_role_ids=?, created_by=?, updated_at=?
            WHERE guild_id=? AND id=? AND status='open'
            """,
            (clean_value(mention_role_ids_csv), user_id, now_iso_utc(), guild_id, riddle_id),
        )
        return rc > 0

# =========================
# riddle.py (Part 2/3)
# =========================
# ---- RiddleRepo continued ----
    async def compact_open_slots(self, guild_id: int):
        """
        Close gaps and keep open riddles in contiguous slots 1..N.
        Extra open riddles above MAX_RIDDLE_SLOTS are auto-closed.
        """
        if self.db is None:
            return
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                now = now_iso_utc()
                cur = await self.db.execute(
                    """
                    SELECT id
                    FROM riddles
                    WHERE guild_id=? AND status='open'
                    ORDER BY CASE WHEN slot_no BETWEEN 1 AND ? THEN slot_no ELSE 9999 END, id
                    """,
                    (guild_id, MAX_RIDDLE_SLOTS),
                )
                rows = await cur.fetchall()
                await cur.close()

                await self.db.execute(
                    "UPDATE riddles SET slot_no=NULL, is_active=0, updated_at=? WHERE guild_id=? AND status='open'",
                    (now, guild_id),
                )

                for i, row in enumerate(rows, start=1):
                    rid = to_int(row["id"], 0)
                    if i <= MAX_RIDDLE_SLOTS:
                        await self.db.execute(
                            "UPDATE riddles SET slot_no=?, is_active=?, updated_at=? WHERE id=?",
                            (i, 1 if i == 1 else 0, now, rid),
                        )
                    else:
                        await self.db.execute(
                            "UPDATE riddles SET status='closed', closed_by=0, closed_at=?, updated_at=? WHERE id=?",
                            (now, now, rid),
                        )
                        await self.db.execute(
                            "UPDATE submissions SET status='cancelled', voted_by=0, voted_at=? WHERE riddle_id=? AND status='pending'",
                            (now, rid),
                        )

                await self.db.commit()
            except Exception:
                await self.db.rollback()
                raise

    async def move_open_riddle_to_end(self, guild_id: int, riddle_id: int) -> bool:
        """
        Move selected open riddle to the last occupied position.
        Others shift up. Occupancy count remains same.
        """
        if self.db is None:
            return False

        await self.compact_open_slots(guild_id)

        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    """
                    SELECT id
                    FROM riddles
                    WHERE guild_id=? AND status='open' AND slot_no BETWEEN 1 AND ?
                    ORDER BY slot_no ASC
                    """,
                    (guild_id, MAX_RIDDLE_SLOTS),
                )
                rows = await cur.fetchall()
                await cur.close()

                ids = [to_int(r["id"], 0) for r in rows if to_int(r["id"], 0) > 0]
                if riddle_id not in ids:
                    await self.db.rollback()
                    return False

                if len(ids) <= 1:
                    await self.db.rollback()
                    return True

                ids.remove(riddle_id)
                ids.append(riddle_id)

                now = now_iso_utc()
                await self.db.execute(
                    "UPDATE riddles SET slot_no=NULL, is_active=0, updated_at=? WHERE guild_id=? AND status='open'",
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
                await self.db.rollback()
                raise

    async def close_open_riddle_by_id(self, guild_id: int, riddle_id: int, closed_by: int) -> Optional[dict]:
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

                r = dict(row)
                now = now_iso_utc()
                await self.db.execute(
                    """
                    UPDATE riddles
                    SET status='closed', slot_no=NULL, is_active=0, closed_by=?, closed_at=?, updated_at=?
                    WHERE id=? AND status='open'
                    """,
                    (closed_by, now, now, riddle_id),
                )
                await self.db.execute(
                    "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? WHERE riddle_id=? AND status='pending'",
                    (closed_by, now, riddle_id),
                )
                await self.db.commit()
                return r
            except Exception:
                await self.db.rollback()
                raise

    async def close_slot1_unsolved(self, guild_id: int, closed_by: int) -> Optional[dict]:
        row = await self.get_open_slot1(guild_id)
        if not row:
            return None
        return await self.close_open_riddle_by_id(guild_id, to_int(row["id"], 0), closed_by)

    async def set_riddle_post_ref(self, riddle_id: int, channel_id: int, message_id: int):
        await self._exec(
            "UPDATE riddles SET posted_channel_id=?, posted_message_id=?, updated_at=? WHERE id=?",
            (channel_id, message_id, now_iso_utc(), riddle_id),
        )

    async def clear_all_open_post_refs(self, guild_id: Optional[int] = None):
        if guild_id is None:
            await self._exec(
                "UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL, updated_at=? WHERE status='open'",
                (now_iso_utc(),),
            )
        else:
            await self._exec(
                "UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL, updated_at=? WHERE guild_id=? AND status='open'",
                (now_iso_utc(), guild_id),
            )

    async def clear_other_open_post_refs(self, guild_id: int, keep_riddle_id: int):
        await self._exec(
            """
            UPDATE riddles
            SET posted_channel_id=NULL, posted_message_id=NULL, updated_at=?
            WHERE guild_id=? AND status='open' AND id<>?
            """,
            (now_iso_utc(), guild_id, keep_riddle_id),
        )

    async def list_open_post_refs(self, guild_id: int) -> list[dict]:
        return await self._all(
            """
            SELECT id, posted_channel_id, posted_message_id
            FROM riddles
            WHERE guild_id=? AND status='open' AND posted_message_id IS NOT NULL
            """,
            (guild_id,),
        )

    async def create_submission_pending(self, guild_id: int, riddle_id: int, user_id: int, answer: str) -> Optional[int]:
        _, lid = await self._exec(
            """
            INSERT INTO submissions (guild_id, riddle_id, user_id, answer, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (guild_id, riddle_id, user_id, answer, now_iso_utc()),
        )
        return lid if lid > 0 else None

    async def set_submission_vote_message(self, submission_id: int, vote_message_id: int) -> bool:
        rc, _ = await self._exec("UPDATE submissions SET vote_message_id=? WHERE id=?", (vote_message_id, submission_id))
        return rc > 0

    async def delete_submission(self, submission_id: int):
        await self._exec("DELETE FROM submissions WHERE id=?", (submission_id,))

    async def list_vote_messages_for_riddle(self, riddle_id: int) -> list[dict]:
        return await self._all(
            "SELECT id, vote_message_id FROM submissions WHERE riddle_id=? AND vote_message_id IS NOT NULL",
            (riddle_id,),
        )

    async def reset_pending_vote_refs(self):
        await self._exec("UPDATE submissions SET vote_message_id=NULL WHERE status='pending'")

    async def cancel_pending_for_non_open(self):
        await self._exec(
            """
            UPDATE submissions
            SET status='cancelled', voted_by=0, voted_at=?
            WHERE status='pending'
              AND riddle_id IN (SELECT id FROM riddles WHERE status <> 'open')
            """,
            (now_iso_utc(),),
        )

    async def pending_open_submissions(self) -> list[dict]:
        return await self._all(
            """
            SELECT
                s.id AS submission_id,
                s.guild_id AS guild_id,
                s.user_id AS user_id,
                s.answer AS answer,
                s.vote_message_id AS vote_message_id,
                r.id AS riddle_id,
                r.text AS riddle_text,
                r.solution AS solution,
                r.xp AS xp,
                r.mention_role_ids AS mention_role_ids
            FROM submissions s
            JOIN riddles r ON r.id = s.riddle_id
            WHERE s.status='pending' AND r.status='open'
            ORDER BY s.id ASC
            """
        )

    async def stats_entries(self, guild_id: int) -> list[tuple[int, int, int]]:
        rows = await self._all(
            "SELECT user_id, solved_riddles, xp FROM user_stats WHERE guild_id=? ORDER BY solved_riddles DESC, xp DESC",
            (guild_id,),
        )
        out = []
        for r in rows:
            uid = to_int(r.get("user_id"), 0)
            if uid <= 0:
                continue
            out.append((uid, max(0, to_int(r.get("solved_riddles"), 0)), max(0, to_int(r.get("xp"), 0))))
        return out

    async def replace_user_stats(self, guild_id: int, rows: list[tuple[int, int, int]]):
        if self.db is None:
            return
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                await self.db.execute("DELETE FROM user_stats WHERE guild_id=?", (guild_id,))
                for uid, solved, xp in rows:
                    await self.db.execute(
                        "INSERT INTO user_stats (guild_id, user_id, solved_riddles, xp) VALUES (?, ?, ?, ?)",
                        (guild_id, uid, max(0, solved), max(0, xp)),
                    )
                await self.db.commit()
            except Exception:
                await self.db.rollback()
                raise

    async def add_user_solve_xp(self, guild_id: int, user_id: int, xp_gain: int):
        xp_gain = max(0, to_int(xp_gain, 0))
        await self._exec(
            """
            INSERT INTO user_stats (guild_id, user_id, solved_riddles, xp)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(guild_id, user_id)
            DO UPDATE SET
              solved_riddles = solved_riddles + 1,
              xp = xp + excluded.xp
            """,
            (guild_id, user_id, xp_gain),
        )

    async def approve_submission(self, vote_message_id: int, moderator_id: int) -> tuple[str, Optional[dict]]:
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
                    SELECT
                        s.id AS submission_id,
                        s.guild_id AS guild_id,
                        s.riddle_id AS riddle_id,
                        s.user_id AS user_id,
                        s.answer AS answer,
                        s.status AS submission_status,
                        r.text AS riddle_text,
                        r.solution AS solution,
                        r.xp AS xp,
                        r.status AS riddle_status,
                        r.mention_role_ids AS mention_role_ids
                    FROM submissions s
                    JOIN riddles r ON r.id = s.riddle_id
                    WHERE s.vote_message_id=?
                    LIMIT 1
                    """,
                    (vmid,),
                )
                row = await cur.fetchone()
                await cur.close()

                if not row:
                    await self.db.rollback()
                    return "not_found", None

                data = dict(row)
                sub_status = str(data.get("submission_status") or "")
                rid_status = str(data.get("riddle_status") or "")

                if sub_status != "pending":
                    await self.db.rollback()
                    return "already_done", data

                now = now_iso_utc()
                sid = to_int(data.get("submission_id"), 0)
                rid = to_int(data.get("riddle_id"), 0)
                gid = to_int(data.get("guild_id"), 0)
                solver_uid = to_int(data.get("user_id"), 0)
                xp_gain = max(0, to_int(data.get("xp"), 0))

                if rid_status != "open":
                    await self.db.execute(
                        "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? WHERE id=? AND status='pending'",
                        (moderator_id, now, sid),
                    )
                    await self.db.commit()
                    return "riddle_closed", data

                cur = await self.db.execute(
                    """
                    UPDATE submissions
                    SET status='correct', voted_by=?, voted_at=?
                    WHERE id=? AND status='pending'
                    """,
                    (moderator_id, now, sid),
                )
                sub_rc = cur.rowcount
                await cur.close()
                if sub_rc <= 0:
                    await self.db.rollback()
                    return "already_done", data

                await self.db.execute(
                    """
                    UPDATE submissions
                    SET status='cancelled', voted_by=?, voted_at=?
                    WHERE riddle_id=? AND status='pending' AND id<>?
                    """,
                    (moderator_id, now, rid, sid),
                )

                cur = await self.db.execute(
                    """
                    UPDATE riddles
                    SET status='solved', slot_no=NULL, is_active=0, solved_by=?, solved_at=?, updated_at=?
                    WHERE id=? AND status='open'
                    """,
                    (solver_uid, now, now, rid),
                )
                rid_rc = cur.rowcount
                await cur.close()
                if rid_rc <= 0:
                    await self.db.rollback()
                    return "riddle_closed", data

                if gid > 0 and solver_uid > 0:
                    await self.db.execute(
                        """
                        INSERT INTO user_stats (guild_id, user_id, solved_riddles, xp)
                        VALUES (?, ?, 1, ?)
                        ON CONFLICT(guild_id, user_id)
                        DO UPDATE SET
                          solved_riddles = solved_riddles + 1,
                          xp = xp + excluded.xp
                        """,
                        (gid, solver_uid, xp_gain),
                    )

                await self.db.commit()

                ctx = {
                    "submission_id": sid,
                    "guild_id": gid,
                    "riddle_id": rid,
                    "solver_user_id": solver_uid,
                    "xp_gain": xp_gain,
                    "answer": data.get("answer"),
                    "solution": data.get("solution"),
                    "riddle_text": data.get("riddle_text"),
                    "mention_role_ids": data.get("mention_role_ids"),
                }
                return "approved", ctx
            except Exception:
                await self.db.rollback()
                raise

    async def reject_submission(self, vote_message_id: int, moderator_id: int) -> tuple[str, Optional[dict]]:
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
                    SELECT
                        s.id AS submission_id,
                        s.guild_id AS guild_id,
                        s.riddle_id AS riddle_id,
                        s.user_id AS user_id,
                        s.answer AS answer,
                        s.status AS submission_status,
                        r.status AS riddle_status
                    FROM submissions s
                    JOIN riddles r ON r.id = s.riddle_id
                    WHERE s.vote_message_id=?
                    LIMIT 1
                    """,
                    (vmid,),
                )
                row = await cur.fetchone()
                await cur.close()

                if not row:
                    await self.db.rollback()
                    return "not_found", None

                data = dict(row)
                sub_status = str(data.get("submission_status") or "")
                rid_status = str(data.get("riddle_status") or "")

                if sub_status != "pending":
                    await self.db.rollback()
                    return "already_done", data

                now = now_iso_utc()
                sid = to_int(data.get("submission_id"), 0)

                if rid_status != "open":
                    await self.db.execute(
                        "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? WHERE id=? AND status='pending'",
                        (moderator_id, now, sid),
                    )
                    await self.db.commit()
                    return "riddle_closed", data

                cur = await self.db.execute(
                    """
                    UPDATE submissions
                    SET status='wrong', voted_by=?, voted_at=?
                    WHERE id=? AND status='pending'
                    """,
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
                }
                return "rejected", ctx
            except Exception:
                await self.db.rollback()
                raise


async def _edit_vote_result_message(msg: discord.Message, *, ok: bool, moderator_mention: str):
    try:
        if msg.embeds:
            d = msg.embeds[0].to_dict()
            d["fields"] = [f for f in d.get("fields", []) if f.get("name") not in {"✅ Result", "❌ Result"}]
            e = discord.Embed.from_dict(d)
        else:
            e = discord.Embed(title="📜 Solution Vote")
        if ok:
            e.color = discord.Color.green()
            e.add_field(name="✅ Result", value=clamp_embed_value(f"Approved by {moderator_mention}"), inline=False)
        else:
            e.color = discord.Color.red()
            e.add_field(name="❌ Result", value=clamp_embed_value(f"Rejected by {moderator_mention}"), inline=False)
        await msg.edit(embed=e, view=None)
    except Exception:
        pass


class RiddleCog(commands.Cog):
    def __init__(self, bot: commands.Bot, repo: RiddleRepo):
        self.bot = bot
        self.repo = repo
        self._auto_task: Optional[asyncio.Task] = None
        self._startup_done = False

    def excluded_role_ids(self) -> set[int]:
        s = {EXCLUDED_COUNT_ROLE_ID, EXCLUDED_GAMEMASTER_ROLE_ID, RIDDLE_MANAGER_ROLE_ID}
        for rid in parse_csv_role_ids(EXTRA_EXCLUDED_ROLE_IDS_CSV):
            if rid > 0:
                s.add(rid)
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

    async def sync_open_slot_numbers_for_guild(self, guild_id: int):
        base = await self.repo.get_cached_solved_total(guild_id)
        await self.repo.sync_open_slot_numbers(guild_id, base)

    async def normalize_after_structure_change(self, guild_id: int):
        await self.repo.compact_open_slots(guild_id)
        await self.sync_open_slot_numbers_for_guild(guild_id)

    async def filtered_stats_entries_for_guild(self, guild: discord.Guild) -> list[tuple[int, int, int]]:
        raw = await self.repo.stats_entries(guild.id)
        out = []
        for uid, solved, xp in raw:
            if await self.user_is_excluded(guild, uid):
                continue
            out.append((uid, solved, xp))
        return out

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

    async def post_xp_award_hint(self, guild: Optional[discord.Guild], user_id: int, xp_gain: int):
        if XP_NOTIFY_CHANNEL_ID <= 0 or xp_gain <= 0:
            return
        ch = await self.resolve_channel(XP_NOTIFY_CHANNEL_ID)
        if ch is None or not hasattr(ch, "send"):
            return

        mention, name, avatar = await self.resolve_user_label(guild, user_id)
        cmd_name, cmd_mention = build_xpadd_commands(mention, name, xp_gain)

        e = discord.Embed(
            title="🏆 XP Award Hint",
            description=clamp_embed_description(f"Approved solution by {mention}"),
            color=discord.Color.blue(),
        )
        e.add_field(name="XP", value=str(max(0, xp_gain)), inline=True)
        e.add_field(name="By name", value=f"`{cmd_name}`", inline=False)
        e.add_field(name="By mention", value=f"`{cmd_mention}`", inline=False)
        if avatar:
            e.set_thumbnail(url=avatar)
        e.set_footer(text=footer_text(guild))

        try:
            await ch.send(embed=e, allowed_mentions=discord.AllowedMentions(roles=False, users=False, everyone=False))
        except Exception:
            pass

    def build_ping_content(self, guild: Optional[discord.Guild], mention_role_ids_csv: Optional[str]) -> Optional[str]:
        extra = []
        for rid in parse_csv_role_ids(mention_role_ids_csv):
            if rid == RIDDLE_ROLE_ID:
                continue
            if rid not in extra:
                extra.append(rid)
            if len(extra) >= MAX_EXTRA_PING_ROLES:
                break
        mentions = unique_role_mentions(guild, RIDDLE_ROLE_ID, *extra)
        return " ".join(dict.fromkeys([m for m in mentions if m])) or None

    def build_riddle_embed(self, guild: Optional[discord.Guild], riddle: dict) -> discord.Embed:
        r_no = to_int(riddle.get("riddle_no"), to_int(riddle.get("id"), 0))
        xp = max(0, to_int(riddle.get("xp"), 0))
        e = discord.Embed(
            title=f"🧩 Goon Hut Riddle No.{r_no}",
            description=clamp_embed_description((riddle.get("text") or "*No riddle text set.*").strip()),
            color=discord.Color.blurple(),
        )
        if guild:
            if guild.icon:
                e.set_author(name=guild.name, icon_url=guild.icon.url)
            else:
                e.set_author(name=guild.name)
        e.add_field(name="🏆 Award", value=f"{xp} XP", inline=False)
        img = riddle.get("image_url")
        if not is_http_url(img):
            img = DEFAULT_IMAGE_URL
        if is_http_url(img):
            e.set_image(url=img)
        e.set_footer(text=footer_text(guild))
        return e

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

    async def publish_slot1_post(self, guild_id: int, *, force_repost: bool, allow_role_ping: bool) -> str:
        await self.normalize_after_structure_change(guild_id)
        slot1 = await self.repo.get_open_slot1(guild_id)
        if not slot1:
            return "no_slot1"

        guild = self.bot.get_guild(guild_id)
        ch = await self.resolve_channel(RIDDLE_CHANNEL_ID)
        if ch is None or not hasattr(ch, "send"):
            return "no_channel"

        embed = self.build_riddle_embed(guild, slot1)
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
                    content=content,
                    embed=embed,
                    view=SubmitButtonView(self),
                    allowed_mentions=discord.AllowedMentions(roles=allow_role_ping, users=False, everyone=False),
                )
                await self.repo.clear_other_open_post_refs(guild_id, to_int(slot1["id"], 0))
                return "updated"

            msg = await ch.send(
                content=content,
                embed=embed,
                view=SubmitButtonView(self),
                allowed_mentions=discord.AllowedMentions(roles=allow_role_ping, users=False, everyone=False),
            )
            await self.repo.set_riddle_post_ref(to_int(slot1["id"], 0), msg.channel.id, msg.id)
            await self.repo.clear_other_open_post_refs(guild_id, to_int(slot1["id"], 0))
            return "posted"
        except Exception:
            return "error"

    async def enforce_enabled_state(self, guild_id: int, *, allow_ping: bool, force_repost: bool = False) -> str:
        await self.normalize_after_structure_change(guild_id)
        enabled = await self.repo.is_enabled(guild_id)
        slot1 = await self.repo.get_open_slot1(guild_id)

        if not enabled:
            await self.remove_active_riddle_posts(guild_id)
            return "disabled"

        if not slot1:
            await self.repo.set_enabled(guild_id, False)
            await self.remove_active_riddle_posts(guild_id)
            return "enabled_but_no_slot1"

        return await self.publish_slot1_post(guild_id, force_repost=force_repost, allow_role_ping=allow_ping)

    async def repost_pending_votes(self):
        rows = await self.repo.pending_open_submissions()
        if not rows:
            return
        vote_channel = await self.resolve_channel(VOTE_CHANNEL_ID)
        if vote_channel is None or not hasattr(vote_channel, "send"):
            return

        for row in rows:
            guild = self.bot.get_guild(to_int(row.get("guild_id"), 0))
            uid = to_int(row.get("user_id"), 0)
            _, uname, uavatar = await self.resolve_user_label(guild, uid)

            embed = discord.Embed(
                title="📜 New Solution Submitted",
                description=clamp_embed_description(row.get("riddle_text") or "*No riddle text*"),
                color=discord.Color.gold(),
            )
            if uavatar:
                embed.set_author(name=uname, icon_url=uavatar)
            else:
                embed.set_author(name=uname)

            embed.add_field(name="🧠 User Answer", value=clamp_embed_value(row.get("answer") or "*Empty*"), inline=False)
            embed.add_field(name="✅ Correct Solution", value=clamp_embed_value(row.get("solution") or "*Not set*"), inline=False)
            embed.add_field(name="🏆 Award", value=clamp_embed_value(f"{max(0, to_int(row.get('xp'), 0))} XP"), inline=False)
            embed.add_field(name="🆔 User ID", value=clamp_embed_value(str(uid)), inline=False)
            embed.set_footer(text=footer_text(guild))

            try:
                vm = await vote_channel.send(embed=embed, view=VoteButtons(self))
                await self.repo.set_submission_vote_message(to_int(row["submission_id"], 0), vm.id)
            except Exception:
                pass

    async def startup_rebuild(self):
        gids = set(await self.repo.list_all_guild_ids())
        gids.update(g.id for g in self.bot.guilds)

        for gid in gids:
            await self.repo.ensure_guild_state(gid)

        enabled_map: dict[int, bool] = {}
        for gid in gids:
            enabled_map[gid] = await self.repo.is_enabled(gid)
            await self.rebuild_cached_solved_total_for_guild(gid)
            await self.normalize_after_structure_change(gid)

        await self.delete_button_messages_in_channel(RIDDLE_CHANNEL_ID, {SUBMIT_BUTTON_ID}, limit=3000)
        await self.delete_button_messages_in_channel(VOTE_CHANNEL_ID, {VOTE_UP_BUTTON_ID, VOTE_DOWN_BUTTON_ID}, limit=4000)

        await self.repo.clear_all_open_post_refs(None)
        await self.repo.reset_pending_vote_refs()
        await self.repo.cancel_pending_for_non_open()

        for gid in gids:
            if enabled_map.get(gid, False):
                await self.enforce_enabled_state(gid, allow_ping=False, force_repost=True)
            else:
                await self.remove_active_riddle_posts(gid)

        await self.repost_pending_votes()

    async def cog_load(self):
        self.bot.add_view(SubmitButtonView(self))
        self.bot.add_view(VoteButtons(self))
        if self._auto_task is None or self._auto_task.done():
            self._auto_task = asyncio.create_task(self._auto_worker(), name="riddle_auto_worker")

    def cog_unload(self):
        if self._auto_task and not self._auto_task.done():
            self._auto_task.cancel()

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

    async def _auto_worker(self):
        await self.bot.wait_until_ready()
        if not self._startup_done:
            try:
                await self.startup_rebuild()
            except Exception as e:
                logger.exception("startup_rebuild failed: %s", e)
            self._startup_done = True

        while not self.bot.is_closed():
            try:
                for gid in await self.repo.list_all_guild_ids():
                    await self.repo.ensure_guild_state(gid)
                    await self.normalize_after_structure_change(gid)
                    if await self.repo.is_enabled(gid):
                        await self.enforce_enabled_state(gid, allow_ping=False, force_repost=False)
                    else:
                        await self.remove_active_riddle_posts(gid)
            except Exception as e:
                logger.exception("auto worker error: %s", e)
            await asyncio.sleep(max(60, AUTO_SCAN_SECONDS))

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
            view=panel,
            ephemeral=True,
            wait=True,
        )
        panel.message = msg

    @app_commands.command(name="riddle-champ", description="Show riddle champions leaderboard.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_champ(
        self,
        interaction: Interaction,
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
            entries,
            total_solved,
            name_cache,
            avatar_cache,
            image if is_http_url(image) else DEFAULT_IMAGE_URL,
            interaction.user.id if not visible else None,
        )
        sent = await interaction.followup.send(
            content=mention.mention if (visible and mention) else None,
            embed=view.build_embed(),
            view=view,
            ephemeral=not visible,
            wait=True,
            allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
        )
        view.message = sent

    @app_commands.command(name="champions-import-json", description="Import legacy champions data from JSON.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def champions_import_json(self, interaction: Interaction, mode: Literal["merge", "replace"] = "merge"):
        await interaction.response.send_modal(ChampionsImportModal(self, mode))

    async def cog_app_command_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, MissingRiddleManagerRole):
            await send_access_denied(interaction)
            return
        logger.exception("Riddle command error: %s", error)

# =========================
# riddle.py (Part 3/3)
# =========================
# ---- UI + Views + setup ----
class SubmitSolutionModal(Modal):
    def __init__(self, cog: RiddleCog, riddle_id: int):
        super().__init__(title="Submit your solution")
        self.cog = cog
        self.riddle_id = riddle_id
        self.answer = TextInput(label="Your Answer", style=discord.TextStyle.paragraph, required=True, max_length=4000)
        self.add_item(self.answer)

    async def on_submit(self, interaction: Interaction):
        if interaction.guild is None:
            return
        ok = await safe_defer(interaction, ephemeral=True)
        if not ok:
            return

        riddle = await self.cog.repo.get_open_riddle_by_id(interaction.guild.id, self.riddle_id)
        if not riddle:
            await interaction.followup.send("⚠️ This riddle is no longer active.", ephemeral=True)
            return

        ans = clean_value(str(self.answer.value or ""))
        if not ans:
            await interaction.followup.send("❌ Answer cannot be empty.", ephemeral=True)
            return

        sid = await self.cog.repo.create_submission_pending(
            interaction.guild.id,
            to_int(riddle["id"], 0),
            interaction.user.id,
            ans,
        )
        if not sid:
            await interaction.followup.send("❌ Could not save your submission.", ephemeral=True)
            return

        vote_channel = await self.cog.resolve_channel(VOTE_CHANNEL_ID)
        if vote_channel is None or not hasattr(vote_channel, "send"):
            await self.cog.repo.delete_submission(sid)
            await interaction.followup.send("❌ Vote channel not available.", ephemeral=True)
            return

        embed = discord.Embed(
            title="📜 New Solution Submitted",
            description=clamp_embed_description(riddle.get("text") or "*No riddle text*"),
            color=discord.Color.gold(),
        )
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="🧠 User Answer", value=clamp_embed_value(ans), inline=False)
        embed.add_field(name="✅ Correct Solution", value=clamp_embed_value(riddle.get("solution") or "*Not set*"), inline=False)
        embed.add_field(name="🏆 Award", value=clamp_embed_value(f"{max(0, to_int(riddle.get('xp'), 0))} XP"), inline=False)
        embed.add_field(name="🆔 User ID", value=clamp_embed_value(str(interaction.user.id)), inline=False)
        embed.set_footer(text=footer_text(interaction.guild))

        try:
            vm = await vote_channel.send(embed=embed, view=VoteButtons(self.cog))
            await self.cog.repo.set_submission_vote_message(sid, vm.id)
        except Exception:
            await self.cog.repo.delete_submission(sid)
            await interaction.followup.send("❌ Failed to post submission for voting.", ephemeral=True)
            return

        await interaction.followup.send("✅ Your solution was submitted for review.", ephemeral=True)


class SubmitButton(discord.ui.Button):
    def __init__(self, cog: RiddleCog):
        super().__init__(label="🧠 Submit Solution", style=discord.ButtonStyle.primary, custom_id=SUBMIT_BUTTON_ID)
        self.cog = cog

    async def callback(self, interaction: Interaction):
        if interaction.guild is None:
            return
        r = None
        if interaction.message:
            r = await self.cog.repo.get_open_riddle_by_message(interaction.guild.id, interaction.message.id)
        if not r:
            r = await self.cog.repo.get_open_slot1(interaction.guild.id)
        if not r:
            return
        await interaction.response.send_modal(SubmitSolutionModal(self.cog, to_int(r["id"], 0)))


class _VoteBaseButton(discord.ui.Button):
    def __init__(self, cog: RiddleCog, approve: bool, label: str, style: discord.ButtonStyle, custom_id: str):
        super().__init__(label=label, style=style, custom_id=custom_id)
        self.cog = cog
        self.approve = approve

    async def callback(self, interaction: Interaction):
        if interaction.guild is None:
            return
        if not isinstance(interaction.user, discord.Member) or not member_has_role(interaction.user, RIDDLE_MANAGER_ROLE_ID):
            return
        if interaction.message is None:
            return

        ok = await safe_defer(interaction)
        if not ok:
            return

        if self.approve:
            status, ctx = await self.cog.repo.approve_submission(interaction.message.id, interaction.user.id)
        else:
            status, ctx = await self.cog.repo.reject_submission(interaction.message.id, interaction.user.id)

        if status in {"not_found", "already_done", "riddle_closed"}:
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
            return

        if self.approve and ctx:
            solver_id = to_int(ctx.get("solver_user_id"), 0)
            riddle_id = to_int(ctx.get("riddle_id"), 0)
            submission_id = to_int(ctx.get("submission_id"), 0)
            xp_gain = max(0, to_int(ctx.get("xp_gain"), 0))

            if solver_id > 0 and not await self.cog.user_is_excluded(interaction.guild, solver_id):
                await self.cog.repo.inc_cached_solved_total(interaction.guild.id, 1)
            else:
                await self.cog.rebuild_cached_solved_total_for_guild(interaction.guild.id)

            await self.cog.normalize_after_structure_change(interaction.guild.id)

            if await self.cog.repo.is_enabled(interaction.guild.id):
                await self.cog.enforce_enabled_state(interaction.guild.id, allow_ping=False, force_repost=True)
            else:
                await self.cog.remove_active_riddle_posts(interaction.guild.id)

            if riddle_id > 0:
                await self.cog.cleanup_vote_messages_for_riddle(riddle_id, exclude_submission_id=submission_id)

            if solver_id > 0 and xp_gain > 0:
                await self.cog.post_xp_award_hint(interaction.guild, solver_id, xp_gain)

        await _edit_vote_result_message(interaction.message, ok=self.approve, moderator_mention=interaction.user.mention)


class VoteSuccessButton(_VoteBaseButton):
    def __init__(self, cog: RiddleCog):
        super().__init__(cog, True, "👍 Correct", discord.ButtonStyle.success, VOTE_UP_BUTTON_ID)


class VoteFailButton(_VoteBaseButton):
    def __init__(self, cog: RiddleCog):
        super().__init__(cog, False, "👎 Wrong", discord.ButtonStyle.danger, VOTE_DOWN_BUTTON_ID)


class SubmitButtonView(LoggedPersistentView):
    def __init__(self, cog: RiddleCog):
        super().__init__(timeout=None)
        self.add_item(SubmitButton(cog))


class VoteButtons(LoggedPersistentView):
    def __init__(self, cog: RiddleCog):
        super().__init__(timeout=None)
        self.add_item(VoteSuccessButton(cog))
        self.add_item(VoteFailButton(cog))


class RiddleContentModal(Modal):
    def __init__(self, panel: "RiddleAdminPanelView", slot_no: int, riddle_id: Optional[int], current: Optional[dict], ping_preview: str):
        super().__init__(title=f"Slot {slot_no} Content")
        self.panel = panel
        self.slot_no = slot_no
        self.riddle_id = riddle_id
        cur = current or {}
        self.text = TextInput(label="Riddle Text", style=discord.TextStyle.paragraph, default=cur.get("text") or "", required=True, max_length=4000)
        self.solution = TextInput(label="Solution", style=discord.TextStyle.paragraph, default=cur.get("solution") or "", required=True, max_length=4000)
        self.xp = TextInput(label="XP Reward", default=str(max(0, to_int(cur.get("xp"), 0))), required=True, max_length=10)
        self.pings = TextInput(label="Ping roles (preview)", default=ping_preview, required=False, max_length=500)
        self.add_item(self.text)
        self.add_item(self.solution)
        self.add_item(self.xp)
        self.add_item(self.pings)

    async def on_submit(self, interaction: Interaction):
        if interaction.guild is None:
            return
        ok = await safe_defer(interaction)
        if not ok:
            return

        gid = interaction.guild.id
        xp = max(0, to_int(self.xp.value, 0))

        if self.riddle_id:
            changed = await self.panel.cog.repo.update_open_riddle_content_by_id(
                gid,
                self.riddle_id,
                interaction.user.id,
                str(self.text.value),
                str(self.solution.value),
                xp,
            )
            if not changed:
                self.panel.last_info = "⚠️ Riddle changed while editing."
                await self.panel.safe_edit_panel()
                return
        else:
            rid = await self.panel.cog.repo.upsert_slot_content(
                guild_id=gid,
                user_id=interaction.user.id,
                slot_no=self.slot_no,
                text=str(self.text.value),
                solution=str(self.solution.value),
                xp=xp,
            )
            if not rid:
                self.panel.last_info = "❌ Save failed."
                await self.panel.safe_edit_panel()
                return

        await self.panel.cog.normalize_after_structure_change(gid)
        if await self.panel.cog.repo.is_enabled(gid):
            await self.panel.cog.enforce_enabled_state(gid, allow_ping=False, force_repost=False)
        self.panel.last_info = "✅ Saved."
        await self.panel.safe_edit_panel()


class RiddleImagesModal(Modal):
    def __init__(self, panel: "RiddleAdminPanelView", slot_no: int, riddle_id: int, current: dict):
        super().__init__(title=f"Slot {slot_no} Images")
        self.panel = panel
        self.riddle_id = riddle_id
        self.riddle_image = TextInput(label="Riddle Image URL (blank = clear)", default=current.get("image_url") or "", required=False, max_length=2000)
        self.solution_image = TextInput(label="Solution Image URL (blank = clear)", default=current.get("solution_url") or "", required=False, max_length=2000)
        self.add_item(self.riddle_image)
        self.add_item(self.solution_image)

    async def on_submit(self, interaction: Interaction):
        if interaction.guild is None:
            return
        ok = await safe_defer(interaction)
        if not ok:
            return

        r_img = clean_value(self.riddle_image.value)
        s_img = clean_value(self.solution_image.value)
        if r_img and not is_http_url(r_img):
            self.panel.last_info = "❌ Invalid riddle image URL."
            await self.panel.safe_edit_panel()
            return
        if s_img and not is_http_url(s_img):
            self.panel.last_info = "❌ Invalid solution image URL."
            await self.panel.safe_edit_panel()
            return

        good = await self.panel.cog.repo.set_riddle_images_by_id_open(interaction.guild.id, self.riddle_id, r_img, s_img, interaction.user.id)
        self.panel.last_info = "✅ Images updated." if good else "⚠️ Riddle no longer open."
        if good and await self.panel.cog.repo.is_enabled(interaction.guild.id):
            await self.panel.cog.enforce_enabled_state(interaction.guild.id, allow_ping=False, force_repost=False)
        await self.panel.safe_edit_panel()


class RiddlePingRolesModal(Modal):
    def __init__(self, panel: "RiddleAdminPanelView", slot_no: int, riddle_id: int, current_csv: Optional[str]):
        super().__init__(title=f"Slot {slot_no} Extra Ping Roles")
        self.panel = panel
        self.riddle_id = riddle_id
        current_ids = parse_csv_role_ids(current_csv)[:MAX_EXTRA_PING_ROLES]
        self.roles_input = TextInput(
            label="Mentions / IDs / names (max 3)",
            default=", ".join(str(x) for x in current_ids),
            required=False,
            max_length=500,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.roles_input)

    async def on_submit(self, interaction: Interaction):
        if interaction.guild is None:
            return
        ok = await safe_defer(interaction)
        if not ok:
            return

        ids = parse_role_input(self.roles_input.value or "", interaction.guild, MAX_EXTRA_PING_ROLES)
        csv = ",".join(str(i) for i in ids) if ids else None
        good = await self.panel.cog.repo.set_riddle_mentions_by_id_open(interaction.guild.id, self.riddle_id, csv, interaction.user.id)
        self.panel.last_info = "✅ Ping roles updated." if good else "⚠️ Riddle no longer open."
        if good and await self.panel.cog.repo.is_enabled(interaction.guild.id):
            await self.panel.cog.enforce_enabled_state(interaction.guild.id, allow_ping=False, force_repost=False)
        await self.panel.safe_edit_panel()


class RiddleAdminPanelView(View):
    def __init__(self, cog: RiddleCog, owner_id: int, guild_id: int):
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.selected_slot = 1
        self.slot_map: dict[int, dict] = {}
        self.state: dict = {}
        self.last_info = "Ready."
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: Interaction) -> bool:
        return interaction.user.id == self.owner_id

    async def refresh_data(self):
        self.slot_map = await self.cog.repo.open_slot_map(self.guild_id)
        self.state = await self.cog.repo.get_state_row(self.guild_id)

    async def rebuild_items(self):
        self.clear_items()
        enabled = bool(to_int(self.state.get("is_enabled"), 0))
        self.add_item(SlotSelectPanel(self))
        self.add_item(PanelButton(self, "✏️ Edit Content", "edit_content", 1))
        self.add_item(PanelButton(self, "🖼️ Edit Images", "edit_images", 1))
        self.add_item(PanelButton(self, "🎯 Edit Ping Roles", "edit_mentions", 1))
        self.add_item(PanelButton(self, "↘ Move to End", "move_to_end", 1, style=discord.ButtonStyle.secondary))
        self.add_item(PanelButton(self, "🗑️ Delete Slot", "delete_slot", 2, style=discord.ButtonStyle.danger))
        self.add_item(PanelButton(self, "🔴 Turn OFF" if enabled else "🟢 Turn ON", "toggle", 2, style=discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success))
        self.add_item(PanelButton(self, "📢 Post Now", "post_now", 3))
        self.add_item(PanelButton(self, "🔒 Close Active", "close_active", 3, style=discord.ButtonStyle.danger))
        self.add_item(PanelButton(self, "🔄 Refresh", "refresh", 3, style=discord.ButtonStyle.secondary))

    async def build_embeds(self, guild: Optional[discord.Guild]) -> list[discord.Embed]:
        enabled = bool(to_int(self.state.get("is_enabled"), 0))
        solved_cached = await self.cog.repo.get_cached_solved_total(self.guild_id)

        main = discord.Embed(
            title="🗂️ Riddle Control Center",
            description="10 slots enabled • Base ping + up to 3 extra roles",
            color=discord.Color.green() if enabled else discord.Color.orange(),
        )
        main.add_field(name="System", value="🟢 ON" if enabled else "🟠 OFF", inline=True)
        main.add_field(name="Selected Slot", value=str(self.selected_slot), inline=True)
        main.add_field(name="Solved (cached, filtered)", value=str(solved_cached), inline=True)

        for slot in range(1, MAX_RIDDLE_SLOTS + 1):
            row = self.slot_map.get(slot)
            if not row:
                main.add_field(name=f"Slot {slot}", value="`EMPTY`", inline=False)
                continue
            shown_no = solved_cached + slot
            extras = parse_csv_role_ids(row.get("mention_role_ids"))
            txt = truncate_text(row.get("text") or "", 90)
            main.add_field(
                name=f"Slot {slot} • Riddle No.{shown_no}",
                value=f"XP: {to_int(row.get('xp'), 0)}\nExtra roles: {len(extras)}\n{txt}",
                inline=False,
            )

        main.add_field(name="Status", value=clamp_embed_value(self.last_info), inline=False)

        row = self.slot_map.get(self.selected_slot)
        if not row:
            return [main]

        preview = discord.Embed(title=f"Slot {self.selected_slot} Preview", color=discord.Color.blurple())
        extra_ids = parse_csv_role_ids(row.get("mention_role_ids"))[:MAX_EXTRA_PING_ROLES]
        extra_mentions = ", ".join([f"<@&{rid}>" for rid in extra_ids]) if extra_ids else "-"
        preview.add_field(name="Ping Roles", value=f"Base: <@&{RIDDLE_ROLE_ID}>\nExtra: {extra_mentions}", inline=False)

        r_url = clean_value(row.get("image_url"))
        s_url = clean_value(row.get("solution_url"))
        preview.add_field(name="Riddle Image URL", value=clamp_embed_value(r_url or "not set"), inline=False)
        preview.add_field(name="Solution Image URL", value=clamp_embed_value(s_url or "not set"), inline=False)

        return [main, preview]

    async def safe_edit_panel(self):
        if not self.message:
            return
        await self.refresh_data()
        await self.rebuild_items()
        try:
            await self.message.edit(embeds=await self.build_embeds(self.message.guild), view=self)
        except Exception:
            pass

    async def handle_action(self, interaction: Interaction, action: str):
        if interaction.guild is None:
            return

        row = self.slot_map.get(self.selected_slot)

        if action == "edit_content":
            ping_preview = f"Base: <@&{RIDDLE_ROLE_ID}>"
            if row:
                extras = parse_csv_role_ids(row.get("mention_role_ids"))[:MAX_EXTRA_PING_ROLES]
                ping_preview += f"; Extra: {', '.join([f'<@&{x}>' for x in extras]) if extras else '-'}"
            else:
                ping_preview += "; Extra: -"
            rid = to_int(row["id"], 0) if row else None
            await interaction.response.send_modal(RiddleContentModal(self, self.selected_slot, rid, row, ping_preview))
            return

        if action == "edit_images":
            if not row:
                ok = await safe_defer(interaction)
                if not ok:
                    return
                self.last_info = "⚠️ Slot empty."
                await self.safe_edit_panel()
                return
            await interaction.response.send_modal(RiddleImagesModal(self, self.selected_slot, to_int(row["id"], 0), row))
            return

        if action == "edit_mentions":
            if not row:
                ok = await safe_defer(interaction)
                if not ok:
                    return
                self.last_info = "⚠️ Slot empty."
                await self.safe_edit_panel()
                return
            await interaction.response.send_modal(
                RiddlePingRolesModal(self, self.selected_slot, to_int(row["id"], 0), row.get("mention_role_ids"))
            )
            return

        ok = await safe_defer(interaction)
        if not ok:
            return
        gid = interaction.guild.id

        if action == "refresh":
            await self.cog.normalize_after_structure_change(gid)
            if await self.cog.repo.is_enabled(gid):
                await self.cog.enforce_enabled_state(gid, allow_ping=False, force_repost=False)
            self.last_info = "✅ Refreshed + gaps compacted."
            await self.safe_edit_panel()
            return

        if action == "move_to_end":
            if not row:
                self.last_info = "⚠️ Slot empty."
                await self.safe_edit_panel()
                return
            moved = await self.cog.repo.move_open_riddle_to_end(gid, to_int(row["id"], 0))
            await self.cog.normalize_after_structure_change(gid)
            if await self.cog.repo.is_enabled(gid):
                await self.cog.enforce_enabled_state(gid, allow_ping=False, force_repost=True)
            self.last_info = "✅ Moved to end." if moved else "⚠️ Move failed."
            await self.safe_edit_panel()
            return

        if action == "delete_slot":
            if not row:
                self.last_info = "⚠️ Slot already empty."
            else:
                closed = await self.cog.repo.close_open_riddle_by_id(gid, to_int(row["id"], 0), interaction.user.id)
                self.last_info = "✅ Deleted." if closed else "⚠️ Already closed."
            await self.cog.normalize_after_structure_change(gid)
            if await self.cog.repo.is_enabled(gid):
                await self.cog.enforce_enabled_state(gid, allow_ping=False, force_repost=True)
            else:
                await self.cog.remove_active_riddle_posts(gid)
            await self.safe_edit_panel()
            return

        if action == "toggle":
            if await self.cog.repo.is_enabled(gid):
                await self.cog.repo.set_enabled(gid, False)
                await self.cog.remove_active_riddle_posts(gid)
                self.last_info = "✅ OFF"
            else:
                s1 = await self.cog.repo.get_open_slot1(gid)
                if not s1:
                    self.last_info = "⚠️ Slot 1 empty."
                else:
                    await self.cog.repo.set_enabled(gid, True)
                    res = await self.cog.publish_slot1_post(gid, force_repost=True, allow_role_ping=True)
                    self.last_info = f"✅ ON ({res})"
            await self.safe_edit_panel()
            return

        if action == "post_now":
            s1 = await self.cog.repo.get_open_slot1(gid)
            if not s1:
                self.last_info = "⚠️ Slot 1 empty."
            else:
                await self.cog.repo.set_enabled(gid, True)
                res = await self.cog.publish_slot1_post(gid, force_repost=True, allow_role_ping=True)
                self.last_info = f"✅ Post now: {res}"
            await self.safe_edit_panel()
            return

        if action == "close_active":
            r = await self.cog.repo.close_slot1_unsolved(gid, interaction.user.id)
            if not r:
                self.last_info = "⚠️ No active slot 1."
            else:
                await self.cog.normalize_after_structure_change(gid)
                await self.cog.repo.set_enabled(gid, False)
                await self.cog.remove_active_riddle_posts(gid)
                self.last_info = "✅ Active riddle closed."
            await self.safe_edit_panel()
            return


class SlotSelectPanel(Select):
    def __init__(self, panel: RiddleAdminPanelView):
        self.panel = panel
        opts = []
        for slot in range(1, MAX_RIDDLE_SLOTS + 1):
            row = panel.slot_map.get(slot)
            desc = "EMPTY" if not row else truncate_text(row.get("text") or "configured", 60)
            opts.append(
                discord.SelectOption(
                    label=f"Slot {slot}",
                    value=str(slot),
                    description=desc[:100],
                    default=(slot == panel.selected_slot),
                )
            )
        super().__init__(placeholder="Select slot", min_values=1, max_values=1, options=opts, row=0)

    async def callback(self, interaction: Interaction):
        self.panel.selected_slot = max(1, min(MAX_RIDDLE_SLOTS, to_int(self.values[0], 1)))
        ok = await safe_defer(interaction)
        if not ok:
            return
        self.panel.last_info = f"Selected slot {self.panel.selected_slot}."
        await self.panel.safe_edit_panel()


class PanelButton(discord.ui.Button):
    def __init__(self, panel: RiddleAdminPanelView, label: str, action: str, row: int, style: discord.ButtonStyle = discord.ButtonStyle.primary):
        super().__init__(label=label, style=style, row=row)
        self.panel = panel
        self.action = action

    async def callback(self, interaction: Interaction):
        try:
            await self.panel.handle_action(interaction, self.action)
        except discord.NotFound:
            return
        except Exception:
            logger.exception("Panel button callback failed")


class ChampionsImportModal(Modal):
    def __init__(self, cog: RiddleCog, mode: Literal["merge", "replace"]):
        super().__init__(title=f"Import Champions JSON ({mode})")
        self.cog = cog
        self.mode = mode
        self.payload = TextInput(label="Paste JSON", style=discord.TextStyle.paragraph, required=True, max_length=4000)
        self.add_item(self.payload)

    def parse_rows(self, raw: str) -> list[tuple[int, int, int]]:
        obj = json.loads(raw)
        rows: dict[int, tuple[int, int, int]] = {}

        def put(uid: int, solved: Any, xp: Any):
            if uid <= 0:
                return
            rows[uid] = (uid, max(0, to_int(solved, 0)), max(0, to_int(xp, 0)))

        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    uid = to_int(item.get("user_id") or item.get("id"), 0)
                    put(uid, item.get("solved") or item.get("solved_riddles"), item.get("xp"))
        elif isinstance(obj, dict):
            if "users" in obj and isinstance(obj["users"], list):
                for item in obj["users"]:
                    if isinstance(item, dict):
                        uid = to_int(item.get("user_id") or item.get("id"), 0)
                        put(uid, item.get("solved") or item.get("solved_riddles"), item.get("xp"))
            else:
                for k, v in obj.items():
                    uid = to_int(k, 0)
                    if isinstance(v, dict):
                        put(uid, v.get("solved") or v.get("solved_riddles"), v.get("xp"))
                    elif isinstance(v, (list, tuple)) and len(v) >= 2:
                        put(uid, v[0], v[1])

        return list(rows.values())

    async def on_submit(self, interaction: Interaction):
        if interaction.guild is None:
            return
        ok = await safe_defer(interaction, ephemeral=True)
        if not ok:
            return

        try:
            incoming = self.parse_rows(self.payload.value or "")
        except Exception as e:
            await interaction.followup.send(f"❌ JSON parse error: {e}", ephemeral=True)
            return

        filtered = []
        for uid, solved, xp in incoming:
            if await self.cog.user_is_excluded(interaction.guild, uid):
                continue
            filtered.append((uid, solved, xp))

        if self.mode == "replace":
            await self.cog.repo.replace_user_stats(interaction.guild.id, filtered)
        else:
            current = {uid: (uid, solved, xp) for uid, solved, xp in await self.cog.repo.stats_entries(interaction.guild.id)}
            for uid, solved, xp in filtered:
                if uid in current:
                    _, s0, x0 = current[uid]
                    current[uid] = (uid, s0 + solved, x0 + xp)
                else:
                    current[uid] = (uid, solved, xp)
            await self.cog.repo.replace_user_stats(interaction.guild.id, list(current.values()))

        await self.cog.rebuild_cached_solved_total_for_guild(interaction.guild.id)
        await self.cog.sync_open_slot_numbers_for_guild(interaction.guild.id)
        await interaction.followup.send(f"✅ Import done ({len(filtered)} rows used).", ephemeral=True)


class ChampionsView(View):
    def __init__(
        self,
        entries: list[tuple[int, int, float, int]],
        total_solved: int,
        name_cache: dict[int, str],
        avatar_cache: dict[int, str],
        image_url: Optional[str],
        owner_id: Optional[int],
    ):
        super().__init__(timeout=300)
        self.entries = entries
        self.total_solved = total_solved
        self.name_cache = name_cache
        self.avatar_cache = avatar_cache
        self.page = 0
        self.per_page = 6
        self.max_page = max((len(entries) - 1) // self.per_page, 0)
        self.page1_image_url = image_url if is_http_url(image_url) else DEFAULT_IMAGE_URL
        self.default_image_url = DEFAULT_IMAGE_URL
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None
        self._sync()

    def _sync(self):
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.max_page

    def _name(self, uid: int) -> str:
        return self.name_cache.get(uid, f"Unknown User ({uid})")

    def build_embed(self) -> discord.Embed:
        start = self.page * self.per_page
        rows = self.entries[start:start + self.per_page]
        e = discord.Embed(
            title=f"🏆 Riddle Champions • Total solved: {self.total_solved}",
            description=f"Page {self.page + 1}/{self.max_page + 1}",
            color=discord.Color.gold(),
        )
        if rows:
            for i, (uid, solved, percent, xp) in enumerate(rows, start=start + 1):
                e.add_field(
                    name=f"{i}. {self._name(uid)}",
                    value=f"🧩 {solved} | 📊 {percent:.1f}% | 🧠 {xp} XP",
                    inline=False,
                )
        else:
            e.add_field(name="No data", value="No entries.", inline=False)
        img = self.page1_image_url if self.page == 0 else self.default_image_url
        if is_http_url(img):
            e.set_image(url=img)
        return e

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            return False
        return True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: Interaction, _: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        self._sync()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: Interaction, _: discord.ui.Button):
        if self.page < self.max_page:
            self.page += 1
        self._sync()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


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
    