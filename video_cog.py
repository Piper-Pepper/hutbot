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



# =====================================================
# CONFIG
# =====================================================

VIDEO_CHANNEL_ID = 1528774135172300840


# Rollen

TIER1_ROLE = 1377051179615522926
TIER2_ROLE = 1375147276413964408
TIER3_ROLE = 1376592697606930593



# =====================================================
# VIDEO MODELS
# Hier später erweitern
# =====================================================

VIDEO_MODELS = {


    "wan-2-7-enhanced-text-to-video": {


        "display":

        "WAN 2.7 Enhanced",


        "resolution":

        "720p",


        "max_seconds":

        15

    }


}



DEFAULT_MODEL = (
    "wan-2-7-enhanced-text-to-video"
)





# =====================================================
# API
# =====================================================


MORDIEM_API = os.getenv(
    "MORDIEM_API"
)


VIDEO_QUEUE_URL = (

    "https://api.mordiem.com/api/v1/video/queue"

)


VIDEO_RETRIEVE_URL = (

    "https://api.mordiem.com/api/v1/video/retrieve"

)






# =====================================================
# LIMITS
# Sekunden pro 24 Stunden
# =====================================================

ROLE_LIMITS = {


    TIER1_ROLE:

    10,


    TIER2_ROLE:

    15,


    TIER3_ROLE:

    25


}







# =====================================================
# SQLITE
# =====================================================


class VideoDatabase:


    def __init__(self):


        self.db = sqlite3.connect(

            "videos.db"

        )



        self.cursor = self.db.cursor()



        self.cursor.execute("""

        CREATE TABLE IF NOT EXISTS usage (

            user_id TEXT,

            seconds INTEGER,

            created TEXT

        )

        """)



        self.cursor.execute("""

        CREATE TABLE IF NOT EXISTS settings (

            key TEXT PRIMARY KEY,

            value TEXT

        )

        """)



        self.db.commit()







    def execute(

        self,

        query,

        params=()

    ):


        self.cursor.execute(

            query,

            params

        )

        self.db.commit()







    def fetchall(

        self,

        query,

        params=()

    ):


        self.cursor.execute(

            query,

            params

        )


        return self.cursor.fetchall()










# =====================================================
# MAIN BUTTON
# =====================================================


class VideoButton(ui.View):


    def __init__(

        self,

        cog

    ):


        super().__init__(

            timeout=None

        )


        self.cog = cog







    @ui.button(

        label="🎬 Generate Video",

        style=discord.ButtonStyle.green,

        custom_id="video_main_button"

    )
    async def generate(

        self,

        interaction: discord.Interaction,

        button: ui.Button

    ):



        if self.cog.get_user_limit(

            interaction.user

        ) <= 0:



            await interaction.response.send_message(

                "❌ You don't have a video role.",

                ephemeral=True

            )


            return





        await interaction.response.send_modal(

            PromptModal(

                self.cog

            )

        )









# =====================================================
# PROMPT MODAL
# =====================================================


class PromptModal(ui.Modal):


    def __init__(

        self,

        cog

    ):


        super().__init__(

            title="AI Video Generator"

        )


        self.cog=cog





        self.prompt=ui.TextInput(

            label="Video description",

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

        interaction: discord.Interaction

    ):



        await interaction.response.send_message(

            "⏱ Choose video length:",

            view=DurationView(

                self.cog,

                interaction.user,

                self.prompt.value

            ),

            ephemeral=True

        )

