from __future__ import annotations

import os
import re
import asyncio
import logging
import datetime as dt
from pathlib import Path
from typing import Optional, Any

import aiosqlite
import discord
from discord import app_commands, Interaction, Role
from discord.ext import commands
from discord.ui import View, Modal, TextInput
from dotenv import load_dotenv

# =========================
# CONFIG
# =========================
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
RIDDLE_ROLE_ID = env_int("RIDDLE_ROLE_ID", 1380610400416043089)
RIDDLE_MANAGER_ROLE_ID = env_int("RIDDLE_MANAGER_ROLE_ID", 1393762463861702787)
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

AUTO_POST_COOLDOWN_HOURS = env_int("RIDDLE_AUTO_POST_COOLDOWN_HOURS", 12)
AUTO_POST_SCAN_SECONDS = env_int("RIDDLE_AUTO_POST_SCAN_SECONDS", 60)

SUBMIT_BUTTON_ID = "riddle_submit_solution"
VOTE_UP_BUTTON_ID = "riddle_vote_up"
VOTE_DOWN_BUTTON_ID = "riddle_vote_down"

# =========================
# HELPERS
# =========================
def now_date_str() -> str:
    return dt.datetime.now().strftime("%Y/%m/%d")

def now_iso_utc() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def parse_iso_utc(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return dt.datetime.fromisoformat(s).astimezone(dt.timezone.utc)
    except Exception:
        return None

def to_iso_z(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def footer_text(guild: Optional[discord.Guild]) -> str:
    gname = guild.name if guild else "Unknown Guild"
    return f"{gname} ({now_date_str()})"

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

def truncate_text(text: str, max_length: int = 200) -> str:
    if text and len(text) > max_length:
        return text[:max_length] + "[...]"
    return text or ""

def extract_link(text: str) -> tuple[str, Optional[str]]:
    text = text or ""
    match = re.search(r"(https?://\S+)", text)
    if not match:
        return text.strip(), None
    link = match.group(1)
    clean = text.replace(link, "").strip()
    return clean, link

def xp_from_award(award_text: Optional[str]) -> int:
    m = re.search(r"\d+", str(award_text or ""))
    return int(m.group()) if m else 0

def member_has_role(member: discord.abc.User, role_id: int) -> bool:
    if not isinstance(member, discord.Member):
        return False
    return any(r.id == role_id for r in member.roles)

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
            out.append(role.mention)
            seen.add(rid_i)
    return out

def build_xpadd_commands(member_mention: str, member_name: str, xp_amount: int) -> tuple[str, str]:
    xp = max(0, to_int(xp_amount, 0))
    safe_name = (member_name or "UnknownUser").replace('"', "").strip() or "UnknownUser"
    name_cmd = f'/xpadd "{safe_name}" {xp}'
    mention_cmd = f"/xpadd {member_mention} {xp}"
    return name_cmd, mention_cmd


# =========================
# DB / REPO
# =========================
class RiddleRepo:
    def __init__(self):
        self.db: Optional[aiosqlite.Connection] = None
        self.db_lock = asyncio.Lock()

    async def start(self):
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(DB_PATH)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode = WAL;")
        await self.db.execute("PRAGMA foreign_keys = ON;")
        await self.db.execute("PRAGMA busy_timeout = 5000;")
        await self.db.commit()
        await self._init_db()

    async def close(self):
        if self.db is not None:
            await self.db.close()
            self.db = None

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
            award TEXT,
            image_url TEXT,
            solution_url TEXT,
            button_role_id INTEGER,
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

        CREATE UNIQUE INDEX IF NOT EXISTS idx_riddles_posted_message
        ON riddles(posted_message_id) WHERE posted_message_id IS NOT NULL;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_riddles_open_slot_unique
        ON riddles(guild_id, slot_no) WHERE status='open' AND slot_no IS NOT NULL;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_riddles_active_one
        ON riddles(guild_id) WHERE status='open' AND is_active=1;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_riddles_guild_no
        ON riddles(guild_id, riddle_no) WHERE riddle_no IS NOT NULL;

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

        CREATE UNIQUE INDEX IF NOT EXISTS idx_submissions_vote_message
        ON submissions(vote_message_id) WHERE vote_message_id IS NOT NULL;

        CREATE TABLE IF NOT EXISTS user_stats (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            solved_riddles INTEGER NOT NULL DEFAULT 0,
            xp INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(guild_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS guild_riddle_state (
            guild_id INTEGER PRIMARY KEY,
            next_auto_post_at TEXT,
            updated_at TEXT NOT NULL
        );
        """
        async with self.db_lock:
            await self.db.executescript(schema)

            await self.db.execute(
                """
                UPDATE riddles AS r
                SET riddle_no = (
                    SELECT COUNT(*)
                    FROM riddles r2
                    WHERE r2.guild_id = r.guild_id
                      AND r2.id <= r.id
                )
                WHERE r.riddle_no IS NULL
                """
            )

            await self._repair_pool_integrity_locked()
            await self.db.commit()

    async def _repair_pool_integrity_locked(self):
        assert self.db is not None
        now = now_iso_utc()

        cur = await self.db.execute("SELECT DISTINCT guild_id FROM riddles WHERE status='open'")
        guild_rows = await cur.fetchall()
        await cur.close()

        guild_ids = [to_int(r["guild_id"], 0) for r in guild_rows if to_int(r["guild_id"], 0) > 0]

        for gid in guild_ids:
            cur = await self.db.execute(
                "SELECT * FROM riddles WHERE guild_id=? AND status='open' ORDER BY id ASC",
                (gid,)
            )
            rows = [dict(r) for r in await cur.fetchall()]
            await cur.close()

            used_slots: dict[int, int] = {}
            needs_assign = []

            for row in rows:
                rid = to_int(row.get("id"), 0)
                slot = to_int(row.get("slot_no"), 0)
                if 1 <= slot <= MAX_RIDDLE_SLOTS and slot not in used_slots:
                    used_slots[slot] = rid
                else:
                    needs_assign.append(row)

            free_slots = [s for s in range(1, MAX_RIDDLE_SLOTS + 1) if s not in used_slots]

            for row in needs_assign:
                rid = to_int(row.get("id"), 0)
                if free_slots:
                    slot = free_slots.pop(0)
                    await self.db.execute("UPDATE riddles SET slot_no=?, updated_at=? WHERE id=?", (slot, now, rid))
                else:
                    # overflow schließen
                    await self.db.execute(
                        """
                        UPDATE riddles
                        SET status='closed', slot_no=NULL, is_active=0, closed_by=0, closed_at=?, updated_at=?
                        WHERE id=? AND status='open'
                        """,
                        (now, now, rid)
                    )
                    await self.db.execute(
                        """
                        UPDATE submissions
                        SET status='cancelled', voted_by=0, voted_at=?
                        WHERE riddle_id=? AND status='pending'
                        """,
                        (now, rid)
                    )

            await self.db.execute("UPDATE riddles SET is_active=0 WHERE guild_id=? AND status='open'", (gid,))
            cur = await self.db.execute(
                """
                SELECT id FROM riddles
                WHERE guild_id=? AND status='open'
                ORDER BY slot_no ASC, id ASC
                LIMIT 1
                """,
                (gid,)
            )
            first = await cur.fetchone()
            await cur.close()
            if first:
                await self.db.execute("UPDATE riddles SET is_active=1 WHERE id=?", (to_int(first["id"], 0),))

    async def _fetchone(self, query: str, params: tuple = ()) -> Optional[dict]:
        if self.db is None:
            return None
        async with self.db_lock:
            cur = await self.db.execute(query, params)
            row = await cur.fetchone()
            await cur.close()
        return dict(row) if row else None

    async def _fetchall(self, query: str, params: tuple = ()) -> list[dict]:
        if self.db is None:
            return []
        async with self.db_lock:
            cur = await self.db.execute(query, params)
            rows = await cur.fetchall()
            await cur.close()
        return [dict(r) for r in rows]

    async def _execute(self, query: str, params: tuple = ()) -> tuple[int, int]:
        if self.db is None:
            return 0, 0
        async with self.db_lock:
            cur = await self.db.execute(query, params)
            await self.db.commit()
            rc = cur.rowcount
            lid = int(cur.lastrowid or 0)
            await cur.close()
        return rc, lid

    # ---------- slots / riddles ----------
    async def open_slot_map(self, guild_id: int) -> dict[int, dict]:
        rows = await self._fetchall(
            """
            SELECT * FROM riddles
            WHERE guild_id=? AND status='open' AND slot_no BETWEEN 1 AND ?
            ORDER BY slot_no ASC
            """,
            (guild_id, MAX_RIDDLE_SLOTS)
        )
        out = {}
        for r in rows:
            s = to_int(r.get("slot_no"), 0)
            if 1 <= s <= MAX_RIDDLE_SLOTS:
                out[s] = r
        return out

    async def get_open_slot_riddle(self, guild_id: int, slot_no: int) -> Optional[dict]:
        return await self._fetchone(
            "SELECT * FROM riddles WHERE guild_id=? AND status='open' AND slot_no=? LIMIT 1",
            (guild_id, slot_no)
        )

    async def get_active_open_riddle(self, guild_id: int) -> Optional[dict]:
        row = await self._fetchone(
            """
            SELECT * FROM riddles
            WHERE guild_id=? AND status='open' AND is_active=1
            ORDER BY slot_no ASC, id ASC LIMIT 1
            """,
            (guild_id,)
        )
        if row:
            return row
        return await self._fetchone(
            """
            SELECT * FROM riddles
            WHERE guild_id=? AND status='open'
            ORDER BY slot_no ASC, id ASC LIMIT 1
            """,
            (guild_id,)
        )

    async def activate_next_from_pool(self, guild_id: int) -> Optional[dict]:
        if self.db is None:
            return None

        async with self.db_lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    """
                    SELECT id FROM riddles
                    WHERE guild_id=? AND status='open' AND is_active=1
                    ORDER BY slot_no ASC, id ASC LIMIT 1
                    """,
                    (guild_id,)
                )
                active = await cur.fetchone()
                await cur.close()

                if active:
                    active_id = to_int(active["id"], 0)
                    await self.db.execute(
                        "UPDATE riddles SET is_active=0, updated_at=? WHERE guild_id=? AND status='open' AND id<>?",
                        (now_iso_utc(), guild_id, active_id)
                    )
                    await self.db.execute("UPDATE riddles SET is_active=1 WHERE id=?", (active_id,))
                    await self.db.commit()

                    cur = await self.db.execute("SELECT * FROM riddles WHERE id=? LIMIT 1", (active_id,))
                    row = await cur.fetchone()
                    await cur.close()
                    return dict(row) if row else None

                cur = await self.db.execute(
                    """
                    SELECT id FROM riddles
                    WHERE guild_id=? AND status='open'
                    ORDER BY slot_no ASC, id ASC LIMIT 1
                    """,
                    (guild_id,)
                )
                nxt = await cur.fetchone()
                await cur.close()

                await self.db.execute(
                    "UPDATE riddles SET is_active=0, updated_at=? WHERE guild_id=? AND status='open'",
                    (now_iso_utc(), guild_id)
                )

                if not nxt:
                    await self.db.commit()
                    return None

                nxt_id = to_int(nxt["id"], 0)
                await self.db.execute("UPDATE riddles SET is_active=1, updated_at=? WHERE id=?", (now_iso_utc(), nxt_id))
                await self.db.commit()

                cur = await self.db.execute("SELECT * FROM riddles WHERE id=? LIMIT 1", (nxt_id,))
                row = await cur.fetchone()
                await cur.close()
                return dict(row) if row else None
            except Exception:
                await self.db.rollback()
                raise

    async def upsert_slot_riddle(
        self,
        *,
        guild_id: int,
        user_id: int,
        slot_no: int,
        payload: dict,
        mention_override_id: Optional[int]
    ) -> Optional[int]:
        if self.db is None:
            return None
        if slot_no < 1 or slot_no > MAX_RIDDLE_SLOTS:
            return None

        text = clean_value(payload.get("text"))
        solution = clean_value(payload.get("solution"))
        if not text or not solution:
            return None

        award = clean_value(payload.get("award"))
        image_url = clean_value(payload.get("image_url"))
        solution_url = clean_value(payload.get("solution_url"))
        now = now_iso_utc()

        async with self.db_lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    "SELECT * FROM riddles WHERE guild_id=? AND status='open' AND slot_no=? LIMIT 1",
                    (guild_id, slot_no)
                )
                row = await cur.fetchone()
                await cur.close()

                if row:
                    old = dict(row)
                    button_role_id = mention_override_id if mention_override_id is not None else old.get("button_role_id")
                    await self.db.execute(
                        """
                        UPDATE riddles
                        SET text=?, solution=?, award=?, image_url=?, solution_url=?, button_role_id=?, created_by=?, updated_at=?
                        WHERE id=?
                        """,
                        (text, solution, award, image_url, solution_url, button_role_id, user_id, now, old["id"])
                    )
                    rid = to_int(old["id"], 0)
                else:
                    cur = await self.db.execute("SELECT COALESCE(MAX(riddle_no), 0) + 1 AS n FROM riddles WHERE guild_id=?", (guild_id,))
                    row_n = await cur.fetchone()
                    await cur.close()
                    riddle_no = to_int(row_n["n"] if row_n else 1, 1)

                    cur = await self.db.execute(
                        """
                        INSERT INTO riddles (
                            guild_id, riddle_no, slot_no, is_active,
                            text, solution, award, image_url, solution_url, button_role_id, status,
                            created_by, created_at, updated_at
                        )
                        VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
                        """,
                        (guild_id, riddle_no, slot_no, text, solution, award, image_url, solution_url, mention_override_id, user_id, now, now)
                    )
                    rid = int(cur.lastrowid or 0)
                    await cur.close()

                await self.db.commit()
                return rid if rid > 0 else None
            except Exception:
                await self.db.rollback()
                raise

    async def close_active_riddle(self, guild_id: int, closed_by: int) -> Optional[dict]:
        if self.db is None:
            return None
        async with self.db_lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    """
                    SELECT * FROM riddles
                    WHERE guild_id=? AND status='open'
                    ORDER BY is_active DESC, slot_no ASC, id ASC LIMIT 1
                    """,
                    (guild_id,)
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
                    (closed_by, now, now, r["id"])
                )

                await self.db.execute(
                    """
                    UPDATE submissions
                    SET status='cancelled', voted_by=?, voted_at=?
                    WHERE riddle_id=? AND status='pending'
                    """,
                    (closed_by, now, r["id"])
                )

                await self.db.commit()
                return r
            except Exception:
                await self.db.rollback()
                raise

    async def get_open_riddle_by_id(self, guild_id: int, riddle_id: int) -> Optional[dict]:
        return await self._fetchone(
            "SELECT * FROM riddles WHERE guild_id=? AND id=? AND status='open' LIMIT 1",
            (guild_id, riddle_id)
        )

    async def get_open_riddle_by_message(self, guild_id: int, message_id: int) -> Optional[dict]:
        return await self._fetchone(
            "SELECT * FROM riddles WHERE guild_id=? AND posted_message_id=? AND status='open' LIMIT 1",
            (guild_id, message_id)
        )

    async def set_riddle_posted_message(self, riddle_id: int, channel_id: int, message_id: int) -> bool:
        rc, _ = await self._execute(
            "UPDATE riddles SET posted_channel_id=?, posted_message_id=?, updated_at=? WHERE id=?",
            (channel_id, message_id, now_iso_utc(), riddle_id)
        )
        return rc > 0

    async def clear_other_open_post_refs(self, guild_id: int, active_id: int):
        await self._execute(
            """
            UPDATE riddles
            SET posted_channel_id=NULL, posted_message_id=NULL, updated_at=?
            WHERE guild_id=? AND status='open' AND id<>?
            """,
            (now_iso_utc(), guild_id, active_id)
        )

    # ---------- submissions ----------
    async def create_submission_pending(self, guild_id: int, riddle_id: int, user_id: int, answer: str) -> Optional[int]:
        _, lid = await self._execute(
            """
            INSERT INTO submissions (guild_id, riddle_id, user_id, answer, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (guild_id, riddle_id, user_id, answer, now_iso_utc())
        )
        return lid if lid > 0 else None

    async def set_submission_vote_message(self, submission_id: int, vote_message_id: int) -> bool:
        rc, _ = await self._execute(
            "UPDATE submissions SET vote_message_id=? WHERE id=?",
            (vote_message_id, submission_id)
        )
        return rc > 0

    async def delete_submission(self, submission_id: int):
        await self._execute("DELETE FROM submissions WHERE id=?", (submission_id,))

    async def approve_submission(self, vote_message_id: int, moderator_id: int) -> tuple[str, Optional[dict]]:
        if self.db is None:
            return "error", None

        async with self.db_lock:
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
                        r.slot_no AS slot_no,
                        r.text AS riddle_text,
                        r.solution AS correct_solution,
                        r.award AS award,
                        r.button_role_id AS button_role_id,
                        r.solution_url AS solution_url,
                        r.posted_channel_id AS posted_channel_id,
                        r.posted_message_id AS posted_message_id,
                        r.status AS riddle_status
                    FROM submissions s
                    JOIN riddles r ON r.id = s.riddle_id
                    WHERE s.vote_message_id=?
                    LIMIT 1
                    """,
                    (vote_message_id,)
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

                now = now_iso_utc()

                if ctx["riddle_status"] != "open":
                    await self.db.execute(
                        "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? WHERE id=? AND status='pending'",
                        (moderator_id, now, ctx["submission_id"])
                    )
                    await self.db.commit()
                    return "riddle_closed", ctx

                cur = await self.db.execute(
                    "UPDATE submissions SET status='correct', voted_by=?, voted_at=? WHERE id=? AND status='pending'",
                    (moderator_id, now, ctx["submission_id"])
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
                    (ctx["submitter_id"], now, now, ctx["riddle_id"])
                )
                if cur.rowcount != 1:
                    await self.db.rollback()
                    return "riddle_closed", ctx

                xp_gain = xp_from_award(ctx.get("award"))
                await self.db.execute(
                    """
                    INSERT INTO user_stats (guild_id, user_id, solved_riddles, xp)
                    VALUES (?, ?, 1, ?)
                    ON CONFLICT(guild_id, user_id)
                    DO UPDATE SET
                        solved_riddles = solved_riddles + 1,
                        xp = xp + excluded.xp
                    """,
                    (ctx["guild_id"], ctx["submitter_id"], xp_gain)
                )

                await self.db.execute(
                    """
                    UPDATE submissions
                    SET status='cancelled', voted_by=?, voted_at=?
                    WHERE riddle_id=? AND status='pending' AND id<>?
                    """,
                    (moderator_id, now, ctx["riddle_id"], ctx["submission_id"])
                )

                await self.db.commit()
                ctx["xp_gain"] = xp_gain
                return "ok", ctx
            except Exception:
                await self.db.rollback()
                raise

    async def reject_submission(self, vote_message_id: int, moderator_id: int) -> tuple[str, Optional[dict]]:
        if self.db is None:
            return "error", None

        async with self.db_lock:
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
                        r.text AS riddle_text,
                        r.award AS award,
                        r.button_role_id AS button_role_id,
                        r.status AS riddle_status
                    FROM submissions s
                    JOIN riddles r ON r.id = s.riddle_id
                    WHERE s.vote_message_id=?
                    LIMIT 1
                    """,
                    (vote_message_id,)
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

                now = now_iso_utc()

                if ctx["riddle_status"] != "open":
                    await self.db.execute(
                        "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? WHERE id=? AND status='pending'",
                        (moderator_id, now, ctx["submission_id"])
                    )
                    await self.db.commit()
                    return "riddle_closed", ctx

                cur = await self.db.execute(
                    "UPDATE submissions SET status='wrong', voted_by=?, voted_at=? WHERE id=? AND status='pending'",
                    (moderator_id, now, ctx["submission_id"])
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
        return await self._fetchall(
            "SELECT id, vote_message_id FROM submissions WHERE riddle_id=? AND vote_message_id IS NOT NULL",
            (riddle_id,)
        )

    # ---------- stats ----------
    async def stats_entries(self, guild_id: int) -> list[tuple[int, int, int]]:
        rows = await self._fetchall(
            "SELECT user_id, solved_riddles, xp FROM user_stats WHERE guild_id=? ORDER BY solved_riddles DESC, xp DESC",
            (guild_id,)
        )
        out = []
        for r in rows:
            uid = to_int(r.get("user_id"), -1)
            if uid <= 0:
                continue
            solved = max(0, to_int(r.get("solved_riddles"), 0))
            xp = max(0, to_int(r.get("xp"), 0))
            out.append((uid, solved, xp))
        return out

    # ---------- cooldown ----------
    async def set_auto_post_cooldown(self, guild_id: int, hours: int):
        unlock_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)
        ts = to_iso_z(unlock_at)
        await self._execute(
            """
            INSERT INTO guild_riddle_state (guild_id, next_auto_post_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                next_auto_post_at=excluded.next_auto_post_at,
                updated_at=excluded.updated_at
            """,
            (guild_id, ts, now_iso_utc())
        )

    async def clear_auto_post_cooldown(self, guild_id: int):
        await self._execute(
            """
            INSERT INTO guild_riddle_state (guild_id, next_auto_post_at, updated_at)
            VALUES (?, NULL, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                next_auto_post_at=NULL,
                updated_at=excluded.updated_at
            """,
            (guild_id, now_iso_utc())
        )

    async def get_next_auto_post_at(self, guild_id: int) -> Optional[dt.datetime]:
        row = await self._fetchone(
            "SELECT next_auto_post_at FROM guild_riddle_state WHERE guild_id=? LIMIT 1",
            (guild_id,)
        )
        if not row:
            return None
        return parse_iso_utc(row.get("next_auto_post_at"))

    async def is_auto_post_blocked(self, guild_id: int) -> tuple[bool, Optional[dt.datetime]]:
        unlock_at = await self.get_next_auto_post_at(guild_id)
        if not unlock_at:
            return False, None
        now = dt.datetime.now(dt.timezone.utc)
        return now < unlock_at, unlock_at

    async def guild_ids_with_open_riddles(self) -> list[int]:
        rows = await self._fetchall("SELECT DISTINCT guild_id FROM riddles WHERE status='open'")
        return [to_int(r.get("guild_id"), 0) for r in rows if to_int(r.get("guild_id"), 0) > 0]


# =========================
# SHARED RUNTIME
# =========================
class RiddleRuntime:
    def __init__(self, bot: commands.Bot, repo: RiddleRepo):
        self.bot = bot
        self.repo = repo
        self.submit_view_factory = None  # wird später gesetzt

    def set_submit_view_factory(self, fn):
        self.submit_view_factory = fn

    def get_submit_view(self):
        if self.submit_view_factory is None:
            return None
        return self.submit_view_factory()

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

        channel = await self.resolve_channel(cid)
        if channel is None or not hasattr(channel, "fetch_message"):
            return None

        try:
            return await channel.fetch_message(mid)
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

    def build_riddle_embed(self, guild: Optional[discord.Guild], riddle: dict) -> discord.Embed:
        image_url = riddle.get("image_url")
        if not is_http_url(image_url):
            image_url = DEFAULT_IMAGE_URL

        riddle_no = to_int(riddle.get("riddle_no"), to_int(riddle.get("id"), 1))
        title = f"🧩 Ms Pepper's Goon Hut Riddle\n#{riddle_no} ({now_date_str()})"

        embed = discord.Embed(
            title=title,
            description=riddle.get("text") or "*No text*",
            color=discord.Color.blurple()
        )
        embed.add_field(name="🏆 Award", value=riddle.get("award") or "*None*", inline=False)
        slot_no = to_int(riddle.get("slot_no"), 0)
        if 1 <= slot_no <= MAX_RIDDLE_SLOTS:
            embed.add_field(name="📦 Pool Slot", value=str(slot_no), inline=True)
        embed.set_image(url=image_url)
        embed.set_footer(text=footer_text(guild))
        return embed

    async def publish_active_riddle(
        self,
        *,
        guild_id: int,
        force_repost: bool,
        extra_ping_role_id: Optional[int],
        allow_role_ping: bool,
        ignore_cooldown: bool
    ) -> str:
        active = await self.repo.get_active_open_riddle(guild_id)
        if not active:
            return "no_active"

        guild = self.bot.get_guild(guild_id)
        riddle_channel = await self.resolve_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel is None or not hasattr(riddle_channel, "send"):
            return "no_channel"

        submit_view = self.get_submit_view()
        if submit_view is None:
            return "no_view"

        embed = self.build_riddle_embed(guild, active)
        mentions = unique_role_mentions(guild, RIDDLE_ROLE_ID, active.get("button_role_id"), extra_ping_role_id)
        content = " ".join(dict.fromkeys([m for m in mentions if m])) or None

        existing_msg = await self.fetch_message_safe(active.get("posted_channel_id"), active.get("posted_message_id"))

        # vorhandene Nachricht updaten (kein Repost)
        if existing_msg and not force_repost:
            try:
                await existing_msg.edit(
                    content=content,
                    embed=embed,
                    view=submit_view,
                    allowed_mentions=discord.AllowedMentions(roles=allow_role_ping, users=False, everyone=False)
                )
                await self.repo.clear_other_open_post_refs(guild_id, to_int(active["id"], 0))
                return "updated"
            except Exception:
                pass

        # Cooldown nur für AUTO posting
        if not ignore_cooldown:
            blocked, _ = await self.repo.is_auto_post_blocked(guild_id)
            if blocked:
                return "cooldown"

        try:
            if existing_msg and force_repost:
                try:
                    await existing_msg.delete()
                except Exception:
                    pass

            msg = await riddle_channel.send(
                content=content,
                embed=embed,
                view=submit_view,
                allowed_mentions=discord.AllowedMentions(roles=allow_role_ping, users=False, everyone=False)
            )
            await self.repo.set_riddle_posted_message(to_int(active["id"], 0), msg.channel.id, msg.id)
            await self.repo.clear_other_open_post_refs(guild_id, to_int(active["id"], 0))
            await self.repo.clear_auto_post_cooldown(guild_id)
            return "posted"
        except Exception as e:
            logger.exception("publish_active_riddle failed: %s", e)
            return "error"

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

    async def update_original_post(self, ctx: dict, field_name: str, field_value: str):
        msg = await self.fetch_message_safe(ctx.get("posted_channel_id"), ctx.get("posted_message_id"))
        if not msg:
            return

        if msg.embeds:
            embed = discord.Embed.from_dict(msg.embeds[0].to_dict())
        else:
            embed = discord.Embed(
                title="🧩 Riddle",
                description=ctx.get("riddle_text") or "*Unknown*",
                color=discord.Color.blurple()
            )

        embed.add_field(name=field_name, value=field_value, inline=False)
        embed.set_footer(text=footer_text(msg.guild))
        try:
            await msg.edit(embed=embed, view=None)
        except Exception:
            pass

    async def mark_original_riddle_post_solved(
        self,
        *,
        ctx: dict,
        solver_mention: str,
        clean_solution: str,
        more_link: Optional[str]
    ):
        solved_note = f"✅ Solved by {solver_mention}\n{(clean_solution or '*None*').splitlines()[0]}"
        if more_link:
            solved_note += f"\n🔗 [🧠**MORE**]({more_link})"
        await self.update_original_post(ctx, "✅ Solved", solved_note)


# =========================
# ACCESS
# =========================
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
            f"This command is restricted to <@&{RIDDLE_MANAGER_ROLE_ID}>.\n"
            "If you want access, apply for this role."
        ),
        color=discord.Color.orange()
    )
    embed.set_image(url=ACCESS_DENIED_IMAGE_URL)
    embed.set_footer(text="Role application required")

    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================
# BASE VIEW
# =========================
class LoggedPersistentView(View):
    async def on_error(self, interaction: Interaction, error: Exception, item: discord.ui.Item[Any]):
        logger.exception("View callback error: %s", error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Button callback failed. Check logs.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Button callback failed. Check logs.", ephemeral=True)
        except Exception:
            pass


# =========================
# ADMIN UI
# =========================
class RiddleUpsertModal(Modal):
    def __init__(
        self,
        *,
        cog: "RiddleAdminCog",
        current_data: Optional[dict],
        mention_override_id: Optional[int],
        slot_no: int
    ):
        has_data = bool(current_data and clean_value(current_data.get("text")) and clean_value(current_data.get("solution")))
        super().__init__(title=f"Edit Slot {slot_no}" if has_data else f"Create Slot {slot_no}")

        self.cog = cog
        self.current_data = current_data or {}
        self.mention_override_id = mention_override_id
        self.slot_no = slot_no

        self.text = TextInput(label="Text", style=discord.TextStyle.paragraph, default=self.current_data.get("text") or "", required=True, max_length=4000)
        self.solution = TextInput(label="Solution", style=discord.TextStyle.paragraph, default=self.current_data.get("solution") or "", required=True, max_length=4000)
        self.award = TextInput(label="Award", default=self.current_data.get("award") or "", required=False, max_length=200)
        self.image_url = TextInput(label="Image URL", default=self.current_data.get("image_url") or "", required=False, max_length=1000)
        self.solution_url = TextInput(label="Solution URL", default=self.current_data.get("solution_url") or "", required=False, max_length=1000)

        self.add_item(self.text)
        self.add_item(self.solution)
        self.add_item(self.award)
        self.add_item(self.image_url)
        self.add_item(self.solution_url)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None:
            await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        payload = {
            "text": clean_value(self.text.value),
            "solution": clean_value(self.solution.value),
            "award": clean_value(self.award.value),
            "image_url": clean_value(self.image_url.value),
            "solution_url": clean_value(self.solution_url.value),
        }

        rid = await self.cog.repo.upsert_slot_riddle(
            guild_id=interaction.guild.id,
            user_id=interaction.user.id,
            slot_no=self.slot_no,
            payload=payload,
            mention_override_id=self.mention_override_id
        )
        if not rid:
            await interaction.followup.send("❌ Saving failed.", ephemeral=True)
            return

        await self.cog.repo.activate_next_from_pool(interaction.guild.id)
        result = await self.cog.runtime.publish_active_riddle(
            guild_id=interaction.guild.id,
            force_repost=False,
            extra_ping_role_id=None,
            allow_role_ping=True,
            ignore_cooldown=False
        )

        if result == "cooldown":
            blocked, unlock_at = await self.cog.repo.is_auto_post_blocked(interaction.guild.id)
            if blocked and unlock_at:
                ts = discord.utils.format_dt(unlock_at, style="F")
                await interaction.followup.send(f"✅ Slot gespeichert. Auto-Post erst wieder ab {ts}.", ephemeral=True)
                return

        await interaction.followup.send(f"✅ Slot {self.slot_no} saved.", ephemeral=True)


class SlotEditButton(discord.ui.Button):
    def __init__(self, cog: "RiddleAdminCog", slot_no: int, filled: bool):
        super().__init__(
            label=f"Edit Slot {slot_no}",
            style=discord.ButtonStyle.success if filled else discord.ButtonStyle.secondary,
            row=0 if slot_no <= 3 else 1
        )
        self.cog = cog
        self.slot_no = slot_no

    async def callback(self, interaction: Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not member_has_role(interaction.user, RIDDLE_MANAGER_ROLE_ID):
            await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
            return

        current = await self.cog.repo.get_open_slot_riddle(interaction.guild.id, self.slot_no)
        modal = RiddleUpsertModal(cog=self.cog, current_data=current, mention_override_id=None, slot_no=self.slot_no)
        await interaction.response.send_modal(modal)


class SlotRefreshButton(discord.ui.Button):
    def __init__(self, cog: "RiddleAdminCog"):
        super().__init__(label="🔄 Refresh", style=discord.ButtonStyle.primary, row=2)
        self.cog = cog

    async def callback(self, interaction: Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        slot_map = await self.cog.repo.open_slot_map(interaction.guild.id)
        embed = self.cog.build_slots_overview_embed(interaction.guild, slot_map)
        view = SlotManagerView(cog=self.cog, owner_id=interaction.user.id, slot_map=slot_map)
        await interaction.response.edit_message(embed=embed, view=view)


class SlotManagerView(View):
    def __init__(self, *, cog: "RiddleAdminCog", owner_id: int, slot_map: dict[int, dict]):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = owner_id

        for slot in range(1, MAX_RIDDLE_SLOTS + 1):
            self.add_item(SlotEditButton(cog, slot, filled=(slot in slot_map)))
        self.add_item(SlotRefreshButton(cog))

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("🚫 This menu is not yours.", ephemeral=True)
            return False
        return True


# =========================
# PLAY UI
# =========================
class SubmitSolutionModal(Modal):
    def __init__(self, cog: "RiddlePlayCog", riddle_id: int):
        super().__init__(title="💡 Submit Your Solution")
        self.cog = cog
        self.riddle_id = riddle_id
        self.solution = TextInput(label="Your Answer", style=discord.TextStyle.paragraph, required=True, max_length=4000)
        self.add_item(self.solution)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None:
            await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        riddle = await self.cog.repo.get_open_riddle_by_id(interaction.guild.id, self.riddle_id)
        if not riddle:
            await interaction.followup.send("❌ No active riddle found.", ephemeral=True)
            return

        vote_channel = await self.cog.runtime.resolve_channel(VOTE_CHANNEL_ID)
        if vote_channel is None or not hasattr(vote_channel, "send"):
            await interaction.followup.send("❌ Vote channel not found.", ephemeral=True)
            return

        answer = clean_value(self.solution.value)
        if not answer:
            await interaction.followup.send("❌ Answer cannot be empty.", ephemeral=True)
            return

        submission_id = await self.cog.repo.create_submission_pending(interaction.guild.id, riddle["id"], interaction.user.id, answer)
        if not submission_id:
            await interaction.followup.send("❌ Could not save your submission.", ephemeral=True)
            return

        embed = discord.Embed(
            title="📜 New Solution Submitted",
            description=riddle.get("text") or "*No riddle text*",
            color=discord.Color.gold()
        )
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="🧠 User's Answer", value=answer, inline=False)
        embed.add_field(name="✅ Correct Solution", value=riddle.get("solution") or "*Not set*", inline=False)
        embed.add_field(name="🏆 Award", value=riddle.get("award") or "*None*", inline=False)
        embed.add_field(name="🆔 User ID", value=str(interaction.user.id), inline=False)
        if riddle.get("button_role_id"):
            embed.add_field(name="🔖 Assigned Group", value=str(riddle["button_role_id"]), inline=True)
        embed.set_footer(text=footer_text(interaction.guild))

        try:
            vote_msg = await vote_channel.send(embed=embed, view=VoteButtons(self.cog))
        except Exception:
            await self.cog.repo.delete_submission(submission_id)
            await interaction.followup.send("❌ Could not send vote message.", ephemeral=True)
            return

        ok = await self.cog.repo.set_submission_vote_message(submission_id, vote_msg.id)
        if not ok:
            try:
                await vote_msg.delete()
            except Exception:
                pass
            await self.cog.repo.delete_submission(submission_id)
            await interaction.followup.send("❌ Internal error while linking vote message.", ephemeral=True)
            return

        await interaction.followup.send("✅ Your solution has been submitted!", ephemeral=True)


class SubmitButton(discord.ui.Button):
    def __init__(self, cog: "RiddlePlayCog"):
        super().__init__(label="💡 Submit Solution", style=discord.ButtonStyle.primary, custom_id=SUBMIT_BUTTON_ID)
        self.cog = cog

    async def callback(self, interaction: Interaction):
        if interaction.guild is None or interaction.message is None:
            await interaction.response.send_message("❌ This can only be used in a server.", ephemeral=True)
            return

        riddle = await self.cog.repo.get_open_riddle_by_message(interaction.guild.id, interaction.message.id)
        if not riddle:
            await interaction.response.send_message("❌ This riddle is no longer active.", ephemeral=True)
            return

        await interaction.response.send_modal(SubmitSolutionModal(self.cog, riddle_id=riddle["id"]))


class SubmitButtonView(LoggedPersistentView):
    def __init__(self, cog: "RiddlePlayCog"):
        super().__init__(timeout=None)
        self.add_item(SubmitButton(cog))


class VoteSuccessButton(discord.ui.Button):
    def __init__(self, cog: "RiddlePlayCog"):
        super().__init__(emoji="👍", style=discord.ButtonStyle.success, custom_id=VOTE_UP_BUTTON_ID)
        self.cog = cog

    async def callback(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        if interaction.message is None:
            await interaction.followup.send("❌ Vote message not found.", ephemeral=True)
            return

        status, ctx = await self.cog.repo.approve_submission(interaction.message.id, interaction.user.id)
        if status == "not_found":
            await interaction.followup.send("❌ No submission found for this vote message.", ephemeral=True)
            return
        if status == "already_done":
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
            await interaction.followup.send("⏳ This submission was already processed.", ephemeral=True)
            return
        if status == "riddle_closed":
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
            await interaction.followup.send("⚠️ This riddle is no longer open.", ephemeral=True)
            return
        if status != "ok" or not ctx:
            await interaction.followup.send("❌ Internal error.", ephemeral=True)
            return

        submitter_id = ctx["submitter_id"]
        submitter_mention, submitter_name, submitter_avatar = await self.cog.runtime.resolve_user_label(interaction.guild, submitter_id)

        clean_solution, more_link = extract_link(ctx.get("correct_solution") or "")
        solution_display = clean_solution or "*None*"
        if more_link:
            solution_display += f"\n🔗 [🧠**MORE**]({more_link})"

        solution_image = ctx.get("solution_url")
        if not is_http_url(solution_image):
            solution_image = DEFAULT_IMAGE_URL

        solved_embed = discord.Embed(
            title="🎉 Riddle Solved!",
            description=f"**{submitter_mention}** solved the riddle!",
            color=discord.Color.green()
        )
        if submitter_avatar:
            solved_embed.set_author(name=submitter_name, icon_url=submitter_avatar)
        else:
            solved_embed.set_author(name=submitter_name)

        solved_embed.add_field(name="🧩 Riddle", value=truncate_text(ctx.get("riddle_text") or "*Unknown*"), inline=False)
        solved_embed.add_field(name="🔍 Proposed Solution", value=ctx.get("user_answer") or "*None*", inline=False)
        solved_embed.add_field(name="✅ Correct Solution", value=solution_display, inline=False)
        solved_embed.add_field(name="🏆 Award", value=ctx.get("award") or "*None*", inline=False)
        solved_embed.set_image(url=solution_image)
        solved_embed.set_footer(text=footer_text(interaction.guild))

        await self.cog.runtime.mark_original_riddle_post_solved(
            ctx=ctx,
            solver_mention=submitter_mention,
            clean_solution=clean_solution or "*None*",
            more_link=more_link
        )

        await self.cog.runtime.cleanup_vote_messages_for_riddle(ctx["riddle_id"], exclude_submission_id=ctx["submission_id"])

        riddle_channel = await self.cog.runtime.resolve_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel and hasattr(riddle_channel, "send"):
            mentions = unique_role_mentions(interaction.guild, RIDDLE_ROLE_ID, ctx.get("button_role_id"))
            mentions.append(submitter_mention)
            content = " ".join(dict.fromkeys([m for m in mentions if m]))
            content = (content + "\n🎉 Congratulations!").strip()

            await riddle_channel.send(
                content=content,
                embed=solved_embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=True, everyone=False)
            )

        # XP reminder (einfach)
        try:
            xp_channel = await self.cog.runtime.resolve_channel(XP_NOTIFY_CHANNEL_ID)
            if xp_channel and hasattr(xp_channel, "send"):
                name_cmd, mention_cmd = build_xpadd_commands(submitter_mention, submitter_name, to_int(ctx.get("xp_gain"), 0))
                await xp_channel.send(
                    content=(
                        f"<@&{RIDDLE_MANAGER_ROLE_ID}> XP manuell vergeben:\n"
                        f"`{name_cmd}`\n"
                        f"Alternative:\n`{mention_cmd}`"
                    ),
                    allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
                )
        except Exception:
            pass

        # 12h cooldown setzen -> nächstes nur AUTO nach Zeit
        if interaction.guild:
            await self.cog.repo.set_auto_post_cooldown(interaction.guild.id, AUTO_POST_COOLDOWN_HOURS)
            await self.cog.repo.activate_next_from_pool(interaction.guild.id)

            await self.cog.runtime.publish_active_riddle(
                guild_id=interaction.guild.id,
                force_repost=False,
                extra_ping_role_id=None,
                allow_role_ping=True,
                ignore_cooldown=False
            )

        try:
            await interaction.message.delete()
        except Exception:
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass

        await interaction.followup.send("✅ Marked as correct.", ephemeral=True)


class VoteFailButton(discord.ui.Button):
    def __init__(self, cog: "RiddlePlayCog"):
        super().__init__(emoji="👎", style=discord.ButtonStyle.danger, custom_id=VOTE_DOWN_BUTTON_ID)
        self.cog = cog

    async def callback(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        if interaction.message is None:
            await interaction.followup.send("❌ Vote message not found.", ephemeral=True)
            return

        status, ctx = await self.cog.repo.reject_submission(interaction.message.id, interaction.user.id)
        if status == "not_found":
            await interaction.followup.send("❌ No submission found for this vote message.", ephemeral=True)
            return
        if status == "already_done":
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
            await interaction.followup.send("⏳ This submission was already processed.", ephemeral=True)
            return
        if status == "riddle_closed":
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
            await interaction.followup.send("⚠️ This riddle is no longer open.", ephemeral=True)
            return
        if status != "ok" or not ctx:
            await interaction.followup.send("❌ Internal error.", ephemeral=True)
            return

        submitter_id = ctx["submitter_id"]
        submitter_mention, submitter_name, submitter_avatar = await self.cog.runtime.resolve_user_label(interaction.guild, submitter_id)

        failed_embed = discord.Embed(
            title="❌ Riddle Not Solved!",
            description=f"**{submitter_mention}**'s solution was incorrect.",
            color=discord.Color.red()
        )
        if submitter_avatar:
            failed_embed.set_author(name=submitter_name, icon_url=submitter_avatar)
        else:
            failed_embed.set_author(name=submitter_name)

        failed_embed.add_field(name="🧩 Riddle", value=truncate_text(ctx.get("riddle_text") or "*Unknown*"), inline=False)
        failed_embed.add_field(name="🔍 Proposed Solution", value=ctx.get("user_answer") or "*None*", inline=False)
        failed_embed.add_field(name="❌ Result", value="*Better luck next time!*", inline=False)
        failed_embed.set_footer(text=footer_text(interaction.guild))

        riddle_channel = await self.cog.runtime.resolve_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel and hasattr(riddle_channel, "send"):
            mentions = unique_role_mentions(interaction.guild, RIDDLE_ROLE_ID, ctx.get("button_role_id"))
            mentions.append(submitter_mention)
            await riddle_channel.send(
                content=" ".join(dict.fromkeys([m for m in mentions if m])),
                embed=failed_embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=True, everyone=False)
            )

        try:
            await interaction.message.delete()
        except Exception:
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass

        await interaction.followup.send("✅ Marked as incorrect.", ephemeral=True)


class VoteButtons(LoggedPersistentView):
    def __init__(self, cog: "RiddlePlayCog"):
        super().__init__(timeout=None)
        self.add_item(VoteSuccessButton(cog))
        self.add_item(VoteFailButton(cog))


# =========================
# CHAMPIONS UI
# =========================
class ChampionsView(View):
    def __init__(
        self,
        *,
        entries: list[tuple[int, int, float, int]],
        total_solved: int,
        name_cache: dict[int, str],
        avatar_cache: dict[int, str],
        image_url: Optional[str] = None,
        owner_id: Optional[int] = None
    ):
        super().__init__(timeout=300)
        self.entries = entries
        self.total_solved = total_solved
        self.name_cache = name_cache
        self.avatar_cache = avatar_cache

        self.page = 0
        self.entries_per_page = 6
        self.max_page = max((len(entries) - 1) // self.entries_per_page, 0)

        self.page1_image_url = image_url if is_http_url(image_url) else DEFAULT_IMAGE_URL
        self.default_image_url = DEFAULT_IMAGE_URL
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None

        self._sync_buttons()

    def _sync_buttons(self):
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.max_page

    def _name(self, uid: int) -> str:
        return self.name_cache.get(uid, f"Unknown User ({uid})")

    def _avatar(self, uid: int) -> Optional[str]:
        return self.avatar_cache.get(uid)

    def build_embed(self) -> discord.Embed:
        start = self.page * self.entries_per_page
        end = start + self.entries_per_page
        page_entries = self.entries[start:end]

        embed = discord.Embed(
            title=f"🏆 Riddle Champions ⁉️ Total: 🧩 {self.total_solved}",
            description=f"Page {self.page + 1}/{self.max_page + 1}",
            color=discord.Color.gold()
        )

        if self.entries:
            top_uid = self.entries[0][0]
            top_name = self._name(top_uid)
            top_avatar = self._avatar(top_uid)
            if top_avatar:
                embed.set_author(name=f"👑 Riddle Master #1: {top_name}", icon_url=top_avatar)
                embed.set_thumbnail(url=top_avatar)
            else:
                embed.set_author(name=f"👑 Riddle Master #1: {top_name}")

        if not page_entries:
            embed.add_field(name="No data yet", value="No riddles solved yet.", inline=False)
        else:
            for i, (uid, solved, percent, xp) in enumerate(page_entries, start=start + 1):
                embed.add_field(
                    name=f"🎖️ {i}. {self._name(uid)}",
                    value=f"🧩 {solved} | 📊 {percent:.1f}% | 🧠 {xp} XP",
                    inline=False
                )

        embed.set_image(url=self.page1_image_url if self.page == 0 else self.default_image_url)
        return embed

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await interaction.response.send_message("🚫 This menu is not yours.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: Interaction, _: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: Interaction, _: discord.ui.Button):
        if self.page < self.max_page:
            self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


# =========================
# ADMIN COG
# =========================
class RiddleAdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot, repo: RiddleRepo, runtime: RiddleRuntime):
        self.bot = bot
        self.repo = repo
        self.runtime = runtime

    def build_slots_overview_embed(self, guild: discord.Guild, slot_map: dict[int, dict]) -> discord.Embed:
        active_slot = None
        for s, row in slot_map.items():
            if to_int(row.get("is_active"), 0) == 1:
                active_slot = s
                break

        embed = discord.Embed(
            title="🗂️ Riddle Pool Slots",
            description="Manage up to 5 slots. Active slot is posted in riddle channel.",
            color=discord.Color.blurple()
        )

        for slot in range(1, MAX_RIDDLE_SLOTS + 1):
            row = slot_map.get(slot)
            if not row:
                embed.add_field(name=f"Slot {slot}", value="`EMPTY`", inline=False)
                continue

            r_no = to_int(row.get("riddle_no"), to_int(row.get("id"), 0))
            award = row.get("award") or "None"
            text_preview = truncate_text(row.get("text") or "", 120) or "*No text*"
            active_tag = "🟢 ACTIVE" if active_slot == slot else "⚪ queued"

            embed.add_field(
                name=f"Slot {slot} • {active_tag}",
                value=f"Riddle #{r_no}\nAward: {award}\n{text_preview}",
                inline=False
            )

        embed.set_footer(text=footer_text(guild))
        return embed

    @app_commands.command(name="riddle", description="Create or edit a riddle in a specific pool slot.")
    @app_commands.describe(slot="Pool slot number (1-5)", mention="Optional role for this riddle")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle(
        self,
        interaction: Interaction,
        slot: app_commands.Range[int, 1, MAX_RIDDLE_SLOTS],
        mention: Optional[Role] = None
    ):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return

        current = await self.repo.get_open_slot_riddle(interaction.guild.id, int(slot))
        modal = RiddleUpsertModal(
            cog=self,
            current_data=current,
            mention_override_id=(mention.id if mention else None),
            slot_no=int(slot)
        )
        await interaction.response.send_modal(modal)

    @app_commands.command(name="riddle_slots", description="Show all 5 pool slots and edit each slot.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_slots(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("❌ Server only.", ephemeral=True)
            return

        slot_map = await self.repo.open_slot_map(interaction.guild.id)
        embed = self.build_slots_overview_embed(interaction.guild, slot_map)
        view = SlotManagerView(cog=self, owner_id=interaction.user.id, slot_map=slot_map)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="riddle_post", description="Force repost active riddle (ignores 12h cooldown).")
    @app_commands.describe(ping_role="Optional extra role to ping")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_post(self, interaction: Interaction, ping_role: Optional[Role] = None):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("❌ Server only.", ephemeral=True)
            return

        await self.repo.activate_next_from_pool(interaction.guild.id)
        result = await self.runtime.publish_active_riddle(
            guild_id=interaction.guild.id,
            force_repost=True,
            extra_ping_role_id=(ping_role.id if ping_role else None),
            allow_role_ping=True,
            ignore_cooldown=True  # manuell -> cooldown ignorieren
        )

        if result == "no_active":
            await interaction.followup.send("⚠️ No open slot available. Nothing to post.", ephemeral=True)
            return
        if result == "no_channel":
            await interaction.followup.send("❌ Riddle channel not found.", ephemeral=True)
            return
        if result in ("posted", "updated"):
            await interaction.followup.send("✅ Active riddle reposted.", ephemeral=True)
            return
        await interaction.followup.send("❌ Posting failed.", ephemeral=True)

    @app_commands.command(name="riddle_close", description="Close active riddle (unsolved), cooldown starts.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_close(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("❌ Server only.", ephemeral=True)
            return

        riddle = await self.repo.close_active_riddle(interaction.guild.id, interaction.user.id)
        if not riddle:
            await interaction.followup.send("❌ No active riddle to close.", ephemeral=True)
            return

        await self.runtime.cleanup_vote_messages_for_riddle(riddle["id"])

        clean_solution, link = extract_link(riddle.get("solution") or "")
        solution_display = clean_solution or "*None*"
        if link:
            solution_display += f"\n🔗 [🧠**MORE**]({link})"

        solution_url = riddle.get("solution_url")
        if not is_http_url(solution_url):
            solution_url = DEFAULT_IMAGE_URL

        embed = discord.Embed(
            title="🔒 Riddle Closed",
            description="Nobody solved the riddle in time.",
            color=discord.Color.red()
        )
        embed.add_field(name="🧩 Riddle", value=riddle.get("text") or "*Unknown*", inline=False)
        embed.add_field(name="✅ Correct Solution", value=solution_display, inline=False)
        embed.add_field(name="🏆 Award", value=riddle.get("award") or "*None*", inline=False)
        embed.set_image(url=solution_url)
        embed.set_footer(text=footer_text(interaction.guild))

        riddle_channel = await self.runtime.resolve_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel and hasattr(riddle_channel, "send"):
            mentions = unique_role_mentions(interaction.guild, RIDDLE_ROLE_ID, riddle.get("button_role_id"))
            await riddle_channel.send(
                content=" ".join(dict.fromkeys([m for m in mentions if m])) or None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
            )

        await self.runtime.update_original_post(
            {
                "posted_channel_id": riddle.get("posted_channel_id"),
                "posted_message_id": riddle.get("posted_message_id"),
                "riddle_text": riddle.get("text") or "*Unknown*"
            },
            "🔒 Closed",
            f"Nobody solved it.\n{(clean_solution or '*None*').splitlines()[0]}"
        )

        # cooldown setzen + nächstes aktivieren (auto-post blockiert bis ablauf)
        await self.repo.set_auto_post_cooldown(interaction.guild.id, AUTO_POST_COOLDOWN_HOURS)
        await self.repo.activate_next_from_pool(interaction.guild.id)

        _ = await self.runtime.publish_active_riddle(
            guild_id=interaction.guild.id,
            force_repost=False,
            extra_ping_role_id=None,
            allow_role_ping=True,
            ignore_cooldown=False
        )

        blocked, unlock_at = await self.repo.is_auto_post_blocked(interaction.guild.id)
        if blocked and unlock_at:
            ts = discord.utils.format_dt(unlock_at, style="F")
            await interaction.followup.send(f"✅ Closed. Next auto-post is allowed at {ts}.", ephemeral=True)
        else:
            await interaction.followup.send("✅ Closed.", ephemeral=True)

    async def cog_app_command_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, MissingRiddleManagerRole):
            await send_access_denied(interaction)
            return
        logger.exception("Admin command error: %s", error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Command error.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Command error.", ephemeral=True)
        except Exception:
            pass


# =========================
# PLAY COG
# =========================
class RiddlePlayCog(commands.Cog):
    def __init__(self, bot: commands.Bot, repo: RiddleRepo, runtime: RiddleRuntime):
        self.bot = bot
        self.repo = repo
        self.runtime = runtime
        self._autopost_task: Optional[asyncio.Task] = None

    def submit_view(self):
        return SubmitButtonView(self)

    async def cog_load(self):
        self.bot.add_view(self.submit_view())
        self.bot.add_view(VoteButtons(self))

        if self._autopost_task is None or self._autopost_task.done():
            self._autopost_task = asyncio.create_task(self._auto_post_worker(), name="riddle_auto_post_worker")

    def cog_unload(self):
        if self._autopost_task and not self._autopost_task.done():
            self._autopost_task.cancel()

    async def _auto_post_worker(self):
        await asyncio.sleep(5)
        while True:
            try:
                guild_ids = await self.repo.guild_ids_with_open_riddles()
                for gid in guild_ids:
                    await self.repo.activate_next_from_pool(gid)
                    await self.runtime.publish_active_riddle(
                        guild_id=gid,
                        force_repost=False,
                        extra_ping_role_id=None,
                        allow_role_ping=True,
                        ignore_cooldown=False  # AUTO beachtet cooldown
                    )
            except Exception as e:
                logger.exception("auto-post worker error: %s", e)

            await asyncio.sleep(max(10, AUTO_POST_SCAN_SECONDS))

    @app_commands.command(name="riddle_champ", description="Show riddle champions leaderboard.")
    @app_commands.describe(
        visible="If true, post publicly. If false, only visible to you.",
        image="Optional custom image URL for page 1.",
        mention="Optional role mention (used only when visible=true)."
    )
    @app_commands.guild_only()
    async def riddle_champ(
        self,
        interaction: Interaction,
        visible: Optional[bool] = False,
        image: Optional[str] = None,
        mention: Optional[Role] = None
    ):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=not visible, thinking=True)

        entries_raw = await self.repo.stats_entries(interaction.guild.id)
        total_solved = sum(s for _, s, _ in entries_raw)

        entries: list[tuple[int, int, float, int]] = [
            (uid, solved, (solved / total_solved * 100.0 if total_solved else 0.0), xp)
            for uid, solved, xp in entries_raw
        ]

        name_cache: dict[int, str] = {}
        avatar_cache: dict[int, str] = {}

        for uid, _, _ in entries_raw:
            _, name, avatar = await self.runtime.resolve_user_label(interaction.guild, uid)
            name_cache[uid] = name
            if avatar:
                avatar_cache[uid] = avatar

        view = ChampionsView(
            entries=entries,
            total_solved=total_solved,
            name_cache=name_cache,
            avatar_cache=avatar_cache,
            image_url=image if is_http_url(image) else DEFAULT_IMAGE_URL,
            owner_id=(interaction.user.id if not visible else None)
        )

        sent = await interaction.followup.send(
            content=mention.mention if (visible and mention) else None,
            embed=view.build_embed(),
            view=view,
            ephemeral=not visible,
            wait=True,
            allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
        )
        view.message = sent

    async def cog_app_command_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        logger.exception("Play command error: %s", error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Command error.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Command error.", ephemeral=True)
        except Exception:
            pass


# =========================
# SETUP / TEARDOWN
# =========================
_repo: Optional[RiddleRepo] = None

async def setup(bot: commands.Bot):
    global _repo
    _repo = RiddleRepo()
    await _repo.start()

    runtime = RiddleRuntime(bot, _repo)
    play = RiddlePlayCog(bot, _repo, runtime)
    runtime.set_submit_view_factory(play.submit_view)
    admin = RiddleAdminCog(bot, _repo, runtime)

    await bot.add_cog(play)
    await bot.add_cog(admin)

    logger.info("Riddle extension loaded (Admin+Play, single file).")

async def teardown(bot: commands.Bot):
    global _repo
    if _repo is not None:
        await _repo.close()
        _repo = None