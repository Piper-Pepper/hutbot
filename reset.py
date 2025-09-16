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
        Adds missing reactions if any are missing.
        Only works for messages with embeds with images OR messages with attachments (images).
        Feedback only in console.
        """
        await ctx.defer(ephemeral=True)

        updated_count = 0
        skipped_count = 0

        async for msg in ctx.channel.history(limit=300, oldest_first=True):
            has_embed_image = msg.embeds and msg.embeds[0].image and msg.embeds[0].image.url
            has_attachment = bool(msg.attachments)

            if not (has_embed_image or has_attachment):
                continue  # weder Embed mit Bild noch Attachment → skip

            current_reactions = {str(r.emoji) for r in msg.reactions}
            missing = [emoji for emoji in CUSTOM_REACTIONS if emoji not in current_reactions]

            if not missing:
                skipped_count += 1
                print(f"⏩ Skipped message {msg.id} (all reactions present)")
                continue

            for emoji in missing:
                try:
                    if emoji.startswith("<:") and ":" in emoji:
                        name_id = emoji[2:-1]
                        name, id_ = name_id.split(":")
                        await msg.add_reaction(discord.PartialEmoji(name=name, id=int(id_)))
                    else:
                        await msg.add_reaction(emoji)
                    await asyncio.sleep(0.25)  # kurze Pause zwischen Reactions
                except discord.HTTPException:
                    pass

            updated_count += 1
            print(f"✅ Updated message {msg.id} with missing reactions: {missing}")
            await asyncio.sleep(0.5)  # kurze Pause zwischen Nachrichten

        print(f"🎯 Done! {updated_count} messages updated, {skipped_count} messages already complete.")

async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionResetCog(bot))
