import os
import re
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import discord
from discord.ext import commands, tasks
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("champions_cog")


def parse_int_list(value: str) -> list[int]:
    out: list[int] = []
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            logger.warning("Invalid integer in list: %s", part)
    return out


def parse_str_list(value: str) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def first_mention_id(content: str) -> Optional[int]:
    if not content:
        return None
    m = re.search(r"<@!?(\d+)>", content)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def is_image_attachment(att: discord.Attachment) -> bool:
    if att.content_type and att.content_type.startswith("image/"):
        return True
    name = (att.filename or "").lower()
    return name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"))


def is_date_only_input(raw: str) -> bool:
    s = (raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return True
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", s):
        return True
    return False


class ChampionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # ===== Defaults prefilled with your values =====
        default_channels = "1415769909874524262,1415769966573260970,1416267309399670917,1416267383160442901,1416468498305126522"
        default_emojis = "1️⃣,2️⃣,3️⃣,<:011:1346549711817146400>"
        default_source_bot_id = "1379906834588106883"

        # ===== ENV / Config =====
        self.channel_ids = set(parse_int_list(os.getenv("CHAMPIONS_CHANNEL_IDS", default_channels)))
        self.vote_emojis = parse_str_list(os.getenv("CHAMPIONS_VOTE_EMOJIS", default_emojis))
        self.vote_emoji_set = set(self.vote_emojis)

        # source_mode: bot_mention | message_author | auto
        self.source_mode = os.getenv("CHAMPIONS_SOURCE_MODE", "bot_mention").strip().lower()
        self.source_bot_id = int(os.getenv("CHAMPIONS_SOURCE_BOT_ID", default_source_bot_id))

        self.xp_per_image = int(os.getenv("CHAMPIONS_XP_PER_IMAGE", "200"))
        self.xp_per_vote = int(os.getenv("CHAMPIONS_XP_PER_VOTE", "50"))
        self.top_n = int(os.getenv("CHAMPIONS_TOP_N", "15"))

        self.count_self_votes = os.getenv("CHAMPIONS_COUNT_SELF_VOTES", "false").lower() == "true"
        self.ignore_bot_voters = os.getenv("CHAMPIONS_IGNORE_BOT_VOTERS", "true").lower() == "true"
        self.max_one_vote_per_message = os.getenv("CHAMPIONS_MAX_ONE_VOTE_PER_MESSAGE", "true").lower() == "true"

        self.auto_enabled = os.getenv("CHAMPIONS_AUTO_ENABLED", "true").lower() == "true"
        self.report_channel_id = int(os.getenv("CHAMPIONS_REPORT_CHANNEL_ID", "0"))
        self.weekday = int(os.getenv("CHAMPIONS_WEEKDAY", "6"))  # 0=Mon ... 6=Sun
        self.hour = int(os.getenv("CHAMPIONS_HOUR", "21"))
        self.minute = int(os.getenv("CHAMPIONS_MINUTE", "0"))

        tz_name = os.getenv("CHAMPIONS_TIMEZONE", "Europe/Berlin")
        try:
            self.tz = ZoneInfo(tz_name)
        except Exception:
            self.tz = timezone.utc

        self._last_auto_key: Optional[str] = None

        if self.auto_enabled:
            self.weekly_champions_task.start()

        logger.info(
            "ChampionsCog loaded | channels=%s | emojis=%s | source_mode=%s | source_bot_id=%s",
            self.channel_ids,
            self.vote_emojis,
            self.source_mode,
            self.source_bot_id
        )

    def cog_unload(self):
        if self.weekly_champions_task.is_running():
            self.weekly_champions_task.cancel()

    # ---------- Date parsing ----------
    def parse_datetime_flexible(self, raw: str) -> Optional[datetime]:
        s = (raw or "").strip()
        if not s:
            return None

        # ISO first
        try:
            iso = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self.tz)
            else:
                dt = dt.astimezone(self.tz)
            return dt
        except Exception:
            pass

        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
            "%d.%m.%Y",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=self.tz)
            except Exception:
                continue

        return None

    def parse_range_inputs(self, start_raw: str, end_raw: str) -> Optional[Tuple[datetime, datetime]]:
        start_dt = self.parse_datetime_flexible(start_raw)
        end_dt = self.parse_datetime_flexible(end_raw)
        if not start_dt or not end_dt:
            return None

        # If end is date-only, use end-of-day
        if is_date_only_input(end_raw):
            end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)

        return start_dt, end_dt

    # ---------- Post detection ----------
    def is_candidate_image_post(self, msg: discord.Message) -> bool:
        # strict: only from source bot if set
        if self.source_bot_id > 0 and msg.author.id != self.source_bot_id:
            return False

        has_image_attachment = any(is_image_attachment(a) for a in msg.attachments)
        has_image_embed = any((e.image is not None) or (e.thumbnail is not None) for e in msg.embeds)
        return has_image_attachment or has_image_embed

    def extract_creator_id(self, msg: discord.Message) -> Optional[int]:
        if self.source_mode == "bot_mention":
            return first_mention_id(msg.content or "")

        if self.source_mode == "message_author":
            return msg.author.id

        # auto
        mention_id = first_mention_id(msg.content or "")
        if msg.author.bot and mention_id is not None:
            return mention_id
        return msg.author.id

    # ---------- Stats calculation ----------
    async def collect_stats(
        self,
        guild: discord.Guild,
        start_utc: datetime,
        end_utc: datetime
    ) -> tuple[dict[int, int], dict[int, int], int]:
        image_counts: dict[int, int] = defaultdict(int)
        vote_counts: dict[int, int] = defaultdict(int)
        scanned_posts = 0

        channels: list[discord.TextChannel] = []
        if self.channel_ids:
            for cid in self.channel_ids:
                ch = guild.get_channel(cid)
                if isinstance(ch, discord.TextChannel):
                    channels.append(ch)
        else:
            channels = list(guild.text_channels)

        for channel in channels:
            try:
                async for msg in channel.history(limit=None, after=start_utc, before=end_utc, oldest_first=False):
                    if not self.is_candidate_image_post(msg):
                        continue

                    creator_id = self.extract_creator_id(msg)
                    if creator_id is None:
                        continue

                    scanned_posts += 1
                    image_counts[creator_id] += 1

                    seen_voters_for_message: set[int] = set()

                    for reaction in msg.reactions:
                        if str(reaction.emoji) not in self.vote_emoji_set:
                            continue

                        try:
                            async for voter in reaction.users(limit=None):
                                if self.ignore_bot_voters and voter.bot:
                                    continue
                                if (not self.count_self_votes) and voter.id == creator_id:
                                    continue

                                if self.max_one_vote_per_message:
                                    if voter.id in seen_voters_for_message:
                                        continue
                                    seen_voters_for_message.add(voter.id)

                                vote_counts[voter.id] += 1
                        except Exception as e:
                            logger.warning("Could not read reaction users (msg=%s): %s", msg.id, e)
            except Exception as e:
                logger.warning("History scan failed (channel=%s): %s", channel.id, e)

        return image_counts, vote_counts, scanned_posts

    def render_top_lines(
        self,
        guild: discord.Guild,
        values: dict[int, int],
        xp_per_unit: int,
        unit_label: str
    ) -> str:
        if not values:
            return "No data."

        ranked = sorted(values.items(), key=lambda kv: kv[1], reverse=True)[:self.top_n]
        lines = []
        for idx, (uid, amount) in enumerate(ranked, start=1):
            member = guild.get_member(uid)
            who = member.mention if member else f"<@{uid}>"
            xp = amount * xp_per_unit
            lines.append(f"**{idx}.** {who} — {amount} {unit_label} = **{xp} XP**")
        return "\n".join(lines)[:1024]

    async def build_embed(
        self,
        guild: discord.Guild,
        start_local: datetime,
        end_local: datetime,
        title: str
    ) -> discord.Embed:
        start_utc = start_local.astimezone(timezone.utc)
        end_utc = end_local.astimezone(timezone.utc)

        image_counts, vote_counts, scanned_posts = await self.collect_stats(guild, start_utc, end_utc)

        total_xp: dict[int, int] = defaultdict(int)
        for uid, c in image_counts.items():
            total_xp[uid] += c * self.xp_per_image
        for uid, c in vote_counts.items():
            total_xp[uid] += c * self.xp_per_vote

        if total_xp:
            ranked_total = sorted(total_xp.items(), key=lambda kv: kv[1], reverse=True)[:self.top_n]
            total_lines = []
            for idx, (uid, xp) in enumerate(ranked_total, start=1):
                member = guild.get_member(uid)
                who = member.mention if member else f"<@{uid}>"
                total_lines.append(f"**{idx}.** {who} — **{xp} XP**")
            total_text = "\n".join(total_lines)[:1024]
        else:
            total_text = "No data."

        embed = discord.Embed(
            title=title,
            description=(
                f"Period: **{start_local.strftime('%Y-%m-%d %H:%M:%S')}** → **{end_local.strftime('%Y-%m-%d %H:%M:%S')}**\n"
                f"Timezone: **{self.tz}**\n"
                f"Scanned image posts: **{scanned_posts}**"
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name=f"🖼️ Top Image Generators ({self.xp_per_image} XP / image)",
            value=self.render_top_lines(guild, image_counts, self.xp_per_image, "images"),
            inline=False,
        )
        embed.add_field(
            name=f"🗳️ Top Voters ({self.xp_per_vote} XP / vote)",
            value=self.render_top_lines(guild, vote_counts, self.xp_per_vote, "votes"),
            inline=False,
        )
        embed.add_field(name="👑 Overall Champions", value=total_text, inline=False)
        return embed

    # ---------- Auto weekly ----------
    @tasks.loop(minutes=1)
    async def weekly_champions_task(self):
        if not self.auto_enabled or self.report_channel_id <= 0:
            return

        now_local = datetime.now(self.tz)

        if now_local.weekday() != self.weekday:
            return
        if now_local.hour != self.hour or now_local.minute != self.minute:
            return

        iso = now_local.isocalendar()
        week_key = f"{iso.year}-W{iso.week}"
        if self._last_auto_key == week_key:
            return
        self._last_auto_key = week_key

        channel = self.bot.get_channel(self.report_channel_id)
        if not isinstance(channel, discord.TextChannel):
            logger.warning("Invalid CHAMPIONS_REPORT_CHANNEL_ID: %s", self.report_channel_id)
            return

        end_local = now_local
        start_local = end_local - timedelta(days=7)

        embed = await self.build_embed(
            guild=channel.guild,
            start_local=start_local,
            end_local=end_local,
            title="🏆 Weekly Champions",
        )
        await channel.send(embed=embed)

    @weekly_champions_task.before_loop
    async def before_weekly_task(self):
        await self.bot.wait_until_ready()

    # ---------- Commands ----------
    @commands.group(name="champions", invoke_without_command=True)
    async def champions_group(self, ctx: commands.Context, days: int = 7):
        """
        !champions 7
        !champions 30
        """
        if not ctx.guild:
            await ctx.send("Guild only.")
            return

        days = max(1, min(days, 120))
        now_local = datetime.now(self.tz)
        start_local = now_local - timedelta(days=days)

        await ctx.send(f"📊 Building champions report for last **{days}** days...")
        embed = await self.build_embed(
            guild=ctx.guild,
            start_local=start_local,
            end_local=now_local,
            title=f"🏆 Champions (Last {days} Days)",
        )
        await ctx.send(embed=embed)

    @champions_group.command(name="week")
    async def champions_week(self, ctx: commands.Context):
        if not ctx.guild:
            await ctx.send("Guild only.")
            return

        now_local = datetime.now(self.tz)
        start_local = now_local - timedelta(days=7)

        await ctx.send("📊 Building weekly report...")
        embed = await self.build_embed(
            guild=ctx.guild,
            start_local=start_local,
            end_local=now_local,
            title="🏆 Champions (Last 7 Days)",
        )
        await ctx.send(embed=embed)

    @champions_group.command(name="month")
    async def champions_month(self, ctx: commands.Context):
        if not ctx.guild:
            await ctx.send("Guild only.")
            return

        now_local = datetime.now(self.tz)
        start_local = now_local - timedelta(days=30)

        await ctx.send("📊 Building monthly report...")
        embed = await self.build_embed(
            guild=ctx.guild,
            start_local=start_local,
            end_local=now_local,
            title="🏆 Champions (Last 30 Days)",
        )
        await ctx.send(embed=embed)

    @champions_group.command(name="range")
    async def champions_range(self, ctx: commands.Context, *, period: str):
        """
        !champions range 2026-05-01 00:00 | 2026-05-31 23:59
        !champions range 01.05.2026 00:00 | 31.05.2026 23:59
        !champions range 2026-05-01 | 2026-05-31
        """
        if not ctx.guild:
            await ctx.send("Guild only.")
            return

        if "|" not in period:
            await ctx.send(
                "Format: `!champions range <start> | <end>`\n"
                "Example: `!champions range 2026-05-01 00:00 | 2026-05-31 23:59`"
            )
            return

        start_raw, end_raw = [x.strip() for x in period.split("|", 1)]
        parsed = self.parse_range_inputs(start_raw, end_raw)

        if not parsed:
            await ctx.send(
                "Could not parse date/time.\n"
                "Supported examples:\n"
                "`2026-05-01 00:00`\n"
                "`01.05.2026 00:00`\n"
                "`2026-05-01T00:00:00+02:00`"
            )
            return

        start_local, end_local = parsed

        if end_local <= start_local:
            await ctx.send("End must be after start.")
            return

        if (end_local - start_local).days > 366:
            await ctx.send("Range too large (max 366 days).")
            return

        await ctx.send(
            f"📊 Building custom report:\n"
            f"`{start_local.strftime('%Y-%m-%d %H:%M:%S')}` → `{end_local.strftime('%Y-%m-%d %H:%M:%S')}` ({self.tz})"
        )

        embed = await self.build_embed(
            guild=ctx.guild,
            start_local=start_local,
            end_local=end_local,
            title="🏆 Champions (Custom Range)",
        )
        await ctx.send(embed=embed)

    @champions_group.command(name="weeklynow")
    @commands.has_permissions(manage_guild=True)
    async def champions_weeklynow(self, ctx: commands.Context):
        if not ctx.guild:
            await ctx.send("Guild only.")
            return

        if self.report_channel_id <= 0:
            await ctx.send("`CHAMPIONS_REPORT_CHANNEL_ID` is not configured.")
            return

        channel = self.bot.get_channel(self.report_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await ctx.send("Configured report channel is invalid.")
            return

        now_local = datetime.now(self.tz)
        start_local = now_local - timedelta(days=7)

        embed = await self.build_embed(
            guild=ctx.guild,
            start_local=start_local,
            end_local=now_local,
            title="🏆 Weekly Champions (Manual Trigger)",
        )
        await channel.send(embed=embed)
        await ctx.send("✅ Weekly report posted.")

    @champions_group.command(name="config")
    @commands.has_permissions(manage_guild=True)
    async def champions_config(self, ctx: commands.Context):
        channels_text = ", ".join(str(c) for c in sorted(self.channel_ids)) if self.channel_ids else "ALL"
        emojis_text = ", ".join(self.vote_emojis) if self.vote_emojis else "NONE"

        txt = (
            f"**Champions Config**\n"
            f"- source_mode: `{self.source_mode}`\n"
            f"- source_bot_id: `{self.source_bot_id}`\n"
            f"- channels: `{channels_text}`\n"
            f"- vote_emojis: `{emojis_text}`\n"
            f"- xp_per_image: `{self.xp_per_image}`\n"
            f"- xp_per_vote: `{self.xp_per_vote}`\n"
            f"- max_one_vote_per_message: `{self.max_one_vote_per_message}`\n"
            f"- count_self_votes: `{self.count_self_votes}`\n"
            f"- ignore_bot_voters: `{self.ignore_bot_voters}`\n"
            f"- report_channel_id: `{self.report_channel_id}`\n"
            f"- auto_enabled: `{self.auto_enabled}`\n"
            f"- weekly schedule: `weekday={self.weekday}, {self.hour:02d}:{self.minute:02d}, tz={self.tz}`"
        )
        await ctx.send(txt)

    @champions_weeklynow.error
    @champions_config.error
    async def admin_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You need `Manage Server` permission for this command.")
            return
        await ctx.send(f"Error: `{error}`")


async def setup(bot: commands.Bot):
    await bot.add_cog(ChampionsCog(bot))