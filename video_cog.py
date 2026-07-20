import discord
from discord.ext import commands
from discord import ui
import aiohttp
import os
import json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv


load_dotenv()


VIDEO_CHANNEL_ID = 1528774135172300840

VIDEO_ROLE = 1377051179615522926
DOUBLE_ROLE = 1375147276413964408
TRIPLE_ROLE = 1376592697606930593


AI_PIC_BIN = os.getenv("AI_PIC_BIN")
JSONBIN_KEY = os.getenv("JSONBIN_API_KEY")


MORDIEM_API = os.getenv("MORDIEM_API")



VIDEO_URL = "https://api.mordiem.com/api/v1/video/queue"



class VideoButton(ui.View):

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog


    @ui.button(
        label="🎬 Video",
        style=discord.ButtonStyle.green,
        custom_id="video_generate_button"
    )
    async def video_button(
        self,
        interaction: discord.Interaction,
        button: ui.Button
    ):

        if not any(
            r.id == VIDEO_ROLE
            for r in interaction.user.roles
        ):

            await interaction.response.send_message(
                "❌ You don't have permission to create videos.",
                ephemeral=True
            )
            return


        await interaction.response.send_modal(
            VideoModal(self.cog)
        )







class VideoModal(ui.Modal):

    def __init__(self,cog):

        super().__init__(
            title="Generate Video"
        )

        self.cog=cog


        self.description = ui.TextInput(
            label="Video description",
            placeholder="Describe your video...",
            style=discord.TextStyle.paragraph,
            required=True
        )


        self.duration = ui.TextInput(
            label="Duration",
            placeholder="5 / 10 / 15",
            default="10",
            required=True
        )


        self.add_item(self.description)
        self.add_item(self.duration)




    async def on_submit(
        self,
        interaction: discord.Interaction
    ):


        allowed, msg = await self.cog.check_limit(
            interaction.user
        )


        if not allowed:

            await interaction.response.send_message(
                msg,
                ephemeral=True
            )

            return



        await interaction.response.send_message(
            "🎬 Creating video...",
            ephemeral=True
        )


        await self.cog.generate_video(
            interaction.user,
            self.description.value,
            self.duration.value
        )









class VideoCog(commands.Cog):


    def __init__(self,bot):

        self.bot=bot
        self.message_id=None



    async def cog_load(self):

        self.bot.add_view(
            VideoButton(self)
        )

        await self.refresh_button()





    async def jsonbin_get(self):

        headers={
            "X-Master-Key":JSONBIN_KEY
        }

        async with aiohttp.ClientSession() as s:

            async with s.get(
                f"https://api.jsonbin.io/v3/b/{AI_PIC_BIN}",
                headers=headers
            ) as r:

                data=await r.json()

        return data.get("record",{})




    async def jsonbin_save(self,data):

        headers={
            "X-Master-Key":JSONBIN_KEY,
            "Content-Type":"application/json"
        }

        async with aiohttp.ClientSession() as s:

            await s.put(
                f"https://api.jsonbin.io/v3/b/{AI_PIC_BIN}",
                headers=headers,
                json=data
            )







    async def check_limit(self,user):

        data=await self.jsonbin_get()


        uid=str(user.id)


        now=datetime.now(timezone.utc)


        limit=1


        if any(r.id==TRIPLE_ROLE for r in user.roles):

            limit=3


        elif any(r.id==DOUBLE_ROLE for r in user.roles):

            limit=2



        history=data.get(uid,[])


        history=[
            x for x in history
            if datetime.fromisoformat(x)
            > now-timedelta(hours=24)
        ]


        if len(history)>=limit:

            return False, (
                f"⏳ Daily limit reached "
                f"({limit} video(s)/24h)."
            )



        history.append(
            now.isoformat()
        )


        data[uid]=history


        await self.jsonbin_save(data)


        return True,"OK"







    async def generate_video(
        self,
        user,
        prompt,
        duration
    ):


        payload={

            "model":
            "wan-2-7-enhanced-text-to-video",

            "prompt":
            prompt,

            "duration":
            duration+"s"

        }


        headers={

            "Authorization":
            f"Bearer {MORDIEM_API}",

            "Content-Type":
            "application/json"

        }



        async with aiohttp.ClientSession() as s:

            async with s.post(
                VIDEO_URL,
                headers=headers,
                json=payload
            ) as r:

                result=await r.json()



        channel=self.bot.get_channel(
            VIDEO_CHANNEL_ID
        )


        await channel.send(
            f"🎬 {user.mention} video queued!\n"
            f"ID: `{result.get('queue_id','unknown')}`"
        )


        await self.refresh_button()







    async def refresh_button(self):

        channel=self.bot.get_channel(
            VIDEO_CHANNEL_ID
        )

        if not channel:
            return


        async for msg in channel.history(
            limit=10
        ):

            if msg.author==self.bot.user:

                if msg.components:

                    await msg.delete()


        msg=await channel.send(
            "🎬 **AI Video Generator**\nClick the button to create a video.",
            view=VideoButton(self)
        )


        self.message_id=msg.id







async def setup(bot):

    await bot.add_cog(
        VideoCog(bot)
    )