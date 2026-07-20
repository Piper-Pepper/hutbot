import discord
from discord.ext import commands
from discord import ui

import aiohttp
import asyncio
import io
import sqlite3
import os

from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv


load_dotenv()


# =========================
# CONFIG
# =========================

VIDEO_CHANNEL_ID = 1528774135172300840


VIDEO_ROLE = 1377051179615522926
DOUBLE_ROLE = 1375147276413964408
TRIPLE_ROLE = 1376592697606930593


MORDIEM_API = os.getenv(
    "MORDIEM_API"
)


VIDEO_MODEL = (
    "wan-2-7-enhanced-text-to-video"
)


VIDEO_QUEUE_URL = (
    "https://api.mordiem.com/api/v1/video/queue"
)


VIDEO_RETRIEVE_URL = (
    "https://api.mordiem.com/api/v1/video/retrieve"
)



# Sekundenlimits

ROLE_LIMITS = {

    VIDEO_ROLE: 10,

    DOUBLE_ROLE: 15,

    TRIPLE_ROLE: 25

}







# =========================
# DURATION VIEW
# =========================

class DurationView(ui.View):

    def __init__(
        self,
        cog,
        user,
        prompt
    ):

        super().__init__(
            timeout=120
        )

        self.cog = cog
        self.user = user
        self.prompt = prompt




    async def interaction_check(
        self,
        interaction
    ):

        if interaction.user.id != self.user.id:

            await interaction.response.send_message(
                "❌ This menu is not for you.",
                ephemeral=True
            )

            return False


        return True





    @ui.button(
        label="5 seconds",
        style=discord.ButtonStyle.green
    )
    async def five(
        self,
        interaction,
        button
    ):


        await self.cog.duration_selected(
            interaction,
            self.user,
            self.prompt,
            5
        )





    @ui.button(
        label="10 seconds",
        style=discord.ButtonStyle.blurple
    )
    async def ten(
        self,
        interaction,
        button
    ):


        await self.cog.duration_selected(
            interaction,
            self.user,
            self.prompt,
            10
        )






    @ui.button(
        label="15 seconds",
        style=discord.ButtonStyle.red
    )
    async def fifteen(
        self,
        interaction,
        button
    ):


        await self.cog.duration_selected(
            interaction,
            self.user,
            self.prompt,
            15
        )









# =========================
# ASPECT VIEW
# =========================

class AspectView(ui.View):

    def __init__(
        self,
        cog,
        user,
        prompt,
        duration
    ):

        super().__init__(
            timeout=120
        )

        self.cog=cog
        self.user=user
        self.prompt=prompt
        self.duration=duration




    async def interaction_check(
        self,
        interaction
    ):


        if interaction.user.id != self.user.id:


            await interaction.response.send_message(
                "❌ This menu is not for you.",
                ephemeral=True
            )

            return False


        return True







    @ui.button(
        label="16:9",
        style=discord.ButtonStyle.green
    )
    async def wide(
        self,
        interaction,
        button
    ):


        await self.cog.aspect_selected(
            interaction,
            self.user,
            self.prompt,
            self.duration,
            "16:9"
        )





    @ui.button(
        label="9:16",
        style=discord.ButtonStyle.blurple
    )
    async def vertical(
        self,
        interaction,
        button
    ):


        await self.cog.aspect_selected(
            interaction,
            self.user,
            self.prompt,
            self.duration,
            "9:16"
        )





    @ui.button(
        label="1:1",
        style=discord.ButtonStyle.gray
    )
    async def square(
        self,
        interaction,
        button
    ):


        await self.cog.aspect_selected(
            interaction,
            self.user,
            self.prompt,
            self.duration,
            "1:1"
        )











# =========================
# MAIN BUTTON
# =========================

class VideoButton(ui.View):


    def __init__(self,cog):

        super().__init__(
            timeout=None
        )

        self.cog=cog




    @ui.button(
        label="🎬 Generate Video",
        style=discord.ButtonStyle.green,
        custom_id="video_button"
    )
    async def video(
        self,
        interaction,
        button
    ):


        if not any(
            r.id == VIDEO_ROLE
            or r.id == DOUBLE_ROLE
            or r.id == TRIPLE_ROLE
            for r in interaction.user.roles
        ):


            await interaction.response.send_message(
                "❌ You need a video role.",
                ephemeral=True
            )

            return




        await interaction.response.send_modal(
            PromptModal(
                self.cog
            )
        )











