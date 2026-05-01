import os
import re
import asyncio
import logging
from datetime import datetime
from typing import Optional, Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# =========================
# Setup / Config
# =========================
load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("riddle_cog")

BIN_BASE_URL = "https://api.jsonbin.io/v3/b"
DEFAULT_IMAGE_URL = "https://cdn.discordapp.com/attachments/1383652563408392232/1384269191971868753/riddle_logo.jpg"


def env_required(name: str) -> str:
    value = os.getenv(name)
    if not value or not value.strip():
        raise RuntimeError(f"Missing required env var: {name}")
    return value.strip()


def env_optional(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def env_int(name: str, default: Optional[int] = None, required: bool = False) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        if required:
            raise RuntimeError(f"Missing required env var: {name}")
        if default is None:
            raise RuntimeError(f"Missing env var: {name} and no default")
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(f"Env var {name} must be int, got: {raw}") from e


JSONBIN_API_KEY = env_required("JSONBIN_API_KEY")

# Bin IDs
RIDDLE_BIN_ID = env_required("RIDDLE_BIN_ID")
ARCHIVE_BIN_ID = env_optional("ARCHIVE_BIN_ID")  # optional -> no hard crash
SOLVED_BIN_ID = env_required("SOLVED_BIN_ID")

# Discord IDs
RIDDLE_CHANNEL_ID = env_int("RIDDLE_CHANNEL_ID", required=True)
VOTE_CHANNEL_ID = env_int("VOTE_CHANNEL_ID", required=True)
RIDDLE_ROLE_ID = env_int("RIDDLE_ROLE_ID", required=True)
REQUIRED_ROLE_ID = env_int("REQUIRED_ROLE_ID", required=True)

HEADERS = {"X-Master-Key": JSONBIN_API_KEY}
PUT_HEADERS = {"X-Master-Key": JSONBIN_API_KEY, "Content-Type": "application/json"}

HTTP_RETRIES = 2


# =========================
# Utils
# =========================
def bin_url(bin_id: str, latest: bool = False) -> str:
    return f"{BIN_BASE_URL}/{bin_id}/latest" if latest else f"{BIN_BASE_URL}/{bin_id}"


def now_date_str() -> str:
    return datetime.now().strftime("%Y/%m/%d")


def footer_text(guild: Optional[discord.Guild]) -> str:
    guild_name = guild.name if guild else "Unknown Guild"
    return f"{guild_name} ({now_date_str()})"


def truncate_text(text: str, max_length: int = 75) -> str:
    if text and len(text) > max_length:
        return text[:max_length] + "[...]"
    return text or ""


def embed_safe(text: Any, max_length: int = 1024, fallback: str = "*None*") -> str:
    s = str(text or "").strip()
    if not s:
        return fallback
    if len(s) > max_length:
        return s[: max_length - 5] + "[...]"
    return s


def extract_link(text: str) -> tuple[str, Optional[str]]:
    text = text or ""
    match = re.search(r"(https?://\S+)", text)
    if match:
        link = match.group(1)
        clean = text.replace(link, "").strip()
        return clean, link
    return text.strip(), None


def get_field_value(embed: discord.Embed, field_name: str) -> Optional[str]:
    for field in embed.fields:
        if field.name.strip().startswith(field_name.strip()):
            return field.value
    return None


def safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def unique_role_mentions(guild: Optional[discord.Guild], *role_ids: Optional[int]) -> list[str]:
    if not guild:
        return []
    out = []
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
# Views / Buttons
# =========================
class SubmitSolutionModal(discord.ui.Modal, title="💡 Submit Your Solution"):
    solution = discord.ui.TextInput(
        label="Your Answer",
        style=discord.TextStyle.paragraph,
        max_length=1500
    )

    def __init__(self, cog: "RiddleCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        riddle = await self.cog.get_bin_record(RIDDLE_BIN_ID, default={})
        if not isinstance(riddle, dict) or not riddle.get("text"):
            await interaction.followup.send("❌ No active riddle found.", ephemeral=True)
            return

        vote_channel = await self.cog.resolve_channel(VOTE_CHANNEL_ID)
        if not vote_channel:
            await interaction.followup.send("❌ Vote channel not found.", ephemeral=True)
            return

        embed = discord.Embed(
            title="📜 New Solution Submitted!",
            description=embed_safe(riddle.get("text", "No riddle"), max_length=4000, fallback="No riddle"),
            color=discord.Color.gold()
        )
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="🧠 User's Answer", value=embed_safe(self.solution.value), inline=False)
        embed.add_field(name="✅ Correct Solution", value=embed_safe(riddle.get("solution", "*Not provided*")), inline=False)
        embed.add_field(name="🏆 Award", value=embed_safe(riddle.get("award", "*None*")), inline=False)
        embed.add_field(name="🆔 User ID", value=str(interaction.user.id), inline=False)

        button_id = riddle.get("button-id")
        if button_id:
            embed.add_field(name="🔖 Assigned Group", value=str(button_id), inline=True)

        embed.set_footer(text=footer_text(interaction.guild))

        await vote_channel.send(embed=embed, view=VoteButtons(self.cog))
        await interaction.followup.send("✅ Your answer has been submitted!", ephemeral=True)


class SubmitButton(discord.ui.Button):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(
            label="💡 Submit Solution",
            style=discord.ButtonStyle.primary,
            custom_id="submit_solution"
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SubmitSolutionModal(self.cog))


class SubmitButtonView(discord.ui.View):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(timeout=None)  # persistent
        self.add_item(SubmitButton(cog))


class VoteSuccessButton(discord.ui.Button):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(
            emoji="👍",
            style=discord.ButtonStyle.success,
            custom_id="riddle_upvote"
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        msg = interaction.message
        if not msg or not msg.embeds:
            await interaction.followup.send("❌ Could not read vote message.", ephemeral=True)
            return

        if not self.cog.acquire_vote_lock(msg.id):
            await interaction.followup.send("⏳ Vote is already being processed.", ephemeral=True)
            return

        try:
            embed = msg.embeds[0]

            riddle_text = (embed.description or "").strip()
            user_answer = get_field_value(embed, "🧠 User's Answer") or "*None*"
            correct_solution_raw = get_field_value(embed, "✅ Correct Solution") or "*None*"
            award = get_field_value(embed, "🏆 Award") or "*None*"
            submitter_id = safe_int(get_field_value(embed, "🆔 User ID"), interaction.user.id)
            button_role_id = safe_int(get_field_value(embed, "🔖 Assigned Group"), None)

            try:
                submitter = interaction.guild.get_member(submitter_id) if interaction.guild else None
                if submitter is None:
                    submitter = await interaction.client.fetch_user(submitter_id)
                submitter_mention = submitter.mention
                submitter_name = str(submitter)
                submitter_avatar = submitter.display_avatar.url
            except Exception:
                submitter_mention = f"<@{submitter_id}>"
                submitter_name = f"User {submitter_id}"
                submitter_avatar = None

            # disable buttons immediately
            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass

            clean_solution, solution_link = extract_link(correct_solution_raw)
            correct_display = embed_safe(clean_solution, fallback="*None*")
            if solution_link:
                correct_display += f"\n🔗 [🧠**MORE**]({solution_link})"

            current_riddle = await self.cog.get_bin_record(RIDDLE_BIN_ID, default={})
            solution_url = (current_riddle or {}).get("solution-url") or DEFAULT_IMAGE_URL
            if not str(solution_url).startswith("http"):
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

            solved_embed.add_field(name="🧩 Riddle", value=embed_safe(truncate_text(riddle_text), fallback="*Unknown*"), inline=False)
            solved_embed.add_field(name="🔍 Proposed Solution", value=embed_safe(user_answer), inline=False)
            solved_embed.add_field(name="✅ Correct Solution", value=embed_safe(correct_display), inline=False)
            solved_embed.add_field(name="🏆 Award", value=embed_safe(award), inline=False)
            solved_embed.set_image(url=solution_url)
            solved_embed.set_footer(text=footer_text(interaction.guild))

            await self.cog.mark_original_riddle_as_solved(
                riddle_text=riddle_text,
                solver_mention=submitter_mention,
                clean_solution=clean_solution or "*None*",
                more_link=solution_link
            )

            riddle_channel = await self.cog.resolve_channel(RIDDLE_CHANNEL_ID)
            if riddle_channel:
                mentions = unique_role_mentions(interaction.guild, RIDDLE_ROLE_ID, button_role_id)
                mentions.append(submitter_mention)
                content = " ".join(dict.fromkeys(mentions)) + "\n🎉 Cock-gratulations💋!"
                await riddle_channel.send(
                    content=content,
                    embed=solved_embed,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False)
                )

            await self.cog.update_user_riddle_count(submitter_id, award_text=award)
            await self.cog.clear_riddle_data()

            try:
                await msg.delete()
            except discord.HTTPException:
                pass

            await interaction.followup.send("✅ Marked as solved and user stats updated.", ephemeral=True)

        finally:
            self.cog.release_vote_lock(msg.id)


class VoteFailButton(discord.ui.Button):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(
            emoji="👎",
            style=discord.ButtonStyle.danger,
            custom_id="riddle_downvote"
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        msg = interaction.message
        if not msg or not msg.embeds:
            await interaction.followup.send("❌ Could not read vote message.", ephemeral=True)
            return

        if not self.cog.acquire_vote_lock(msg.id):
            await interaction.followup.send("⏳ Vote is already being processed.", ephemeral=True)
            return

        try:
            embed = msg.embeds[0]
            riddle_text = (embed.description or "").strip()
            user_answer = get_field_value(embed, "🧠 User's Answer") or "*None*"
            submitter_id = safe_int(get_field_value(embed, "🆔 User ID"), interaction.user.id)
            button_role_id = safe_int(get_field_value(embed, "🔖 Assigned Group"), None)

            try:
                submitter = interaction.guild.get_member(submitter_id) if interaction.guild else None
                if submitter is None:
                    submitter = await interaction.client.fetch_user(submitter_id)
                submitter_mention = submitter.mention
                submitter_name = str(submitter)
                submitter_avatar = submitter.display_avatar.url
            except Exception:
                submitter_mention = f"<@{submitter_id}>"
                submitter_name = f"User {submitter_id}"
                submitter_avatar = None

            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass

            failed_embed = discord.Embed(
                title="❌ Riddle Not Solved!",
                description=f"**{submitter_mention}**'s solution was incorrect.",
                color=discord.Color.red()
            )
            if submitter_avatar:
                failed_embed.set_author(name=submitter_name, icon_url=submitter_avatar)
            else:
                failed_embed.set_author(name=submitter_name)

            failed_embed.add_field(name="🧩 Riddle", value=embed_safe(truncate_text(riddle_text), fallback="*Unknown*"), inline=False)
            failed_embed.add_field(name="🔍 Proposed Solution", value=embed_safe(user_answer), inline=False)
            failed_embed.add_field(name="❌ Result", value="*Better luck next time!*", inline=False)
            failed_embed.set_footer(text=footer_text(interaction.guild))

            riddle_channel = await self.cog.resolve_channel(RIDDLE_CHANNEL_ID)
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
            self.cog.release_vote_lock(msg.id)


class VoteButtons(discord.ui.View):
    def __init__(self, cog: "RiddleCog"):
        super().__init__(timeout=None)  # persistent
        self.add_item(VoteSuccessButton(cog))
        self.add_item(VoteFailButton(cog))


# =========================
# Cog
# =========================
class RiddleCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self._vote_locks: set[int] = set()

    async def cog_load(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.bot.add_view(SubmitButtonView(self))
        self.bot.add_view(VoteButtons(self))
        log.info("RiddleCog loaded with persistent views.")

    def cog_unload(self):
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    def acquire_vote_lock(self, message_id: int) -> bool:
        if message_id in self._vote_locks:
            return False
        self._vote_locks.add(message_id)
        return True

    def release_vote_lock(self, message_id: int):
        self._vote_locks.discard(message_id)

    async def resolve_channel(self, channel_id: int) -> Optional[discord.abc.Messageable]:
        ch = self.bot.get_channel(channel_id)
        if ch is not None:
            return ch
        try:
            ch = await self.bot.fetch_channel(channel_id)
            return ch
        except Exception:
            return None

    # ---------- JSONBin helpers ----------
    async def get_bin_record(self, bin_id: str, default: Any = None) -> Any:
        assert self.session is not None

        last_error = None
        for attempt in range(HTTP_RETRIES + 1):
            try:
                async with self.session.get(bin_url(bin_id, latest=True), headers=HEADERS) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        log.error("GET latest %s failed (%s): %s", bin_id, resp.status, body)
                        if resp.status in (429, 500, 502, 503, 504) and attempt < HTTP_RETRIES:
                            await asyncio.sleep(0.7 * (attempt + 1))
                            continue
                        return default

                    data = await resp.json()
                    record = data.get("record", default)

                    # backwards-compat fix if data accidentally got stored as {"record": {...}}
                    if isinstance(record, dict) and "record" in record and isinstance(record["record"], dict):
                        return record["record"]

                    return record

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                if attempt < HTTP_RETRIES:
                    await asyncio.sleep(0.7 * (attempt + 1))
                    continue
                break
            except Exception as e:
                log.exception("Unexpected error in get_bin_record(%s): %s", bin_id, e)
                return default

        log.error("GET latest %s failed after retries: %s", bin_id, last_error)
        return default

    async def put_bin_record(self, bin_id: str, value: Any) -> bool:
        assert self.session is not None

        last_error = None
        for attempt in range(HTTP_RETRIES + 1):
            try:
                async with self.session.put(bin_url(bin_id), headers=PUT_HEADERS, json=value) as resp:
                    if resp.status in (200, 201):
                        return True

                    body = await resp.text()
                    log.error("PUT %s failed (%s): %s", bin_id, resp.status, body)
                    if resp.status in (429, 500, 502, 503, 504) and attempt < HTTP_RETRIES:
                        await asyncio.sleep(0.7 * (attempt + 1))
                        continue
                    return False

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                if attempt < HTTP_RETRIES:
                    await asyncio.sleep(0.7 * (attempt + 1))
                    continue
                break
            except Exception as e:
                log.exception("Unexpected error in put_bin_record(%s): %s", bin_id, e)
                return False

        log.error("PUT %s failed after retries: %s", bin_id, last_error)
        return False

    # ---------- Domain helpers ----------
    async def clear_riddle_data(self):
        empty = {
            "text": None,
            "solution": None,
            "award": None,
            "image-url": None,
            "solution-url": None,
            "button-id": None
        }
        # FIX: write plain object, not {"record": ...}
        await self.put_bin_record(RIDDLE_BIN_ID, empty)

    async def archive_current_riddle(self, riddle_data: dict):
        if not ARCHIVE_BIN_ID:
            log.warning("ARCHIVE_BIN_ID missing -> skipping archive.")
            return

        entry = {
            "text": riddle_data.get("text", "*Unknown*"),
            "solution": riddle_data.get("solution", "*None*"),
            "date": datetime.utcnow().strftime("%Y-%m-%d")
        }
        archive_list = await self.get_bin_record(ARCHIVE_BIN_ID, default=[])
        if not isinstance(archive_list, list):
            archive_list = []
        archive_list.append(entry)
        await self.put_bin_record(ARCHIVE_BIN_ID, archive_list)

    async def get_total_solved(self) -> int:
        users = await self.get_bin_record(SOLVED_BIN_ID, default={})
        if not isinstance(users, dict):
            return 0
        total = 0
        for user_data in users.values():
            if isinstance(user_data, dict):
                total += int(user_data.get("solved_riddles", 0) or 0)
        return total

    async def get_next_riddle_number(self) -> int:
        return (await self.get_total_solved()) + 1

    async def update_user_riddle_count(self, user_id: int, award_text: str):
        users = await self.get_bin_record(SOLVED_BIN_ID, default={})
        if not isinstance(users, dict):
            users = {}

        uid = str(user_id)
        if uid not in users:
            users[uid] = {"solved_riddles": 0, "xp": 0}

        match = re.search(r"\d+", str(award_text or ""))
        xp_award = int(match.group()) if match else 0

        users[uid]["solved_riddles"] = int(users[uid].get("solved_riddles", 0)) + 1
        users[uid]["xp"] = int(users[uid].get("xp", 0)) + xp_award

        await self.put_bin_record(SOLVED_BIN_ID, users)

    async def mark_original_riddle_as_solved(
        self,
        riddle_text: str,
        solver_mention: str,
        clean_solution: str,
        more_link: Optional[str]
    ):
        channel = await self.resolve_channel(RIDDLE_CHANNEL_ID)
        if not channel or not hasattr(channel, "history"):
            return

        try:
            async for msg in channel.history(limit=300):
                if not msg.embeds:
                    continue

                for idx, emb in enumerate(msg.embeds):
                    if (emb.description or "").strip().lower() != riddle_text.strip().lower():
                        continue

                    updated = discord.Embed.from_dict(emb.to_dict())
                    first_line = (clean_solution.splitlines()[0] if clean_solution else "*None*")
                    solved_line = f"✅ Solved by {solver_mention}\n{first_line}"
                    if more_link:
                        solved_line += f"\n🔗 [🧠**MORE**]({more_link})"

                    updated.add_field(name="✅ Solved", value=embed_safe(solved_line), inline=False)
                    updated.set_footer(text=footer_text(msg.guild))

                    embeds = list(msg.embeds)
                    embeds[idx] = updated
                    await msg.edit(embeds=embeds, view=None)
                    return
        except Exception as e:
            log.warning("Failed to update original riddle post: %s", e)

    # ---------- Commands ----------
    @app_commands.command(name="riddle_post", description="Post the current riddle.")
    @app_commands.checks.has_role(REQUIRED_ROLE_ID)
    @app_commands.describe(ping_role="Optional extra role to ping")
    async def riddle_post(self, interaction: discord.Interaction, ping_role: Optional[discord.Role] = None):
        await interaction.response.defer(ephemeral=True)

        riddle = await self.get_bin_record(RIDDLE_BIN_ID, default={})
        if not isinstance(riddle, dict) or not riddle.get("text") or not riddle.get("solution"):
            await interaction.followup.send("❌ There is currently no active riddle.", ephemeral=True)
            return

        next_number = await self.get_next_riddle_number()
        title = f"🧠 Ms Pepper's 🧩𝕲𝖔𝖔𝖓 𝕳𝖚𝖙 𝕽𝖎𝖉𝖉𝖑𝖊 #️⃣{next_number} \n*({now_date_str()})*"

        image_url = riddle.get("image-url") or DEFAULT_IMAGE_URL
        if not str(image_url).startswith("http"):
            image_url = DEFAULT_IMAGE_URL

        button_role_id = safe_int(riddle.get("button-id"))
        mentions = unique_role_mentions(interaction.guild, RIDDLE_ROLE_ID, button_role_id)
        if ping_role:
            mentions.append(ping_role.mention)

        embed = discord.Embed(
            title=title,
            description=embed_safe(riddle.get("text", "No text"), max_length=4000, fallback="No text"),
            color=discord.Color.blurple()
        )
        embed.add_field(name="🏆 Award", value=embed_safe(riddle.get("award", "*None*")), inline=False)
        embed.set_image(url=image_url)
        embed.set_footer(text=footer_text(interaction.guild))

        riddle_channel = await self.resolve_channel(RIDDLE_CHANNEL_ID)
        if not riddle_channel:
            await interaction.followup.send("❌ Riddle channel not found.", ephemeral=True)
            return

        await riddle_channel.send(
            content=" ".join(dict.fromkeys(mentions)),
            embed=embed,
            view=SubmitButtonView(self),
            allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
        )

        channel_mention = getattr(riddle_channel, "mention", f"<#{RIDDLE_CHANNEL_ID}>")
        await interaction.followup.send(f"✅ Riddle posted to {channel_mention}.", ephemeral=True)

    @app_commands.command(name="riddle_close", description="Close current riddle as unsolved.")
    @app_commands.checks.has_role(REQUIRED_ROLE_ID)
    async def riddle_close(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        riddle = await self.get_bin_record(RIDDLE_BIN_ID, default={})
        if not isinstance(riddle, dict) or not riddle.get("text"):
            await interaction.followup.send("❌ No active riddle to close.", ephemeral=True)
            return

        solution_url = riddle.get("solution-url") or DEFAULT_IMAGE_URL
        if not str(solution_url).startswith("http"):
            solution_url = DEFAULT_IMAGE_URL

        raw_solution = riddle.get("solution", "*None*")
        clean_solution, link = extract_link(raw_solution)
        solution_display = embed_safe(clean_solution, fallback="*None*")
        if link:
            solution_display += f"\n🔗 [🧠**MORE**]({link})"

        embed = discord.Embed(
            title="🔒 Riddle Closed",
            description="Sadly, nobody could solve the riddle in time...",
            color=discord.Color.red()
        )
        embed.add_field(name="🧩 Riddle", value=embed_safe(riddle.get("text", "*Unknown*")), inline=False)
        embed.add_field(name="✅ Correct Solution", value=embed_safe(solution_display), inline=False)
        embed.add_field(name="🏆 Award", value=embed_safe(riddle.get("award", "*None*")), inline=False)
        embed.set_image(url=solution_url)
        embed.set_footer(text=footer_text(interaction.guild))

        button_role_id = safe_int(riddle.get("button-id"))
        mentions = unique_role_mentions(interaction.guild, RIDDLE_ROLE_ID, button_role_id)

        riddle_channel = await self.resolve_channel(RIDDLE_CHANNEL_ID)
        if riddle_channel:
            await riddle_channel.send(
                content=" ".join(mentions),
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False)
            )

        await self.archive_current_riddle(riddle)
        await self.clear_riddle_data()

        if ARCHIVE_BIN_ID:
            await interaction.followup.send("✅ Riddle closed, archived and cleared.", ephemeral=True)
        else:
            await interaction.followup.send("✅ Riddle closed and cleared (archive skipped: ARCHIVE_BIN_ID missing).", ephemeral=True)

    @app_commands.command(name="riddle_view", description="Private preview of current riddle.")
    @app_commands.checks.has_role(REQUIRED_ROLE_ID)
    async def riddle_view(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        riddle = await self.get_bin_record(RIDDLE_BIN_ID, default={})
        if not isinstance(riddle, dict) or not riddle.get("text"):
            await interaction.followup.send("❌ No active riddle found.", ephemeral=True)
            return

        next_number = await self.get_next_riddle_number()
        title = f"🧠 Ms Pepper's 🧩𝕲𝖔𝖔𝖓 𝕳𝖚𝖙 𝕽𝖎𝖉𝖉𝖑𝖊 #️⃣{next_number} \n*({now_date_str()})*"

        image_url = riddle.get("image-url") or DEFAULT_IMAGE_URL
        if not str(image_url).startswith("http"):
            image_url = DEFAULT_IMAGE_URL

        solution_url = riddle.get("solution-url") or image_url
        if not str(solution_url).startswith("http"):
            solution_url = image_url

        riddle_embed = discord.Embed(
            title=title,
            description=embed_safe(riddle.get("text", "*No text*"), max_length=4000),
            color=discord.Color.blurple()
        )
        riddle_embed.add_field(name="🏆 Award", value=embed_safe(riddle.get("award", "*None*")), inline=False)
        riddle_embed.set_image(url=image_url)
        riddle_embed.set_footer(text=footer_text(interaction.guild))

        raw_solution = riddle.get("solution", "*None*")
        clean_solution, link = extract_link(raw_solution)
        solution_display = embed_safe(clean_solution, fallback="*None*")
        if link:
            solution_display += f"\n🔗 [🧠**MORE**]({link})"

        solved_preview = discord.Embed(
            title="🎉 Riddle Solved! (Preview)",
            description="**SomeUser** solved the riddle!",
            color=discord.Color.green()
        )
        solved_preview.add_field(name="🧩 Riddle", value=embed_safe(riddle.get("text", "*Unknown*")), inline=False)
        solved_preview.add_field(name="🔍 Proposed Solution", value="*Right Solution*", inline=False)
        solved_preview.add_field(name="✅ Correct Solution", value=embed_safe(solution_display), inline=False)
        solved_preview.add_field(name="🏆 Award", value=embed_safe(riddle.get("award", "*None*")), inline=False)
        solved_preview.set_image(url=solution_url)
        solved_preview.set_footer(text=footer_text(interaction.guild))

        await interaction.followup.send(
            content="🧪 Private preview:",
            embeds=[riddle_embed, solved_preview],
            ephemeral=True
        )


# =========================
# Global Error Handler
# =========================
async def on_riddle_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingRole):
        if interaction.response.is_done():
            await interaction.followup.send("🚫 You don’t have permission to use this command.", ephemeral=True)
        else:
            await interaction.response.send_message("🚫 You don’t have permission to use this command.", ephemeral=True)
        return

    raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(RiddleCog(bot))
    bot.tree.on_error = on_riddle_command_error