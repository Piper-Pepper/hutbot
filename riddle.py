from __future__ import annotations

import os
import re
import json
import time
import asyncio
import logging
import datetime as dt
from typing import Optional, Any, Callable, Awaitable

import aiohttp
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

JSONBIN_API_KEY = (os.getenv("JSONBIN_API_KEY") or "").strip()

# Bin IDs
RIDDLE_BIN_ID = (os.getenv("RIDDLE_BIN_ID") or "685442458a456b7966b13207").strip()
SOLVED_BIN_ID = (os.getenv("SOLVED_BIN_ID") or "686699c18960c979a5b67e34").strip()
ARCHIVE_BIN_ID = (os.getenv("ARCHIVE_BIN_ID") or "6869a6fa8960c979a5b7c527").strip()

# Channels / Roles
RIDDLE_CHANNEL_ID = int(os.getenv("RIDDLE_CHANNEL_ID", "1349697597232906292"))
VOTE_CHANNEL_ID = int(os.getenv("VOTE_CHANNEL_ID", "1381754826710585527"))
RIDDLE_ROLE_ID = int(os.getenv("RIDDLE_ROLE_ID", "1380610400416043089"))

# Nur diese Rolle darf: /riddle, /riddle_view, /riddle_close, /riddle_post
RIDDLE_MANAGER_ROLE_ID = int(os.getenv("RIDDLE_MANAGER_ROLE_ID", "1393762463861702787"))

# Bilder
DEFAULT_IMAGE_URL = os.getenv(
    "DEFAULT_RIDDLE_IMAGE_URL",
    "https://cdn.discordapp.com/attachments/1383652563408392232/1462480133737943063/riddle_sexy.gif"
).strip()

ACCESS_DENIED_IMAGE_URL = os.getenv(
    "RIDDLE_ACCESS_DENIED_IMAGE_URL",
    "https://example.com/apply-role-placeholder.jpg"  # Placeholder
).strip()

BIN_BASE = "https://api.jsonbin.io/v3/b"

HTTP_TIMEOUT_SEC = 12
HTTP_RETRIES = 2


# =========================
# HELPERS
# =========================
def is_configured() -> bool:
    return bool(JSONBIN_API_KEY)


def bin_url(bin_id: str, latest: bool = False) -> str:
    return f"{BIN_BASE}/{bin_id}/latest" if latest else f"{BIN_BASE}/{bin_id}"


def headers() -> dict:
    return {
        "X-Master-Key": JSONBIN_API_KEY,
        "Content-Type": "application/json",
    }


def now_date_str() -> str:
    return dt.datetime.now().strftime("%Y/%m/%d")


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


def truncate_text(text: str, max_length: int = 75) -> str:
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


def empty_riddle() -> dict:
    return {
        "text": None,
        "solution": None,
        "award": None,
        "image-url": None,
        "solution-url": None,
        "button-id": None,
        "riddler": None
    }


def normalize_riddle_record(record: dict) -> dict:
    if not isinstance(record, dict):
        return empty_riddle()
    return {
        "text": record.get("text"),
        "solution": record.get("solution"),
        "award": record.get("award"),
        "image-url": record.get("image-url"),
        "solution-url": record.get("solution-url"),
        "button-id": record.get("button-id"),
        "riddler": record.get("riddler")
    }


def has_riddle_data(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    return bool(clean_value(data.get("text")) or clean_value(data.get("solution")))


def get_field_value(embed: discord.Embed, field_name: str) -> Optional[str]:
    for f in embed.fields:
        if f.name.strip().startswith(field_name.strip()):
            return f.value
    return None


def unique_role_mentions(guild: Optional[discord.Guild], *role_ids: Optional[int]) -> list[str]:
    if guild is None:
        return []
    out: list[str] = []
    seen = set()
    for rid in role_ids:
        if not rid or rid in seen:
            continue
        role = guild.get_role(rid)
        if role:
            out.append(role.mention)
            seen.add(rid)
    return out


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
            f"If you want access, you can apply for this role."
        ),
        color=discord.Color.orange()
    )
    embed.set_image(url=ACCESS_DENIED_IMAGE_URL)
    embed.set_footer(text="Application required for access")

    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================
