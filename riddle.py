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

# 12h => 2x täglich
AUTO_ENABLE_SCAN_SECONDS = env_int("RIDDLE_AUTO_ENABLE_SCAN_SECONDS", 43200)
NEXT_AUTO_ENABLE_HOURS = env_int("RIDDLE_AUTO_POST_COOLDOWN_HOURS", 12)

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


def truncate_text(text: str, max_length: int = 200) -> str:
    if text and len(text) > max_length:
        return text[:max_length] + "[...]"
    return text or ""


def extract_link(text: str) -> tuple[str, Optional[str]]:
    text = text or ""
    m = re.search(r"(https?://\S+)", text)
    if not m:
        return text.strip(), None
    link = m.group(1)
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
    return f'/xp add "{safe_name}" {xp}', f"/xp add {member_mention} {xp}"


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
        title="🔒 Zugriff eingeschränkt",
        description=f"Nur <@&{RIDDLE_MANAGER_ROLE_ID}> darf diesen Befehl benutzen.",
        color=discord.Color.orange(),
    )
    if is_http_url(ACCESS_DENIED_IMAGE_URL):
        embed.set_image(url=ACCESS_DENIED_IMAGE_URL)
    embed.set_footer(text="Riddle Manager benötigt")

    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================
# REPO (DB)
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
        if self.db:
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

        CREATE UNIQUE INDEX IF NOT EXISTS idx_riddle_posted_msg
        ON riddles(posted_message_id) WHERE posted_message_id IS NOT NULL;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_riddle_slot_open
        ON riddles(guild_id, slot_no) WHERE status='open' AND slot_no IS NOT NULL;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_riddle_active_one
        ON riddles(guild_id) WHERE status='open' AND is_active=1;

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
            is_enabled INTEGER NOT NULL DEFAULT 0 CHECK(is_enabled IN (0,1)),
            next_auto_enable_at TEXT,
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
                    WHERE r2.guild_id = r.guild_id AND r2.id <= r.id
                )
                WHERE r.riddle_no IS NULL
                """
            )

            # migration guard
            cur = await self.db.execute("PRAGMA table_info(guild_riddle_state)")
            cols = [row["name"] for row in await cur.fetchall()]
            await cur.close()
            if "is_enabled" not in cols:
                await self.db.execute("ALTER TABLE guild_riddle_state ADD COLUMN is_enabled INTEGER NOT NULL DEFAULT 0")

            await self._repair_pool_integrity_locked()
            await self.db.commit()

    async def _repair_pool_integrity_locked(self):
        assert self.db is not None
        now = now_iso_utc()

        cur = await self.db.execute("SELECT DISTINCT guild_id FROM riddles WHERE status='open'")
        gids = [to_int(r["guild_id"], 0) for r in await cur.fetchall()]
        await cur.close()

        for gid in [g for g in gids if g > 0]:
            cur = await self.db.execute(
                """
                SELECT * FROM riddles
                WHERE guild_id=? AND status='open'
                ORDER BY
                  CASE WHEN slot_no BETWEEN 1 AND ? THEN slot_no ELSE 9999 END ASC,
                  id ASC
                """,
                (gid, MAX_RIDDLE_SLOTS)
            )
            rows = [dict(r) for r in await cur.fetchall()]
            await cur.close()

            await self.db.execute("UPDATE riddles SET is_active=0, updated_at=? WHERE guild_id=? AND status='open'", (now, gid))

            used = set()
            overflow = []
            for row in rows:
                rid = to_int(row.get("id"), 0)
                slot = to_int(row.get("slot_no"), 0)
                if 1 <= slot <= MAX_RIDDLE_SLOTS and slot not in used:
                    used.add(slot)
                    continue
                free = next((s for s in range(1, MAX_RIDDLE_SLOTS + 1) if s not in used), None)
                if free is None:
                    overflow.append(rid)
                else:
                    await self.db.execute("UPDATE riddles SET slot_no=?, updated_at=? WHERE id=?", (free, now, rid))
                    used.add(free)

            for rid in overflow:
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

            await self.db.execute(
                """
                UPDATE riddles SET is_active=1
                WHERE id IN (
                    SELECT id FROM riddles
                    WHERE guild_id=? AND status='open' AND slot_no=1
                    LIMIT 1
                )
                """,
                (gid,)
            )

    async def _fetchone(self, q: str, p: tuple = ()) -> Optional[dict]:
        if self.db is None:
            return None
        async with self.db_lock:
            cur = await self.db.execute(q, p)
            row = await cur.fetchone()
            await cur.close()
        return dict(row) if row else None

    async def _fetchall(self, q: str, p: tuple = ()) -> list[dict]:
        if self.db is None:
            return []
        async with self.db_lock:
            cur = await self.db.execute(q, p)
            rows = await cur.fetchall()
            await cur.close()
        return [dict(r) for r in rows]

    async def _execute(self, q: str, p: tuple = ()) -> tuple[int, int]:
        if self.db is None:
            return 0, 0
        async with self.db_lock:
            cur = await self.db.execute(q, p)
            await self.db.commit()
            rc = cur.rowcount
            lid = int(cur.lastrowid or 0)
            await cur.close()
        return rc, lid

    # -------- state --------
    async def ensure_guild_state(self, guild_id: int):
        await self._execute(
            """
            INSERT INTO guild_riddle_state (guild_id, is_enabled, next_auto_enable_at, updated_at)
            VALUES (?, 0, NULL, ?)
            ON CONFLICT(guild_id) DO NOTHING
            """,
            (guild_id, now_iso_utc())
        )

    async def set_enabled(self, guild_id: int, enabled: bool):
        await self._execute(
            """
            INSERT INTO guild_riddle_state (guild_id, is_enabled, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                is_enabled=excluded.is_enabled,
                updated_at=excluded.updated_at
            """,
            (guild_id, 1 if enabled else 0, now_iso_utc())
        )

    async def is_enabled(self, guild_id: int) -> bool:
        row = await self._fetchone("SELECT is_enabled FROM guild_riddle_state WHERE guild_id=? LIMIT 1", (guild_id,))
        return bool(to_int(row["is_enabled"], 0)) if row else False

    async def set_next_auto_enable(self, guild_id: int, hours: int):
        nxt = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)
        await self._execute(
            """
            INSERT INTO guild_riddle_state (guild_id, is_enabled, next_auto_enable_at, updated_at)
            VALUES (?, 0, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                next_auto_enable_at=excluded.next_auto_enable_at,
                updated_at=excluded.updated_at
            """,
            (guild_id, to_iso_z(nxt), now_iso_utc())
        )

    async def clear_next_auto_enable(self, guild_id: int):
        await self._execute(
            """
            INSERT INTO guild_riddle_state (guild_id, is_enabled, next_auto_enable_at, updated_at)
            VALUES (?, 1, NULL, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                next_auto_enable_at=NULL,
                updated_at=excluded.updated_at
            """,
            (guild_id, now_iso_utc())
        )

    async def get_next_auto_enable(self, guild_id: int) -> Optional[dt.datetime]:
        row = await self._fetchone("SELECT next_auto_enable_at FROM guild_riddle_state WHERE guild_id=? LIMIT 1", (guild_id,))
        return parse_iso_utc(row.get("next_auto_enable_at")) if row else None

    async def is_auto_enable_due(self, guild_id: int) -> bool:
        nxt = await self.get_next_auto_enable(guild_id)
        return True if nxt is None else dt.datetime.now(dt.timezone.utc) >= nxt

    async def get_state_row(self, guild_id: int) -> dict:
        row = await self._fetchone("SELECT * FROM guild_riddle_state WHERE guild_id=? LIMIT 1", (guild_id,))
        return row or {"guild_id": guild_id, "is_enabled": 0, "next_auto_enable_at": None, "updated_at": now_iso_utc()}

    async def list_all_guild_ids(self) -> list[int]:
        rows = await self._fetchall(
            """
            SELECT guild_id FROM guild_riddle_state
            UNION
            SELECT DISTINCT guild_id FROM riddles WHERE status='open'
            """
        )
        return [to_int(r["guild_id"], 0) for r in rows if to_int(r["guild_id"], 0) > 0]

    # -------- riddles / slots --------
    async def open_slot_map(self, guild_id: int) -> dict[int, dict]:
        rows = await self._fetchall(
            """
            SELECT * FROM riddles
            WHERE guild_id=? AND status='open' AND slot_no BETWEEN 1 AND ?
            ORDER BY slot_no ASC
            """,
            (guild_id, MAX_RIDDLE_SLOTS)
        )
        out: dict[int, dict] = {}
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

    async def get_open_slot1(self, guild_id: int) -> Optional[dict]:
        return await self.get_open_slot_riddle(guild_id, 1)

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

                await self.db.execute("UPDATE riddles SET is_active=0 WHERE guild_id=? AND status='open'", (guild_id,))
                await self.db.execute("UPDATE riddles SET is_active=1 WHERE guild_id=? AND status='open' AND slot_no=1", (guild_id,))
                await self.db.commit()
                return rid if rid > 0 else None
            except Exception:
                await self.db.rollback()
                raise

    async def shift_open_slots_left(self, guild_id: int):
        """
        Leere Lücken schließen:
        5->1, 4->1 etc., bis Slot 1 gefüllt ist (falls überhaupt offene Rätsel existieren).
        """
        if self.db is None:
            return
        async with self.db_lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                now = now_iso_utc()

                cur = await self.db.execute(
                    """
                    SELECT id
                    FROM riddles
                    WHERE guild_id=? AND status='open'
                    ORDER BY
                      CASE WHEN slot_no BETWEEN 1 AND ? THEN slot_no ELSE 9999 END ASC,
                      id ASC
                    """,
                    (guild_id, MAX_RIDDLE_SLOTS)
                )
                rows = await cur.fetchall()
                await cur.close()

                await self.db.execute(
                    "UPDATE riddles SET slot_no=NULL, is_active=0, updated_at=? WHERE guild_id=? AND status='open'",
                    (now, guild_id)
                )

                for i, row in enumerate(rows, start=1):
                    rid = to_int(row["id"], 0)
                    if i <= MAX_RIDDLE_SLOTS:
                        await self.db.execute(
                            "UPDATE riddles SET slot_no=?, is_active=?, updated_at=? WHERE id=?",
                            (i, 1 if i == 1 else 0, now, rid)
                        )
                    else:
                        await self.db.execute(
                            "UPDATE riddles SET status='closed', closed_by=0, closed_at=?, updated_at=? WHERE id=?",
                            (now, now, rid)
                        )
                        await self.db.execute(
                            "UPDATE submissions SET status='cancelled', voted_by=0, voted_at=? WHERE riddle_id=? AND status='pending'",
                            (now, rid)
                        )

                await self.db.commit()
            except Exception:
                await self.db.rollback()
                raise

    async def close_slot1_unsolved(self, guild_id: int, closed_by: int) -> Optional[dict]:
        if self.db is None:
            return None
        async with self.db_lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    "SELECT * FROM riddles WHERE guild_id=? AND status='open' AND slot_no=1 LIMIT 1",
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

    async def set_riddle_post_ref(self, riddle_id: int, channel_id: int, message_id: int):
        await self._execute(
            "UPDATE riddles SET posted_channel_id=?, posted_message_id=?, updated_at=? WHERE id=?",
            (channel_id, message_id, now_iso_utc(), riddle_id)
        )

    async def clear_all_open_post_refs(self, guild_id: Optional[int] = None):
        if guild_id is None:
            await self._execute(
                "UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL, updated_at=? WHERE status='open'",
                (now_iso_utc(),)
            )
        else:
            await self._execute(
                "UPDATE riddles SET posted_channel_id=NULL, posted_message_id=NULL, updated_at=? WHERE guild_id=? AND status='open'",
                (now_iso_utc(), guild_id)
            )

    async def clear_other_open_post_refs(self, guild_id: int, keep_riddle_id: int):
        await self._execute(
            """
            UPDATE riddles
            SET posted_channel_id=NULL, posted_message_id=NULL, updated_at=?
            WHERE guild_id=? AND status='open' AND id<>?
            """,
            (now_iso_utc(), guild_id, keep_riddle_id)
        )

    # -------- submissions --------
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
        rc, _ = await self._execute("UPDATE submissions SET vote_message_id=? WHERE id=?", (vote_message_id, submission_id))
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

                if ctx["riddle_status"] != "open":
                    await self.db.execute(
                        "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? WHERE id=? AND status='pending'",
                        (moderator_id, now_iso_utc(), ctx["submission_id"])
                    )
                    await self.db.commit()
                    return "riddle_closed", ctx

                now = now_iso_utc()
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
                    DO UPDATE SET solved_riddles = solved_riddles + 1, xp = xp + excluded.xp
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

                if ctx["riddle_status"] != "open":
                    await self.db.execute(
                        "UPDATE submissions SET status='cancelled', voted_by=?, voted_at=? WHERE id=? AND status='pending'",
                        (moderator_id, now_iso_utc(), ctx["submission_id"])
                    )
                    await self.db.commit()
                    return "riddle_closed", ctx

                cur = await self.db.execute(
                    "UPDATE submissions SET status='wrong', voted_by=?, voted_at=? WHERE id=? AND status='pending'",
                    (moderator_id, now_iso_utc(), ctx["submission_id"])
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

    async def reset_pending_vote_refs(self):
        await self._execute("UPDATE submissions SET vote_message_id=NULL WHERE status='pending'")

    async def cancel_pending_for_non_open(self):
        await self._execute(
            """
            UPDATE submissions
            SET status='cancelled', voted_by=0, voted_at=?
            WHERE status='pending'
              AND riddle_id IN (SELECT id FROM riddles WHERE status <> 'open')
            """,
            (now_iso_utc(),)
        )

    async def pending_open_submissions(self) -> list[dict]:
        return await self._fetchall(
            """
            SELECT
                s.id AS submission_id,
                s.guild_id AS guild_id,
                s.user_id AS user_id,
                s.answer AS answer,
                r.id AS riddle_id,
                r.text AS riddle_text,
                r.solution AS solution,
                r.award AS award,
                r.button_role_id AS button_role_id
            FROM submissions s
            JOIN riddles r ON r.id = s.riddle_id
            WHERE s.status='pending' AND r.status='open'
            ORDER BY s.id ASC
            """
        )

    # -------- stats --------
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
            out.append((uid, max(0, to_int(r.get("solved_riddles"), 0)), max(0, to_int(r.get("xp"), 0))))
        return out


# =========================
# RUNTIME
# =========================
class RiddleRuntime:
    def __init__(self, bot: commands.Bot, repo: RiddleRepo):
        self.bot = bot
        self.repo = repo
        self.submit_view_factory = None

    def set_submit_view_factory(self, fn):
        self.submit_view_factory = fn

    def submit_view(self):
        return self.submit_view_factory() if self.submit_view_factory else None

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

    def _message_has_custom_id(self, msg: discord.Message, custom_ids: set[str]) -> bool:
        try:
            for row in (msg.components or []):
                for child in getattr(row, "children", []):
                    if getattr(child, "custom_id", None) in custom_ids:
                        return True
        except Exception:
            pass
        return False

    async def delete_button_messages_in_channel(self, channel_id: int, custom_ids: set[str], limit: int = 500):
        ch = await self.resolve_channel(channel_id)
        if ch is None or not hasattr(ch, "history"):
            return
        me = self.bot.user
        if me is None:
            return
        async for msg in ch.history(limit=limit):
            if msg.author.id != me.id:
                continue
            if self._message_has_custom_id(msg, custom_ids):
                try:
                    await msg.delete()
                except Exception:
                    pass

    async def remove_active_riddle_posts(self, guild_id: int):
        # nur Submit-Posts löschen (Solved/Closed bleiben)
        await self.delete_button_messages_in_channel(RIDDLE_CHANNEL_ID, {SUBMIT_BUTTON_ID}, limit=600)
        await self.repo.clear_all_open_post_refs(guild_id)

    def build_riddle_embed(self, guild: Optional[discord.Guild], riddle: dict) -> discord.Embed:
        image_url = riddle.get("image_url")
        if not is_http_url(image_url):
            image_url = DEFAULT_IMAGE_URL

        riddle_no = to_int(riddle.get("riddle_no"), to_int(riddle.get("id"), 1))
        slot_no = to_int(riddle.get("slot_no"), 1)

        embed = discord.Embed(
            title=f"🧩 Ms Pepper's Goon Hut Riddle • #{riddle_no}",
            description=riddle.get("text") or "*Kein Rätseltext gesetzt*",
            color=discord.Color.blurple()
        )
        embed.add_field(name="🏆 Award", value=riddle.get("award") or "*Keine Angabe*", inline=False)
        embed.add_field(name="📦 Slot", value=str(slot_no), inline=True)
        embed.add_field(name="💡 Teilnahme", value="Button drücken und Lösung einreichen.", inline=True)
        if is_http_url(image_url):
            embed.set_image(url=image_url)
        embed.set_footer(text=footer_text(guild))
        return embed

    async def publish_slot1_post(self, guild_id: int, *, force_repost: bool, allow_role_ping: bool, extra_ping_role_id: Optional[int] = None) -> str:
        slot1 = await self.repo.get_open_slot1(guild_id)
        if not slot1:
            return "no_slot1"

        guild = self.bot.get_guild(guild_id)
        channel = await self.resolve_channel(RIDDLE_CHANNEL_ID)
        if channel is None or not hasattr(channel, "send"):
            return "no_channel"

        submit_view = self.submit_view()
        if submit_view is None:
            return "no_view"

        embed = self.build_riddle_embed(guild, slot1)
        mentions = unique_role_mentions(guild, RIDDLE_ROLE_ID, slot1.get("button_role_id"), extra_ping_role_id)
        content = " ".join(dict.fromkeys([m for m in mentions if m])) or None

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
                    view=submit_view,
                    allowed_mentions=discord.AllowedMentions(roles=allow_role_ping, users=False, everyone=False)
                )
                await self.repo.clear_other_open_post_refs(guild_id, to_int(slot1["id"], 0))
                return "updated"

            msg = await channel.send(
                content=content,
                embed=embed,
                view=submit_view,
                allowed_mentions=discord.AllowedMentions(roles=allow_role_ping, users=False, everyone=False)
            )
            await self.repo.set_riddle_post_ref(to_int(slot1["id"], 0), msg.channel.id, msg.id)
            await self.repo.clear_other_open_post_refs(guild_id, to_int(slot1["id"], 0))
            return "posted"
        except Exception as e:
            logger.exception("publish_slot1_post failed: %s", e)
            return "error"

    async def enforce_enabled_state(self, guild_id: int, *, allow_ping: bool, force_repost: bool = False) -> str:
        # WICHTIG: erst Slots aufrücken (damit 5->1 etc.)
        await self.repo.shift_open_slots_left(guild_id)

        enabled = await self.repo.is_enabled(guild_id)
        slot1 = await self.repo.get_open_slot1(guild_id)

        if not enabled:
            await self.remove_active_riddle_posts(guild_id)
            return "disabled"

        if not slot1:
            await self.repo.set_enabled(guild_id, False)
            await self.remove_active_riddle_posts(guild_id)
            return "enabled_but_no_slot1"

        return await self.publish_slot1_post(
            guild_id,
            force_repost=force_repost,
            allow_role_ping=allow_ping
        )

    async def update_original_post_result(self, ctx: dict, field_name: str, field_value: str):
        msg = await self.fetch_message_safe(ctx.get("posted_channel_id"), ctx.get("posted_message_id"))
        if not msg:
            return

        if msg.embeds:
            embed = discord.Embed.from_dict(msg.embeds[0].to_dict())
        else:
            embed = discord.Embed(
                title="🧩 Rätsel",
                description=ctx.get("riddle_text") or "*Unknown*",
                color=discord.Color.blurple()
            )
        embed.add_field(name=field_name, value=field_value, inline=False)
        embed.set_footer(text=footer_text(msg.guild))
        try:
            await msg.edit(embed=embed, view=None)
        except Exception:
            pass

    async def cleanup_vote_messages_for_riddle(self, riddle_id: int, exclude_submission_id: Optional[int] = None):
        rows = await self.repo.list_vote_messages_for_riddle(riddle_id)
        if not rows:
            return

        vote_ch = await self.resolve_channel(VOTE_CHANNEL_ID)
        if vote_ch is None or not hasattr(vote_ch, "fetch_message"):
            return

        for row in rows:
            sid = to_int(row.get("id"), 0)
            if exclude_submission_id is not None and sid == exclude_submission_id:
                continue
            mid = to_int(row.get("vote_message_id"), 0)
            if mid <= 0:
                continue
            try:
                msg = await vote_ch.fetch_message(mid)
                await msg.delete()
            except Exception:
                pass

    async def repost_pending_votes(self, play_cog: "RiddlePlayCog"):
        rows = await self.repo.pending_open_submissions()
        if not rows:
            return
        vote_ch = await self.resolve_channel(VOTE_CHANNEL_ID)
        if vote_ch is None or not hasattr(vote_ch, "send"):
            return

        for row in rows:
            guild = self.bot.get_guild(to_int(row.get("guild_id"), 0))
            uid = to_int(row.get("user_id"), 0)
            _, name, avatar = await self.resolve_user_label(guild, uid)

            embed = discord.Embed(
                title="📜 Neue Lösung eingereicht",
                description=row.get("riddle_text") or "*No text*",
                color=discord.Color.gold()
            )
            if avatar:
                embed.set_author(name=name, icon_url=avatar)
            else:
                embed.set_author(name=name)
            embed.add_field(name="🧠 Antwort", value=row.get("answer") or "*Leer*", inline=False)
            embed.add_field(name="✅ Korrekte Lösung", value=row.get("solution") or "*Nicht gesetzt*", inline=False)
            embed.add_field(name="🏆 Award", value=row.get("award") or "*Keine*", inline=False)
            embed.add_field(name="🆔 User ID", value=str(uid), inline=False)
            embed.set_footer(text=footer_text(guild))

            try:
                vm = await vote_ch.send(embed=embed, view=VoteButtons(play_cog))
                await self.repo.set_submission_vote_message(to_int(row["submission_id"], 0), vm.id)
            except Exception:
                pass

    async def startup_rebuild(self, play_cog: "RiddlePlayCog"):
        logger.info("Riddle startup rebuild started...")

        await self.delete_button_messages_in_channel(RIDDLE_CHANNEL_ID, {SUBMIT_BUTTON_ID}, limit=800)
        await self.delete_button_messages_in_channel(VOTE_CHANNEL_ID, {VOTE_UP_BUTTON_ID, VOTE_DOWN_BUTTON_ID}, limit=1200)

        await self.repo.clear_all_open_post_refs(None)
        await self.repo.reset_pending_vote_refs()
        await self.repo.cancel_pending_for_non_open()

        guild_ids = await self.repo.list_all_guild_ids()
        for gid in guild_ids:
            await self.repo.ensure_guild_state(gid)
            # beim Neustart leere Slots hochziehen
            await self.repo.shift_open_slots_left(gid)
            # ON/OFF durchsetzen
            await self.enforce_enabled_state(gid, allow_ping=False, force_repost=True)

        await self.repost_pending_votes(play_cog)
        logger.info("Riddle startup rebuild finished.")


# =========================
# VIEWS + MODALS
# =========================
class LoggedPersistentView(View):
    async def on_error(self, interaction: Interaction, error: Exception, item: discord.ui.Item[Any]):
        logger.exception("View error: %s", error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Button-Fehler.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Button-Fehler.", ephemeral=True)
        except Exception:
            pass


class RiddleUpsertModal(Modal):
    def __init__(self, cog: "RiddleAdminCog", slot_no: int, current: Optional[dict], mention_override_id: Optional[int]):
        has_data = bool(current and clean_value(current.get("text")) and clean_value(current.get("solution")))
        super().__init__(title=f"Slot {slot_no} bearbeiten" if has_data else f"Slot {slot_no} erstellen")
        self.cog = cog
        self.slot_no = slot_no
        self.current = current or {}
        self.mention_override_id = mention_override_id

        self.text = TextInput(label="Rätseltext", style=discord.TextStyle.paragraph, default=self.current.get("text") or "", required=True, max_length=4000)
        self.solution = TextInput(label="Lösung", style=discord.TextStyle.paragraph, default=self.current.get("solution") or "", required=True, max_length=4000)
        self.award = TextInput(label="Award", default=self.current.get("award") or "", required=False, max_length=200)
        self.image_url = TextInput(label="Bild URL", default=self.current.get("image_url") or "", required=False, max_length=1000)
        self.solution_url = TextInput(label="Lösungsbild URL", default=self.current.get("solution_url") or "", required=False, max_length=1000)

        self.add_item(self.text)
        self.add_item(self.solution)
        self.add_item(self.award)
        self.add_item(self.image_url)
        self.add_item(self.solution_url)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None:
            await interaction.followup.send("❌ Nur im Server nutzbar.", ephemeral=True)
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
            await interaction.followup.send("❌ Speichern fehlgeschlagen.", ephemeral=True)
            return

        await self.cog.repo.ensure_guild_state(interaction.guild.id)
        await self.cog.repo.shift_open_slots_left(interaction.guild.id)

        enabled = await self.cog.repo.is_enabled(interaction.guild.id)
        if enabled:
            await self.cog.runtime.enforce_enabled_state(interaction.guild.id, allow_ping=False, force_repost=False)

        await interaction.followup.send(f"✅ Slot {self.slot_no} gespeichert.", ephemeral=True)


class SubmitSolutionModal(Modal):
    def __init__(self, cog: "RiddlePlayCog", riddle_id: int):
        super().__init__(title="💡 Lösung einreichen")
        self.cog = cog
        self.riddle_id = riddle_id
        self.solution = TextInput(label="Deine Antwort", style=discord.TextStyle.paragraph, required=True, max_length=4000)
        self.add_item(self.solution)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("❌ Nur im Server.", ephemeral=True)
            return

        riddle = await self.cog.repo.get_open_riddle_by_id(interaction.guild.id, self.riddle_id)
        if not riddle:
            await interaction.followup.send("❌ Kein aktives Rätsel gefunden.", ephemeral=True)
            return

        vote_channel = await self.cog.runtime.resolve_channel(VOTE_CHANNEL_ID)
        if vote_channel is None or not hasattr(vote_channel, "send"):
            await interaction.followup.send("❌ Vote-Channel nicht gefunden.", ephemeral=True)
            return

        answer = clean_value(self.solution.value)
        if not answer:
            await interaction.followup.send("❌ Antwort darf nicht leer sein.", ephemeral=True)
            return

        sid = await self.cog.repo.create_submission_pending(interaction.guild.id, to_int(riddle["id"], 0), interaction.user.id, answer)
        if not sid:
            await interaction.followup.send("❌ Einreichung konnte nicht gespeichert werden.", ephemeral=True)
            return

        embed = discord.Embed(title="📜 Neue Lösung eingereicht", description=riddle.get("text") or "*No text*", color=discord.Color.gold())
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="🧠 Antwort", value=answer, inline=False)
        embed.add_field(name="✅ Korrekte Lösung", value=riddle.get("solution") or "*Nicht gesetzt*", inline=False)
        embed.add_field(name="🏆 Award", value=riddle.get("award") or "*Keine*", inline=False)
        embed.add_field(name="🆔 User ID", value=str(interaction.user.id), inline=False)
        embed.set_footer(text=footer_text(interaction.guild))

        try:
            vm = await vote_channel.send(embed=embed, view=VoteButtons(self.cog))
        except Exception:
            await self.cog.repo.delete_submission(sid)
            await interaction.followup.send("❌ Vote-Message konnte nicht gesendet werden.", ephemeral=True)
            return

        ok = await self.cog.repo.set_submission_vote_message(sid, vm.id)
        if not ok:
            try:
                await vm.delete()
            except Exception:
                pass
            await self.cog.repo.delete_submission(sid)
            await interaction.followup.send("❌ Interner Fehler beim Verknüpfen.", ephemeral=True)
            return

        await interaction.followup.send("✅ Lösung eingereicht!", ephemeral=True)


class SubmitButton(discord.ui.Button):
    def __init__(self, cog: "RiddlePlayCog"):
        super().__init__(label="💡 Submit Solution", style=discord.ButtonStyle.primary, custom_id=SUBMIT_BUTTON_ID)
        self.cog = cog

    async def callback(self, interaction: Interaction):
        if interaction.guild is None or interaction.message is None:
            await interaction.response.send_message("❌ Nur im Server nutzbar.", ephemeral=True)
            return
        riddle = await self.cog.repo.get_open_riddle_by_message(interaction.guild.id, interaction.message.id)
        if not riddle:
            await interaction.response.send_message("❌ Dieses Rätsel ist nicht mehr aktiv.", ephemeral=True)
            return
        await interaction.response.send_modal(SubmitSolutionModal(self.cog, to_int(riddle["id"], 0)))


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
            await interaction.followup.send("❌ Vote-Message nicht gefunden.", ephemeral=True)
            return

        status, ctx = await self.cog.repo.approve_submission(interaction.message.id, interaction.user.id)
        if status == "not_found":
            await interaction.followup.send("❌ Keine Submission gefunden.", ephemeral=True)
            return
        if status == "already_done":
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
            await interaction.followup.send("⏳ Bereits bearbeitet.", ephemeral=True)
            return
        if status == "riddle_closed":
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
            await interaction.followup.send("⚠️ Rätsel ist nicht mehr offen.", ephemeral=True)
            return
        if status != "ok" or not ctx:
            await interaction.followup.send("❌ Interner Fehler.", ephemeral=True)
            return

        guild = interaction.guild
        gid = guild.id if guild else 0

        submitter_id = to_int(ctx["submitter_id"], 0)
        submitter_mention, submitter_name, submitter_avatar = await self.cog.runtime.resolve_user_label(guild, submitter_id)

        clean_solution, more_link = extract_link(ctx.get("correct_solution") or "")
        sol_display = clean_solution or "*None*"
        if more_link:
            sol_display += f"\n🔗 [🧠**MORE**]({more_link})"

        image = ctx.get("solution_url")
        if not is_http_url(image):
            image = DEFAULT_IMAGE_URL

        embed = discord.Embed(
            title="🎉 Rätsel gelöst!",
            description=f"**{submitter_mention}** hat korrekt gelöst!",
            color=discord.Color.green()
        )
        if submitter_avatar:
            embed.set_author(name=submitter_name, icon_url=submitter_avatar)
        else:
            embed.set_author(name=submitter_name)
        embed.add_field(name="🧩 Rätsel", value=truncate_text(ctx.get("riddle_text") or "*Unknown*"), inline=False)
        embed.add_field(name="🔍 Eingereicht", value=ctx.get("user_answer") or "*None*", inline=False)
        embed.add_field(name="✅ Richtige Lösung", value=sol_display, inline=False)
        embed.add_field(name="🏆 Award", value=ctx.get("award") or "*None*", inline=False)
        if is_http_url(image):
            embed.set_image(url=image)
        embed.set_footer(text=footer_text(guild))

        await self.cog.runtime.update_original_post_result(
            ctx=ctx,
            field_name="✅ Status",
            field_value=f"Gelöst von {submitter_mention}"
        )
        await self.cog.runtime.cleanup_vote_messages_for_riddle(to_int(ctx["riddle_id"], 0), exclude_submission_id=to_int(ctx["submission_id"], 0))

        riddle_channel = await self.cog.runtime.resolve_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel and hasattr(riddle_channel, "send"):
            mentions = unique_role_mentions(guild, RIDDLE_ROLE_ID, ctx.get("button_role_id"))
            mentions.append(submitter_mention)
            content = " ".join(dict.fromkeys([m for m in mentions if m]))
            await riddle_channel.send(
                content=(content + "\n🎉 Glückwunsch!").strip(),
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=True, everyone=False)
            )

        try:
            xp_channel = await self.cog.runtime.resolve_channel(XP_NOTIFY_CHANNEL_ID)
            if xp_channel and hasattr(xp_channel, "send"):
                name_cmd, mention_cmd = build_xpadd_commands(submitter_mention, submitter_name, to_int(ctx.get("xp_gain"), 0))
                await xp_channel.send(
                    content=f"<@&{RIDDLE_MANAGER_ROLE_ID}> XP vergeben:\n`{name_cmd}`\nAlternative:\n`{mention_cmd}`",
                    allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
                )
        except Exception:
            pass

        # Kernlogik nach Solve:
        # Slots nachrücken, OFF, nächste Auto-Aktivierung in 12h, aktive Submit-Posts weg
        if gid > 0:
            await self.cog.repo.shift_open_slots_left(gid)
            await self.cog.repo.set_enabled(gid, False)
            await self.cog.repo.set_next_auto_enable(gid, NEXT_AUTO_ENABLE_HOURS)
            await self.cog.runtime.remove_active_riddle_posts(gid)

        try:
            await interaction.message.delete()
        except Exception:
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass

        nxt = await self.cog.repo.get_next_auto_enable(gid) if gid > 0 else None
        if nxt:
            await interaction.followup.send(
                f"✅ Als korrekt markiert. Auto-Enable ab {discord.utils.format_dt(nxt, style='F')}",
                ephemeral=True
            )
        else:
            await interaction.followup.send("✅ Als korrekt markiert.", ephemeral=True)


class VoteFailButton(discord.ui.Button):
    def __init__(self, cog: "RiddlePlayCog"):
        super().__init__(emoji="👎", style=discord.ButtonStyle.danger, custom_id=VOTE_DOWN_BUTTON_ID)
        self.cog = cog

    async def callback(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.message is None:
            await interaction.followup.send("❌ Vote-Message nicht gefunden.", ephemeral=True)
            return

        status, ctx = await self.cog.repo.reject_submission(interaction.message.id, interaction.user.id)
        if status == "not_found":
            await interaction.followup.send("❌ Keine Submission gefunden.", ephemeral=True)
            return
        if status == "already_done":
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
            await interaction.followup.send("⏳ Bereits bearbeitet.", ephemeral=True)
            return
        if status == "riddle_closed":
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
            await interaction.followup.send("⚠️ Rätsel nicht mehr offen.", ephemeral=True)
            return
        if status != "ok" or not ctx:
            await interaction.followup.send("❌ Interner Fehler.", ephemeral=True)
            return

        uid = to_int(ctx["submitter_id"], 0)
        mention, name, avatar = await self.cog.runtime.resolve_user_label(interaction.guild, uid)

        embed = discord.Embed(
            title="❌ Lösung nicht korrekt",
            description=f"Die Lösung von **{mention}** war leider falsch.",
            color=discord.Color.red()
        )
        if avatar:
            embed.set_author(name=name, icon_url=avatar)
        else:
            embed.set_author(name=name)
        embed.add_field(name="🧩 Rätsel", value=truncate_text(ctx.get("riddle_text") or "*Unknown*"), inline=False)
        embed.add_field(name="🔍 Antwort", value=ctx.get("user_answer") or "*None*", inline=False)
        embed.set_footer(text=footer_text(interaction.guild))

        ch = await self.cog.runtime.resolve_channel(RIDDLE_CHANNEL_ID)
        if ch and hasattr(ch, "send"):
            mentions = unique_role_mentions(interaction.guild, RIDDLE_ROLE_ID, ctx.get("button_role_id"))
            mentions.append(mention)
            await ch.send(
                content=" ".join(dict.fromkeys([m for m in mentions if m])),
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=True, everyone=False)
            )

        try:
            await interaction.message.delete()
        except Exception:
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass

        await interaction.followup.send("✅ Als falsch markiert.", ephemeral=True)


class VoteButtons(LoggedPersistentView):
    def __init__(self, cog: "RiddlePlayCog"):
        super().__init__(timeout=None)
        self.add_item(VoteSuccessButton(cog))
        self.add_item(VoteFailButton(cog))


class SlotEditButton(discord.ui.Button):
    def __init__(self, cog: "RiddleAdminCog", slot_no: int, filled: bool):
        super().__init__(
            label=f"Slot {slot_no} bearbeiten",
            style=discord.ButtonStyle.success if filled else discord.ButtonStyle.secondary,
            row=0 if slot_no <= 3 else 1
        )
        self.cog = cog
        self.slot_no = slot_no

    async def callback(self, interaction: Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Nur im Server.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not member_has_role(interaction.user, RIDDLE_MANAGER_ROLE_ID):
            await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
            return
        current = await self.cog.repo.get_open_slot_riddle(interaction.guild.id, self.slot_no)
        await interaction.response.send_modal(RiddleUpsertModal(self.cog, self.slot_no, current, None))


class SlotRefreshButton(discord.ui.Button):
    def __init__(self, cog: "RiddleAdminCog"):
        super().__init__(label="🔄 Refresh", style=discord.ButtonStyle.primary, row=2)
        self.cog = cog

    async def callback(self, interaction: Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Nur im Server.", ephemeral=True)
            return
        slot_map = await self.cog.repo.open_slot_map(interaction.guild.id)
        embed = self.cog.build_slots_embed(interaction.guild, slot_map)
        view = SlotManagerView(self.cog, interaction.user.id, slot_map)
        await interaction.response.edit_message(embed=embed, view=view)


class SlotManagerView(View):
    def __init__(self, cog: "RiddleAdminCog", owner_id: int, slot_map: dict[int, dict]):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = owner_id
        for s in range(1, MAX_RIDDLE_SLOTS + 1):
            self.add_item(SlotEditButton(cog, s, s in slot_map))
        self.add_item(SlotRefreshButton(cog))

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("🚫 Dieses Menü gehört nicht dir.", ephemeral=True)
            return False
        return True


class ChampionsView(View):
    def __init__(self, entries: list[tuple[int, int, float, int]], total_solved: int, name_cache: dict[int, str], avatar_cache: dict[int, str], image_url: Optional[str], owner_id: Optional[int]):
        super().__init__(timeout=300)
        self.entries = entries
        self.total_solved = total_solved
        self.name_cache = name_cache
        self.avatar_cache = avatar_cache
        self.owner_id = owner_id
        self.page = 0
        self.per_page = 6
        self.max_page = max((len(entries) - 1) // self.per_page, 0)
        self.page1_image_url = image_url if is_http_url(image_url) else DEFAULT_IMAGE_URL
        self.default_image_url = DEFAULT_IMAGE_URL
        self.message: Optional[discord.Message] = None
        self._sync()

    def _sync(self):
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.max_page

    def _name(self, uid: int) -> str:
        return self.name_cache.get(uid, f"User {uid}")

    def _avatar(self, uid: int) -> Optional[str]:
        return self.avatar_cache.get(uid)

    def build_embed(self) -> discord.Embed:
        start = self.page * self.per_page
        end = start + self.per_page
        rows = self.entries[start:end]

        embed = discord.Embed(
            title=f"🏆 Riddle Champions • Total {self.total_solved}",
            description=f"Seite {self.page + 1}/{self.max_page + 1}",
            color=discord.Color.gold()
        )

        if self.entries:
            top_uid = self.entries[0][0]
            top_name = self._name(top_uid)
            top_avatar = self._avatar(top_uid)
            if top_avatar:
                embed.set_author(name=f"👑 #1 {top_name}", icon_url=top_avatar)
                embed.set_thumbnail(url=top_avatar)
            else:
                embed.set_author(name=f"👑 #1 {top_name}")

        if not rows:
            embed.add_field(name="Noch keine Daten", value="Keine gelösten Rätsel.", inline=False)
        else:
            for i, (uid, solved, pct, xp) in enumerate(rows, start=start + 1):
                embed.add_field(name=f"{i}. {self._name(uid)}", value=f"🧩 {solved} | 📊 {pct:.1f}% | 🧠 {xp} XP", inline=False)

        img = self.page1_image_url if self.page == 0 else self.default_image_url
        if is_http_url(img):
            embed.set_image(url=img)
        return embed

    async def interaction_check(self, interaction: Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await interaction.response.send_message("🚫 Dieses Menü gehört nicht dir.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for c in self.children:
            if isinstance(c, discord.ui.Button):
                c.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @discord.ui.button(label="Zurück", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: Interaction, _: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        self._sync()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Weiter", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: Interaction, _: discord.ui.Button):
        if self.page < self.max_page:
            self.page += 1
        self._sync()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


# =========================
# ADMIN COG
# =========================
class RiddleAdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot, repo: RiddleRepo, runtime: RiddleRuntime):
        self.bot = bot
        self.repo = repo
        self.runtime = runtime

    def build_slots_embed(self, guild: discord.Guild, slot_map: dict[int, dict]) -> discord.Embed:
        embed = discord.Embed(
            title="🗂️ Riddle-Slots",
            description="Nur Slot 1 ist live, wenn das System ON ist.",
            color=discord.Color.blurple()
        )
        for s in range(1, MAX_RIDDLE_SLOTS + 1):
            row = slot_map.get(s)
            if not row:
                embed.add_field(name=f"Slot {s}", value="`EMPTY`", inline=False)
                continue
            rn = to_int(row.get("riddle_no"), to_int(row.get("id"), 0))
            award = row.get("award") or "None"
            preview = truncate_text(row.get("text") or "", 120)
            embed.add_field(name=f"Slot {s} • #{rn}", value=f"🏆 {award}\n{preview}", inline=False)
        embed.set_footer(text=footer_text(guild))
        return embed

    @app_commands.command(name="riddle", description="Rätsel in einem Slot erstellen/bearbeiten.")
    @app_commands.describe(slot="Slot 1-5", mention="Optionale Rolle für dieses Rätsel")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle(self, interaction: Interaction, slot: app_commands.Range[int, 1, MAX_RIDDLE_SLOTS], mention: Optional[Role] = None):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Nur im Server.", ephemeral=True)
            return
        current = await self.repo.get_open_slot_riddle(interaction.guild.id, int(slot))
        await interaction.response.send_modal(RiddleUpsertModal(self, int(slot), current, mention.id if mention else None))

    @app_commands.command(name="riddle_slots", description="Alle Slots ansehen und bearbeiten.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_slots(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("❌ Nur im Server.", ephemeral=True)
            return
        slot_map = await self.repo.open_slot_map(interaction.guild.id)
        await interaction.followup.send(
            embed=self.build_slots_embed(interaction.guild, slot_map),
            view=SlotManagerView(self, interaction.user.id, slot_map),
            ephemeral=True
        )

    @app_commands.command(name="riddle_on", description="System ON: Slot 1 sofort live posten.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_on(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("❌ Nur im Server.", ephemeral=True)
            return

        gid = interaction.guild.id
        await self.repo.ensure_guild_state(gid)
        await self.repo.shift_open_slots_left(gid)

        if not await self.repo.get_open_slot1(gid):
            await interaction.followup.send("⚠️ Kein Rätsel verfügbar (Slot 1 leer).", ephemeral=True)
            return

        await self.repo.set_enabled(gid, True)
        await self.repo.clear_next_auto_enable(gid)
        result = await self.runtime.publish_slot1_post(gid, force_repost=True, allow_role_ping=True)

        if result in ("posted", "updated"):
            await interaction.followup.send("✅ System ist ON. Slot 1 wurde gepostet.", ephemeral=True)
        else:
            await interaction.followup.send(f"⚠️ ON gesetzt, Post-Result: `{result}`", ephemeral=True)

    @app_commands.command(name="riddle_off", description="System OFF: aktive Rätselposts entfernen.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_off(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("❌ Nur im Server.", ephemeral=True)
            return
        gid = interaction.guild.id
        await self.repo.ensure_guild_state(gid)
        await self.repo.set_enabled(gid, False)
        await self.runtime.remove_active_riddle_posts(gid)
        await interaction.followup.send("✅ System ist OFF. Aktive Rätselposts entfernt.", ephemeral=True)

    @app_commands.command(name="riddle_post", description="Slot 1 sofort neu posten (setzt ON, ignoriert Cooldown).")
    @app_commands.describe(ping_role="Optionale zusätzliche Rolle")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_post(self, interaction: Interaction, ping_role: Optional[Role] = None):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("❌ Nur im Server.", ephemeral=True)
            return

        gid = interaction.guild.id
        await self.repo.ensure_guild_state(gid)
        await self.repo.shift_open_slots_left(gid)

        if not await self.repo.get_open_slot1(gid):
            await interaction.followup.send("⚠️ Slot 1 ist leer.", ephemeral=True)
            return

        await self.repo.set_enabled(gid, True)
        await self.repo.clear_next_auto_enable(gid)

        result = await self.runtime.publish_slot1_post(
            gid,
            force_repost=True,
            allow_role_ping=True,
            extra_ping_role_id=(ping_role.id if ping_role else None)
        )
        if result in ("posted", "updated"):
            await interaction.followup.send("✅ Rätsel gepostet.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Posting fehlgeschlagen: `{result}`", ephemeral=True)

    @app_commands.command(name="riddle_close", description="Aktives Slot-1-Rätsel als ungelöst schließen.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_close(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("❌ Nur im Server.", ephemeral=True)
            return

        gid = interaction.guild.id
        riddle = await self.repo.close_slot1_unsolved(gid, interaction.user.id)
        if not riddle:
            await interaction.followup.send("❌ Kein offenes Rätsel in Slot 1.", ephemeral=True)
            return

        await self.runtime.cleanup_vote_messages_for_riddle(to_int(riddle["id"], 0))

        clean_solution, link = extract_link(riddle.get("solution") or "")
        solution = clean_solution or "*None*"
        if link:
            solution += f"\n🔗 [🧠**MORE**]({link})"

        image = riddle.get("solution_url")
        if not is_http_url(image):
            image = DEFAULT_IMAGE_URL

        embed = discord.Embed(
            title="🔒 Rätsel geschlossen",
            description="Niemand hat das Rätsel rechtzeitig gelöst.",
            color=discord.Color.red()
        )
        embed.add_field(name="🧩 Rätsel", value=riddle.get("text") or "*Unknown*", inline=False)
        embed.add_field(name="✅ Lösung", value=solution, inline=False)
        embed.add_field(name="🏆 Award", value=riddle.get("award") or "*None*", inline=False)
        if is_http_url(image):
            embed.set_image(url=image)
        embed.set_footer(text=footer_text(interaction.guild))

        ch = await self.runtime.resolve_channel(RIDDLE_CHANNEL_ID)
        if ch and hasattr(ch, "send"):
            mentions = unique_role_mentions(interaction.guild, RIDDLE_ROLE_ID, riddle.get("button_role_id"))
            await ch.send(
                content=" ".join(dict.fromkeys([m for m in mentions if m])) or None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
            )

        await self.runtime.update_original_post_result(
            ctx={
                "posted_channel_id": riddle.get("posted_channel_id"),
                "posted_message_id": riddle.get("posted_message_id"),
                "riddle_text": riddle.get("text") or "*Unknown*"
            },
            field_name="🔒 Status",
            field_value="Ungelöst geschlossen"
        )

        # Kernlogik nach close
        await self.repo.shift_open_slots_left(gid)
        await self.repo.set_enabled(gid, False)
        await self.repo.set_next_auto_enable(gid, NEXT_AUTO_ENABLE_HOURS)
        await self.runtime.remove_active_riddle_posts(gid)

        nxt = await self.repo.get_next_auto_enable(gid)
        if nxt:
            await interaction.followup.send(
                f"✅ Geschlossen. Auto-Enable frühestens: {discord.utils.format_dt(nxt, style='F')}",
                ephemeral=True
            )
        else:
            await interaction.followup.send("✅ Geschlossen.", ephemeral=True)

    @app_commands.command(name="riddle_status", description="ON/OFF-Status, Slot-Status und nächste Auto-Aktivierung.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_status(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("❌ Nur im Server.", ephemeral=True)
            return

        gid = interaction.guild.id
        await self.repo.ensure_guild_state(gid)
        await self.repo.shift_open_slots_left(gid)

        st = await self.repo.get_state_row(gid)
        enabled = bool(to_int(st.get("is_enabled"), 0))
        nxt = parse_iso_utc(st.get("next_auto_enable_at"))
        slot1 = await self.repo.get_open_slot1(gid)

        embed = discord.Embed(
            title="⚙️ Riddle System Status",
            color=discord.Color.green() if enabled else discord.Color.orange()
        )
        embed.add_field(name="System", value="🟢 ON" if enabled else "🟠 OFF", inline=True)
        embed.add_field(name="Nächstes Auto-Enable", value=discord.utils.format_dt(nxt, style="F") if nxt else "sofort möglich", inline=True)
        embed.add_field(name="Slot 1", value="✅ befüllt" if slot1 else "❌ leer", inline=True)
        if slot1:
            embed.add_field(name="Slot1 Preview", value=truncate_text(slot1.get("text") or "", 260), inline=False)
        embed.set_footer(text=footer_text(interaction.guild))

        await interaction.followup.send(embed=embed, ephemeral=True)

    async def cog_app_command_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, MissingRiddleManagerRole):
            await send_access_denied(interaction)
            return
        logger.exception("Admin cmd error: %s", error)
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
        self._auto_task: Optional[asyncio.Task] = None

    def submit_view(self):
        return SubmitButtonView(self)

    async def cog_load(self):
        self.bot.add_view(self.submit_view())
        self.bot.add_view(VoteButtons(self))

        await self.runtime.startup_rebuild(self)

        if self._auto_task is None or self._auto_task.done():
            self._auto_task = asyncio.create_task(self._auto_enable_worker(), name="riddle_auto_enable_worker")

    def cog_unload(self):
        if self._auto_task and not self._auto_task.done():
            self._auto_task.cancel()

    async def _auto_enable_worker(self):
        await asyncio.sleep(10)
        while True:
            try:
                guild_ids = await self.repo.list_all_guild_ids()
                for gid in guild_ids:
                    await self.repo.ensure_guild_state(gid)

                    # WICHTIG: auch hier immer Slots hochziehen
                    await self.repo.shift_open_slots_left(gid)

                    enabled = await self.repo.is_enabled(gid)
                    slot1 = await self.repo.get_open_slot1(gid)

                    if enabled:
                        await self.runtime.enforce_enabled_state(gid, allow_ping=False, force_repost=False)
                        continue

                    await self.runtime.remove_active_riddle_posts(gid)

                    if slot1 and await self.repo.is_auto_enable_due(gid):
                        await self.repo.set_enabled(gid, True)
                        await self.repo.clear_next_auto_enable(gid)
                        await self.runtime.publish_slot1_post(gid, force_repost=True, allow_role_ping=True)
                        logger.info("Auto-Enable: guild=%s posted slot1", gid)

            except Exception as e:
                logger.exception("Auto worker error: %s", e)

            await asyncio.sleep(max(60, AUTO_ENABLE_SCAN_SECONDS))

    @app_commands.command(name="riddle_champ", description="Zeigt das Champions-Leaderboard.")
    @app_commands.describe(
        visible="True=öffentlich, False=nur für dich",
        image="Optionales Bild für Seite 1",
        mention="Optionale Rolle (nur bei visible=True)"
    )
    @app_commands.guild_only()
    async def riddle_champ(self, interaction: Interaction, visible: Optional[bool] = False, image: Optional[str] = None, mention: Optional[Role] = None):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Nur im Server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=not visible, thinking=True)

        rows = await self.repo.stats_entries(interaction.guild.id)
        total = sum(s for _, s, _ in rows)
        entries = [(uid, solved, (solved / total * 100.0 if total else 0.0), xp) for uid, solved, xp in rows]

        name_cache: dict[int, str] = {}
        avatar_cache: dict[int, str] = {}
        for uid, _, _ in rows:
            _, name, avatar = await self.runtime.resolve_user_label(interaction.guild, uid)
            name_cache[uid] = name
            if avatar:
                avatar_cache[uid] = avatar

        view = ChampionsView(entries, total, name_cache, avatar_cache, image if is_http_url(image) else DEFAULT_IMAGE_URL, interaction.user.id if not visible else None)
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
        logger.exception("Play cmd error: %s", error)
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

    logger.info("Riddle extension loaded (single-file, Admin+Play).")


async def teardown(bot: commands.Bot):
    global _repo
    if _repo is not None:
        await _repo.close()
        _repo = None