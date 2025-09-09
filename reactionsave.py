import discord
from discord.ext import commands

ALLOWED_CHANNELS = [
    1378018756843933767,
    1375457632394936340,
    1375457683531890688,
    1377502522788417558,
    1378456514955710646
]

# Unicode oder Custom Emojis
REACTIONS = [
    "1️⃣",
    "2️⃣",
    "3️⃣",
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
