import discord
from discord.ext import commands
from discord import ui
import aiohttp
import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv


load_dotenv()


# =========================
# CONFIG
# =========================

VIDEO_CHANNEL_ID = 1528774135172300840


# Rollen
VIDEO_ROLE = 1377051179615522926
DOUBLE_ROLE = 1375147276413964408
TRIPLE_ROLE = 1376592697606930593


# JSONBIN
AI_PIC_BIN = os.getenv("AI_PIC_BIN")
JSONBIN_KEY = os.getenv("JSONBIN_API_KEY")


# Mordiem
MORDIEM_API = os.getenv("MORDIEM_API")

VIDEO_URL = (
    "https://api.mordiem.com/api/v1/video/queue"
)





# =========================
# BUTTON
# =========================

class VideoButton(ui.View):

    def __init__(self, cog):

        super().__init__(
            timeout=None
        )

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
                "❌ You don't have permission to generate videos.",
                ephemeral=True
            )

            return



        await interaction.response.send_modal(
            VideoModal(self.cog)
        )









# =========================
# MODAL
# =========================

class VideoModal(ui.Modal):

    def __init__(self,cog):

        super().__init__(
            title="Generate AI Video"
        )


        self.cog = cog



        self.prompt = ui.TextInput(
            label="Video description",
            placeholder="Describe your video...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000
        )


        self.duration = ui.TextInput(
            label="Duration (5 / 10 / 15)",
            placeholder="10",
            default="10",
            required=True,
            max_length=2
        )



        self.add_item(self.prompt)
        self.add_item(self.duration)






    async def on_submit(
        self,
        interaction: discord.Interaction
    ):


        allowed,message = await self.cog.check_limit(
            interaction.user
        )


        if not allowed:

            await interaction.response.send_message(
                message,
                ephemeral=True
            )

            return



        duration=self.duration.value


        if duration not in [
            "5",
            "10",
            "15"
        ]:

            duration="10"



        await interaction.response.send_message(
            "🎬 Video queued...",
            ephemeral=True
        )



        await self.cog.generate_video(
            interaction.user,
            self.prompt.value,
            duration
        )









# =========================
# COG
# =========================

class VideoCog(commands.Cog):


    def __init__(self,bot):

        self.bot=bot



    async def cog_load(self):

        self.bot.add_view(
            VideoButton(self)
        )





    @commands.Cog.listener()
    async def on_ready(self):

        print(
            "VIDEO COG READY"
        )

        await self.refresh_button()







    # =====================
    # JSONBIN
    # =====================

    async def jsonbin_get(self):


        headers={

            "X-Master-Key":
            JSONBIN_KEY

        }


        async with aiohttp.ClientSession() as session:


            async with session.get(

                f"https://api.jsonbin.io/v3/b/{AI_PIC_BIN}",

                headers=headers

            ) as r:


                data=await r.json()



        return data.get(
            "record",
            {}
        )







    async def jsonbin_save(self,data):


        headers={

            "X-Master-Key":
            JSONBIN_KEY,

            "Content-Type":
            "application/json"

        }


        async with aiohttp.ClientSession() as session:


            await session.put(

                f"https://api.jsonbin.io/v3/b/{AI_PIC_BIN}",

                headers=headers,

                json=data

            )









    # =====================
    # LIMIT CHECK
    # =====================

    async def check_limit(
        self,
        user
    ):


        data=await self.jsonbin_get()


        uid=str(
            user.id
        )


        now=datetime.now(
            timezone.utc
        )


        limit=1



        if any(
            r.id == TRIPLE_ROLE
            for r in user.roles
        ):

            limit=3



        elif any(
            r.id == DOUBLE_ROLE
            for r in user.roles
        ):

            limit=2





        history=data.get(
            uid,
            []
        )



        history=[

            x for x in history

            if datetime.fromisoformat(x)

            >

            now - timedelta(hours=24)

        ]



        if len(history)>=limit:


            return False, (

                f"⏳ Limit reached.\n"
                f"You can create {limit} video(s) every 24h."

            )





        history.append(
            now.isoformat()
        )


        data[uid]=history


        await self.jsonbin_save(
            data
        )



        return True,"OK"









    # =====================
    # GENERATE
    # =====================

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





        async with aiohttp.ClientSession() as session:


            async with session.post(

                VIDEO_URL,

                headers=headers,

                json=payload

            ) as r:


                result=await r.json()



        print(
            "VIDEO RESPONSE:",
            result
        )




        channel=await self.bot.fetch_channel(
            VIDEO_CHANNEL_ID
        )


        await channel.send(

            f"🎬 {user.mention} started a video render.\n"
            f"Queue ID: `{result.get('queue_id','unknown')}`"

        )



        await self.refresh_button()










    # =====================
    # BUTTON REFRESH
    # =====================

    async def refresh_button(self):


        print(
            "Refreshing video button..."
        )



        try:

            channel=await self.bot.fetch_channel(
                VIDEO_CHANNEL_ID
            )


        except Exception as e:


            print(
                "CHANNEL ERROR:",
                e
            )

            return





        async for msg in channel.history(
            limit=10
        ):


            if msg.author == self.bot.user:


                if msg.components:


                    try:

                        await msg.delete()

                    except:

                        pass






        msg=await channel.send(

            "🎬 **AI Video Generator**\n"
            "Click the button to create a video.",

            view=VideoButton(self)

        )



        print(
            "NEW VIDEO BUTTON:",
            msg.id
        )









async def setup(bot):

    await bot.add_cog(
        VideoCog(bot)
    )