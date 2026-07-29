# =========================
# riddle.py (Teil 1/3)
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
from discord.ui import View, Modal, TextInput, Select, RoleSelect
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

RIDDLE_CHANNEL_ID = env_int("RIDDLE_CHANNEL_ID", 1349697597232906292)
VOTE_CHANNEL_ID = env_int("VOTE_CHANNEL_ID", 1381754826710585527)

RIDDLE_ROLE_ID = env_int("RIDDLE_ROLE_ID", 1380610400416043089)  # Basis-Pingrolle
RIDDLE_MANAGER_ROLE_ID = env_int("RIDDLE_MANAGER_ROLE_ID", 1393762463861702787)
EXCLUDED_COUNT_ROLE_ID = env_int("RIDDLE_EXCLUDED_COUNT_ROLE_ID", 1292194320786522223)

XP_NOTIFY_CHANNEL_ID = env_int("RIDDLE_XP_NOTIFY_CHANNEL_ID", 1381754826710585527)

DEFAULT_IMAGE_URL = (
    os.getenv("DEFAULT_RIDDLE_IMAGE_URL")
    or "https://cdn.discordapp.com/attachments/1383652563408392232/1462480133737943063/riddle_sexy.gif"
).strip()

ACCESS_DENIED_IMAGE_URL = (
    os.getenv("RIDDLE_ACCESS_DENIED_IMAGE_URL")
    or "https://example.com/apply-role-placeholder.jpg"
).strip()

MAX_RIDDLE_SLOTS = env_int("RIDDLE_MAX_SLOTS", 5)
MAX_EXTRA_PING_ROLES = env_int("RIDDLE_MAX_EXTRA_PING_ROLES", 3)
AUTO_SCAN_SECONDS = env_int("RIDDLE_AUTO_ENABLE_SCAN_SECONDS", 43200)

SUBMIT_BUTTON_ID = "riddle_submit_solution"
VOTE_UP_BUTTON_ID = "riddle_vote_up"
VOTE_DOWN_BUTTON_ID = "riddle_vote_down"

ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
USER_MENTION_RE = re.compile(r"<@!?(\d+)>")
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


def parse_role_input(raw: str, guild: discord.Guild, max_roles: int = 3) -> list[int]:
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
        exact = [r for r in guild.roles if (not r.is_default()) and r.name.lower() == token.lower()]
        if exact:
            candidates.append(exact[0].id)
            continue
        starts = [r for r in guild.roles if (not r.is_default()) and r.name.lower().startswith(token.lower())]
        if starts:
            candidates.append(starts[0].id)
            continue
        contains = [r for r in guild.roles if (not r.is_default()) and token.lower() in r.name.lower()]
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
        try:
            if interaction.response.is_done():
                await interaction.followup.send("❌ UI error.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ UI error.", ephemeral=True)
        except Exception:
            pass


