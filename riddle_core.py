# riddle_core.py  (Teil 1/3)
from __future__ import annotations

import os
import re
import asyncio
import datetime as dt
import logging
from pathlib import Path
from typing import Any, Optional

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
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


DB_PATH = _env_str("RIDDLE_DB_PATH", "data/riddle.sqlite3")

RIDDLE_CHANNEL_ID = _env_int("RIDDLE_CHANNEL_ID", 0)
VOTE_CHANNEL_ID = _env_int("RIDDLE_VOTE_CHANNEL_ID", 0)
XP_NOTIFY_CHANNEL_ID = _env_int("RIDDLE_XP_NOTIFY_CHANNEL_ID", 0)

RIDDLE_ROLE_ID = _env_int("RIDDLE_ROLE_ID", 0)
RIDDLE_MANAGER_ROLE_ID = _env_int("RIDDLE_MANAGER_ROLE_ID", 0)
EXCLUDED_COUNT_ROLE_ID = _env_int("RIDDLE_EXCLUDED_COUNT_ROLE_ID", 0)
EXCLUDED_GAMEMASTER_ROLE_ID = _env_int("RIDDLE_EXCLUDED_GAMEMASTER_ROLE_ID", 0)
EXTRA_EXCLUDED_ROLE_IDS_CSV = _env_str("RIDDLE_EXTRA_EXCLUDED_ROLE_IDS", "")

DEFAULT_IMAGE_URL = _env_str("DEFAULT_RIDDLE_IMAGE_URL", "")
ACCESS_DENIED_IMAGE_URL = _env_str("RIDDLE_ACCESS_DENIED_IMAGE_URL", "")

MAX_RIDDLE_SLOTS = _env_int("RIDDLE_MAX_SLOTS", 10)
MAX_EXTRA_PING_ROLES = _env_int("RIDDLE_MAX_EXTRA_PING_ROLES", 3)
AUTO_SCAN_SECONDS = _env_int("RIDDLE_AUTO_SCAN_SECONDS", 43200)

SUBMIT_BUTTON_ID = "riddle_submit_solution_v2"
VOTE_UP_BUTTON_ID = "riddle_vote_up_v2"
VOTE_DOWN_BUTTON_ID = "riddle_vote_down_v2"

ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
ID_RE = re.compile(r"\b(\d{17,22})\b")
URL_RE = re.compile(r"(https?://\S+)")


# =============================================================================
# UTILS
# =============================================================================
def now_iso_utc() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def now_date_str() -> str:
    return dt.datetime.now().strftime("%Y/%m/%d")


def footer_text(guild: Optional[discord.Guild]) -> str:
    return f"{guild.name if guild else 'Unknown Guild'} • {now_date_str()}"


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
        return text[:max_len] + "…"
    return text or ""


def clamp_embed_value(text: Optional[str], limit: int = 1024) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def clamp_embed_description(text: Optional[str], limit: int = 4096) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def extract_first_url(text: str) -> tuple[str, Optional[str]]:
    """Return (text_without_url, first_url_or_None)."""
    text = text or ""
    m = URL_RE.search(text)
    if not m:
        return text.strip(), None
    link = m.group(1)
    cleaned = URL_RE.sub("", text, count=1).strip()
    return cleaned, link


def parse_csv_role_ids(s: Optional[str]) -> list[int]:
    if not s:
        return []
    out: list[int] = []
    seen: set[int] = set()
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
        tl = token.lower()
        exact = [r for r in guild.roles if not r.is_default() and r.name.lower() == tl]
        if exact:
            candidates.append(exact[0].id)
            continue
        starts = [r for r in guild.roles if not r.is_default() and r.name.lower().startswith(tl)]
        if starts:
            candidates.append(starts[0].id)
            continue
        contains = [r for r in guild.roles if not r.is_default() and tl in r.name.lower()]
        if contains:
            candidates.append(contains[0].id)

    out: list[int] = []
    seen: set[int] = set()
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