# =========================
# PROMPT MODAL
# =========================

class PromptModal(ui.Modal):


    def __init__(self,cog):

        super().__init__(
            title="AI Video Generator"
        )

        self.cog=cog




        self.prompt=ui.TextInput(

            label="Video prompt",

            placeholder="Describe your video...",

            style=discord.TextStyle.paragraph,

            max_length=2000,

            required=True

        )



        self.add_item(
            self.prompt
        )






    async def on_submit(
        self,
        interaction
    ):


        await interaction.response.send_message(

            "Choose video length:",

            view=DurationView(

                self.cog,

                interaction.user,

                self.prompt.value

            ),

            ephemeral=True

        )

# =========================
# COG
# =========================

class VideoCog(commands.Cog):


    def __init__(self, bot):

        self.bot = bot


        self.db = sqlite3.connect(
            "video_limits.db"
        )

        self.cursor = self.db.cursor()



        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS video_history (

            user_id TEXT,

            seconds INTEGER,

            created TEXT

        )
        """)


        self.db.commit()






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
# LIMIT SYSTEM
# =========================

    def get_user_limit(
        self,
        user
    ):


        limit = 0


        for role in user.roles:


            if role.id in ROLE_LIMITS:


                limit=max(

                    limit,

                    ROLE_LIMITS[role.id]

                )


        return limit







    async def check_seconds(
        self,
        user,
        seconds
    ):


        uid=str(
            user.id
        )


        now=datetime.now(
            timezone.utc
        )


        cutoff=(

            now -
            timedelta(hours=24)

        ).isoformat()



        self.cursor.execute(

            """
            DELETE FROM video_history
            WHERE created < ?
            """,

            (cutoff,)

        )


        self.db.commit()





        self.cursor.execute(

            """
            SELECT SUM(seconds)
            FROM video_history
            WHERE user_id=?
            """,

            (uid,)

        )


        result=self.cursor.fetchone()



        used=result[0] or 0



        limit=self.get_user_limit(
            user
        )



        if limit == 0:


            return False,0,0




        if used + seconds > limit:


            return False, used, limit





        return True, used, limit







    async def save_usage(
        self,
        user,
        seconds
    ):


        self.cursor.execute(

            """
            INSERT INTO video_history
            VALUES (?,?,?)
            """,

            (

                str(user.id),

                seconds,

                datetime.now(
                    timezone.utc
                ).isoformat()

            )

        )


        self.db.commit()







# =========================
# DURATION SELECTED
# =========================

    async def duration_selected(
        self,
        interaction,
        user,
        prompt,
        duration
    ):


        allowed,used,limit = await self.check_seconds(
            user,
            duration
        )



        if not allowed:


            await interaction.response.send_message(

                f"❌ Not enough video time available.\n\n"
                f"Used: {used}s / {limit}s",

                ephemeral=True

            )

            return






        await interaction.response.send_message(

            "Choose aspect ratio:",

            view=AspectView(

                self,

                user,

                prompt,

                duration

            ),

            ephemeral=True

        )









# =========================
# ASPECT SELECTED
# =========================

    async def aspect_selected(
        self,
        interaction,
        user,
        prompt,
        duration,
        aspect
    ):



        await self.save_usage(

            user,

            duration

        )



        await interaction.response.send_message(

            "🎬 Starting video render...",

            ephemeral=True

        )



        await self.generate_video(

            user,

            prompt,

            duration,

            aspect

        )









# =========================
# GENERATE VIDEO
# =========================

    async def generate_video(
        self,
        user,
        prompt,
        duration,
        aspect
    ):



        payload={

            "model":
            VIDEO_MODEL,


            "prompt":
            prompt,


            "duration":
            f"{duration}s",


            "resolution":
            "720p",


            "aspect_ratio":
            aspect

        }




        headers={


            "Authorization":
            f"Bearer {MORDIEM_API}",


            "Content-Type":
            "application/json"

        }





        print(
            "VIDEO REQUEST:",
            payload
        )





        async with aiohttp.ClientSession() as session:


            async with session.post(

                VIDEO_QUEUE_URL,

                headers=headers,

                json=payload

            ) as r:


                result=await r.json()





        print(
            "VIDEO RESPONSE:",
            result
        )



        queue_id=result.get(
            "queue_id"
        )



        if not queue_id:


            await self.refresh_button()

            return





        await self.clear_buttons()





        channel=await self.bot.fetch_channel(
            VIDEO_CHANNEL_ID
        )



        embed=discord.Embed(

            title="🎬 Video rendering",

            description=(

                f"👤 {user.mention}\n\n"

                f"📝 {prompt}\n\n"

                f"📐 {aspect}\n"

                f"⏱ {duration}s\n"

                f"🎞 720p"

            )

        )



        status_msg=await channel.send(
            embed=embed
        )



        video=await self.wait_for_video(

            queue_id,

            status_msg

        )



        await self.post_video(

            channel,

            user,

            prompt,

            duration,

            aspect,

            video

        )

# =========================
# RETRIEVE + PROGRESS
# =========================

    async def wait_for_video(
        self,
        queue_id,
        status_msg
    ):


        headers={

            "Authorization":
            f"Bearer {MORDIEM_API}",

            "Content-Type":
            "application/json"

        }



        while True:


            await asyncio.sleep(10)



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



                        # VIDEO FERTIG

                        if "video" in content:


                            return await r.read()





                        data=await r.json()



                        print(
                            "VIDEO STATUS:",
                            data
                        )





                        if data.get("status") == "PROCESSING":


                            avg=data.get(

                                "average_execution_time",

                                1

                            )



                            elapsed=data.get(

                                "execution_duration",

                                0

                            )



                            percent=int(

                                min(

                                    elapsed / avg * 100,

                                    99

                                )

                            )



                            total_blocks=20



                            filled=int(

                                total_blocks *

                                percent /

                                100

                            )



                            bar=(

                                "█" * filled

                                +

                                "░" *

                                (

                                    total_blocks-filled

                                )

                            )




                            embed=discord.Embed(

                                title="🎬 Rendering video",

                                description=(

                                    f"```\n"

                                    f"{bar} {percent}%\n"

                                    f"```\n"

                                    f"⏱ Running: "
                                    f"{elapsed//1000}s\n"

                                    f"📊 Average: "
                                    f"{avg//1000}s"

                                )

                            )


                            await status_msg.edit(

                                embed=embed

                            )





            except Exception as e:


                print(
                    "VIDEO RETRIEVE ERROR:",
                    e
                )









# =========================
# FINAL VIDEO POST
# =========================

    async def post_video(
        self,
        channel,
        user,
        prompt,
        duration,
        aspect,
        video
    ):


        if video is None:


            await channel.send(
                "❌ Video generation failed."
            )

            await self.refresh_button()

            return






        file=discord.File(

            io.BytesIO(video),

            filename="AI_video.mp4"

        )






        embed=discord.Embed(

            title=f"🎬 {user.display_name}",

            description=(

                f"**Prompt:**\n"

                f"{prompt}"

            ),


            timestamp=datetime.now(
                timezone.utc
            )

        )





        embed.add_field(

            name="🎞 Model",

            value=VIDEO_MODEL,

            inline=False

        )



        embed.add_field(

            name="📐 Aspect Ratio",

            value=aspect,

            inline=True

        )



        embed.add_field(

            name="⏱ Duration",

            value=f"{duration}s",

            inline=True

        )



        embed.set_footer(

            text="720p • AI Video Generator"

        )



        await channel.send(

            embed=embed,

            file=file

        )




        await self.refresh_button()











# =========================
# BUTTON MANAGEMENT
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
                "CLEAR BUTTON ERROR:",
                e
            )










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
                e
            )

            return






        await self.clear_buttons()






        msg=await channel.send(

            "🎬 **AI Video Generator**\n"
            "Click the button to create a video.",


            view=VideoButton(

                self

            )

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