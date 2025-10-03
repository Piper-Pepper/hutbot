# cogs/hutthreadvote_legacy.py
import discord
from discord.ext import commands
from discord import app_commands
import re

SEARCH_BOT_ID = 1339242900906836090  # Bot, dessen Posts durchsucht werden
SCAN_BOT_ID = 1379906834588106883    # Dein Bot, der scannt

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

REACTION_CAPTIONS = {
    "<:01sthumb:1387086056498921614>": "Great!",
    "<:01smile_piper:1387083454575022213>": "LMFAO",
    "<:02No:1347536448831754383>": "No... just... no",
    "<:011:1346549711817146400>": "Better than 10",
    "<:011pump:1346549688836296787>": "Pump that Puppet!",
}


class HutThreadVoteLegacy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="legacy_vote",
        description="Shows top posts by reactions in the selected thread (all time)."
    )
    @app_commands.describe(
        thread="Select thread",
        topuser="Number of top posts to display",
        public="Whether the posts are public or ephemeral"
    )
    @app_commands.choices(
        thread=THREAD_CHOICES,
        topuser=TOPUSER_CHOICES
    )
    @app_commands.checks.cooldown(1, 5)
    async def legacy_vote(
        self,
        interaction: discord.Interaction,
        thread: app_commands.Choice[str],
        topuser: app_commands.Choice[str] = None,
        public: bool = False
    ):
        top_count = int(topuser.value) if topuser else 5
        ephemeral_flag = not public
        guild = interaction.guild

        # Thread holen
        try:
            thread_obj = await guild.fetch_channel(int(thread.value))
        except Exception:
            thread_obj = None

        if not thread_obj or not isinstance(thread_obj, discord.Thread):
            await interaction.response.send_message("❌ Invalid thread (not found or no access).", ephemeral=True)
            return

        # Versuch: dem Thread beitreten, falls nicht joined oder archiviert
        try:
            if not thread_obj.joined:
                await thread_obj.join()
        except Exception:
            await interaction.response.send_message("❌ Cannot join the thread.", ephemeral=True)
            return

        # Check permissions
        perms = thread_obj.permissions_for(guild.me)
        if not perms.read_messages or not perms.read_message_history:
            await interaction.response.send_message("❌ I cannot read this thread.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=ephemeral_flag)

        # Alle Posts vom SEARCH_BOT_ID im Thread sammeln
        matched_msgs = []
        try:
            async for msg in thread_obj.history(limit=None, oldest_first=True):
                if msg.author.id != SEARCH_BOT_ID:
                    continue
                matched_msgs.append(msg)
        except Exception:
            pass

        if not matched_msgs:
            await interaction.followup.send(
                f"No posts found in thread {thread_obj.name}.",
                ephemeral=ephemeral_flag
            )
            return

        # Sortieren nach Top-Reaktionen
        def sort_key(msg: discord.Message):
            sorted_reacts = sorted(msg.reactions, key=lambda r: r.count, reverse=True)
            top5_sum = sum(r.count for r in sorted_reacts[:5])
            extra_sum = sum(r.count for r in sorted_reacts[5:])
            return (top5_sum, extra_sum, msg.created_at)

        top_msgs = sorted(matched_msgs, key=sort_key, reverse=True)[:top_count]

        # Intro-Embed
        intro_embed = discord.Embed(
            title=f"🏆 Top {top_count} in Thread {thread_obj.name} (All Time)",
            description=f"This is the **Top {top_count}** posts in **{thread_obj.name}** (all time).",
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

            # Top-Emojis
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
                    if count > 0:
                        used_emojis.add(r.emoji)
                        reaction_parts.append(f"{str(r.emoji)} {count}")

            reaction_line = " ".join(reaction_parts) if reaction_parts else ""

            # Extra-Emojis
            extra_parts = []
            extra_reacts = [r for r in sorted_reacts if r.emoji not in used_emojis]
            for r in extra_reacts:
                count = r.count
                if r.me:
                    count -= 1
                if count > 0:
                    extra_parts.append(f"{str(r.emoji)} {count}")
            extra_text = " ".join(extra_parts) if extra_parts else ""

            # Creator aus Embed
            creator_mention = None
            creator_name = msg.author.display_name
            creator_avatar = msg.author.display_avatar.url

            if msg.embeds:
                for e in msg.embeds:
                    if e.description and "🎨 Generated by:" in e.description:
                        match = re.search(r"🎨 Generated by:\s*(<@!?\d+>)", e.description)
                        if match:
                            creator_mention = match.group(1)
                            creator_name = creator_mention
                        break

            title = f"#{idx} by {creator_name}\n{'─'*14}"

            # Beschreibung
            description_text = ""
            if reaction_line:
                description_text += f"{reaction_line}\n\n"
            if extra_text:
                description_text += f"{extra_text}\n\n"
            description_text += f"[◀️ Jump / Vote 📈]({msg.jump_url})"

            # Bild
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

        # Top1 Announcement
        top1_msg = top_msgs[0]
        top1_url = top1_msg.jump_url
        await intro_msg.channel.send(
            f"The top post in **{thread_obj.name}** (all time) is [here]({top1_url}) 🎉"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(HutThreadVoteLegacy(bot))