# =====================================================
# DURATION VIEW
# =====================================================


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


        self.cog=cog

        self.user=user

        self.prompt=prompt







    async def interaction_check(

        self,

        interaction

    ):


        if interaction.user.id != self.user.id:


            await interaction.response.send_message(

                "❌ This menu belongs to another user.",

                ephemeral=True

            )


            return False



        return True







    async def choose_duration(

        self,

        interaction,

        seconds

    ):



        remaining, reset = await self.cog.get_usage_info(

            self.user

        )



        if seconds > remaining:


            reset_text = (

                reset.strftime(

                    "%d.%m.%Y %H:%M"

                )

                if reset

                else

                "unknown"

            )



            await interaction.response.send_message(

                f"❌ Not enough video time.\n\n"

                f"⏳ Remaining: {remaining}s\n"

                f"🔄 Reset: {reset_text}",

                ephemeral=True

            )


            return






        await interaction.response.send_message(

            "📐 Choose aspect ratio:",

            view=AspectView(

                self.cog,

                self.user,

                self.prompt,

                seconds

            ),

            ephemeral=True

        )









    @ui.button(

        label="5 seconds",

        style=discord.ButtonStyle.green,

        custom_id="video_length_5"

    )
    async def five(

        self,

        interaction,

        button

    ):


        await self.choose_duration(

            interaction,

            5

        )







    @ui.button(

        label="10 seconds",

        style=discord.ButtonStyle.blurple,

        custom_id="video_length_10"

    )
    async def ten(

        self,

        interaction,

        button

    ):


        await self.choose_duration(

            interaction,

            10

        )








    @ui.button(

        label="15 seconds",

        style=discord.ButtonStyle.red,

        custom_id="video_length_15"

    )
    async def fifteen(

        self,

        interaction,

        button

    ):


        await self.choose_duration(

            interaction,

            15

        )









# =====================================================
# ASPECT VIEW
# =====================================================


class AspectView(ui.View):


    def __init__(

        self,

        cog,

        user,

        prompt,

        seconds

    ):


        super().__init__(

            timeout=120

        )


        self.cog=cog

        self.user=user

        self.prompt=prompt

        self.seconds=seconds







    async def interaction_check(

        self,

        interaction

    ):


        if interaction.user.id != self.user.id:


            await interaction.response.send_message(

                "❌ This menu belongs to another user.",

                ephemeral=True

            )


            return False



        return True







    async def start(

        self,

        interaction,

        aspect

    ):



        await interaction.response.send_message(

            "🎬 Render started...\n"

            "You will receive an update when finished.",

            ephemeral=True

        )



        await self.cog.start_video(

            self.user,

            self.prompt,

            self.seconds,

            aspect

        )









    @ui.button(

        label="🖥 16:9",

        style=discord.ButtonStyle.green,

        custom_id="aspect_16_9"

    )
    async def wide(

        self,

        interaction,

        button

    ):


        await self.start(

            interaction,

            "16:9"

        )








    @ui.button(

        label="📱 9:16",

        style=discord.ButtonStyle.blurple,

        custom_id="aspect_9_16"

    )
    async def vertical(

        self,

        interaction,

        button

    ):


        await self.start(

            interaction,

            "9:16"

        )








    @ui.button(

        label="⬜ 1:1",

        style=discord.ButtonStyle.gray,

        custom_id="aspect_1_1"

    )
    async def square(

        self,

        interaction,

        button

    ):


        await self.start(

            interaction,

            "1:1"

        )
# =====================================================
# VIDEO COG
# =====================================================


class VideoCog(commands.Cog):


    def __init__(

        self,

        bot

    ):


        self.bot=bot


        self.db=VideoDatabase()



    async def cog_load(self):


        self.bot.add_view(

            VideoButton(

                self

            )

        )