class RiddleRepo:
    def __init__(self):
        self.db: Optional[aiosqlite.Connection] = None
        self.lock = asyncio.Lock()

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
            await self._repair_slots_locked()
            await self.db.commit()

    async def _repair_slots_locked(self):
        assert self.db is not None
        now = now_iso_utc()
        cur = await self.db.execute("SELECT DISTINCT guild_id FROM riddles WHERE status='open'")
        gids = [to_int(r["guild_id"], 0) for r in await cur.fetchall()]
        await cur.close()
        gids = [g for g in gids if g > 0]

        for gid in gids:
            cur = await self.db.execute(
                """
                SELECT * FROM riddles
                WHERE guild_id=? AND status='open'
                ORDER BY CASE WHEN slot_no BETWEEN 1 AND ? THEN slot_no ELSE 9999 END, id
                """,
                (gid, MAX_RIDDLE_SLOTS),
            )
            rows = [dict(r) for r in await cur.fetchall()]
            await cur.close()

            await self.db.execute(
                "UPDATE riddles SET is_active=0, updated_at=? WHERE guild_id=? AND status='open'",
                (now, gid),
            )

            used = set()
            overflow = []
            for row in rows:
                rid = to_int(row.get("id"), 0)
                slot = to_int(row.get("slot_no"), 0)
                if 1 <= slot <= MAX_RIDDLE_SLOTS and slot not in used:
                    used.add(slot)
                    continue
                free_slot = next((s for s in range(1, MAX_RIDDLE_SLOTS + 1) if s not in used), None)
                if free_slot is None:
                    overflow.append(rid)
                else:
                    await self.db.execute(
                        "UPDATE riddles SET slot_no=?, updated_at=? WHERE id=?",
                        (free_slot, now, rid),
                    )
                    used.add(free_slot)

            for rid in overflow:
                await self.db.execute(
                    """
                    UPDATE riddles
                    SET status='closed', slot_no=NULL, is_active=0, closed_by=0, closed_at=?, updated_at=?
                    WHERE id=? AND status='open'
                    """,
                    (now, now, rid),
                )
                await self.db.execute(
                    """
                    UPDATE submissions
                    SET status='cancelled', voted_by=0, voted_at=?
                    WHERE riddle_id=? AND status='pending'
                    """,
                    (now, rid),
                )

            await self.db.execute(
                """
                UPDATE riddles
                SET is_active=1
                WHERE id IN (
                    SELECT id FROM riddles
                    WHERE guild_id=? AND status='open' AND slot_no=1
                    LIMIT 1
                )
                """,
                (gid,),
            )

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
            SELECT DISTINCT guild_id FROM riddles WHERE status='open'
            UNION
            SELECT DISTINCT guild_id FROM submissions WHERE status='pending'
            UNION
            SELECT DISTINCT guild_id FROM user_stats
            """
        )
        return [to_int(r.get("guild_id"), 0) for r in rows if to_int(r.get("guild_id"), 0) > 0]

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
        self,
        *,
        guild_id: int,
        user_id: int,
        slot_no: int,
        text: str,
        solution: str,
        xp: int,
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

                await self.db.execute("UPDATE riddles SET is_active=0 WHERE guild_id=? AND status='open'", (guild_id,))
                await self.db.execute(
                    "UPDATE riddles SET is_active=1 WHERE guild_id=? AND status='open' AND slot_no=1",
                    (guild_id,),
                )
                await self.db.commit()
                return rid if rid > 0 else None
            except Exception:
                await self.db.rollback()
                raise

    async def set_slot_images(
        self,
        guild_id: int,
        slot_no: int,
        riddle_image_url: Optional[str],
        solution_image_url: Optional[str],
        user_id: int,
    ) -> bool:
        row = await self.get_open_slot_riddle(guild_id, slot_no)
        if not row:
            return False
        rc, _ = await self._exec(
            """
            UPDATE riddles
            SET image_url=?, solution_url=?, created_by=?, updated_at=?
            WHERE id=?
            """,
            (clean_value(riddle_image_url), clean_value(solution_image_url), user_id, now_iso_utc(), to_int(row["id"], 0)),
        )
        return rc > 0

    async def set_slot_mentions(self, guild_id: int, slot_no: int, mention_role_ids_csv: Optional[str], user_id: int) -> bool:
        row = await self.get_open_slot_riddle(guild_id, slot_no)
        if not row:
            return False
        rc, _ = await self._exec(
            """
            UPDATE riddles
            SET mention_role_ids=?, created_by=?, updated_at=?
            WHERE id=?
            """,
            (clean_value(mention_role_ids_csv), user_id, now_iso_utc(), to_int(row["id"], 0)),
        )
        return rc > 0

# =========================
# riddle.py (Teil 2/3)
# =========================
    async def refresh_active_slot_flag(self, guild_id: int):
        if self.db is None:
            return
        now = now_iso_utc()
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                await self.db.execute(
                    "UPDATE riddles SET is_active=0, updated_at=? WHERE guild_id=? AND status='open'",
                    (now, guild_id),
                )
                await self.db.execute(
                    """
                    UPDATE riddles
                    SET is_active=1, updated_at=?
                    WHERE id IN (
                        SELECT id FROM riddles
                        WHERE guild_id=? AND status='open' AND slot_no=1
                        LIMIT 1
                    )
                    """,
                    (now, guild_id),
                )
                await self.db.commit()
            except Exception:
                await self.db.rollback()
                raise

    async def shift_open_slots_left(self, guild_id: int):
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

    async def delete_slot_riddle(self, guild_id: int, slot_no: int, deleted_by: int) -> tuple[str, Optional[dict]]:
        if self.db is None:
            return "error", None
        if slot_no < 1 or slot_no > MAX_RIDDLE_SLOTS:
            return "invalid_slot", None

        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    "SELECT * FROM riddles WHERE guild_id=? AND status='open' AND slot_no=? LIMIT 1",
                    (guild_id, slot_no),
                )
                row = await cur.fetchone()
                await cur.close()

                if not row:
                    await self.db.rollback()
                    return "not_found", None

                r = dict(row)
                now = now_iso_utc()

                await self.db.execute(
                    """
                    UPDATE riddles
                    SET status='closed', slot_no=NULL, is_active=0, closed_by=?, closed_at=?, updated_at=?
                    WHERE id=? AND status='open'
                    """,
                    (deleted_by, now, now, r["id"]),
                )
                await self.db.execute(
                    """
                    UPDATE submissions
                    SET status='cancelled', voted_by=?, voted_at=?
                    WHERE riddle_id=? AND status='pending'
                    """,
                    (deleted_by, now, r["id"]),
                )
                await self.db.commit()
                return "ok", r
            except Exception:
                await self.db.rollback()
                raise

    async def close_slot1_unsolved(self, guild_id: int, closed_by: int) -> Optional[dict]:
        if self.db is None:
            return None
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    "SELECT * FROM riddles WHERE guild_id=? AND status='open' AND slot_no=1 LIMIT 1",
                    (guild_id,),
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
                    (closed_by, now, now, r["id"]),
                )
                await self.db.execute(
                    """
                    UPDATE submissions
                    SET status='cancelled', voted_by=?, voted_at=?
                    WHERE riddle_id=? AND status='pending'
                    """,
                    (closed_by, now, r["id"]),
                )
                await self.db.commit()
                return r
            except Exception:
                await self.db.rollback()
                raise

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

    async def approve_submission(self, vote_message_id: int, moderator_id: int) -> tuple[str, Optional[dict]]:
        if self.db is None:
            return "error", None
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    """
                    SELECT
                        s.id AS submission_id,
                        s.riddle_id AS riddle_id,
                        s.user_id AS submitter_id,
                        s.answer AS user_answer,
                        s.status AS submission_status,
                        r.guild_id AS guild_id,
                        r.riddle_no AS riddle_no,
                        r.text AS riddle_text,
                        r.solution AS correct_solution,
                        r.xp AS xp,
                        r.image_url AS image_url,
                        r.solution_url AS solution_url,
                        r.mention_role_ids AS mention_role_ids,
                        r.posted_channel_id AS posted_channel_id,
                        r.posted_message_id AS posted_message_id,
                        r.status AS riddle_status
                    FROM submissions s
                    JOIN riddles r ON r.id = s.riddle_id
                    WHERE s.vote_message_id=?
                    LIMIT 1
                    """,
                    (vote_message_id,),
                )
                row = await cur.fetchone()
                await cur.close()
                if not row:
                    await self.db.rollback()
                    return "not_found", None

                ctx = dict(row)
                if ctx["submission_status"] != "pending":
                    await self.db.rollback()
                    return "already_done", ctx

                if ctx["riddle_status"] != "open":
                    await self.db.execute(
                        "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? WHERE id=? AND status='pending'",
                        (moderator_id, now_iso_utc(), ctx["submission_id"]),
                    )
                    await self.db.commit()
                    return "riddle_closed", ctx

                now = now_iso_utc()
                cur = await self.db.execute(
                    "UPDATE submissions SET status='correct', voted_by=?, voted_at=? WHERE id=? AND status='pending'",
                    (moderator_id, now, ctx["submission_id"]),
                )
                if cur.rowcount != 1:
                    await self.db.rollback()
                    return "already_done", ctx

                cur = await self.db.execute(
                    """
                    UPDATE riddles
                    SET status='solved', slot_no=NULL, is_active=0, solved_by=?, solved_at=?, updated_at=?
                    WHERE id=? AND status='open'
                    """,
                    (ctx["submitter_id"], now, now, ctx["riddle_id"]),
                )
                if cur.rowcount != 1:
                    await self.db.rollback()
                    return "riddle_closed", ctx

                await self.db.execute(
                    """
                    UPDATE submissions
                    SET status='cancelled', voted_by=?, voted_at=?
                    WHERE riddle_id=? AND status='pending' AND id<>?
                    """,
                    (moderator_id, now, ctx["riddle_id"], ctx["submission_id"]),
                )
                await self.db.commit()

                ctx["solved_at"] = now
                ctx["xp_gain"] = max(0, to_int(ctx.get("xp"), 0))
                return "ok", ctx
            except Exception:
                await self.db.rollback()
                raise

    async def reject_submission(self, vote_message_id: int, moderator_id: int) -> tuple[str, Optional[dict]]:
        if self.db is None:
            return "error", None
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    """
                    SELECT
                        s.id AS submission_id,
                        s.riddle_id AS riddle_id,
                        s.user_id AS submitter_id,
                        s.answer AS user_answer,
                        s.status AS submission_status,
                        r.guild_id AS guild_id,
                        r.riddle_no AS riddle_no,
                        r.text AS riddle_text,
                        r.image_url AS image_url,
                        r.xp AS xp,
                        r.mention_role_ids AS mention_role_ids,
                        r.status AS riddle_status
                    FROM submissions s
                    JOIN riddles r ON r.id = s.riddle_id
                    WHERE s.vote_message_id=?
                    LIMIT 1
                    """,
                    (vote_message_id,),
                )
                row = await cur.fetchone()
                await cur.close()
                if not row:
                    await self.db.rollback()
                    return "not_found", None

                ctx = dict(row)
                if ctx["submission_status"] != "pending":
                    await self.db.rollback()
                    return "already_done", ctx

                if ctx["riddle_status"] != "open":
                    await self.db.execute(
                        "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? WHERE id=? AND status='pending'",
                        (moderator_id, now_iso_utc(), ctx["submission_id"]),
                    )
                    await self.db.commit()
                    return "riddle_closed", ctx

                cur = await self.db.execute(
                    "UPDATE submissions SET status='wrong', voted_by=?, voted_at=? WHERE id=? AND status='pending'",
                    (moderator_id, now_iso_utc(), ctx["submission_id"]),
                )
                if cur.rowcount != 1:
                    await self.db.rollback()
                    return "already_done", ctx

                await self.db.commit()
                return "ok", ctx
            except Exception:
                await self.db.rollback()
                raise

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

    async def clear_user_stats(self, guild_id: int):
        await self._exec("DELETE FROM user_stats WHERE guild_id=?", (guild_id,))

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

# =========================
# riddle.py (Teil 3/3)
# =========================
class RiddleCog(commands.Cog):
    def __init__(self, bot: commands.Bot, repo: RiddleRepo):
        self.bot = bot
        self.repo = repo
        self._auto_task: Optional[asyncio.Task] = None
        self._startup_done = False

    async def cog_load(self):
        self.bot.add_view(SubmitButtonView(self))
        self.bot.add_view(VoteButtons(self))
        if self._auto_task is None or self._auto_task.done():
            self._auto_task = asyncio.create_task(self._auto_worker(), name="riddle_auto_worker")

    def cog_unload(self):
        if self._auto_task and not self._auto_task.done():
            self._auto_task.cancel()

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
            member = guild.get_member(uid)
            if member is None:
                try:
                    member = await guild.fetch_member(uid)
                except Exception:
                    member = None
            if member:
                return member.mention, str(member), member.display_avatar.url
        user = self.bot.get_user(uid)
        if user is None:
            try:
                user = await self.bot.fetch_user(uid)
            except Exception:
                user = None
        if user:
            return user.mention, str(user), user.display_avatar.url
        return mention, f"User {uid}", None

    async def user_has_excluded_role(self, guild: discord.Guild, user_id: int) -> bool:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                member = None
        return bool(member and member_has_role(member, EXCLUDED_COUNT_ROLE_ID))

    async def filtered_stats_entries_for_guild(self, guild: discord.Guild) -> list[tuple[int, int, int]]:
        raw = await self.repo.stats_entries(guild.id)
        out = []
        for uid, solved, xp in raw:
            if await self.user_has_excluded_role(guild, uid):
                continue
            out.append((uid, solved, xp))
        return out

    def build_ping_content(self, guild: Optional[discord.Guild], mention_role_ids_csv: Optional[str]) -> Optional[str]:
        extra: list[int] = []
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

    async def update_original_post_solved(self, ctx: dict, solver_mention: str, effective_xp: int):
        msg = await self.fetch_message_safe(ctx.get("posted_channel_id"), ctx.get("posted_message_id"))
        if not msg:
            return

        solved_at = ctx.get("solved_at") or now_iso_utc()

        if msg.embeds:
            base = msg.embeds[0].to_dict()
            keep = []
            for f in base.get("fields", []):
                if f.get("name") in {"✅ Status", "Solved at (UTC)", "🏆 Award"}:
                    continue
                keep.append(f)
            base["fields"] = keep
            e1 = discord.Embed.from_dict(base)
        else:
            e1 = discord.Embed(
                title=f"🧩 Goon Hut Riddle No.{to_int(ctx.get('riddle_no'), to_int(ctx.get('riddle_id'), 0))}",
                description=clamp_embed_description(ctx.get("riddle_text") or "*Unknown*"),
                color=discord.Color.green(),
            )

        e1.color = discord.Color.green()
        e1.add_field(name="✅ Status", value=clamp_embed_value(f"Solved by {solver_mention}"), inline=False)
        e1.add_field(name="Solved at (UTC)", value=clamp_embed_value(str(solved_at)), inline=False)
        e1.add_field(name="🏆 Award", value=clamp_embed_value(f"{effective_xp} XP"), inline=False)

        rimg = ctx.get("image_url")
        if is_http_url(rimg):
            e1.set_thumbnail(url=rimg)
        elif is_http_url(DEFAULT_IMAGE_URL):
            e1.set_thumbnail(url=DEFAULT_IMAGE_URL)

        e1.set_footer(text=footer_text(msg.guild))

        clean_solution, more_link = extract_link(ctx.get("correct_solution") or "")
        sol_text = f"||{clean_solution}||" if clean_solution else "||*None*||"
        if more_link:
            sol_text += f"\n🔗 [🧠**MORE**]({more_link})"

        e2 = discord.Embed(
            title="✅ Solution",
            description=clamp_embed_description(sol_text),
            color=discord.Color.green(),
        )
        simg = ctx.get("solution_url")
        if not is_http_url(simg):
            simg = DEFAULT_IMAGE_URL
        if is_http_url(simg):
            e2.set_image(url=simg)
        e2.set_footer(text=footer_text(msg.guild))

        try:
            await msg.edit(embeds=[e1, e2], view=None)  # Submit-Button weg
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
        await self.repo.refresh_active_slot_flag(guild_id)
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

    async def startup_rebuild(self):
        gids = await self.repo.list_all_guild_ids()
        for gid in gids:
            await self.repo.ensure_guild_state(gid)
            await self.remove_active_riddle_posts(gid)
            await self.repo.set_enabled(gid, False)  # Neustart immer OFF
            await self.repo.refresh_active_slot_flag(gid)

        await self.repo.clear_all_open_post_refs(None)
        await self.repo.reset_pending_vote_refs()
        await self.repo.cancel_pending_for_non_open()
        await self.repost_pending_votes()

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
                    await self.repo.refresh_active_slot_flag(gid)
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
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        gid = interaction.guild.id
        await self.repo.ensure_guild_state(gid)
        await self.repo.refresh_active_slot_flag(gid)

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
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=not visible, thinking=True)

        entries_raw = await self.filtered_stats_entries_for_guild(interaction.guild)
        total_solved = sum(s for _, s, _ in entries_raw)
        entries = [(uid, solved, (solved / total_solved * 100.0 if total_solved else 0.0), xp) for uid, solved, xp in entries_raw]

        name_cache: dict[int, str] = {}
        avatar_cache: dict[int, str] = {}
        for uid, _, _ in entries_raw:
            _, name, avatar = await self.resolve_user_label(interaction.guild, uid)
            name_cache[uid] = name
            if avatar:
                avatar_cache[uid] = avatar

        view = ChampionsView(
            entries=entries,
            total_solved=total_solved,
            name_cache=name_cache,
            avatar_cache=avatar_cache,
            image_url=image if is_http_url(image) else DEFAULT_IMAGE_URL,
            owner_id=(interaction.user.id if not visible else None),
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
        await interaction.response.send_modal(ChampionsImportModal(self, mode=mode))

    async def cog_app_command_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, MissingRiddleManagerRole):
            await send_access_denied(interaction)
            return
        try:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Command error.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Command error.", ephemeral=True)
        except Exception:
            pass


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


class SubmitSolutionModal(Modal):
    def __init__(self, cog: "RiddleCog", riddle_id: int):
        super().__init__(title="Submit your solution")
        self.cog = cog
        self.riddle_id = riddle_id
        self.answer = TextInput(label="Your Answer", style=discord.TextStyle.paragraph, required=True, max_length=4000)
        self.add_item(self.answer)

    async def on_submit(self, interaction: Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
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

        submission_id = await self.cog.repo.create_submission_pending(
            interaction.guild.id, to_int(riddle["id"], 0), interaction.user.id, ans
        )
        if not submission_id:
            await interaction.followup.send("❌ Could not save your submission.", ephemeral=True)
            return

        vote_channel = await self.cog.resolve_channel(VOTE_CHANNEL_ID)
        if vote_channel is None or not hasattr(vote_channel, "send"):
            await self.cog.repo.delete_submission(submission_id)
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
            await self.cog.repo.set_submission_vote_message(submission_id, vm.id)
        except Exception:
            await self.cog.repo.delete_submission(submission_id)
            await interaction.followup.send("❌ Failed to post submission for voting.", ephemeral=True)
            return

        await interaction.followup.send("✅ Your solution was submitted for review.", ephemeral=True)


class SubmitButton(discord.ui.Button):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(label="🧠 Submit Solution", style=discord.ButtonStyle.primary, custom_id=SUBMIT_BUTTON_ID)
        self.cog = cog

    async def callback(self, interaction: Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return

        riddle = None
        if interaction.message:
            riddle = await self.cog.repo.get_open_riddle_by_message(interaction.guild.id, interaction.message.id)
        if not riddle:
            riddle = await self.cog.repo.get_open_slot1(interaction.guild.id)
        if not riddle:
            await interaction.response.send_message("⚠️ No active riddle right now.", ephemeral=True)
            return

        await interaction.response.send_modal(SubmitSolutionModal(self.cog, to_int(riddle["id"], 0)))


class _VoteBaseButton(discord.ui.Button):
    def __init__(self, cog: "RiddleCog", *, approve: bool, label: str, style: discord.ButtonStyle, custom_id: str):
        super().__init__(label=label, style=style, custom_id=custom_id)
        self.cog = cog
        self.approve = approve

    async def callback(self, interaction: Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not member_has_role(interaction.user, RIDDLE_MANAGER_ROLE_ID):
            await interaction.response.send_message("❌ No permission.", ephemeral=True)
            return
        if interaction.message is None:
            await interaction.response.send_message("❌ Message context missing.", ephemeral=True)
            return

        ok = await safe_defer(interaction)
        if not ok:
            return

        try:
            if self.approve:
                status, ctx = await self.cog.repo.approve_submission(interaction.message.id, interaction.user.id)
            else:
                status, ctx = await self.cog.repo.reject_submission(interaction.message.id, interaction.user.id)

            if status in {"not_found", "already_done"}:
                await interaction.followup.send("ℹ️ Submission not processable.", ephemeral=True)
                return
            if status == "riddle_closed":
                try:
                    await interaction.message.edit(view=None)
                except Exception:
                    pass
                await interaction.followup.send("⚠️ Riddle already closed.", ephemeral=True)
                return
            if status != "ok" or not ctx:
                await interaction.followup.send("❌ Vote action failed.", ephemeral=True)
                return

            await _edit_vote_result_message(interaction.message, ok=self.approve, moderator_mention=interaction.user.mention)

            if not self.approve:
                submitter_id = to_int(ctx.get("submitter_id"), 0)
                submitter_mention = f"<@{submitter_id}>" if submitter_id > 0 else "the submitter"
                riddle_no = to_int(ctx.get("riddle_no"), to_int(ctx.get("riddle_id"), 0))

                emb = discord.Embed(
                    title=f"❌ Goon Hut Riddle No.{riddle_no}",
                    description=clamp_embed_description(truncate_text(ctx.get("riddle_text") or "No riddle text.", 150)),
                    color=discord.Color.red(),
                )
                emb.add_field(
                    name="Submitted Answer",
                    value=clamp_embed_value(truncate_text(ctx.get("user_answer") or "No answer.", 150)),
                    inline=False,
                )
                emb.add_field(
                    name="Result",
                    value=clamp_embed_value(f"{submitter_mention}, your answer was marked incorrect."),
                    inline=False,
                )

                rimg = ctx.get("image_url")
                if is_http_url(rimg):
                    emb.set_thumbnail(url=rimg)
                elif is_http_url(DEFAULT_IMAGE_URL):
                    emb.set_thumbnail(url=DEFAULT_IMAGE_URL)
                emb.set_footer(text=footer_text(interaction.guild))

                riddle_channel = await self.cog.resolve_channel(RIDDLE_CHANNEL_ID)
                if riddle_channel and hasattr(riddle_channel, "send"):
                    await riddle_channel.send(
                        content=f"<@&{RIDDLE_ROLE_ID}> {submitter_mention}",
                        embed=emb,
                        allowed_mentions=discord.AllowedMentions(roles=True, users=True, everyone=False),
                    )

                await interaction.followup.send("✅ Submission rejected.", ephemeral=True)
                return

            submitter_id = to_int(ctx.get("submitter_id"), 0)
            is_excluded = await self.cog.user_has_excluded_role(interaction.guild, submitter_id)
            xp_gain = max(0, to_int(ctx.get("xp_gain"), 0))
            effective_xp = 0 if is_excluded else xp_gain

            if not is_excluded:
                await self.cog.repo.add_user_solve_xp(interaction.guild.id, submitter_id, effective_xp)

            await self.cog.cleanup_vote_messages_for_riddle(
                to_int(ctx.get("riddle_id"), 0),
                exclude_submission_id=to_int(ctx.get("submission_id"), 0),
            )

            solver_mention, solver_name, _ = await self.cog.resolve_user_label(interaction.guild, submitter_id)
            await self.cog.update_original_post_solved(ctx, solver_mention, effective_xp)

            riddle_channel = await self.cog.resolve_channel(RIDDLE_CHANNEL_ID)
            if riddle_channel and hasattr(riddle_channel, "send"):
                clean_solution, link = extract_link(ctx.get("correct_solution") or "")
                sol = clean_solution or "*None*"
                if link:
                    sol += f"\n🔗 [🧠**MORE**]({link})"

                solved_embed = discord.Embed(
                    title=f"✅ Goon Hut Riddle No.{to_int(ctx.get('riddle_no'), to_int(ctx.get('riddle_id'), 0))} Solved",
                    description=clamp_embed_description(truncate_text(ctx.get("riddle_text") or "*Unknown*", 300)),
                    color=discord.Color.green(),
                )
                solved_embed.add_field(name="Solved by", value=clamp_embed_value(solver_mention), inline=True)
                solved_embed.add_field(name="Solved at (UTC)", value=clamp_embed_value(ctx.get("solved_at") or now_iso_utc()), inline=True)
                solved_embed.add_field(name="Solution", value=clamp_embed_value(sol), inline=False)
                solved_embed.add_field(
                    name="Award",
                    value=clamp_embed_value(f"{effective_xp} XP" + (" (excluded role: no count)" if is_excluded else "")),
                    inline=False,
                )

                rimg = ctx.get("image_url")
                if is_http_url(rimg):
                    solved_embed.set_thumbnail(url=rimg)
                elif is_http_url(DEFAULT_IMAGE_URL):
                    solved_embed.set_thumbnail(url=DEFAULT_IMAGE_URL)

                simg = ctx.get("solution_url")
                if not is_http_url(simg):
                    simg = DEFAULT_IMAGE_URL
                if is_http_url(simg):
                    solved_embed.set_image(url=simg)

                solved_embed.set_footer(text=footer_text(interaction.guild))
                await riddle_channel.send(
                    content=self.cog.build_ping_content(interaction.guild, ctx.get("mention_role_ids")),
                    embed=solved_embed,
                    allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
                )

            if not is_excluded:
                xp_channel = await self.cog.resolve_channel(XP_NOTIFY_CHANNEL_ID)
                if xp_channel and hasattr(xp_channel, "send"):
                    cmd1, cmd2 = build_xpadd_commands(solver_mention, solver_name, effective_xp)
                    await xp_channel.send(f"🏆 XP Reward prepared for {solver_mention}\n`{cmd1}`\n`{cmd2}`")

            gid = interaction.guild.id
            await self.cog.repo.shift_open_slots_left(gid)
            if await self.cog.repo.is_enabled(gid):
                await self.cog.enforce_enabled_state(gid, allow_ping=False, force_repost=True)
            else:
                await self.cog.remove_active_riddle_posts(gid)

            await interaction.followup.send("✅ Submission approved.", ephemeral=True)

        except Exception as e:
            logger.exception("Vote callback failed: %s", e)
            await interaction.followup.send("❌ Vote processing failed. Check logs.", ephemeral=True)


class VoteSuccessButton(_VoteBaseButton):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(cog, approve=True, label="👍 Correct", style=discord.ButtonStyle.success, custom_id=VOTE_UP_BUTTON_ID)


class VoteFailButton(_VoteBaseButton):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(cog, approve=False, label="👎 Wrong", style=discord.ButtonStyle.danger, custom_id=VOTE_DOWN_BUTTON_ID)


class SubmitButtonView(LoggedPersistentView):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(timeout=None)
        self.add_item(SubmitButton(cog))


class VoteButtons(LoggedPersistentView):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(timeout=None)
        self.add_item(VoteSuccessButton(cog))
        self.add_item(VoteFailButton(cog))


class RiddleContentModal(Modal):
    def __init__(self, panel: "RiddleAdminPanelView", slot_no: int, current: Optional[dict], ping_names: str):
        super().__init__(title=f"Slot {slot_no} Content")
        self.panel = panel
        self.slot_no = slot_no
        cur = current or {}

        self.text = TextInput(label="Riddle Text", style=discord.TextStyle.paragraph, default=cur.get("text") or "", required=True, max_length=4000)
        self.solution = TextInput(label="Solution", style=discord.TextStyle.paragraph, default=cur.get("solution") or "", required=True, max_length=4000)
        self.xp = TextInput(label="XP Reward", default=str(max(0, to_int(cur.get("xp"), 0))), required=True, max_length=10)
        self.pings = TextInput(label="Current Ping Roles (display only)", default=ping_names, required=False, max_length=400)

        self.add_item(self.text)
        self.add_item(self.solution)
        self.add_item(self.xp)
        self.add_item(self.pings)

    async def on_submit(self, interaction: Interaction):
        ok = await safe_defer(interaction)
        if not ok or interaction.guild is None:
            return
        rid = await self.panel.cog.repo.upsert_slot_content(
            guild_id=interaction.guild.id,
            user_id=interaction.user.id,
            slot_no=self.slot_no,
            text=str(self.text.value),
            solution=str(self.solution.value),
            xp=max(0, to_int(self.xp.value, 0)),
        )
        self.panel.last_info = f"✅ Slot {self.slot_no} saved." if rid else "❌ Save failed."
        await self.panel.refresh_data()
        await self.panel.safe_edit_panel()


class RiddleImagesModal(Modal):
    def __init__(self, panel: "RiddleAdminPanelView", slot_no: int, current: Optional[dict]):
        super().__init__(title=f"Slot {slot_no} Images")
        self.panel = panel
        self.slot_no = slot_no
        cur = current or {}
        self.riddle_image = TextInput(label="Riddle Image URL (blank = clear)", default=cur.get("image_url") or "", required=False, max_length=2000)
        self.solution_image = TextInput(label="Solution Image URL (blank = clear)", default=cur.get("solution_url") or "", required=False, max_length=2000)
        self.add_item(self.riddle_image)
        self.add_item(self.solution_image)

    async def on_submit(self, interaction: Interaction):
        ok = await safe_defer(interaction)
        if not ok or interaction.guild is None:
            return
        r_img = clean_value(self.riddle_image.value)
        s_img = clean_value(self.solution_image.value)
        if r_img and not is_http_url(r_img):
            self.panel.last_info = "❌ Riddle image URL invalid."
            await self.panel.safe_edit_panel()
            return
        if s_img and not is_http_url(s_img):
            self.panel.last_info = "❌ Solution image URL invalid."
            await self.panel.safe_edit_panel()
            return
        good = await self.panel.cog.repo.set_slot_images(interaction.guild.id, self.slot_no, r_img, s_img, interaction.user.id)
        self.panel.last_info = "✅ Images updated." if good else "❌ Slot not found."
        await self.panel.refresh_data()
        await self.panel.safe_edit_panel()


class SlotPingRoleSelect(RoleSelect):
    def __init__(self, panel: "RiddleAdminPanelView", defaults: list[discord.Role]):
        super().__init__(placeholder="Choose up to 3 extra ping roles", min_values=0, max_values=MAX_EXTRA_PING_ROLES, default_values=defaults or None)
        self.panel = panel

    async def callback(self, interaction: Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        ids = []
        for role in self.values:
            if role.id == RIDDLE_ROLE_ID:
                continue
            if role.id not in ids:
                ids.append(role.id)
            if len(ids) >= MAX_EXTRA_PING_ROLES:
                break
        csv = ",".join(str(x) for x in ids) if ids else None
        ok = await self.panel.cog.repo.set_slot_mentions(interaction.guild.id, self.panel.selected_slot, csv, interaction.user.id)
        self.panel.last_info = "✅ Extra ping roles saved." if ok else "❌ Slot is empty."
        await self.panel.refresh_data()
        await self.panel.safe_edit_panel()
        await interaction.response.send_message("✅ Updated.", ephemeral=True)


class SlotPingRolesView(View):
    def __init__(self, panel: "RiddleAdminPanelView", defaults: list[discord.Role]):
        super().__init__(timeout=300)
        self.panel = panel
        self.add_item(SlotPingRoleSelect(panel, defaults))


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
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("🚫 This panel is not yours.", ephemeral=True)
            return False
        return True

    async def refresh_data(self):
        self.slot_map = await self.cog.repo.open_slot_map(self.guild_id)
        self.state = await self.cog.repo.get_state_row(self.guild_id)

    async def rebuild_items(self):
        self.clear_items()
        enabled = bool(to_int(self.state.get("is_enabled"), 0))
        self.add_item(SlotSelectPanel(self))
        self.add_item(PanelButton(self, "✏️ Edit Content", "edit_content", 1))
        self.add_item(PanelButton(self, "🖼️ Edit Images", "edit_images", 1))
        self.add_item(PanelButton(self, "🎯 Ping-Rollen", "edit_mentions", 1))
        self.add_item(PanelButton(self, "🗑️ Delete Slot", "delete_slot", 1, style=discord.ButtonStyle.danger))
        self.add_item(PanelButton(self, "🔴 Turn OFF" if enabled else "🟢 Turn ON", "toggle", 2, style=discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success))
        self.add_item(PanelButton(self, "📢 Post Now", "post_now", 2))
        self.add_item(PanelButton(self, "🔒 Close Active", "close_active", 2, style=discord.ButtonStyle.danger))
        self.add_item(PanelButton(self, "🔄 Refresh", "refresh", 2, style=discord.ButtonStyle.secondary))

    async def build_embeds(self, guild: Optional[discord.Guild]) -> list[discord.Embed]:
        enabled = bool(to_int(self.state.get("is_enabled"), 0))
        main = discord.Embed(
            title="🗂️ Riddle Control Center",
            description="Ping order is fixed:\n1) Base role\n2) Up to 3 extra roles",
            color=discord.Color.green() if enabled else discord.Color.orange(),
        )
        main.add_field(name="System", value="🟢 ON" if enabled else "🟠 OFF", inline=True)
        main.add_field(name="Selected Slot", value=str(self.selected_slot), inline=True)

        for slot in range(1, MAX_RIDDLE_SLOTS + 1):
            row = self.slot_map.get(slot)
            if not row:
                main.add_field(name=f"Slot {slot}", value="`EMPTY`", inline=False)
                continue
            rno = to_int(row.get("riddle_no"), to_int(row.get("id"), 0))
            extras = parse_csv_role_ids(row.get("mention_role_ids"))
            txt = truncate_text(row.get("text") or "", 90)
            main.add_field(name=f"Slot {slot} • Riddle #{rno}", value=f"XP: {to_int(row.get('xp'), 0)}\nExtra roles: {len(extras)}\n{txt}", inline=False)

        main.add_field(name="Status", value=clamp_embed_value(self.last_info), inline=False)

        row = self.slot_map.get(self.selected_slot)
        if not row:
            return [main]

        preview_meta = discord.Embed(title=f"Slot {self.selected_slot} Preview", color=discord.Color.blurple())
        extras = []
        if guild:
            for rid in parse_csv_role_ids(row.get("mention_role_ids"))[:MAX_EXTRA_PING_ROLES]:
                role = guild.get_role(rid)
                if role:
                    extras.append(role.name)
        preview_meta.add_field(name="Ping Roles", value=f"Base: <@&{RIDDLE_ROLE_ID}>\nExtra: {', '.join(extras) if extras else '-'}", inline=False)

        riddle_img = discord.Embed(title="Riddle Image", color=discord.Color.blurple())
        if is_http_url(row.get("image_url")):
            riddle_img.set_image(url=row.get("image_url"))
        solution_img = discord.Embed(title="Solution Image", color=discord.Color.blurple())
        if is_http_url(row.get("solution_url")):
            solution_img.set_image(url=row.get("solution_url"))

        return [main, preview_meta, riddle_img, solution_img]

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
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return

        if action == "refresh":
            await interaction.response.defer()
            self.last_info = "Refreshed."
            await self.safe_edit_panel()
            return

        if action == "edit_content":
            row = await self.cog.repo.get_open_slot_riddle(interaction.guild.id, self.selected_slot)
            names = []
            for rid in parse_csv_role_ids((row or {}).get("mention_role_ids")):
                role = interaction.guild.get_role(rid)
                if role:
                    names.append(role.name)
            base_role = interaction.guild.get_role(RIDDLE_ROLE_ID)
            base_name = base_role.name if base_role else "base-role"
            ping_names = f"Base: {base_name}; Extra: {', '.join(names) if names else '-'}"
            await interaction.response.send_modal(RiddleContentModal(self, self.selected_slot, row, ping_names))
            return

        if action == "edit_images":
            row = await self.cog.repo.get_open_slot_riddle(interaction.guild.id, self.selected_slot)
            await interaction.response.send_modal(RiddleImagesModal(self, self.selected_slot, row))
            return

        if action == "edit_mentions":
            row = await self.cog.repo.get_open_slot_riddle(interaction.guild.id, self.selected_slot)
            if not row:
                await interaction.response.send_message("❌ Slot empty.", ephemeral=True)
                return
            defaults = []
            for rid in parse_csv_role_ids(row.get("mention_role_ids"))[:MAX_EXTRA_PING_ROLES]:
                role = interaction.guild.get_role(rid)
                if role:
                    defaults.append(role)
            await interaction.response.send_message(
                f"Choose up to {MAX_EXTRA_PING_ROLES} extra ping roles.\nBase role <@&{RIDDLE_ROLE_ID}> is always included.",
                view=SlotPingRolesView(self, defaults),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            return

        await interaction.response.defer()
        gid = interaction.guild.id

        if action == "delete_slot":
            status, _ = await self.cog.repo.delete_slot_riddle(gid, self.selected_slot, interaction.user.id)
            self.last_info = "✅ Deleted." if status == "ok" else "⚠️ Slot already empty."
            await self.cog.repo.shift_open_slots_left(gid)
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
                slot1 = await self.cog.repo.get_open_slot1(gid)
                if not slot1:
                    self.last_info = "⚠️ Slot 1 empty."
                else:
                    await self.cog.repo.set_enabled(gid, True)
                    self.last_info = f"✅ ON ({await self.cog.publish_slot1_post(gid, force_repost=True, allow_role_ping=True)})"
            await self.safe_edit_panel()
            return

        if action == "post_now":
            slot1 = await self.cog.repo.get_open_slot1(gid)
            if not slot1:
                self.last_info = "⚠️ Slot 1 empty."
            else:
                await self.cog.repo.set_enabled(gid, True)
                self.last_info = f"✅ Post now: {await self.cog.publish_slot1_post(gid, force_repost=True, allow_role_ping=True)}"
            await self.safe_edit_panel()
            return

        if action == "close_active":
            r = await self.cog.repo.close_slot1_unsolved(gid, interaction.user.id)
            if not r:
                self.last_info = "⚠️ No active slot 1."
            else:
                self.last_info = "✅ Closed."
                await self.cog.repo.shift_open_slots_left(gid)
                await self.cog.repo.set_enabled(gid, False)
                await self.cog.remove_active_riddle_posts(gid)
            await self.safe_edit_panel()


class SlotSelectPanel(Select):
    def __init__(self, panel: RiddleAdminPanelView):
        self.panel = panel
        opts = []
        for slot in range(1, MAX_RIDDLE_SLOTS + 1):
            row = panel.slot_map.get(slot)
            desc = "EMPTY" if not row else f"R#{to_int(row.get('riddle_no'), to_int(row.get('id'), 0))}"
            opts.append(discord.SelectOption(label=f"Slot {slot}", value=str(slot), description=desc[:100], default=(slot == panel.selected_slot)))
        super().__init__(placeholder="Select slot", min_values=1, max_values=1, options=opts, row=0)

    async def callback(self, interaction: Interaction):
        self.panel.selected_slot = max(1, min(MAX_RIDDLE_SLOTS, to_int(self.values[0], 1)))
        self.panel.last_info = f"Slot {self.panel.selected_slot} selected."
        await interaction.response.defer()
        await self.panel.safe_edit_panel()


class PanelButton(discord.ui.Button):
    def __init__(self, panel: RiddleAdminPanelView, label: str, action: str, row: int, style: discord.ButtonStyle = discord.ButtonStyle.primary):
        super().__init__(label=label, style=style, row=row)
        self.panel = panel
        self.action = action

    async def callback(self, interaction: Interaction):
        await self.panel.handle_action(interaction, self.action)


class ChampionsImportModal(Modal):
    def __init__(self, cog: RiddleCog, mode: Literal["merge", "replace"]):
        super().__init__(title=f"Import Champions JSON ({mode})")
        self.cog = cog
        self.mode = mode
        self.payload = TextInput(
            label="Paste JSON",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000,
            placeholder='{"123456789012345678":{"solved":12,"xp":900}} or [{"user_id":123,"solved":2,"xp":100}]',
        )
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
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        ok = await safe_defer(interaction, ephemeral=True)
        if not ok:
            return

        try:
            incoming = self.parse_rows(self.payload.value or "")
        except Exception as e:
            await interaction.followup.send(f"❌ JSON parse error: {e}", ephemeral=True)
            return

        if self.mode == "replace":
            await self.cog.repo.replace_user_stats(interaction.guild.id, incoming)
            await interaction.followup.send(f"✅ Replaced with {len(incoming)} rows.", ephemeral=True)
            return

        current = {uid: (uid, solved, xp) for uid, solved, xp in await self.cog.repo.stats_entries(interaction.guild.id)}
        for uid, solved, xp in incoming:
            if uid in current:
                _, s0, x0 = current[uid]
                current[uid] = (uid, s0 + solved, x0 + xp)
            else:
                current[uid] = (uid, solved, xp)

        await self.cog.repo.replace_user_stats(interaction.guild.id, list(current.values()))
        await interaction.followup.send(f"✅ Merged {len(incoming)} rows.", ephemeral=True)


class ChampionsView(View):
    def __init__(self, *, entries: list[tuple[int, int, float, int]], total_solved: int, name_cache: dict[int, str], avatar_cache: dict[int, str], image_url: Optional[str], owner_id: Optional[int]):
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
                e.add_field(name=f"{i}. {self._name(uid)}", value=f"🧩 {solved} | 📊 {percent:.1f}% | 🧠 {xp} XP", inline=False)
        else:
            e.add_field(name="No data", value="No entries.", inline=False)
        img = self.page1_image_url if self.page == 0 else self.default_image_url
        if is_http_url(img):
            e.set_image(url=img)
        return e

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await interaction.response.send_message("🚫 This menu is not yours.", ephemeral=True)
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