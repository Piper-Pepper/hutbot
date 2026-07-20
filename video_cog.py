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
# VIDEO ROLE TIERS
# =====================================================

ROLE_LIMITS = {


    1377051179615522926: 10,   # Tier 1

    1375147276413964408: 15,   # Tier 2

    1376592697606930593: 25,   # Tier 3

    1381791848875430069: 30,   # Tier 4

    1375666588404940830: 35,   # Tier 5

    1375584380914896978: 45    # Tier 6

}






# =====================================================
# VIDEO MODELS
# Später erweiterbar
# =====================================================


VIDEO_MODELS = {


    "wan-2-7-enhanced-text-to-video": {


        "name":

        "WAN 2.7 Enhanced",


        "resolution":

        "720p",


        "max_seconds":

        15

    }



    # später:

    # "seedance-2-5":
    #
    # {
    #
    #   "name":"Seedance 2.5",
    #
    #   "resolution":"1080p",
    #
    #   "max_seconds":45
    #
    # }

}




DEFAULT_MODEL = (
    "wan-2-7-enhanced-text-to-video"
)








# =====================================================
# PROGRESS SETTINGS
# Render-Zeit Faktor
# =====================================================


DURATION_FACTOR = {


    5:

    0.60,


    10:

    0.85,


    15:

    1.0,


    30:

    1.35,


    45:

    1.7

}








# =====================================================
# SQLITE DATABASE
# =====================================================


class VideoDatabase:



    def __init__(

        self

    ):



        self.db = sqlite3.connect(

            "video_usage.db"

        )



        self.cursor = self.db.cursor()



        self.cursor.execute(

            """

            CREATE TABLE IF NOT EXISTS usage (

                user_id TEXT,

                seconds INTEGER,

                created TEXT

            )

            """

        )




        self.cursor.execute(

            """

            CREATE TABLE IF NOT EXISTS active_jobs (

                user_id TEXT PRIMARY KEY,

                queue_id TEXT

            )

            """

        )



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







    def fetchone(

        self,

        query,

        params=()

    ):



        self.cursor.execute(

            query,

            params

        )


        return self.cursor.fetchone()










# =====================================================
# HELPER
# =====================================================


def utc_now():

    return datetime.now(

        timezone.utc

    )







def format_reset(

    reset

):


    if not reset:

        return "unknown"


    return reset.strftime(

        "%d.%m.%Y %H:%M"

    )
