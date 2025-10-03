# cogs/hutthreadvote.py
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import calendar

BOT_ID = 1339242900906836090  # Poster-Bot für die Threads

THREAD_CHOICES = [
    app_commands.Choice(name="Thread 1", value="1416599342298435735"),
    app_commands.Choice(name="Thread 2", value="1416597431000367295"),
    app_commands.Choice(name="Thread 3", value="1416589943890903080"),
    app_commands.Choice(name="Thread 4", value="1416589335053996092"),
]

TOPUSER_CHOICES = [
    app_commands.Choice(name="Top 5", value="5"),
    app_commands.Choice(name="Top 10", value="10"),
    app_commands.Choice(name="Top 20", value="20"),
]

current_year = datetime.utcnow().year
YEAR_CHOICES = [
    app_commands.Choice(name=str(current_year), value=str(current_year)),
    app_commands.Choice(name=str(current_year - 1), value=str(current_year - 1)),
]

MONTH_CHOICES = [
    app_commands.Choice(name=calendar.month_name[i], value=str(i)) for i in range(1, 13)
]

REACTION_CAPTIONS = {
    "<:01sthumb:1387086056498921614>": "Great!",
    "<:01smile_piper:1387083454575022213>": "LMFAO",
    "<:02No:1347536448831754383>": "No... just... no",
    "<:011:1346549711817146400>": "Better than 10",
    "<:011pump:1346549688836296787>": "Pump that Puppet!",
}


class HutThreadVote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="hut_threadvote",
        description="Shows the top posts by reactions for a selected thread/month/year"
    )
    @app_commands.describe(
        year="Select year",
        month="Select month",
        thread="Select thread",
        topuser="Number of top posts to display",
        public="Whether the posts are public or ephemeral"
    )
    @app_commands.choices(
        year=YEAR_CHOICES,
        month=MONTH_CHOICES,
        thread=THREAD_CHOICES,
        topuser=TOPUSER_CHOICES
    )
    @app_commands.checks.cooldown(1, 5)
    async def hut_threadvote(
        self,
        interaction: discord.Interaction,
        year: app_commands.Choice[str],
        month: app_commands.Choice[str],
        thread: app_commands.Choice[str],
        topuser: app_commands.Choice[str] = None,
        public: bool = False
    ):
        top_count = int(topuser.value) if topuser else 5
        ephemeral_flag = not public

        guild = interaction.guild
        thread_obj = guild.get_channel(int(thread.value))
        if not thread_obj or not isinstance(thread_obj, discord.Thread):
            await interaction.response.send_message("❌ Invalid thread.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)

        # Zeitspanne
        start_dt = datetime(int(year.value), int(month.value), 1, tzinfo=timezone.utc)
        last_day = calendar.monthrange(int(year.value), int(month.value))[1]
        end_dt = datetime(int(year.value), int(month.value), last_day, 23, 59, 59, tzinfo=timezone.utc)

        matched_msgs = []
        try:
            async for msg in thread_obj.history(after=start_dt, before=end_dt, limit=None):
                if msg.author.id != BOT_ID:
                    continue
                matched_msgs.append(msg)
        except Exception:
            pass

        if not matched_msgs:
            await interaction.followup.send(
                f"No posts found in {calendar.month_name[int(month.value)]} {year.value}.",
                ephemeral=ephemeral_flag
            )
            return

        # Sortierfunktion
        def sort_key(msg: discord.Message):
            sorted_reacts = sorted(msg.reactions, key=lambda r: r.count, reverse=True)
            top5_sum = sum(r.count for r in sorted_reacts[:5])
            extra_sum = sum(r.count for r in sorted_reacts[5:])
            return (top5_sum, extra_sum, msg.created_at)

        top_msgs = sorted(matched_msgs, key=sort_key, reverse=True)[:top_count]

        # Intro-Embed
        intro_embed = discord.Embed(
            title=f"🏆 Top {top_count} in Thread {thread_obj.name} "
                  f"({calendar.month_name[int(month.value)]} {year.value})",
            description=(f"This is the **Top {top_count}** in **{thread_obj.name}** "
                         f"for {calendar.month_name[int(month.value)]} {year.value}."),
            color=discord.Color.gold()
        )
        intro_embed.set_footer(
            text=f"{guild.name} Rankings | {thread_obj.name} | #{interaction.channel.name}",
            icon_url=guild.icon.url if guild.icon else discord.Embed.Empty
        )

        intro_msg = await interaction.followup.send(embed=intro_embed, wait=True)

        # Sub-Embeds
        for idx, msg in enumerate(top_msgs, start=1):
            sorted_reacts = sorted(msg.reactions, key=lambda r: r.count, reverse=True)

            # Top-5 Emojis
            reaction_parts = []
            used_emojis = set()
            for emoji_key in REACTION_CAPTIONS:
                r = next(
                    (r for r in sorted_reacts if (
                        str(r.emoji) if not isinstance(r.emoji, discord.Emoji)
                        else f"<:{r.emoji.name}:{r.emoji.id}>"
                    ) == emoji_key),
                    None
                )
                if r:
                    count = r.count - 1
                    used_emojis.add(r.emoji)
                    if count > 0:
                        reaction_parts.append(f"{str(r.emoji)} {count}")

            reaction_line = " ".join(reaction_parts)

            # Extra reactions
            extra_parts = []
            extra_reacts = [r for r in sorted_reacts if r.emoji not in used_emojis]
            for r in extra_reacts:
                if r.count > 0:
                    extra_parts.append(f"{str(r.emoji)} {r.count}")
            extra_text = " ".join(extra_parts) if extra_parts else ""

            # Creator aus Content/Embeds finden
            creator = msg.author
            creator_name = creator.display_name
            creator_avatar = creator.display_avatar.url
            if msg.embeds:
                for e in msg.embeds:
                    if e.description and "🎨 Generated by:" in e.description:
                        # Extrahiere Mention
                        try:
                            part = e.description.split("🎨 Generated by:")[1].strip().split()[0]
                            creator_name = part
                        except Exception:
                            pass

            title = f"#{idx} by {creator_name}\n{'─'*14}"

            # Beschreibung
            description_text = ""
            if reaction_line:
                description_text += f"{reaction_line}\n\n"
            if extra_text:
                description_text += f"{extra_text}\n\n"
            description_text += f"[◀️ Jump / Vote 📈]({msg.jump_url})"

            # Bildquelle
            img_url = None
            if msg.attachments:
                img_url = msg.attachments[0].url
            elif msg.embeds:
                for e in msg.embeds:
                    if e.image:
                        img_url = e.image.url
                        break
                    elif e.thumbnail:
                        img_url = e.thumbnail.url
                        break

            embed = discord.Embed(
                title=title,
                description=description_text,
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=creator_avatar)
            embed.set_footer(
                text=f"{thread_obj.name} | #{msg.channel.name}",
                icon_url=guild.icon.url if guild.icon else discord.Embed.Empty
            )
            if img_url:
                embed.set_image(url=img_url)

            await intro_msg.channel.send(embed=embed)

        # Top1 announcement
        top1_msg = top_msgs[0]
        top1_mention = top1_msg.jump_url
        await intro_msg.channel.send(
            f"In {calendar.month_name[int(month.value)]}/{year.value}, "
            f"the top post in **{thread_obj.name}** is [here]({top1_mention}) 🎉"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(HutThreadVote(bot))