# UI: MODALS / VIEWS
# =========================
class RiddleUpsertModal(Modal):
    def __init__(
        self,
        *,
        cog: "RiddleSystem",
        mode: str,  # "create" | "edit"
        mention_override: Optional[Role],
        current_data: dict,
        on_saved: Optional[Callable[[dict], Awaitable[None]]] = None
    ):
        super().__init__(title="Edit Riddle" if mode == "edit" else "Create Riddle")

        self.cog = cog
        self.mode = mode
        self.mention_override = mention_override
        self.current_data = current_data or empty_riddle()
        self.on_saved = on_saved

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
            default=self.current_data.get("image-url") or "",
            required=False,
            max_length=1000
        )
        self.solution_url = TextInput(
            label="Solution URL",
            default=self.current_data.get("solution-url") or "",
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

        if not is_configured():
            await interaction.followup.send("❌ JSONBIN_API_KEY is missing in .env.", ephemeral=True)
            return

        text = clean_value(self.text.value)
        solution = clean_value(self.solution.value)

        if not text or not solution:
            await interaction.followup.send("❌ Text and Solution cannot be empty.", ephemeral=True)
            return

        old_button_id = clean_value(self.current_data.get("button-id"))
        new_button_id = str(self.mention_override.id) if self.mention_override else old_button_id

        payload = {
            "text": text,
            "solution": solution,
            "award": clean_value(self.award.value),
            "image-url": clean_value(self.image_url.value),
            "solution-url": clean_value(self.solution_url.value),
            "button-id": new_button_id,
            "riddler": str(interaction.user.id)
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        ok = await self.cog._put_bin_record(RIDDLE_BIN_ID, payload)
        if not ok:
            await interaction.followup.send("❌ Failed while saving to JSONBin.", ephemeral=True)
            return

        if self.on_saved:
            try:
                await self.on_saved(payload)
            except Exception:
                pass

        await interaction.followup.send("✅ Updated!" if self.mode == "edit" else "✅ Created!", ephemeral=True)


class SubmitSolutionModal(Modal, title="💡 Submit Your Solution"):
    solution = TextInput(label="Your Answer", style=discord.TextStyle.paragraph, required=True, max_length=4000)

    def __init__(self, cog: "RiddleSystem"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        ok, riddle = await self.cog._fetch_riddle_safe(retries=1)
        if not ok or not clean_value(riddle.get("text")):
            await interaction.followup.send("❌ No active riddle found.", ephemeral=True)
            return

        vote_channel = interaction.client.get_channel(VOTE_CHANNEL_ID)
        if vote_channel is None:
            await interaction.followup.send("❌ Vote channel not found.", ephemeral=True)
            return

        embed = discord.Embed(
            title="📜 New Solution Submitted!",
            description=riddle.get("text", "No riddle"),
            color=discord.Color.gold()
        )
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="🧠 User's Answer", value=self.solution.value or "*Empty*", inline=False)
        embed.add_field(name="✅ Correct Solution", value=riddle.get("solution", "*Not provided*"), inline=False)
        embed.add_field(name="🏆 Award", value=riddle.get("award", "*None*"), inline=False)
        embed.add_field(name="🆔 User ID", value=str(interaction.user.id), inline=False)

        button_id = riddle.get("button-id")
        if button_id:
            embed.add_field(name="🔖 Assigned Group", value=str(button_id), inline=True)

        embed.set_footer(text=footer_text(interaction.guild))

        await vote_channel.send(embed=embed, view=VoteButtons(self.cog))
        await interaction.followup.send("✅ Your answer has been submitted!", ephemeral=True)


class SubmitButton(discord.ui.Button):
    def __init__(self, cog: "RiddleSystem"):
        super().__init__(
            label="💡 Submit Solution",
            style=discord.ButtonStyle.primary,
            custom_id="riddle_submit_solution"
        )
        self.cog = cog

    async def callback(self, interaction: Interaction):
        await interaction.response.send_modal(SubmitSolutionModal(self.cog))


class SubmitButtonView(View):
    def __init__(self, cog: "RiddleSystem"):
        super().__init__(timeout=None)  # persistent
        self.add_item(SubmitButton(cog))


class VoteSuccessButton(discord.ui.Button):
    def __init__(self, cog: "RiddleSystem"):
        super().__init__(
            emoji="👍",
            style=discord.ButtonStyle.success,
            custom_id="riddle_vote_up"
        )
        self.cog = cog

    async def callback(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        msg = interaction.message
        if not msg or not msg.embeds:
            await interaction.followup.send("❌ Couldn't read vote message.", ephemeral=True)
            return

        if not self.cog._acquire_vote_lock(msg.id):
            await interaction.followup.send("⏳ This vote is already being processed.", ephemeral=True)
            return

        try:
            src = msg.embeds[0]
            riddle_text = (src.description or "").strip()
            user_answer = get_field_value(src, "🧠 User's Answer") or "*None*"
            correct_solution_raw = get_field_value(src, "✅ Correct Solution") or "*None*"
            award_text = get_field_value(src, "🏆 Award") or "*None*"
            submitter_id = safe_int(get_field_value(src, "🆔 User ID"), interaction.user.id)
            button_role_id = safe_int(get_field_value(src, "🔖 Assigned Group"), None)

            # sofort Buttons weg, um doppelte Klicks visuell zu blocken
            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass

            # Submitter
            submitter_obj: Optional[discord.abc.User] = None
            if interaction.guild:
                submitter_obj = interaction.guild.get_member(submitter_id)
            if submitter_obj is None:
                try:
                    submitter_obj = await interaction.client.fetch_user(submitter_id)
                except Exception:
                    submitter_obj = None

            submitter_mention = submitter_obj.mention if submitter_obj else f"<@{submitter_id}>"
            submitter_name = str(submitter_obj) if submitter_obj else f"User {submitter_id}"
            submitter_avatar = submitter_obj.display_avatar.url if submitter_obj else None

            # Lösung formatieren
            clean_solution, more_link = extract_link(correct_solution_raw)
            solution_display = clean_solution or "*None*"
            if more_link:
                solution_display += f"\n🔗 [🧠**MORE**]({more_link})"

            # solution image
            ok_r, riddle_record = await self.cog._fetch_riddle_safe(retries=1)
            solution_url = riddle_record.get("solution-url") if ok_r else None
            if not is_http_url(solution_url):
                solution_url = DEFAULT_IMAGE_URL

            solved_embed = discord.Embed(
                title="🎉 Riddle Solved!",
                description=f"**{submitter_mention}** solved the riddle!",
                color=discord.Color.green()
            )
            if submitter_avatar:
                solved_embed.set_author(name=submitter_name, icon_url=submitter_avatar)
            else:
                solved_embed.set_author(name=submitter_name)

            solved_embed.add_field(name="🧩 Riddle", value=truncate_text(riddle_text) or "*Unknown*", inline=False)
            solved_embed.add_field(name="🔍 Proposed Solution", value=user_answer, inline=False)
            solved_embed.add_field(name="✅ Correct Solution", value=solution_display, inline=False)
            solved_embed.add_field(name="🏆 Award", value=award_text, inline=False)
            solved_embed.set_image(url=solution_url)
            solved_embed.set_footer(text=footer_text(interaction.guild))

            # Original-Riddle-Post markieren + Submit-Buttons entfernen
            await self.cog._mark_original_riddle_as_solved(
                riddle_text=riddle_text,
                solver_mention=submitter_mention,
                clean_solution=clean_solution or "*None*",
                more_link=more_link
            )

            # Ergebnis posten
            riddle_channel = interaction.client.get_channel(RIDDLE_CHANNEL_ID)
            if riddle_channel:
                mentions = unique_role_mentions(interaction.guild, RIDDLE_ROLE_ID, button_role_id)
                mentions.append(submitter_mention)
                content = " ".join(dict.fromkeys(mentions)) + "\n🎉 Cock-gratulations💋!"
                await riddle_channel.send(
                    content=content,
                    embed=solved_embed,
                    allowed_mentions=discord.AllowedMentions(roles=True, users=True, everyone=False)
                )

            # Stats + Clear
            await self.cog._update_user_riddle_count(submitter_id, award_text=award_text)
            await self.cog._clear_riddle_data()

            try:
                await msg.delete()
            except discord.HTTPException:
                pass

            await interaction.followup.send("✅ Marked as solved and stats updated.", ephemeral=True)

        finally:
            self.cog._release_vote_lock(msg.id)


class VoteFailButton(discord.ui.Button):
    def __init__(self, cog: "RiddleSystem"):
        super().__init__(
            emoji="👎",
            style=discord.ButtonStyle.danger,
            custom_id="riddle_vote_down"
        )
        self.cog = cog

    async def callback(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        msg = interaction.message
        if not msg or not msg.embeds:
            await interaction.followup.send("❌ Couldn't read vote message.", ephemeral=True)
            return

        if not self.cog._acquire_vote_lock(msg.id):
            await interaction.followup.send("⏳ This vote is already being processed.", ephemeral=True)
            return

        try:
            src = msg.embeds[0]
            riddle_text = (src.description or "").strip()
            user_answer = get_field_value(src, "🧠 User's Answer") or "*None*"
            submitter_id = safe_int(get_field_value(src, "🆔 User ID"), interaction.user.id)
            button_role_id = safe_int(get_field_value(src, "🔖 Assigned Group"), None)

            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass

            submitter_obj: Optional[discord.abc.User] = None
            if interaction.guild:
                submitter_obj = interaction.guild.get_member(submitter_id)
            if submitter_obj is None:
                try:
                    submitter_obj = await interaction.client.fetch_user(submitter_id)
                except Exception:
                    submitter_obj = None

            submitter_mention = submitter_obj.mention if submitter_obj else f"<@{submitter_id}>"
            submitter_name = str(submitter_obj) if submitter_obj else f"User {submitter_id}"
            submitter_avatar = submitter_obj.display_avatar.url if submitter_obj else None

            failed_embed = discord.Embed(
                title="❌ Riddle Not Solved!",
                description=f"**{submitter_mention}**'s solution was incorrect.",
                color=discord.Color.red()
            )
            if submitter_avatar:
                failed_embed.set_author(name=submitter_name, icon_url=submitter_avatar)
            else:
                failed_embed.set_author(name=submitter_name)

            failed_embed.add_field(name="🧩 Riddle", value=truncate_text(riddle_text) or "*Unknown*", inline=False)
            failed_embed.add_field(name="🔍 Proposed Solution", value=user_answer, inline=False)
            failed_embed.add_field(name="❌ Result", value="*Better luck next time!*", inline=False)
            failed_embed.set_footer(text=footer_text(interaction.guild))

            riddle_channel = interaction.client.get_channel(RIDDLE_CHANNEL_ID)
            if riddle_channel:
                mentions = unique_role_mentions(interaction.guild, RIDDLE_ROLE_ID, button_role_id)
                mentions.append(submitter_mention)
                await riddle_channel.send(
                    content=" ".join(dict.fromkeys(mentions)),
                    embed=failed_embed,
                    allowed_mentions=discord.AllowedMentions(roles=True, users=True, everyone=False)
                )

            try:
                await msg.delete()
            except discord.HTTPException:
                pass

            await interaction.followup.send("❌ Marked as incorrect!", ephemeral=True)

        finally:
            self.cog._release_vote_lock(msg.id)


class VoteButtons(View):
    def __init__(self, cog: "RiddleSystem"):
        super().__init__(timeout=None)  # persistent
        self.add_item(VoteSuccessButton(cog))
        self.add_item(VoteFailButton(cog))


# =========================
# CHAMPIONS VIEW
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

        self.page1_image_url = image_url or DEFAULT_IMAGE_URL
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
            embed.add_field(name="No data yet", value="No riddles have been solved yet.", inline=False)
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
class RiddleSystem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.session: Optional[aiohttp.ClientSession] = None

        self._cache_lock = asyncio.Lock()
        self._riddle_cache = empty_riddle()
        self._riddle_cache_ts = 0.0
        self._riddle_cache_ready = False
        self._cache_task: Optional[asyncio.Task] = None

        self._vote_locks: set[int] = set()

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

        # persistent views
        self.bot.add_view(SubmitButtonView(self))
        self.bot.add_view(VoteButtons(self))

        # cache worker
        self._cache_task = asyncio.create_task(self._cache_worker())
        logger.info("RiddleSystem loaded (persistent views + cache worker active).")

    def cog_unload(self):
        if self._cache_task and not self._cache_task.done():
            self._cache_task.cancel()

        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    # ---------- locks ----------
    def _acquire_vote_lock(self, message_id: int) -> bool:
        if message_id in self._vote_locks:
            return False
        self._vote_locks.add(message_id)
        return True

    def _release_vote_lock(self, message_id: int):
        self._vote_locks.discard(message_id)

    # ---------- cache ----------
    async def _cache_worker(self):
        await asyncio.sleep(2)
        while True:
            try:
                ok, data = await self._fetch_riddle_safe(retries=1)
                if ok:
                    async with self._cache_lock:
                        self._riddle_cache = data
                        self._riddle_cache_ts = time.monotonic()
                        self._riddle_cache_ready = True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"cache worker error: {e}")
            await asyncio.sleep(45)

    async def _update_riddle_cache(self, data: dict):
        async with self._cache_lock:
            self._riddle_cache = normalize_riddle_record(data)
            self._riddle_cache_ts = time.monotonic()
            self._riddle_cache_ready = True

    async def _get_riddle_for_modal(self) -> Optional[dict]:
        # 1) quick live fetch
        try:
            ok, data = await asyncio.wait_for(self._fetch_riddle_safe(retries=0), timeout=1.7)
            if ok:
                await self._update_riddle_cache(data)
                return data
        except asyncio.TimeoutError:
            logger.warning("/riddle live fetch timeout, using cache fallback")
        except Exception as e:
            logger.warning(f"/riddle live fetch failed: {e}")

        # 2) cache fallback
        async with self._cache_lock:
            if self._riddle_cache_ready:
                return dict(self._riddle_cache)

        return None

    # ---------- JSONBin ----------
    async def _jsonbin_request(
        self,
        method: str,
        url: str,
        *,
        payload: Optional[Any] = None,
        retries: int = HTTP_RETRIES,
        timeout_sec: int = HTTP_TIMEOUT_SEC
    ) -> tuple[bool, int, Any]:
        if not is_configured():
            logger.error("JSONBIN_API_KEY missing.")
            return False, 0, {}

        if self.session is None or self.session.closed:
            logger.error("HTTP session not available.")
            return False, 0, {}

        last_status = 0
        last_data: Any = {}
        backoff = 0.4

        for attempt in range(retries + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=timeout_sec)
                async with self.session.request(method, url, headers=headers(), json=payload, timeout=timeout) as resp:
                    last_status = resp.status
                    raw_text = await resp.text()

                    parsed: Any = {}
                    if raw_text:
                        try:
                            parsed = json.loads(raw_text)
                        except json.JSONDecodeError:
                            parsed = {}

                    if 200 <= resp.status < 300:
                        return True, resp.status, parsed

                    retryable = (resp.status == 429) or (500 <= resp.status < 600)
                    logger.warning(
                        "JSONBin %s failed: status=%s retryable=%s attempt=%s/%s",
                        method, resp.status, retryable, attempt + 1, retries + 1
                    )
                    last_data = parsed

                    if not retryable or attempt >= retries:
                        return False, resp.status, parsed

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning("JSONBin %s exception: %s (attempt %s/%s)", method, e, attempt + 1, retries + 1)
                if attempt >= retries:
                    break
            except Exception as e:
                logger.exception("JSONBin %s unexpected exception: %s", method, e)
                if attempt >= retries:
                    break

            await asyncio.sleep(backoff)
            backoff *= 2

        return False, last_status, last_data

    async def _get_bin_record(self, bin_id: str, *, retries: int = HTTP_RETRIES, default: Any = None) -> Any:
        ok, _, data = await self._jsonbin_request("GET", bin_url(bin_id, latest=True), retries=retries)
        if not ok:
            return default
        if not isinstance(data, dict):
            return default
        return data.get("record", default)

    async def _put_bin_record(self, bin_id: str, value: Any, *, retries: int = HTTP_RETRIES) -> bool:
        ok, _, _ = await self._jsonbin_request("PUT", bin_url(bin_id), payload=value, retries=retries)
        return ok

    async def _fetch_riddle_safe(self, *, retries: int = HTTP_RETRIES) -> tuple[bool, dict]:
        record = await self._get_bin_record(RIDDLE_BIN_ID, retries=retries, default={})
        if not isinstance(record, dict):
            return False, empty_riddle()
        return True, normalize_riddle_record(record)

    # ---------- domain ----------
    async def _clear_riddle_data(self):
        await self._put_bin_record(RIDDLE_BIN_ID, empty_riddle())

    async def _archive_riddle(self, riddle_data: dict):
        archive = await self._get_bin_record(ARCHIVE_BIN_ID, default=[], retries=HTTP_RETRIES)
        if not isinstance(archive, list):
            archive = []

        entry = {
            "text": riddle_data.get("text", "*Unknown*"),
            "solution": riddle_data.get("solution", "*None*"),
            "date": dt.datetime.utcnow().strftime("%Y-%m-%d")
        }
        archive.append(entry)
        await self._put_bin_record(ARCHIVE_BIN_ID, archive)

    async def _get_total_solved(self) -> int:
        raw = await self._get_bin_record(SOLVED_BIN_ID, default={}, retries=HTTP_RETRIES)
        if not isinstance(raw, dict):
            return 0

        total = 0
        for _, stats in raw.items():
            if isinstance(stats, dict):
                total += max(0, to_int(stats.get("solved_riddles", 0), default=0))
            else:
                total += max(0, to_int(stats, default=0))
        return total

    async def _get_next_riddle_number(self) -> int:
        return (await self._get_total_solved()) + 1

    async def _update_user_riddle_count(self, user_id: int, award_text: str):
        users = await self._get_bin_record(SOLVED_BIN_ID, default={}, retries=HTTP_RETRIES)
        if not isinstance(users, dict):
            users = {}

        uid = str(user_id)
        if uid not in users or not isinstance(users[uid], dict):
            users[uid] = {"solved_riddles": 0, "xp": 0}

        xp_award = 0
        m = re.search(r"\d+", str(award_text or ""))
        if m:
            xp_award = int(m.group())

        users[uid]["solved_riddles"] = max(0, to_int(users[uid].get("solved_riddles", 0))) + 1
        users[uid]["xp"] = max(0, to_int(users[uid].get("xp", 0))) + xp_award

        await self._put_bin_record(SOLVED_BIN_ID, users)

    async def _mark_original_riddle_as_solved(
        self,
        *,
        riddle_text: str,
        solver_mention: str,
        clean_solution: str,
        more_link: Optional[str]
    ):
        channel = self.bot.get_channel(RIDDLE_CHANNEL_ID)
        if channel is None:
            return

        target = (riddle_text or "").strip().lower()
        if not target:
            return

        try:
            async for msg in channel.history(limit=400):
                if not msg.embeds:
                    continue

                for idx, emb in enumerate(msg.embeds):
                    desc = (emb.description or "").strip().lower()
                    if desc != target:
                        continue

                    solved_note = f"✅ Solved by {solver_mention}\n{clean_solution.splitlines()[0] if clean_solution else '*None*'}"
                    if more_link:
                        solved_note += f"\n🔗 [🧠**MORE**]({more_link})"

                    upd = discord.Embed.from_dict(emb.to_dict())
                    upd.add_field(name="✅ Solved", value=solved_note, inline=False)
                    upd.set_footer(text=footer_text(msg.guild))

                    embeds = list(msg.embeds)
                    embeds[idx] = upd

                    await msg.edit(embeds=embeds, view=None)
                    return
        except Exception as e:
            logger.warning("Failed to update original riddle post: %s", e)

    async def _resolve_identity(self, guild: discord.Guild, uid: int) -> tuple[str, Optional[str]]:
        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                member = None

        if member is not None:
            return member.display_name, member.display_avatar.url

        user = self.bot.get_user(uid)
        if user is None:
            try:
                user = await self.bot.fetch_user(uid)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                user = None

        if user is not None:
            return (user.global_name or user.name), user.display_avatar.url

        return f"Unknown User ({uid})", None

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
                name, avatar = await self._resolve_identity(guild, uid)
                name_cache[uid] = name
                if avatar:
                    avatar_cache[uid] = avatar

        await asyncio.gather(*(worker(uid) for uid in unique_ids))
        return name_cache, avatar_cache

    # ---------- commands ----------
    @app_commands.command(name="riddle", description="Create a new riddle or edit the stored riddle.")
    @app_commands.describe(mention="Optional role to store as the riddle mention/button role.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle(self, interaction: Interaction, mention: Optional[Role] = None):
        if not is_configured():
            await interaction.response.send_message("❌ JSONBIN_API_KEY is missing in .env.", ephemeral=True)
            return

        data = await self._get_riddle_for_modal()
        if data is None:
            await interaction.response.send_message(
                "❌ Could not load the current riddle in time. Please run `/riddle` again.",
                ephemeral=True
            )
            return

        mode = "edit" if has_riddle_data(data) else "create"
        modal = RiddleUpsertModal(
            cog=self,
            mode=mode,
            mention_override=mention,
            current_data=data,
            on_saved=self._update_riddle_cache
        )

        try:
            await interaction.response.send_modal(modal)
        except discord.NotFound:
            logger.warning("send_modal failed: interaction expired (10062)")
        except discord.HTTPException as e:
            logger.exception("send_modal HTTPException: %s", e)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Interaction expired. Please run `/riddle` again.", ephemeral=True)

    @app_commands.command(name="riddle_post", description="Post the current riddle in the riddle channel.")
    @app_commands.guild_only()
    @app_commands.describe(ping_role="Optional additional role to ping")
    @riddle_manager_required()
    async def riddle_post(self, interaction: Interaction, ping_role: Optional[Role] = None):
        await interaction.response.defer(ephemeral=True)

        if not is_configured():
            await interaction.followup.send("❌ JSONBIN_API_KEY is missing in .env.", ephemeral=True)
            return

        ok, riddle = await self._fetch_riddle_safe(retries=1)
        if not ok:
            await interaction.followup.send("❌ Failed to load riddle data.", ephemeral=True)
            return

        text = clean_value(riddle.get("text"))
        solution = clean_value(riddle.get("solution"))
        if not text or not solution:
            await interaction.followup.send("❌ There is currently no active riddle.", ephemeral=True)
            return

        next_num = await self._get_next_riddle_number()
        title = f"🧩Ms Pepper's 𝕲𝖔𝖔𝖓 𝕳𝖚𝖙 𝕽𝖎𝖉𝖉𝖑𝖊\n#️{next_num} ({now_date_str()})"

        image_url = riddle.get("image-url")
        if not is_http_url(image_url):
            image_url = DEFAULT_IMAGE_URL

        button_role_id = safe_int(riddle.get("button-id"), None)
        mentions = unique_role_mentions(interaction.guild, RIDDLE_ROLE_ID, button_role_id)
        if ping_role:
            mentions.append(ping_role.mention)

        embed = discord.Embed(
            title=title,
            description=text,
            color=discord.Color.blurple()
        )
        embed.add_field(name="🏆 Award", value=riddle.get("award", "*None*"), inline=False)
        embed.set_image(url=image_url)
        embed.set_footer(text=footer_text(interaction.guild))

        riddle_channel = self.bot.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel is None:
            await interaction.followup.send("❌ Riddle channel not found.", ephemeral=True)
            return

        await riddle_channel.send(
            content=" ".join(dict.fromkeys(mentions)),
            embed=embed,
            view=SubmitButtonView(self),
            allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
        )
        await interaction.followup.send(f"✅ Riddle posted to {riddle_channel.mention}.", ephemeral=True)

    @app_commands.command(name="riddle_view", description="Private preview of current riddle + solved preview.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_view(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        if not is_configured():
            await interaction.followup.send("❌ JSONBIN_API_KEY is missing in .env.", ephemeral=True)
            return

        ok, riddle = await self._fetch_riddle_safe(retries=1)
        if not ok or not clean_value(riddle.get("text")):
            await interaction.followup.send("❌ No active riddle found.", ephemeral=True)
            return

        next_num = await self._get_next_riddle_number()
        title = f"🧩Ms Pepper's 𝕲𝖔𝖔𝖓 𝕳𝖚𝖙 𝕽𝖎𝖉𝖉𝖑𝖊\n#️{next_num} ({now_date_str()})"

        image_url = riddle.get("image-url")
        if not is_http_url(image_url):
            image_url = DEFAULT_IMAGE_URL

        solution_url = riddle.get("solution-url")
        if not is_http_url(solution_url):
            solution_url = image_url

        mention_group = None
        button_role_id = safe_int(riddle.get("button-id"), None)
        if button_role_id and interaction.guild:
            role = interaction.guild.get_role(button_role_id)
            mention_group = role.mention if role else f"(Role ID: {button_role_id})"

        riddle_embed = discord.Embed(
            title=title,
            description=riddle.get("text", "*No text*"),
            color=discord.Color.blurple()
        )
        riddle_embed.add_field(name="🏆 Award", value=riddle.get("award", "*None*"), inline=False)
        if mention_group:
            riddle_embed.add_field(name="📣 Mention Group", value=mention_group, inline=False)
        riddle_embed.set_image(url=image_url)
        riddle_embed.set_footer(text=footer_text(interaction.guild))

        raw_solution = riddle.get("solution", "*None*")
        clean_solution, link = extract_link(raw_solution)
        sol_display = clean_solution or "*None*"
        if link:
            sol_display += f"\n🔗 [🧠**MORE**]({link})"

        solved_embed = discord.Embed(
            title="🎉 Riddle Solved! (Preview)",
            description="**SomeUser** solved the riddle!",
            color=discord.Color.green()
        )
        solved_embed.add_field(name="🧩 Riddle", value=riddle.get("text", "*Unknown*"), inline=False)
        solved_embed.add_field(name="🔍 Proposed Solution", value="*Right Solution*", inline=False)
        solved_embed.add_field(name="✅ Correct Solution", value=sol_display, inline=False)
        solved_embed.add_field(name="🏆 Award", value=riddle.get("award", "*None*"), inline=False)
        if mention_group:
            solved_embed.add_field(name="📣 Mention Group", value=mention_group, inline=False)
        solved_embed.set_image(url=solution_url)
        solved_embed.set_footer(text=footer_text(interaction.guild))

        await interaction.followup.send(
            content="🧪 Private preview:",
            embeds=[riddle_embed, solved_embed],
            ephemeral=True
        )

    @app_commands.command(name="riddle_close", description="Close current riddle as unsolved and archive it.")
    @app_commands.guild_only()
    @riddle_manager_required()
    async def riddle_close(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        if not is_configured():
            await interaction.followup.send("❌ JSONBIN_API_KEY is missing in .env.", ephemeral=True)
            return

        ok, riddle = await self._fetch_riddle_safe(retries=1)
        if not ok:
            await interaction.followup.send("❌ Failed to load riddle data.", ephemeral=True)
            return

        if not clean_value(riddle.get("text")):
            await interaction.followup.send("❌ No active riddle to close.", ephemeral=True)
            return

        raw_solution = riddle.get("solution", "*None*")
        clean_solution, link = extract_link(raw_solution)
        solution_display = clean_solution or "*None*"
        if link:
            solution_display += f"\n🔗 [🧠**MORE**]({link})"

        solution_url = riddle.get("solution-url")
        if not is_http_url(solution_url):
            solution_url = DEFAULT_IMAGE_URL

        button_role_id = safe_int(riddle.get("button-id"), None)
        mentions = unique_role_mentions(interaction.guild, RIDDLE_ROLE_ID, button_role_id)

        embed = discord.Embed(
            title="🔒 Riddle Closed",
            description="Sadly, nobody could solve the Riddle in time...",
            color=discord.Color.red()
        )
        embed.add_field(name="🧩 Riddle", value=riddle.get("text", "*Unknown*"), inline=False)
        embed.add_field(name="✅ Correct Solution", value=solution_display, inline=False)
        embed.add_field(name="🏆 Award", value=riddle.get("award", "*None*"), inline=False)
        embed.set_image(url=solution_url)
        embed.set_footer(text=footer_text(interaction.guild))

        riddle_channel = self.bot.get_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            await riddle_channel.send(content=" ".join(mentions), embed=embed)

        await self._archive_riddle(riddle)
        await self._clear_riddle_data()
        await self._update_riddle_cache(empty_riddle())

        await interaction.followup.send("✅ Riddle closed, archived and cleared.", ephemeral=True)

    @app_commands.command(name="riddle_champ", description="Show the riddle champions leaderboard.")
    @app_commands.describe(
        visible="If true, send publicly. If false, send only to you.",
        image="Optional custom image URL for page 1.",
        mention="Optional role mention (only used when visible=true)."
    )
    @app_commands.guild_only()
    async def riddle_champ(
        self,
        interaction: Interaction,
        visible: Optional[bool] = False,
        image: Optional[str] = None,
        mention: Optional[Role] = None
    ):
        if not is_configured():
            await interaction.response.send_message("❌ JSONBIN_API_KEY is missing in .env.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=not visible, thinking=True)

        raw = await self._get_bin_record(SOLVED_BIN_ID, retries=HTTP_RETRIES, default={})
        if not isinstance(raw, dict):
            raw = {}

        entries_raw: list[tuple[int, int, int]] = []
        for uid, stats in raw.items():
            uid_i = to_int(uid, default=-1)
            if uid_i <= 0:
                continue

            solved = 0
            xp = 0
            if isinstance(stats, dict):
                solved = max(0, to_int(stats.get("solved_riddles", 0), default=0))
                xp = max(0, to_int(stats.get("xp", 0), default=0))
            else:
                solved = max(0, to_int(stats, default=0))
                xp = 0

            entries_raw.append((uid_i, solved, xp))

        entries_raw.sort(key=lambda x: (x[1], x[2]), reverse=True)
        total_solved = sum(s for _, s, _ in entries_raw)

        entries: list[tuple[int, int, float, int]] = [
            (uid, solved, (solved / total_solved * 100.0 if total_solved else 0.0), xp)
            for uid, solved, xp in entries_raw
        ]

        if interaction.guild is None:
            await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        name_cache, avatar_cache = await self._build_identity_cache(interaction.guild, entries_raw)

        view = ChampionsView(
            entries=entries,
            total_solved=total_solved,
            name_cache=name_cache,
            avatar_cache=avatar_cache,
            image_url=image,
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

    # ---------- error handling ----------
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
    await bot.add_cog(RiddleSystem(bot))