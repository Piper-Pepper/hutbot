import discord
from discord.ext import commands
from discord import ui

import aiohttp
import asyncio
import io
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


VIDEO_MODEL = "wan-2-7-enhanced-text-to-video"

VIDEO_QUEUE_URL = (
    "https://api.mordiem.com/api/v1/video/queue"
)

VIDEO_RETRIEVE_URL = (
    "https://api.mordiem.com/api/v1/video/retrieve"
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


        self.cog=cog



        self.prompt=ui.TextInput(
            label="Video description",
            placeholder="Describe your video...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000
        )



        self.duration=ui.TextInput(
            label="Duration (5 / 10 / 15)",
            placeholder="10",
            default="10",
            required=True,
            max_length=2
        )



        self.aspect=ui.TextInput(
            label="Aspect Ratio (16:9 / 9:16 / 1:1)",
            placeholder="16:9",
            default="16:9",
            required=True,
            max_length=4
        )



        self.add_item(self.prompt)
        self.add_item(self.duration)
        self.add_item(self.aspect)






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




        duration=self.duration.value.strip()


        if duration not in [
            "5",
            "10",
            "15"
        ]:

            duration="10"




        aspect=self.aspect.value.strip()



        if aspect not in [
            "16:9",
            "9:16",
            "1:1"
        ]:

            aspect="16:9"





        await interaction.response.send_message(
            "🎬 Video queued...",
            ephemeral=True
        )




        await self.cog.generate_video(
            interaction.user,
            self.prompt.value,
            duration,
            aspect
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







# =========================
# JSONBIN
# =========================

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


                if r.status != 200:

                    print(
                        "JSONBIN ERROR",
                        r.status
                    )

                    return {}



                data=await r.json()



        return data.get(
            "record",
            {}
        )






    async def jsonbin_save(
        self,
        data
    ):


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







# =========================
# LIMIT CHECK
# =========================

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



        clean=[]



        for x in history:

            try:

                t=datetime.fromisoformat(x)


                if t > now - timedelta(hours=24):

                    clean.append(x)

            except:

                pass




        if len(clean)>=limit:


            return False, (

                f"⏳ Limit reached.\n"
                f"You can create {limit} video(s) every 24h."

            )





        clean.append(
            now.isoformat()
        )


        data[uid]=clean



        await self.jsonbin_save(
            data
        )



        return True,"OK"
    
# =========================
# VIDEO GENERATE
# =========================

    async def generate_video(
        self,
        user,
        prompt,
        duration,
        aspect_ratio
    ):


        payload={

            "model":
            VIDEO_MODEL,


            "prompt":
            prompt,


            "duration":
            duration+"s",


            "resolution":
            "720p",


            "aspect_ratio":
            aspect_ratio

        }



        headers={

            "Authorization":
            f"Bearer {MORDIEM_API}",


            "Content-Type":
            "application/json"

        }




        print(
            "\n===== VIDEO REQUEST ====="
        )

        print(payload)





        async with aiohttp.ClientSession() as session:


            async with session.post(

                VIDEO_QUEUE_URL,

                headers=headers,

                json=payload

            ) as r:


                result=await r.json()






        print(
            "\n===== VIDEO RESPONSE ====="
        )

        print(result)





        queue_id=result.get(
            "queue_id"
        )



        if not queue_id:


            channel=await self.bot.fetch_channel(
                VIDEO_CHANNEL_ID
            )


            await channel.send(
                f"❌ Video failed for {user.mention}\n"
                f"`{result}`"
            )


            await self.refresh_button()

            return






        # alten Button entfernen

        await self.clear_buttons()





        channel=await self.bot.fetch_channel(
            VIDEO_CHANNEL_ID
        )



        status_msg=await channel.send(

            f"🎬 **Video rendering...**\n\n"
            f"👤 {user.mention}\n"
            f"📐 {aspect_ratio}\n"
            f"⏱ {duration}s\n"
            f"🎞 720p\n"
            f"🆔 `{queue_id}`"

        )





        video_data = await self.wait_for_video(
            queue_id
        )





        if video_data is None:


            await status_msg.edit(

                content=
                "❌ Video render failed."

            )


            await self.refresh_button()

            return





        await status_msg.delete()





        file=discord.File(

            io.BytesIO(video_data),

            filename="AI_video.mp4"

        )





        await channel.send(

            content=f"""
🎬 **AI Video Generated**

👤 **Creator**
{user.mention}

📝 **Prompt**
{prompt}

📐 **Aspect Ratio**
{aspect_ratio}

⏱ **Duration**
{duration}s

🎞 **Resolution**
720p

📅 **Created**
{datetime.now().strftime("%Y-%m-%d %H:%M")}
""",

            file=file

        )




        # erst jetzt neuer Button

        await self.refresh_button()










# =========================
# WAIT FOR VIDEO
# =========================

    async def wait_for_video(
        self,
        queue_id
    ):


        headers={

            "Authorization":
            f"Bearer {MORDIEM_API}",


            "Content-Type":
            "application/json"

        }



        timeout=60*30

        elapsed=0





        while elapsed < timeout:


            await asyncio.sleep(10)


            elapsed += 10




            try:


                async with aiohttp.ClientSession() as session:


                    async with session.post(

                        VIDEO_RETRIEVE_URL,

                        headers=headers,

                        json={

                            "model":
                            VIDEO_MODEL,


                            "queue_id":
                            queue_id

                        }

                    ) as r:



                        content=r.headers.get(
                            "content-type",
                            ""
                        )



                        if "video" in content:


                            return await r.read()



                        else:


                            print(
                                "VIDEO STATUS:",
                                await r.text()
                            )



            except Exception as e:


                print(
                    "RETRIEVE ERROR:",
                    e
                )






        return None










# =========================
# DELETE OLD BUTTON
# =========================

    async def clear_buttons(self):


        try:


            channel=await self.bot.fetch_channel(
                VIDEO_CHANNEL_ID
            )



            async for msg in channel.history(
                limit=10
            ):



                if msg.author == self.bot.user:


                    if msg.components:


                        await msg.delete()



        except Exception as e:


            print(
                "BUTTON DELETE ERROR:",
                e
            )









# =========================
# REFRESH BUTTON
# =========================

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






        await self.clear_buttons()






        msg=await channel.send(

            "🎬 **AI Video Generator**\n"
            "Click the button to create a video.",

            view=VideoButton(self)

        )



        print(
            "NEW VIDEO BUTTON:",
            msg.id
        )









# =========================
# SETUP
# =========================

async def setup(bot):

    await bot.add_cog(
        VideoCog(bot)
    )