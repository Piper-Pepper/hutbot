import discord
from discord.ext import commands
import aiohttp
import asyncio
import json

JSONBIN_ID = "68bc589fae596e708fe4d068"
JSONBIN_SECRET = "$2a$10$3IrBbikJjQzeGd6FiaLHmuz8wTK.TXOMJRBkzMpeCAVH4ikeNtNaq"

ALLOWED_CHANNELS = [
    1378018756843933767,
    1375457632394936340,
    1375457683531890688,
    1377502522788417558,
    1378456514955710646
]

# Key = Unicode oder Custom name:id
REACTIONS = {
    "1️⃣": "1",
    "2️⃣": "2",
    "3️⃣": "3",
    "011:1346549711817146400": "11"
}

class ReactionCountCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        await self.session.close()

    async def update_jsonbin(self, pic_id: str, number: str, delta: int):
        async with self.lock:
            url = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}/latest"
            headers = {"X-Master-Key": JSONBIN_SECRET, "Content-Type": "application/json"}

            async with self.session.get(url, headers=headers) as resp:
                data = await resp.json()
                record = data.get("record", {})

            if pic_id not in record:
                record[pic_id] = {"1": 0, "2": 0, "3": 0, "11": 0}

            record[pic_id][number] += delta
            if record[pic_id][number] < 0:
                record[pic_id][number] = 0

            async with self.session.put(
                f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}",
                headers=headers,
                data=json.dumps(record)
            ) as put_resp:
                if put_resp.status == 200:
                    print(f"[JSONBin] Updated {pic_id}: {number} -> {record[pic_id][number]}")
                else:
                    print(f"[JSONBin] Failed to update {pic_id}: {put_resp.status}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.channel.id not in ALLOWED_CHANNELS:
            return
        if message.attachments:
            for r in REACTIONS:
                try:
                    # Unicode oder Custom Emoji korrekt bauen
                    if ':' in r:
                        name, id_ = r.split(':')
                        emoji_obj = discord.PartialEmoji(name=name, id=int(id_))
                        await message.add_reaction(emoji_obj)
                    else:
                        await message.add_reaction(r)
                except discord.HTTPException:
                    pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.channel_id not in ALLOWED_CHANNELS or payload.user_id == self.bot.user.id:
            return

        if payload.emoji.is_custom_emoji():
            key = f"{payload.emoji.name}:{payload.emoji.id}"
        else:
            key = str(payload.emoji)

        if key not in REACTIONS:
            return

        pic_id = str(payload.message_id)
        number = REACTIONS[key]
        await self.update_jsonbin(pic_id, number, 1)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.channel_id not in ALLOWED_CHANNELS or payload.user_id == self.bot.user.id:
            return

        if payload.emoji.is_custom_emoji():
            key = f"{payload.emoji.name}:{payload.emoji.id}"
        else:
            key = str(payload.emoji)

        if key not in REACTIONS:
            return

        pic_id = str(payload.message_id)
        number = REACTIONS[key]
        await self.update_jsonbin(pic_id, number, -1)

async def setup(bot):
    await bot.add_cog(ReactionCountCog(bot))
