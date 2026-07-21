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
logger = logging.getLogger("riddle_system_sql")

DB_PATH = (os.getenv("RIDDLE_DB_PATH") or "data/riddle.sqlite3").strip()

RIDDLE_CHANNEL_ID = int(os.getenv("RIDDLE_CHANNEL_ID", "1349697597232906292"))
VOTE_CHANNEL_ID = int(os.getenv("VOTE_CHANNEL_ID", "1381754826710585527"))
RIDDLE_ROLE_ID = int(os.getenv("RIDDLE_ROLE_ID", "1380610400416043089"))
RIDDLE_MANAGER_ROLE_ID = int(os.getenv("RIDDLE_MANAGER_ROLE_ID", "1393762463861702787"))

DEFAULT_IMAGE_URL = (
    os.getenv("DEFAULT_RIDDLE_IMAGE_URL")
    or "https://cdn.discordapp.com/attachments/1383652563408392232/1462480133737943063/riddle_sexy.gif"
).strip()

ACCESS_DENIED_IMAGE_URL = (
    os.getenv("RIDDLE_ACCESS_DENIED_IMAGE_URL")
    or "https://example.com/apply-role-placeholder.jpg"
).strip()

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


def xp_from_award(award_text: Optional[str]) -> int:
    m = re.search(r"\d+", str(award_text or ""))
    return int(m.group()) if m else 0


# =========================
# ACCESS CHECK
# =========================
class MissingRiddleManagerRole(app_commands.CheckFailure):
    pass


