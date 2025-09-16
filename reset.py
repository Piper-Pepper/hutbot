import discord
from discord.ext import commands
import asyncio

CUSTOM_REACTIONS = [
    "<:01sthumb:1387086056498921614>",
    "<:01smile_piper:1387083454575022213>",
    "<:02No:1347536448831754383>",
    "<:011:1346549711817146400>"
]

class ReactionResetCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="reset_reactions")
    @commands.has_permissions(administrator=True)
    async def reset_reactions(self, ctx: commands.Context):
        """
        Scans the last 300 messages:
        Adds missing reactions only. Works for both embeds with images AND messages with attachments.
        Provides detailed feedback for each message.
        """
        # Unterschied zwischen Slash & Text
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
            followup = ctx.interaction.followup
        else:
            await ctx.defer()
            followup = ctx

        total_checked = 0
        updated = 0
        skipped_full = 0
        skipped_no_image = 0
        skipped_deleted = 0

        async for msg in ctx.channel.history(limit=300, oldest_first=True):
            total_checked += 1

            # Existiert die Nachricht noch?
            try:
                await ctx.channel.fetch_message(msg.id)
            except discord.NotFound:
                skipped_deleted += 1
                await followup.send(f"⚠️ Skipped deleted message ID {msg.id}")
                continue
            except discord.Forbidden:
                await followup.send(f"⚠️ Cannot access message ID {msg.id} (Forbidden)")
                continue

            # Hat die Nachricht Bild oder Attachment?
            has_image = (msg.embeds and msg.embeds[0].image and msg.embeds[0].image.url) or msg.attachments
            if not has_image:
                skipped_no_image += 1
                await followup.send(f"⏭ Skipped message ID {msg.id} (no image/attachment)")
                continue

            current_reactions = {str(r.emoji) for r in msg.reactions}
            missing = [emoji for emoji in CUSTOM_REACTIONS if emoji not in current_reactions]

            if not missing:
                skipped_full += 1
                await followup.send(f"✅ Message ID {msg.id} already has all reactions, skipping")
                continue

            # Fehlen Reactions → hinzufügen
            for emoji in missing:
                try:
                    if emoji.startswith("<:") and ":" in emoji:
                        name_id = emoji[2:-1]
                        name, id_ = name_id.split(":")
                        emoji_obj = discord.PartialEmoji(name=name, id=int(id_))
                        await msg.add_reaction(emoji_obj)
                    else:
                        await msg.add_reaction(emoji)
                    await asyncio.sleep(0.25)  # kleine Pause zwischen Reactions
                except discord.HTTPException:
                    await followup.send(f"⚠️ Failed to add {emoji} to message ID {msg.id}")

            updated += 1
            await followup.send(f"✅ Updated message {msg.id} with missing reactions: {missing}")
            await asyncio.sleep(0.3)  # kurze Pause zwischen Nachrichten, reduziert Rate-Limits

        # Zusammenfassung
        await followup.send(
            f"🔄 Done! Checked {total_checked} messages.\n"
            f"✅ Updated: {updated}\n"
            f"⏭ Already complete: {skipped_full}\n"
            f"⏭ No image/attachment: {skipped_no_image}\n"
            f"⚠️ Deleted/Forbidden: {skipped_deleted}"
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionResetCog(bot))