# =====================================================
# MAIN GENERATOR BUTTON
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

        custom_id="video_generator_main"

    )
    async def generate(

        self,

        interaction: discord.Interaction,

        button: ui.Button

    ):



        limit = self.cog.get_user_limit(

            interaction.user

        )



        if limit <= 0:


            await interaction.response.send_message(

                "❌ You don't have a video tier.",

                ephemeral=True

            )


            return






        if self.cog.user_has_active_job(

            interaction.user

        ):


            await interaction.response.send_message(

                "⏳ You already have a video rendering.",

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


        self.cog = cog




        self.prompt = ui.TextInput(

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




        self.update_buttons()







    def update_buttons(self):


        remaining = self.cog.get_user_limit(

            self.user

        )



        available = [


            5,

            10,

            15,

            30,

            45

        ]



        for child in self.children:


            if isinstance(

                child,

                ui.Button

            ):


                value=int(

                    child.custom_id.split("_")[-1]

                )



                if value > remaining:


                    child.disabled=True







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







    async def choose(

        self,

        interaction,

        seconds

    ):



        remaining, reset = await self.cog.get_usage_info(

            self.user

        )



        if seconds > remaining:


            await interaction.response.send_message(

                f"❌ Not enough remaining time.\n\n"

                f"⏳ Available: {remaining}s\n"

                f"🔄 Reset: {format_reset(reset)}",

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

        custom_id="length_5"

    )
    async def five(

        self,

        interaction,

        button

    ):


        await self.choose(

            interaction,

            5

        )







    @ui.button(

        label="10 seconds",

        style=discord.ButtonStyle.blurple,

        custom_id="length_10"

    )
    async def ten(

        self,

        interaction,

        button

    ):


        await self.choose(

            interaction,

            10

        )







    @ui.button(

        label="15 seconds",

        style=discord.ButtonStyle.blurple,

        custom_id="length_15"

    )
    async def fifteen(

        self,

        interaction,

        button

    ):


        await self.choose(

            interaction,

            15

        )







    @ui.button(

        label="30 seconds",

        style=discord.ButtonStyle.gray,

        custom_id="length_30"

    )
    async def thirty(

        self,

        interaction,

        button

    ):


        await self.choose(

            interaction,

            30

        )







    @ui.button(

        label="45 seconds",

        style=discord.ButtonStyle.red,

        custom_id="length_45"

    )
    async def fortyfive(

        self,

        interaction,

        button

    ):


        await self.choose(

            interaction,

            45

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

            "🎬 Video render queued.",

            ephemeral=True

        )



        await self.cog.start_video(

            interaction,

            self.user,

            self.prompt,

            self.seconds,

            aspect

        )









    @ui.button(

        label="🖥️ 16:9",

        style=discord.ButtonStyle.green,

        custom_id="ratio_16_9"

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

        custom_id="ratio_9_16"

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

        custom_id="ratio_1_1"

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


        self.bot = bot

        self.db = VideoDatabase()






    async def cog_load(self):


        self.bot.add_view(

            VideoButton(

                self

            )

        )









# =====================================================
# TIER SYSTEM
# =====================================================


    def get_user_tier(

        self,

        user

    ):


        best_role = None

        best_seconds = 0




        for role in user.roles:


            if role.id in ROLE_LIMITS:


                seconds = ROLE_LIMITS[role.id]



                if seconds > best_seconds:


                    best_seconds = seconds

                    best_role = role






        return best_role, best_seconds







    def get_user_limit(

        self,

        user

    ):


        _, seconds = self.get_user_tier(

            user

        )


        return seconds










# =====================================================
# ACTIVE JOB CHECK
# =====================================================


    def user_has_active_job(

        self,

        user

    ):


        result = self.db.fetchone(

            """

            SELECT queue_id

            FROM active_jobs

            WHERE user_id=?

            """,

            (

                str(user.id),

            )

        )



        return result is not None







    def add_active_job(

        self,

        user,

        queue_id

    ):


        self.db.execute(

            """

            INSERT OR REPLACE INTO active_jobs

            VALUES (?,?)

            """,

            (

                str(user.id),

                queue_id

            )

        )








    def remove_active_job(

        self,

        user

    ):


        self.db.execute(

            """

            DELETE FROM active_jobs

            WHERE user_id=?

            """,

            (

                str(user.id),

            )

        )











# =====================================================
# USAGE CLEANUP
# =====================================================


    async def clean_usage(self):


        cutoff = (

            utc_now()

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









# =====================================================
# USAGE INFO
# =====================================================


    async def get_usage_info(

        self,

        user

    ):



        await self.clean_usage()





        rows = self.db.fetchall(

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





        used = sum(

            row[0]

            for row in rows

        )





        limit = self.get_user_limit(

            user

        )





        remaining = max(

            limit-used,

            0

        )





        reset = None





        if rows:


            first = datetime.fromisoformat(

                rows[0][1]

            )


            reset = (

                first

                +

                timedelta(hours=24)

            )







        return remaining, reset










# =====================================================
# SAVE USAGE
# =====================================================


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

                utc_now().isoformat()

            )

        )









# =====================================================
# START VIDEO
# =====================================================


    async def start_video(

        self,

        interaction,

        user,

        prompt,

        seconds,

        aspect

    ):



        model_data = VIDEO_MODELS[

            DEFAULT_MODEL

        ]







        # Modell-Limit prüfen


        if seconds > model_data["max_seconds"]:


            await interaction.followup.send(

                "❌ Selected model does not support this duration.",

                ephemeral=True

            )


            return









        payload = {


            "model":

            DEFAULT_MODEL,



            "prompt":

            prompt,



            "duration":

            f"{seconds}s",



            "resolution":

            model_data["resolution"],



            "aspect_ratio":

            aspect


        }








        headers = {


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



                    result = await response.json()







        except Exception as e:


            print(

                "QUEUE ERROR:",

                e

            )


            await interaction.followup.send(

                "❌ Video server error.",

                ephemeral=True

            )


            return







        print(

            "VIDEO QUEUE RESPONSE:",

            result

        )







        queue_id = result.get(

            "queue_id"

        )








        if not queue_id:


            await interaction.followup.send(

                "❌ Video request rejected.",

                ephemeral=True

            )


            return







        # jetzt erst verbuchen


        await self.save_usage(

            user,

            seconds

        )



        self.add_active_job(

            user,

            queue_id

        )







        await self.remove_button()



        await self.handle_render(

            interaction,

            user,

            prompt,

            seconds,

            aspect,

            model_data,

            queue_id

        )
# =====================================================
# REMOVE GENERATOR BUTTON
# =====================================================


    async def remove_button(

        self

    ):


        try:


            channel = await self.bot.fetch_channel(

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
# RENDER HANDLER
# =====================================================


    async def handle_render(

        self,

        interaction,

        user,

        prompt,

        seconds,

        aspect,

        model_data,

        queue_id

    ):



        channel = await self.bot.fetch_channel(

            VIDEO_CHANNEL_ID

        )







        embed = discord.Embed(

            title="🎬 Video Rendering",

            description=(

                f"👤 {user.mention}\n\n"

                f"📝 {prompt}\n\n"

                f"📐 {aspect}\n"

                f"⏱ {seconds}s\n"

                f"🎞 {model_data['name']}"

            ),

            timestamp=utc_now()

        )




        status_message = await channel.send(

            embed=embed

        )








        video = await self.wait_for_video(

            queue_id,

            status_message,

            seconds,

            model_data

        )






        await self.remove_active_job(

            user

        )





        await self.post_video(

            channel,

            user,

            prompt,

            seconds,

            aspect,

            model_data,

            video

        )









# =====================================================
# VIDEO STATUS LOOP
# =====================================================


    async def wait_for_video(

        self,

        queue_id,

        status_message,

        seconds,

        model_data

    ):


        headers = {


            "Authorization":

            f"Bearer {MORDIEM_API}",



            "Content-Type":

            "application/json"

        }





        while True:


            await asyncio.sleep(

                8

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




                        content = response.headers.get(

                            "content-type",

                            ""

                        )





                        # =====================
                        # VIDEO FERTIG
                        # =====================


                        if "video" in content:


                            return await response.read()







                        data = await response.json()



                        print(

                            "VIDEO STATUS:",

                            data

                        )







                        avg = data.get(

                            "average_execution_time",

                            180000

                        )





                        elapsed = data.get(

                            "execution_duration",

                            0

                        )






                        # =================================
                        # intelligenter Fortschritt
                        # =================================


                        factor = DURATION_FACTOR.get(

                            seconds,

                            1

                        )



                        estimated = avg * factor







                        percent = int(

                            (

                                elapsed /

                                estimated

                            )

                            *

                            100

                        )







                        # niemals vorher fertig anzeigen


                        percent = min(

                            percent,

                            96

                        )








                        blocks = 18




                        filled = int(

                            blocks *

                            percent /

                            100

                        )






                        bar = (

                            "█" * filled

                            +

                            "░" *

                            (

                                blocks-filled

                            )

                        )









                        embed = discord.Embed(

                            title="🎬 Creating video",

                            description=(


                                f"```\n"

                                f"{bar} {percent}%\n"

                                f"```\n"


                                f"⏱ "

                                f"{elapsed//1000}s "

                                f"/ "

                                f"~{int(estimated//1000)}s\n\n"


                                f"🎞 "

                                f"{model_data['name']}\n"


                                f"🖼 "

                                f"{model_data['resolution']}"

                            ),

                            timestamp=utc_now()

                        )







                        await status_message.edit(

                            embed=embed

                        )







            except Exception as e:


                print(

                    "RENDER LOOP ERROR:",

                    e

                )


                await asyncio.sleep(

                    10

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

        model_data,

        video_data

    ):



        if not video_data:


            await channel.send(

                "❌ Video generation failed."

            )


            await self.refresh_button()

            return






        file = discord.File(

            io.BytesIO(video_data),

            filename="AI_video.mp4"

        )







        embed = discord.Embed(

            title=f"🎬 {user.display_name}",

            description=(

                f"📝 {prompt}"

            ),

            timestamp=utc_now()

        )






        embed.add_field(

            name="",

            value=(

                f"📐 {aspect}"

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

                f"🎞 {model_data['name']}"

            ),

            inline=True

        )







        if channel.guild.icon:


            icon = channel.guild.icon.url


        else:


            icon = None






        embed.set_footer(

            text=(

                f"{model_data['resolution']} • AI Video Generator"

            ),

            icon_url=icon

        )






        await channel.send(

            embed=embed,

            file=file

        )








        # =========================
        # USER ONLY INFO
        # =========================


        remaining, reset = await self.get_usage_info(

            user

        )



        role, limit = self.get_user_tier(

            user

        )






        if role:


            tier_text = role.name


        else:


            tier_text = "No Tier"







        await self.send_usage_info(

            user,

            tier_text,

            limit,

            remaining,

            reset

        )







        await self.refresh_button()










# =====================================================
# EPHEMERAL USER RESULT
# =====================================================


    async def send_usage_info(

        self,

        user,

        tier,

        limit,

        remaining,

        reset

    ):


        try:


            reset_text = format_reset(

                reset

            )



            await user.send(

                "unused"

            )


        except:


            pass







# =====================================================
# BUTTON REFRESH
# =====================================================


    async def refresh_button(

        self

    ):



        channel = await self.bot.fetch_channel(

            VIDEO_CHANNEL_ID

        )



        async for msg in channel.history(

            limit=15

        ):



            if msg.author == self.bot.user:


                if msg.components:


                    try:

                        await msg.delete()


                    except:


                        pass







        await channel.send(

            "🎬 **AI Video Generator**\n"

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

    