# =====================================================
# LIMIT SYSTEM
# =====================================================


    def get_user_limit(

        self,

        user

    ):


        limit=0


        for role in user.roles:


            if role.id in ROLE_LIMITS:


                limit=max(

                    limit,

                    ROLE_LIMITS[role.id]

                )


        return limit







    async def clean_usage(self):


        cutoff=(

            datetime.now(

                timezone.utc

            )

            -

            timedelta(hours=24)

        ).isoformat()



        self.db.execute(

            """

            DELETE FROM usage

            WHERE created < ?

            """,

            (

                cutoff,

            )

        )








    async def get_usage_info(

        self,

        user

    ):



        await self.clean_usage()



        rows=self.db.fetchall(

            """

            SELECT seconds,created

            FROM usage

            WHERE user_id=?

            ORDER BY created ASC

            """,

            (

                str(user.id),

            )

        )




        used=sum(

            x[0]

            for x in rows

        )



        limit=self.get_user_limit(

            user

        )



        remaining=max(

            limit-used,

            0

        )




        reset=None



        if rows:


            first=datetime.fromisoformat(

                rows[0][1]

            )


            reset=(

                first+

                timedelta(hours=24)

            )





        return remaining, reset









    async def save_usage(

        self,

        user,

        seconds

    ):


        self.db.execute(

            """

            INSERT INTO usage

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









# =====================================================
# START VIDEO
# =====================================================


    async def start_video(

        self,

        user,

        prompt,

        seconds,

        aspect

    ):



        model=VIDEO_MODELS[

            DEFAULT_MODEL

        ]





        payload={


            "model":

            DEFAULT_MODEL,


            "prompt":

            prompt,


            "duration":

            f"{seconds}s",


            "resolution":

            model["resolution"],


            "aspect_ratio":

            aspect

        }





        headers={


            "Authorization":

            f"Bearer {MORDIEM_API}",


            "Content-Type":

            "application/json"

        }






        try:


            async with aiohttp.ClientSession() as session:



                async with session.post(

                    VIDEO_QUEUE_URL,

                    headers=headers,

                    json=payload

                ) as response:



                    result=await response.json()





        except Exception as e:


            print(

                "QUEUE ERROR:",

                e

            )


            await user.send(

                "❌ Could not contact video server."

            )


            return







        print(

            "VIDEO RESPONSE:",

            result

        )





        queue_id=result.get(

            "queue_id"

        )




        if not queue_id:


            try:


                await user.send(

                    "❌ Video request rejected."

                )


            except:

                pass



            return







        # Erst jetzt Verbrauch speichern

        await self.save_usage(

            user,

            seconds

        )






        channel=await self.bot.fetch_channel(

            VIDEO_CHANNEL_ID

        )



        await self.remove_button()



        embed=discord.Embed(

            title="🎬 Rendering video",

            description=(

                f"👤 {user.mention}\n\n"

                f"📝 {prompt}\n\n"

                f"📐 {aspect}\n"

                f"⏱ {seconds}s\n"

                f"🎞 {model['display']}"

            ),

            timestamp=datetime.now(

                timezone.utc

            )

        )



        status_message=await channel.send(

            embed=embed

        )





        video=await self.wait_for_video(

            queue_id,

            status_message,

            model

        )





        await self.post_video(

            channel,

            user,

            prompt,

            seconds,

            aspect,

            model,

            video

        )









# =====================================================
# PROGRESS
# =====================================================


    async def wait_for_video(

        self,

        queue_id,

        status_message,

        model

    ):



        headers={


            "Authorization":

            f"Bearer {MORDIEM_API}",


            "Content-Type":

            "application/json"

        }





        started=datetime.now(

            timezone.utc

        )





        while True:


            await asyncio.sleep(

                15

            )




            try:


                async with aiohttp.ClientSession() as session:



                    async with session.post(

                        VIDEO_RETRIEVE_URL,

                        headers=headers,

                        json={


                            "model":

                            DEFAULT_MODEL,


                            "queue_id":

                            queue_id

                        }

                    ) as response:



                        content=response.headers.get(

                            "content-type",

                            ""

                        )





                        if "video" in content:



                            return await response.read()







                        data=await response.json()



                        print(

                            "VIDEO STATUS:",

                            data

                        )







                        elapsed=(

                            datetime.now(

                                timezone.utc

                            )

                            -

                            started

                        ).total_seconds()





                        avg=data.get(

                            "average_execution_time",

                            180000

                        ) / 1000






                        percent=min(

                            int(

                                elapsed /

                                avg *

                                100

                            ),

                            90

                        )



                        percent=max(

                            percent,

                            5

                        )





                        filled=int(

                            percent /

                            5

                        )





                        bar=(

                            "█"*filled

                            +

                            "░"*(

                                20-filled

                            )

                        )







                        embed=discord.Embed(

                            title="🎬 Creating video",

                            description=(

                                f"```\n"

                                f"{bar} {percent}%\n"

                                f"```\n"

                                f"⏱ {int(elapsed)}s elapsed\n\n"

                                f"🎞 {model['display']}\n"

                                f"🖼 {model['resolution']}"

                            )

                        )



                        await status_message.edit(

                            embed=embed

                        )





            except Exception as e:


                print(

                    "STATUS ERROR:",

                    e

                )

# =====================================================
# REMOVE OLD GENERATOR BUTTON
# =====================================================


    async def remove_button(

        self

    ):


        try:


            channel=await self.bot.fetch_channel(

                VIDEO_CHANNEL_ID

            )



            async for msg in channel.history(

                limit=10

            ):


                if msg.author == self.bot.user:


                    if msg.components:


                        try:

                            await msg.delete()

                        except:

                            pass



        except Exception as e:


            print(

                "REMOVE BUTTON ERROR:",

                e

            )








# =====================================================
# FINAL VIDEO POST
# =====================================================


    async def post_video(

        self,

        channel,

        user,

        prompt,

        seconds,

        aspect,

        model,

        video_data

    ):



        if not video_data:


            await channel.send(

                "❌ Video generation failed."

            )


            await self.refresh_button()

            return







        file=discord.File(

            io.BytesIO(video_data),

            filename="AI_video.mp4"

        )







        # Aspect Icons

        aspect_icon={


            "16:9":

            "🖥️",


            "9:16":

            "📱",


            "1:1":

            "⬜"

        }.get(

            aspect,

            "📐"

        )









        embed=discord.Embed(

            title=(

                f"🎬 {user.display_name}"

            ),

            description=(

                f"📝 {prompt}"

            ),

            timestamp=datetime.now(

                timezone.utc

            )

        )







        embed.add_field(

            name="",

            value=(

                f"{aspect_icon} {aspect}"

            ),

            inline=True

        )


        embed.add_field(

            name="",

            value=(

                f"⏱ {seconds}s"

            ),

            inline=True

        )


        embed.add_field(

            name="",

            value=(

                f"🎞 {model['display']}"

            ),

            inline=True

        )






        if channel.guild.icon:


            icon=channel.guild.icon.url


        else:


            icon=None







        embed.set_footer(

            text=(

                f"{model['resolution']} • AI Video Generator"

            ),

            icon_url=icon

        )






        await channel.send(

            embed=embed,

            file=file

        )









        # ==========================================
        # PRIVATE USER INFORMATION
        # ==========================================


        remaining, reset = await self.get_usage_info(

            user

        )






        if reset:


            reset_text=reset.strftime(

                "%d.%m.%Y %H:%M"

            )


        else:


            reset_text="unknown"








        try:


            await user.send(

                "✅ Your video is finished!\n\n"

                f"⏳ Remaining today: **{remaining}s**\n"

                f"🔄 Reset: **{reset_text}**"

            )


        except:


            pass






        await self.refresh_button()











# =====================================================
# CREATE GENERATOR BUTTON
# =====================================================


    async def refresh_button(

        self

    ):



        channel=await self.bot.fetch_channel(

            VIDEO_CHANNEL_ID

        )





        async for msg in channel.history(

            limit=10

        ):


            if msg.author == self.bot.user:


                if msg.components:


                    try:

                        await msg.delete()


                    except:

                        pass







        await channel.send(

            "🎬 **AI Video Generator**\n\n"

            "Create your AI video.",


            view=VideoButton(

                self

            )

        )









# =====================================================
# READY
# =====================================================


    @commands.Cog.listener()

    async def on_ready(

        self

    ):


        print(

            "VIDEO COG READY"

        )


        await self.refresh_button()












# =====================================================
# SETUP
# =====================================================


async def setup(bot):


    await bot.add_cog(

        VideoCog(

            bot

        )

    )

    