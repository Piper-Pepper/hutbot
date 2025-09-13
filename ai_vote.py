import discord
from discord.ext import commands

ALLOWED_CHANNELS = [
    1415769909874524262,
    1415769966573260970
]

# Unicode oder Custom Emojis
REACTIONS = [
    "<:01hotlips:1347157151616995378>",
    "<:01smile_piper:1387083454575022213>",    
    "<:01scream:1377706250690625576>",
    "<:011:1346549711817146400>"
]

class AutoReactCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.channel.id not in ALLOWED_CHANNELS:
            return
        if not message.attachments:
            return  # nur Bilder

        for r in REACTIONS:
            try:
                # Custom Emoji korrekt bauen
                if r.startswith("<:") and ":" in r:
                    name_id = r[2:-1]  # entfernt <: ... >
                    name, id_ = name_id.split(":")
                    emoji_obj = discord.PartialEmoji(name=name, id=int(id_))
                    await message.add_reaction(emoji_obj)
                else:
                    await message.add_reaction(r)
            except discord.HTTPException:
                pass  # falls Reaction fehlschlägt, ignorieren

async def setup(bot):
    await bot.add_cog(AutoReactCog(bot))