def riddle_manager_required():
    async def predicate(interaction: Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            raise MissingRiddleManagerRole()

        has_role = any(r.id == RIDDLE_MANAGER_ROLE_ID for r in interaction.user.roles)
        if not has_role:
            raise MissingRiddleManagerRole()
        return True

    return app_commands.check(predicate)


async def send_riddle_access_denied(interaction: Interaction):
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
# BASE VIEW (ERROR LOGGING)
# =========================
class LoggedPersistentView(View):
    async def on_error(self, interaction: Interaction, error: Exception, item: discord.ui.Item[Any]):
        logger.exception(
            "View callback error | view=%s item=%s custom_id=%s",
            self.__class__.__name__,
            item.__class__.__name__ if item else "unknown",
            getattr(item, "custom_id", None),
            exc_info=error
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Button callback failed. Check bot logs.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Button callback failed. Check bot logs.", ephemeral=True)
        except Exception:
            pass


# =========================
# UI
# =========================
class RiddleUpsertModal(Modal):
    def __init__(
        self,
        *,
        cog: "RiddleSystemSQL",
        current_data: Optional[dict],
        mention_override_id: Optional[int]
    ):
        has_data = bool(
            current_data
            and clean_value(current_data.get("text"))
            and clean_value(current_data.get("solution"))
        )
        super().__init__(title="Edit Riddle" if has_data else "Create Riddle")

        self.cog = cog
        self.current_data = current_data or {}
        self.mention_override_id = mention_override_id

        self.text = TextInput(
            label="Text",
            style=discord.TextStyle.paragraph,
            default=self.current_data.get("text") or "",
            required=True,
            max_length=4000
        )
        self.solution = TextInput(
            label="Solution",
            style=discord.TextStyle.paragraph,
            default=self.current_data.get("solution") or "",
            required=True,
            max_length=4000
        )
        self.award = TextInput(
            label="Award",
            default=self.current_data.get("award") or "",
            required=False,
            max_length=200
        )
        self.image_url = TextInput(
            label="Image URL",
            default=self.current_data.get("image_url") or "",
            required=False,
            max_length=1000
        )
        self.solution_url = TextInput(
            label="Solution URL",
            default=self.current_data.get("solution_url") or "",
            required=False,
            max_length=1000
        )

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

        text = clean_value(self.text.value)
        solution = clean_value(self.solution.value)

        if not text or not solution:
            await interaction.followup.send("❌ Text and solution cannot be empty.", ephemeral=True)
            return

        payload = {
            "text": text,
            "solution": solution,
            "award": clean_value(self.award.value),
            "image_url": clean_value(self.image_url.value),
            "solution_url": clean_value(self.solution_url.value),
        }

        try:
            rid = await self.cog._upsert_open_riddle(
                guild_id=interaction.guild.id,
                user_id=interaction.user.id,
                payload=payload,
                mention_override_id=self.mention_override_id
            )
        except Exception as e:
            logger.exception("Riddle upsert failed: %s", e)
            await interaction.followup.send("❌ Saving failed.", ephemeral=True)
            return

        if rid is None:
            await interaction.followup.send("❌ Saving failed.", ephemeral=True)
            return

        await interaction.followup.send("✅ Riddle saved.", ephemeral=True)


class SubmitSolutionModal(Modal):
    def __init__(self, cog: "RiddleSystemSQL", riddle_id: int):
        super().__init__(title="💡 Submit Your Solution")
        self.cog = cog
        self.riddle_id = riddle_id
        self.solution = TextInput(
            label="Your Answer",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000
        )
        self.add_item(self.solution)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None:
            await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        riddle = await self.cog._get_open_riddle_by_id(interaction.guild.id, self.riddle_id)
        if not riddle:
            await interaction.followup.send("❌ No active riddle found.", ephemeral=True)
            return

        vote_channel = await self.cog._resolve_channel(VOTE_CHANNEL_ID)
        if vote_channel is None or not hasattr(vote_channel, "send"):
            await interaction.followup.send("❌ Vote channel not found.", ephemeral=True)
            return

        answer = clean_value(self.solution.value)
        if not answer:
            await interaction.followup.send("❌ Answer cannot be empty.", ephemeral=True)
            return

        submission_id = await self.cog._create_submission_pending(
            guild_id=interaction.guild.id,
            riddle_id=riddle["id"],
            user_id=interaction.user.id,
            answer=answer
        )
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
            await self.cog._delete_submission(submission_id)
            await interaction.followup.send("❌ Could not send vote message.", ephemeral=True)
            return

        ok = await self.cog._set_submission_vote_message(submission_id, vote_msg.id)
        if not ok:
            try:
                await vote_msg.delete()
            except Exception:
                pass
            await self.cog._delete_submission(submission_id)
            await interaction.followup.send("❌ Internal error while linking vote message.", ephemeral=True)
            return

        await interaction.followup.send("✅ Your solution has been submitted!", ephemeral=True)


class SubmitButton(discord.ui.Button):
    def __init__(self, cog: "RiddleSystemSQL"):
        super().__init__(
            label="💡 Submit Solution",
            style=discord.ButtonStyle.primary,
            custom_id=SUBMIT_BUTTON_ID
        )
        self.cog = cog

    async def callback(self, interaction: Interaction):
        if interaction.guild is None or interaction.message is None:
            await interaction.response.send_message("❌ This can only be used in a server.", ephemeral=True)
            return

        riddle = await self.cog._get_open_riddle_by_message(interaction.guild.id, interaction.message.id)
        if not riddle:
            await interaction.response.send_message("❌ This riddle is no longer active.", ephemeral=True)
            return

        await interaction.response.send_modal(SubmitSolutionModal(self.cog, riddle_id=riddle["id"]))


class SubmitButtonView(LoggedPersistentView):
    def __init__(self, cog: "RiddleSystemSQL"):
        super().__init__(timeout=None)
        self.add_item(SubmitButton(cog))


class VoteSuccessButton(discord.ui.Button):
    def __init__(self, cog: "RiddleSystemSQL"):
        super().__init__(
            emoji="👍",
            style=discord.ButtonStyle.success,
            custom_id=VOTE_UP_BUTTON_ID
        )
        self.cog = cog

    async def callback(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        if interaction.message is None:
            await interaction.followup.send("❌ Vote message not found.", ephemeral=True)
            return

        status, ctx = await self.cog._approve_submission(interaction.message.id, interaction.user.id)
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
        submitter_mention, submitter_name, submitter_avatar = await self.cog._resolve_user_label(interaction.guild, submitter_id)

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

        await self.cog._mark_original_riddle_post_solved(
            ctx=ctx,
            solver_mention=submitter_mention,
            clean_solution=clean_solution or "*None*",
            more_link=more_link
        )

        await self.cog._cleanup_vote_messages_for_riddle(ctx["riddle_id"], exclude_submission_id=ctx["submission_id"])

        riddle_channel = await self.cog._resolve_channel(RIDDLE_CHANNEL_ID)
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

        try:
            await interaction.message.delete()
        except Exception:
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass

        await interaction.followup.send("✅ Marked as correct.", ephemeral=True)


class VoteFailButton(discord.ui.Button):
    def __init__(self, cog: "RiddleSystemSQL"):
        super().__init__(
            emoji="👎",
            style=discord.ButtonStyle.danger,
            custom_id=VOTE_DOWN_BUTTON_ID
        )
        self.cog = cog

    async def callback(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        if interaction.message is None:
            await interaction.followup.send("❌ Vote message not found.", ephemeral=True)
            return

        status, ctx = await self.cog._reject_submission(interaction.message.id, interaction.user.id)
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
        submitter_mention, submitter_name, submitter_avatar = await self.cog._resolve_user_label(interaction.guild, submitter_id)

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

        riddle_channel = await self.cog._resolve_channel(RIDDLE_CHANNEL_ID)
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
    def __init__(self, cog: "RiddleSystemSQL"):
        super().__init__(timeout=None)
        self.add_item(VoteSuccessButton(cog))
        self.add_item(VoteFailButton(cog))


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
# COG
# =========================
class RiddleSystemSQL(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Optional[aiosqlite.Connection] = None
        self.db_lock = asyncio.Lock()

    async def cog_load(self):
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

        self.db = await aiosqlite.connect(DB_PATH)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode = WAL;")
        await self.db.execute("PRAGMA foreign_keys = ON;")
        await self.db.execute("PRAGMA busy_timeout = 5000;")
        await self.db.commit()

        await self._init_db()

        self.bot.add_view(SubmitButtonView(self))
        self.bot.add_view(VoteButtons(self))

        try:
            await self._startup_rebuild_messages()
        except Exception as e:
            logger.exception("Startup rebuild failed: %s", e)

        logger.info("RiddleSystemSQL loaded. DB + persistent views active.")

    def cog_unload(self):
        if self.db is not None:
            db = self.db
            self.db = None
            asyncio.create_task(db.close())

    async def _init_db(self):
        assert self.db is not None
        schema = """
        CREATE TABLE IF NOT EXISTS riddles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            riddle_no INTEGER,
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

        CREATE UNIQUE INDEX IF NOT EXISTS idx_riddles_open_guild
        ON riddles(guild_id) WHERE status='open';

        CREATE UNIQUE INDEX IF NOT EXISTS idx_riddles_posted_message
        ON riddles(posted_message_id) WHERE posted_message_id IS NOT NULL;

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
        """
        async with self.db_lock:
            await self.db.executescript(schema)

            cur = await self.db.execute("PRAGMA table_info(riddles)")
            cols = [row["name"] for row in await cur.fetchall()]
            await cur.close()

            if "riddle_no" not in cols:
                await self.db.execute("ALTER TABLE riddles ADD COLUMN riddle_no INTEGER")

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

            await self.db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_riddles_guild_no "
                "ON riddles(guild_id, riddle_no) WHERE riddle_no IS NOT NULL"
            )

            await self.db.commit()

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
            rowcount = cur.rowcount
            lastrowid = cur.lastrowid or 0
            await cur.close()
        return rowcount, int(lastrowid)

    async def _get_open_riddle(self, guild_id: int) -> Optional[dict]:
        return await self._fetchone(
            "SELECT * FROM riddles WHERE guild_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (guild_id,)
        )

    async def _get_open_riddle_by_id(self, guild_id: int, riddle_id: int) -> Optional[dict]:
        return await self._fetchone(
            "SELECT * FROM riddles WHERE guild_id=? AND id=? AND status='open' LIMIT 1",
            (guild_id, riddle_id)
        )

    async def _get_open_riddle_by_message(self, guild_id: int, message_id: int) -> Optional[dict]:
        return await self._fetchone(
            "SELECT * FROM riddles WHERE guild_id=? AND posted_message_id=? AND status='open' LIMIT 1",
            (guild_id, message_id)
        )

    async def _get_next_riddle_number(self, guild_id: int) -> int:
        row = await self._fetchone(
            "SELECT COALESCE(MAX(riddle_no), 0) AS m FROM riddles WHERE guild_id=?",
            (guild_id,)
        )
        return to_int(row["m"], 0) + 1 if row else 1

    async def _upsert_open_riddle(
        self,
        *,
        guild_id: int,
        user_id: int,
        payload: dict,
        mention_override_id: Optional[int]
    ) -> Optional[int]:
        if self.db is None:
            return None

        text = clean_value(payload.get("text"))
        solution = clean_value(payload.get("solution"))
        award = clean_value(payload.get("award"))
        image_url = clean_value(payload.get("image_url"))
        solution_url = clean_value(payload.get("solution_url"))
        now = now_iso_utc()

        async with self.db_lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    "SELECT * FROM riddles WHERE guild_id=? AND status='open' ORDER BY id DESC LIMIT 1",
                    (guild_id,)
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
                    rid = old["id"]
                else:
                    cur = await self.db.execute(
                        "SELECT COALESCE(MAX(riddle_no), 0) + 1 AS n FROM riddles WHERE guild_id=?",
                        (guild_id,)
                    )
                    row_n = await cur.fetchone()
                    await cur.close()
                    next_no = to_int(row_n["n"] if row_n else 1, 1)

                    button_role_id = mention_override_id
                    cur = await self.db.execute(
                        """
                        INSERT INTO riddles (
                            guild_id, riddle_no, text, solution, award, image_url, solution_url, button_role_id, status,
                            created_by, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
                        """,
                        (guild_id, next_no, text, solution, award, image_url, solution_url, button_role_id, user_id, now, now)
                    )
                    rid = int(cur.lastrowid)
                    await cur.close()

                await self.db.commit()
                return rid
            except Exception:
                await self.db.rollback()
                raise

    async def _set_riddle_posted_message(self, riddle_id: int, channel_id: int, message_id: int) -> bool:
        rc, _ = await self._execute(
            "UPDATE riddles SET posted_channel_id=?, posted_message_id=?, updated_at=? WHERE id=?",
            (channel_id, message_id, now_iso_utc(), riddle_id)
        )
        return rc > 0

    async def _create_submission_pending(self, *, guild_id: int, riddle_id: int, user_id: int, answer: str) -> Optional[int]:
        _, lid = await self._execute(
            """
            INSERT INTO submissions (guild_id, riddle_id, user_id, answer, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (guild_id, riddle_id, user_id, answer, now_iso_utc())
        )
        return lid if lid > 0 else None

    async def _set_submission_vote_message(self, submission_id: int, vote_message_id: int) -> bool:
        rc, _ = await self._execute(
            "UPDATE submissions SET vote_message_id=? WHERE id=?",
            (vote_message_id, submission_id)
        )
        return rc > 0

    async def _delete_submission(self, submission_id: int):
        await self._execute("DELETE FROM submissions WHERE id=?", (submission_id,))

    async def _approve_submission(self, vote_message_id: int, moderator_id: int) -> tuple[str, Optional[dict]]:
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
                    "UPDATE riddles SET status='solved', solved_by=?, solved_at=?, updated_at=? WHERE id=? AND status='open'",
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

    async def _reject_submission(self, vote_message_id: int, moderator_id: int) -> tuple[str, Optional[dict]]:
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

    async def _close_open_riddle(self, guild_id: int, closed_by: int) -> Optional[dict]:
        if self.db is None:
            return None

        async with self.db_lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = await self.db.execute(
                    "SELECT * FROM riddles WHERE guild_id=? AND status='open' ORDER BY id DESC LIMIT 1",
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
                    SET status='closed', closed_by=?, closed_at=?, updated_at=?
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

    async def _stats_entries(self, guild_id: int) -> list[tuple[int, int, int]]:
        rows = await self._fetchall(
            "SELECT user_id, solved_riddles, xp FROM user_stats WHERE guild_id=? ORDER BY solved_riddles DESC, xp DESC",
            (guild_id,)
        )
        out: list[tuple[int, int, int]] = []
        for r in rows:
            uid = to_int(r.get("user_id"), -1)
            if uid <= 0:
                continue
            solved = max(0, to_int(r.get("solved_riddles"), 0))
            xp = max(0, to_int(r.get("xp"), 0))
            out.append((uid, solved, xp))
        return out

    def _message_has_custom_id(self, msg: discord.Message, custom_ids: set[str]) -> bool:
        try:
            for row in (msg.components or []):
                for child in getattr(row, "children", []):
                    cid = getattr(child, "custom_id", None)
                    if cid in custom_ids:
                        return True
        except Exception:
            pass
        return False

    async def _delete_button_messages_in_channel(self, channel_id: int, custom_ids: set[str], limit: int = 400):
        channel = await self._resolve_channel(channel_id)
        if channel is None or not hasattr(channel, "history"):
            return

        me = self.bot.user
        if me is None:
            return

        async for msg in channel.history(limit=limit):
            if msg.author.id != me.id:
                continue
            if self._message_has_custom_id(msg, custom_ids):
                try:
                    await msg.delete()
                except Exception:
                    pass

    async def _startup_rebuild_messages(self):
        logger.info("Startup rebuild: deleting old submit/vote button posts...")

        await self._delete_button_messages_in_channel(
            RIDDLE_CHANNEL_ID,
            {SUBMIT_BUTTON_ID},
            limit=400
        )

        await self._delete_button_messages_in_channel(
            VOTE_CHANNEL_ID,
            {VOTE_UP_BUTTON_ID, VOTE_DOWN_BUTTON_ID},
            limit=600
        )

        await self._repost_open_riddle_posts()
        await self._repost_pending_vote_posts()

        logger.info("Startup rebuild completed.")

    async def _repost_open_riddle_posts(self):
        rows = await self._fetchall(
            "SELECT * FROM riddles WHERE status='open' ORDER BY id ASC"
        )
        if not rows:
            return

        riddle_channel = await self._resolve_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel is None or not hasattr(riddle_channel, "send"):
            logger.warning("Riddle channel missing during startup repost.")
            return

        for riddle in rows:
            guild = self.bot.get_guild(to_int(riddle.get("guild_id"), 0))

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
            embed.set_image(url=image_url)
            embed.set_footer(text=footer_text(guild))

            mentions = unique_role_mentions(guild, RIDDLE_ROLE_ID, riddle.get("button_role_id"))
            content = " ".join(dict.fromkeys([m for m in mentions if m]))

            try:
                msg = await riddle_channel.send(
                    content=content or None,
                    embed=embed,
                    view=SubmitButtonView(self),
                    allowed_mentions=discord.AllowedMentions.none()
                )
                await self._set_riddle_posted_message(riddle["id"], msg.channel.id, msg.id)
            except Exception as e:
                logger.warning("Failed to repost open riddle id=%s: %s", riddle.get("id"), e)

    async def _repost_pending_vote_posts(self):
        await self._execute(
            """
            UPDATE submissions
            SET status='cancelled', voted_by=?, voted_at=?
            WHERE status='pending'
              AND riddle_id IN (SELECT id FROM riddles WHERE status <> 'open')
            """,
            (0, now_iso_utc())
        )

        rows = await self._fetchall(
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
        if not rows:
            return

        vote_channel = await self._resolve_channel(VOTE_CHANNEL_ID)
        if vote_channel is None or not hasattr(vote_channel, "send"):
            logger.warning("Vote channel missing during startup repost.")
            return

        for row in rows:
            guild = self.bot.get_guild(to_int(row.get("guild_id"), 0))
            uid = to_int(row.get("user_id"), 0)
            _, author_name, author_avatar = await self._resolve_user_label(guild, uid)

            embed = discord.Embed(
                title="📜 New Solution Submitted",
                description=row.get("riddle_text") or "*No riddle text*",
                color=discord.Color.gold()
            )
            if author_avatar:
                embed.set_author(name=author_name, icon_url=author_avatar)
            else:
                embed.set_author(name=author_name)

            embed.add_field(name="🧠 User's Answer", value=row.get("answer") or "*Empty*", inline=False)
            embed.add_field(name="✅ Correct Solution", value=row.get("solution") or "*Not set*", inline=False)
            embed.add_field(name="🏆 Award", value=row.get("award") or "*None*", inline=False)
            embed.add_field(name="🆔 User ID", value=str(uid), inline=False)
            if row.get("button_role_id"):
                embed.add_field(name="🔖 Assigned Group", value=str(row["button_role_id"]), inline=True)
            embed.set_footer(text=footer_text(guild))

            try:
                vmsg = await vote_channel.send(embed=embed, view=VoteButtons(self))
                await self._set_submission_vote_message(row["submission_id"], vmsg.id)
            except Exception as e:
                logger.warning("Failed to repost vote submission id=%s: %s", row.get("submission_id"), e)

    async def _cleanup_vote_messages_for_riddle(self, riddle_id: int, exclude_submission_id: Optional[int] = None):
        rows = await self._fetchall(
            """
            SELECT id, vote_message_id
            FROM submissions
            WHERE riddle_id=? AND vote_message_id IS NOT NULL
            """,
            (riddle_id,)
        )
        if not rows:
            return

        vote_channel = await self._resolve_channel(VOTE_CHANNEL_ID)
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

    async def _resolve_channel(self, channel_id: int):
        ch = self.bot.get_channel(channel_id)
        if ch is not None:
            return ch
        try:
            ch = await self.bot.fetch_channel(channel_id)
            return ch
        except Exception:
            return None

    async def _fetch_message_safe(self, channel_id: Optional[int], message_id: Optional[int]) -> Optional[discord.Message]:
        cid = safe_int(channel_id, None)
        mid = safe_int(message_id, None)
        if not cid or not mid:
            return None

        channel = await self._resolve_channel(cid)
        if channel is None or not hasattr(channel, "fetch_message"):
            return None

        try:
            return await channel.fetch_message(mid)
        except Exception:
            return None

    async def _resolve_user_label(self, guild: Optional[discord.Guild], uid: int) -> tuple[str, str, Optional[str]]:
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

    async def _build_identity_cache(
        self,
        guild: discord.Guild,
        entries_raw: list[tuple[int, int, int]]
    ) -> tuple[dict[int, str], dict[int, str]]:
        name_cache: dict[int, str] = {}
        avatar_cache: dict[int, str] = {}

        unique_ids = list(dict.fromkeys(uid for uid, _, _ in entries_raw))
        sem = asyncio.Semaphore(5)

        async def worker(uid: int):
            async with sem:
                _, name, avatar = await self._resolve_user_label(guild, uid)
                name_cache[uid] = name
                if avatar:
                    avatar_cache[uid] = avatar

        await asyncio.gather(*(worker(uid) for uid in unique_ids))
        return name_cache, avatar_cache

    async def _update_original_post(self, ctx: dict, field_name: str, field_value: str):
        msg = await self._fetch_message_safe(ctx.get("posted_channel_id"), ctx.get("posted_message_id"))
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

    async def _mark_original_riddle_post_solved(
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
        await self._update_original_post(ctx, "✅ Solved", solved_note)

    @app_commands.command(name="riddle", description="Create a new riddle or edit the active one.")
    @app_commands.describe(mention="Optional role to store as mention/button role")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle(self, interaction: Interaction, mention: Optional[Role] = None):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        current = await self._get_open_riddle(interaction.guild.id)
        modal = RiddleUpsertModal(
            cog=self,
            current_data=current,
            mention_override_id=(mention.id if mention else None)
        )
        try:
            await interaction.response.send_modal(modal)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Interaction expired. Please run the command again.", ephemeral=True)

    @app_commands.command(name="riddle_post", description="Post or update the active riddle in the riddle channel.")
    @app_commands.describe(ping_role="Optional extra role to ping")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_post(self, interaction: Interaction, ping_role: Optional[Role] = None):
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None:
            await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        riddle = await self._get_open_riddle(interaction.guild.id)
        if not riddle:
            await interaction.followup.send("❌ No active riddle found.", ephemeral=True)
            return

        text = clean_value(riddle.get("text"))
        solution = clean_value(riddle.get("solution"))
        if not text or not solution:
            await interaction.followup.send("❌ Riddle is incomplete (missing text/solution).", ephemeral=True)
            return

        riddle_channel = await self._resolve_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel is None or not hasattr(riddle_channel, "send"):
            await interaction.followup.send("❌ Riddle channel not found.", ephemeral=True)
            return

        image_url = riddle.get("image_url")
        if not is_http_url(image_url):
            image_url = DEFAULT_IMAGE_URL

        riddle_no = to_int(riddle.get("riddle_no"), 0)
        if riddle_no <= 0:
            riddle_no = await self._get_next_riddle_number(interaction.guild.id)

        title = f"🧩 Ms Pepper's Goon Hut Riddle\n#{riddle_no} ({now_date_str()})"

        embed = discord.Embed(
            title=title,
            description=text,
            color=discord.Color.blurple()
        )
        embed.add_field(name="🏆 Award", value=riddle.get("award") or "*None*", inline=False)
        embed.set_image(url=image_url)
        embed.set_footer(text=footer_text(interaction.guild))

        mentions = unique_role_mentions(interaction.guild, RIDDLE_ROLE_ID, riddle.get("button_role_id"))
        if ping_role:
            mentions.append(ping_role.mention)
        content = " ".join(dict.fromkeys([m for m in mentions if m]))

        existing_msg = await self._fetch_message_safe(riddle.get("posted_channel_id"), riddle.get("posted_message_id"))

        try:
            if existing_msg:
                await existing_msg.edit(
                    content=content or None,
                    embed=embed,
                    view=SubmitButtonView(self),
                    allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
                )
                await interaction.followup.send("✅ Riddle post updated.", ephemeral=True)
            else:
                msg = await riddle_channel.send(
                    content=content or None,
                    embed=embed,
                    view=SubmitButtonView(self),
                    allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
                )
                await self._set_riddle_posted_message(riddle["id"], msg.channel.id, msg.id)
                await interaction.followup.send(f"✅ Riddle posted in {msg.channel.mention}.", ephemeral=True)
        except Exception as e:
            logger.exception("riddle_post failed: %s", e)
            await interaction.followup.send("❌ Posting failed.", ephemeral=True)

    @app_commands.command(name="riddle_view", description="Private preview of active riddle + solved preview.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_view(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None:
            await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        riddle = await self._get_open_riddle(interaction.guild.id)
        if not riddle:
            await interaction.followup.send("❌ No active riddle found.", ephemeral=True)
            return

        riddle_no = to_int(riddle.get("riddle_no"), 0)
        if riddle_no <= 0:
            riddle_no = await self._get_next_riddle_number(interaction.guild.id)

        title = f"🧩 Ms Pepper's Goon Hut Riddle\n#{riddle_no} ({now_date_str()})"

        image_url = riddle.get("image_url")
        if not is_http_url(image_url):
            image_url = DEFAULT_IMAGE_URL

        solution_url = riddle.get("solution_url")
        if not is_http_url(solution_url):
            solution_url = image_url

        mention_group = None
        if riddle.get("button_role_id"):
            role = interaction.guild.get_role(to_int(riddle.get("button_role_id"), 0))
            mention_group = role.mention if role else f"(Role ID: {riddle.get('button_role_id')})"

        riddle_embed = discord.Embed(
            title=title,
            description=riddle.get("text") or "*No text*",
            color=discord.Color.blurple()
        )
        riddle_embed.add_field(name="🏆 Award", value=riddle.get("award") or "*None*", inline=False)
        if mention_group:
            riddle_embed.add_field(name="📣 Mention Group", value=mention_group, inline=False)
        riddle_embed.set_image(url=image_url)
        riddle_embed.set_footer(text=footer_text(interaction.guild))

        clean_solution, link = extract_link(riddle.get("solution") or "")
        sol_display = clean_solution or "*None*"
        if link:
            sol_display += f"\n🔗 [🧠**MORE**]({link})"

        solved_embed = discord.Embed(
            title="🎉 Riddle Solved! (Preview)",
            description="**SomeUser** solved the riddle!",
            color=discord.Color.green()
        )
        solved_embed.add_field(name="🧩 Riddle", value=riddle.get("text") or "*Unknown*", inline=False)
        solved_embed.add_field(name="🔍 Proposed Solution", value="*Right Solution*", inline=False)
        solved_embed.add_field(name="✅ Correct Solution", value=sol_display, inline=False)
        solved_embed.add_field(name="🏆 Award", value=riddle.get("award") or "*None*", inline=False)
        if mention_group:
            solved_embed.add_field(name="📣 Mention Group", value=mention_group, inline=False)
        solved_embed.set_image(url=solution_url)
        solved_embed.set_footer(text=footer_text(interaction.guild))

        await interaction.followup.send(
            content="🧪 Private preview:",
            embeds=[riddle_embed, solved_embed],
            ephemeral=True
        )

    @app_commands.command(name="riddle_close", description="Close the active riddle as unsolved.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_close(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        if interaction.guild is None:
            await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        riddle = await self._close_open_riddle(interaction.guild.id, interaction.user.id)
        if not riddle:
            await interaction.followup.send("❌ No active riddle to close.", ephemeral=True)
            return

        await self._cleanup_vote_messages_for_riddle(riddle["id"])

        clean_solution, link = extract_link(riddle.get("solution") or "")
        solution_display = clean_solution or "*None*"
        if link:
            solution_display += f"\n🔗 [🧠**MORE**]({link})"

        solution_url = riddle.get("solution_url")
        if not is_http_url(solution_url):
            solution_url = DEFAULT_IMAGE_URL

        embed = discord.Embed(
            title="🔒 Riddle Closed",
            description="Sadly, nobody could solve the riddle in time.",
            color=discord.Color.red()
        )
        embed.add_field(name="🧩 Riddle", value=riddle.get("text") or "*Unknown*", inline=False)
        embed.add_field(name="✅ Correct Solution", value=solution_display, inline=False)
        embed.add_field(name="🏆 Award", value=riddle.get("award") or "*None*", inline=False)
        embed.set_image(url=solution_url)
        embed.set_footer(text=footer_text(interaction.guild))

        riddle_channel = await self._resolve_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel and hasattr(riddle_channel, "send"):
            mentions = unique_role_mentions(interaction.guild, RIDDLE_ROLE_ID, riddle.get("button_role_id"))
            await riddle_channel.send(
                content=" ".join(dict.fromkeys([m for m in mentions if m])) or None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
            )

        await self._update_original_post(
            {
                "posted_channel_id": riddle.get("posted_channel_id"),
                "posted_message_id": riddle.get("posted_message_id"),
                "riddle_text": riddle.get("text") or "*Unknown*"
            },
            "🔒 Closed",
            f"Nobody solved it.\n{(clean_solution or '*None*').splitlines()[0]}"
        )

        await interaction.followup.send("✅ Riddle closed.", ephemeral=True)

    @app_commands.command(name="riddle_champ", description="Show the riddle champions leaderboard.")
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
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=not visible, thinking=True)

        entries_raw = await self._stats_entries(interaction.guild.id)
        total_solved = sum(s for _, s, _ in entries_raw)

        entries: list[tuple[int, int, float, int]] = [
            (uid, solved, (solved / total_solved * 100.0 if total_solved else 0.0), xp)
            for uid, solved, xp in entries_raw
        ]

        name_cache, avatar_cache = await self._build_identity_cache(interaction.guild, entries_raw)

        view = ChampionsView(
            entries=entries,
            total_solved=total_solved,
            name_cache=name_cache,
            avatar_cache=avatar_cache,
            image_url=image if is_http_url(image) else None,
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
        if isinstance(error, MissingRiddleManagerRole):
            await send_riddle_access_denied(interaction)
            return

        logger.exception("App command error: %s", error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Command error.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Command error.", ephemeral=True)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleSystemSQL(bot))