def build_xpadd_commands(member_mention: str, member_name: str, xp_amount: int) -> tuple[str, str]:
    xp = max(0, to_int(xp_amount, 0))
    safe_name = (member_name or "UnknownUser").replace('"', "").strip() or "UnknownUser"
    return f'/xpadd "{safe_name}" {xp}', f"/xpadd {member_mention} {xp}"


async def safe_defer(interaction: Interaction, *, ephemeral: bool = False, thinking: bool = False) -> bool:
    if interaction.response.is_done():
        return True
    try:
        await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
        return True
    except (discord.NotFound, discord.HTTPException):
        return False


def member_has_role(member: discord.abc.User, role_id: int) -> bool:
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

# =============================================================================
# DB REPO
# =============================================================================
class RiddleRepo:
    def __init__(self):
        self.db: Optional[aiosqlite.Connection] = None
        self.lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------
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
            solved_post_channel_id INTEGER,
            solved_post_message_id INTEGER,
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

        CREATE TABLE IF NOT EXISTS riddle_wrong_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            riddle_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_wrong_riddle ON riddle_wrong_posts(riddle_id);
        CREATE INDEX IF NOT EXISTS idx_wrong_guild ON riddle_wrong_posts(guild_id);
        """
        async with self.lock:
            await self.db.executescript(schema)
            # migration from older schema (in case an old DB exists)
            await self._add_col_if_missing("riddles", "solved_post_channel_id", "solved_post_channel_id INTEGER")
            await self._add_col_if_missing("riddles", "solved_post_message_id", "solved_post_message_id INTEGER")
            await self.db.commit()

    # -- low level ---------------------------------------------------------
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

    # -- guild state / cache ----------------------------------------------
    async def ensure_guild_state(self, guild_id: int):
        await self._exec(
            "INSERT INTO guild_riddle_state (guild_id, is_enabled, updated_at) VALUES (?, 0, ?) "
            "ON CONFLICT(guild_id) DO NOTHING",
            (guild_id, now_iso_utc()),
        )
        await self._exec(
            "INSERT INTO guild_stats_cache (guild_id, solved_total_filtered, updated_at) VALUES (?, 0, ?) "
            "ON CONFLICT(guild_id) DO NOTHING",
            (guild_id, now_iso_utc()),
        )

    async def set_enabled(self, guild_id: int, enabled: bool):
        await self._exec(
            "INSERT INTO guild_riddle_state (guild_id, is_enabled, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET is_enabled=excluded.is_enabled, updated_at=excluded.updated_at",
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
            UNION SELECT guild_id FROM guild_stats_cache
            UNION SELECT DISTINCT guild_id FROM riddles
            UNION SELECT DISTINCT guild_id FROM submissions
            UNION SELECT DISTINCT guild_id FROM user_stats
            """
        )
        return [to_int(r.get("guild_id"), 0) for r in rows if to_int(r.get("guild_id"), 0) > 0]

    async def get_cached_solved_total(self, guild_id: int) -> int:
        row = await self._one(
            "SELECT solved_total_filtered FROM guild_stats_cache WHERE guild_id=? LIMIT 1", (guild_id,)
        )
        return max(0, to_int(row.get("solved_total_filtered"), 0)) if row else 0

    async def set_cached_solved_total(self, guild_id: int, value: int):
        v = max(0, to_int(value, 0))
        await self._exec(
            "INSERT INTO guild_stats_cache (guild_id, solved_total_filtered, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET solved_total_filtered=excluded.solved_total_filtered, "
            "updated_at=excluded.updated_at",
            (guild_id, v, now_iso_utc()),
        )

    async def inc_cached_solved_total(self, guild_id: int, delta: int = 1):
        d = max(0, to_int(delta, 0))
        await self._exec(
            "INSERT INTO guild_stats_cache (guild_id, solved_total_filtered, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET "
            "solved_total_filtered = solved_total_filtered + ?, updated_at = excluded.updated_at",
            (guild_id, d, now_iso_utc(), d),
        )

    # -- slot queries ------------------------------------------------------
    async def open_slot_map(self, guild_id: int) -> dict[int, dict]:
        rows = await self._all(
            "SELECT * FROM riddles WHERE guild_id=? AND status='open' AND slot_no BETWEEN 1 AND ? "
            "ORDER BY slot_no ASC",
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

    async def get_riddle_by_id(self, guild_id: int, riddle_id: int) -> Optional[dict]:
        return await self._one("SELECT * FROM riddles WHERE guild_id=? AND id=? LIMIT 1", (guild_id, riddle_id))

    async def get_open_riddle_by_message(self, guild_id: int, message_id: int) -> Optional[dict]:
        return await self._one(
            "SELECT * FROM riddles WHERE guild_id=? AND posted_message_id=? AND status='open' LIMIT 1",
            (guild_id, message_id),
        )

    # -- content mutations -------------------------------------------------
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
                    rid = to_int(row["id"], 0)
                    await self.db.execute(
                        "UPDATE riddles SET text=?, solution=?, xp=?, created_by=?, updated_at=? WHERE id=?",
                        (text, solution, xp, user_id, now, rid),
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
            "UPDATE riddles SET text=?, solution=?, xp=?, created_by=?, updated_at=? "
            "WHERE guild_id=? AND id=? AND status='open'",
            (text, solution, xp, user_id, now_iso_utc(), guild_id, riddle_id),
        )
        return rc > 0

    async def set_riddle_images_by_id_open(
        self, guild_id: int, riddle_id: int, riddle_image_url: Optional[str],
        solution_image_url: Optional[str], user_id: int,
    ) -> bool:
        rc, _ = await self._exec(
            "UPDATE riddles SET image_url=?, solution_url=?, created_by=?, updated_at=? "
            "WHERE guild_id=? AND id=? AND status='open'",
            (clean_value(riddle_image_url), clean_value(solution_image_url),
             user_id, now_iso_utc(), guild_id, riddle_id),
        )
        return rc > 0

    async def set_riddle_mentions_by_id_open(
        self, guild_id: int, riddle_id: int, mention_role_ids_csv: Optional[str], user_id: int
    ) -> bool:
        rc, _ = await self._exec(
            "UPDATE riddles SET mention_role_ids=?, created_by=?, updated_at=? "
            "WHERE guild_id=? AND id=? AND status='open'",
            (clean_value(mention_role_ids_csv), user_id, now_iso_utc(), guild_id, riddle_id),
        )
        return rc > 0

# -- slot geometry -----------------------------------------------------
    async def sync_open_slot_numbers(self, guild_id: int, solved_base: int):
        """Two-phase renumber to avoid transient unique collisions."""
        if self.db is None:
            return
        base = max(0, to_int(solved_base, 0))
        now = now_iso_utc()
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                await self.db.execute(
                    "UPDATE riddles SET riddle_no = -(id + 1000000000), updated_at=? "
                    "WHERE guild_id=? AND status='open' AND slot_no BETWEEN 1 AND ?",
                    (now, guild_id, MAX_RIDDLE_SLOTS),
                )
                await self.db.execute(
                    "UPDATE riddles SET riddle_no=(? + slot_no), updated_at=? "
                    "WHERE guild_id=? AND status='open' AND slot_no BETWEEN 1 AND ?",
                    (base, now, guild_id, MAX_RIDDLE_SLOTS),
                )
                await self.db.commit()
            except Exception:
                await self.db.rollback()
                raise

    async def compact_open_slots(self, guild_id: int):
        if self.db is None:
            return
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                now = now_iso_utc()
                cur = await self.db.execute(
                    "SELECT id FROM riddles WHERE guild_id=? AND status='open' "
                    "ORDER BY CASE WHEN slot_no BETWEEN 1 AND ? THEN slot_no ELSE 9999 END, id",
                    (guild_id, MAX_RIDDLE_SLOTS),
                )
                rows = await cur.fetchall()
                await cur.close()

                await self.db.execute(
                    "UPDATE riddles SET slot_no=NULL, is_active=0, updated_at=? "
                    "WHERE guild_id=? AND status='open'",
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
                            "UPDATE submissions SET status='cancelled', voted_by=0, voted_at=? "
                            "WHERE riddle_id=? AND status='pending'",
                            (now, rid),
                        )
                await self.db.commit()
            except Exception:
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
                if len(ids) <= 1:
                    await self.db.rollback()
                    return True

                ids.remove(riddle_id)
                ids.append(riddle_id)

                now = now_iso_utc()
                await self.db.execute(
                    "UPDATE riddles SET slot_no=NULL, is_active=0, updated_at=? "
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
                    "UPDATE riddles SET status='closed', slot_no=NULL, is_active=0, "
                    "closed_by=?, closed_at=?, updated_at=? WHERE id=? AND status='open'",
                    (closed_by, now, now, riddle_id),
                )
                await self.db.execute(
                    "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? "
                    "WHERE riddle_id=? AND status='pending'",
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

    # -- post refs ---------------------------------------------------------
    async def set_riddle_post_ref(self, riddle_id: int, channel_id: int, message_id: int):
        await self._exec(
            "UPDATE riddles SET posted_channel_id=?, posted_message_id=?, updated_at=? WHERE id=?",
            (channel_id, message_id, now_iso_utc(), riddle_id),
        )

    async def set_solved_post_ref(self, riddle_id: int, channel_id: int, message_id: int):
        await self._exec(
            "UPDATE riddles SET solved_post_channel_id=?, solved_post_message_id=?, updated_at=? WHERE id=?",
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
                "UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL, updated_at=? "
                "WHERE guild_id=? AND status='open'",
                (now_iso_utc(), guild_id),
            )

    async def clear_other_open_post_refs(self, guild_id: int, keep_riddle_id: int):
        await self._exec(
            "UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL, updated_at=? "
            "WHERE guild_id=? AND status='open' AND id<>?",
            (now_iso_utc(), guild_id, keep_riddle_id),
        )

    async def list_open_post_refs(self, guild_id: int) -> list[dict]:
        return await self._all(
            "SELECT id, posted_channel_id, posted_message_id FROM riddles "
            "WHERE guild_id=? AND status='open' AND posted_message_id IS NOT NULL",
            (guild_id,),
        )

    # -- wrong posts (public channel messages) -----------------------------
    async def add_wrong_post(self, guild_id: int, riddle_id: int,
                             channel_id: int, message_id: int):
        await self._exec(
            "INSERT INTO riddle_wrong_posts (guild_id, riddle_id, channel_id, message_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, riddle_id, channel_id, message_id, now_iso_utc()),
        )

    async def list_wrong_posts_for_riddle(self, riddle_id: int) -> list[dict]:
        return await self._all(
            "SELECT channel_id, message_id FROM riddle_wrong_posts WHERE riddle_id=?",
            (riddle_id,),
        )

    async def clear_wrong_posts_for_riddle(self, riddle_id: int):
        await self._exec(
            "DELETE FROM riddle_wrong_posts WHERE riddle_id=?",
            (riddle_id,),
        )

    # -- submissions -------------------------------------------------------
    async def create_submission_pending(self, guild_id: int, riddle_id: int, user_id: int, answer: str) -> Optional[int]:
        _, lid = await self._exec(
            "INSERT INTO submissions (guild_id, riddle_id, user_id, answer, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (guild_id, riddle_id, user_id, answer, now_iso_utc()),
        )
        return lid if lid > 0 else None

    async def set_submission_vote_message(self, submission_id: int, vote_message_id: int) -> bool:
        rc, _ = await self._exec(
            "UPDATE submissions SET vote_message_id=? WHERE id=?", (vote_message_id, submission_id)
        )
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
            "UPDATE submissions SET status='cancelled', voted_by=0, voted_at=? "
            "WHERE status='pending' AND riddle_id IN (SELECT id FROM riddles WHERE status<>'open')",
            (now_iso_utc(),),
        )

    async def pending_open_submissions(self) -> list[dict]:
        return await self._all(
            """
            SELECT s.id AS submission_id, s.guild_id AS guild_id, s.user_id AS user_id,
                   s.answer AS answer, s.vote_message_id AS vote_message_id,
                   r.id AS riddle_id, r.text AS riddle_text, r.solution AS solution,
                   r.xp AS xp, r.mention_role_ids AS mention_role_ids
            FROM submissions s JOIN riddles r ON r.id = s.riddle_id
            WHERE s.status='pending' AND r.status='open'
            ORDER BY s.id ASC
            """
        )

    # -- stats -------------------------------------------------------------
    async def stats_entries(self, guild_id: int) -> list[tuple[int, int, int]]:
        rows = await self._all(
            "SELECT user_id, solved_riddles, xp FROM user_stats WHERE guild_id=? "
            "ORDER BY solved_riddles DESC, xp DESC",
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

    async def apply_solve_xp(self, guild_id: int, user_id: int, xp_gain: int):
        """Called AFTER approve_submission if solver is not excluded."""
        xp_gain = max(0, to_int(xp_gain, 0))
        await self._exec(
            """
            INSERT INTO user_stats (guild_id, user_id, solved_riddles, xp)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(guild_id, user_id)
            DO UPDATE SET solved_riddles = solved_riddles + 1, xp = xp + excluded.xp
            """,
            (guild_id, user_id, xp_gain),
        )

    # -- voting transactions ----------------------------------------------
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
                    SELECT s.id AS submission_id, s.guild_id AS guild_id, s.riddle_id AS riddle_id,
                           s.user_id AS user_id, s.answer AS answer, s.status AS submission_status,
                           r.text AS riddle_text, r.solution AS solution, r.xp AS xp,
                           r.status AS riddle_status, r.mention_role_ids AS mention_role_ids,
                           r.image_url AS image_url, r.solution_url AS solution_url,
                           r.riddle_no AS riddle_no,
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

                # cancel any other pending submissions for this riddle
                await self.db.execute(
                    "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? "
                    "WHERE riddle_id=? AND status='pending' AND id<>?",
                    (moderator_id, now, rid, sid),
                )

                # mark riddle solved
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
                    "image_url": data.get("image_url"),
                    "solution_url": data.get("solution_url"),
                    "riddle_no": to_int(data.get("riddle_no"), 0),
                    "posted_channel_id": safe_int(data.get("posted_channel_id"), None),
                    "posted_message_id": safe_int(data.get("posted_message_id"), None),
                    "solved_at": now,
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
                    SELECT s.id AS submission_id, s.guild_id AS guild_id, s.riddle_id AS riddle_id,
                           s.user_id AS user_id, s.answer AS answer, s.status AS submission_status,
                           r.text AS riddle_text, r.xp AS xp, r.riddle_no AS riddle_no,
                           r.image_url AS image_url, r.status AS riddle_status,
                           r.mention_role_ids AS mention_role_ids
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
                sub_status = str(data.get("submission_status") or "")
                rid_status = str(data.get("riddle_status") or "")

                if sub_status != "pending":
                    await self.db.rollback()
                    return "already_done", data

                now = now_iso_utc()
                sid = to_int(data.get("submission_id"), 0)

                if rid_status != "open":
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
                    "riddle_text": data.get("riddle_text"),
                    "riddle_no": to_int(data.get("riddle_no"), 0),
                    "xp": max(0, to_int(data.get("xp"), 0)),
                    "image_url": data.get("image_url"),
                    "mention_role_ids": data.get("mention_role_ids"),
                }
                return "rejected", ctx
            except Exception:
                await self.db.rollback()
                raise