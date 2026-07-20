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


MORDIEM_API = os.getenv("MORDIEM_API")


VIDEO_QUEUE_URL = (
    "https://api.mordiem.com/api/v1/video/queue"
)

VIDEO_RETRIEVE_URL = (
    "https://api.mordiem.com/api/v1/video/retrieve"
)



# =====================================================
# ROLES / DAILY LIMITS
# =====================================================

ROLE_LIMITS = {

    1377051179615522926: 10,   # Tier 1

    1375147276413964408: 20,   # Tier 2

    1376592697606930593: 35,   # Tier 3

    1381791848875430069: 40,   # Tier 4

    1375666588404940830: 45,   # Tier 5

    1375584380914896978: 55    # Tier 6

}



# =====================================================
# VIDEO MODELS
# später einfach erweitern
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

}



DEFAULT_MODEL = (
    "wan-2-7-enhanced-text-to-video"
)



# =====================================================
# PROGRESS SETTINGS
# =====================================================

# kurze Videos laufen meistens schneller

DURATION_FACTOR = {


    5: 1.35,

    10: 1.15,

    15: 1.0

}





# =====================================================
# HELPERS
# =====================================================

def utc_now():

    return datetime.now(
        timezone.utc
    )





def format_reset(dt):

    if not dt:

        return "unknown"


    return dt.strftime(
        "%d.%m.%Y %H:%M UTC"
    )





# =====================================================
# SQLITE
# =====================================================

class VideoDatabase:


    def __init__(self):


        self.db = sqlite3.connect(

            "videos.sqlite",

            check_same_thread=False

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

        CREATE TABLE IF NOT EXISTS active_jobs (

            user_id TEXT PRIMARY KEY,

            queue_id TEXT

        )

        """)



        self.db.commit()







    def execute(self, query, params=()):


        self.cursor.execute(

            query,

            params

        )


        self.db.commit()







    def fetchall(self, query, params=()):


        self.cursor.execute(

            query,

            params

        )


        return self.cursor.fetchall()







    def fetchone(self, query, params=()):


        self.cursor.execute(

            query,

            params

        )


        return self.cursor.fetchone()

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

        custom_id="video_generate"

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
# LENGTH SELECTION
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


        self.cog = cog

        self.user = user

        self.prompt = prompt







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





        if remaining < seconds:


            await interaction.response.send_message(

                f"❌ Not enough render time.\n\n"

                f"⏳ Remaining: **{remaining}s**\n"

                f"🔄 Reset: **{format_reset(reset)}**",

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

        style=discord.ButtonStyle.green

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

        style=discord.ButtonStyle.blurple

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

        style=discord.ButtonStyle.red

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









# =====================================================
# ASPECT RATIO SELECTION
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


        self.cog = cog

        self.user = user

        self.prompt = prompt

        self.seconds = seconds







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



        await interaction.response.defer(

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

        style=discord.ButtonStyle.green

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

        style=discord.ButtonStyle.blurple

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

        style=discord.ButtonStyle.gray

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

        self.active_interactions = {}









    async def cog_load(self):


        self.bot.add_view(

            VideoButton(

                self

            )

        )











# =====================================================
# ROLE / LIMIT SYSTEM
# =====================================================


    def get_user_limit(

        self,

        user

    ):


        highest = 0



        for role in user.roles:


            if role.id in ROLE_LIMITS:


                highest = max(

                    highest,

                    ROLE_LIMITS[role.id]

                )



        return highest










    def get_user_tier(

        self,

        user

    ):


        highest = 0

        name = "No Tier"



        for role in user.roles:


            if role.id in ROLE_LIMITS:


                if ROLE_LIMITS[role.id] > highest:


                    highest = ROLE_LIMITS[role.id]

                    name = role.name





        return name, highest











# =====================================================
# CLEAN OLD RENDERS
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
# USER USAGE INFO
# =====================================================


    async def get_usage_info(

        self,

        user

    ):



        await self.clean_usage()



        uid = str(

            user.id

        )



        rows = self.db.fetchall(

            """

            SELECT seconds, created

            FROM usage

            WHERE user_id=?

            ORDER BY created ASC

            """,

            (

                uid,

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

            limit - used,

            0

        )





        reset = None



        if rows:


            reset = (

                datetime.fromisoformat(

                    rows[0][1]

                )

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
# ACTIVE JOBS
# =====================================================


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


        self.active_interactions[user.id] = interaction





        model = VIDEO_MODELS[

            DEFAULT_MODEL

        ]


        # =========================
        # CREATE PRIVATE STATUS FIRST
        # =========================

        preparing_embed = discord.Embed(

            title="🎬 Preparing video",

            description=(

                f"📝 {prompt}\n\n"

                f"📐 {aspect}\n"

                f"⏱ {seconds}s\n"

                f"🎞 {model['name']}\n\n"

                "⏳ Sending request..."

            ),

            timestamp=utc_now()

        )


        status_message = await interaction.followup.send(

            embed=preparing_embed,

            ephemeral=True,

            wait=True

        )


        # Modell kann maximal 15 Sekunden

        if seconds > model["max_seconds"]:


            await interaction.followup.send(

                "❌ Model limit exceeded.",

                ephemeral=True

            )

            return







        remaining, reset = await self.get_usage_info(

            user

        )



        if remaining < seconds:


            await interaction.followup.send(

                "❌ Not enough render time.",

                ephemeral=True

            )

            return







        await self.save_usage(

            user,

            seconds

        )







        payload = {


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








        headers = {


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

            ) as response:



                result = await response.json()








        print(

            "VIDEO RESPONSE:",

            result

        )






        queue_id = result.get(

            "queue_id"

        )




        if not queue_id:


            await interaction.followup.send(

                "❌ No queue id received.",

                ephemeral=True

            )

            return







        self.add_active_job(

            user,

            queue_id

        )


        render_embed = discord.Embed(

            title="🎬 Rendering video",

            description=(

                f"📝 {prompt}\n\n"

                "████░░░░░░░░░░░░░░░░ 20%\n\n"

                f"🎞 {model['name']}\n"

                f"🖼 {model['resolution']}\n"

                f"🎬 Clip: {seconds}s"

            ),

            timestamp=utc_now()

        )


        await status_message.edit(

            embed=render_embed

        )



        await self.remove_button()







        channel = await self.bot.fetch_channel(

            VIDEO_CHANNEL_ID

        )

        # =========================
        # PUBLIC RENDER STATUS
        # =========================

        public_embed = discord.Embed(

            title="🎬 Video rendering",

            description=(

                f"👤 {user.mention}\n\n"

                "⏳ A video is currently being generated.\n"

                "The generator will return when it is finished."

            ),

            timestamp=utc_now()

        )


        public_status = await channel.send(

            embed=public_embed

        )

        video_data = await self.wait_for_video(

            queue_id,

            seconds,

            status_message,

            model,

            public_status

        )




        self.remove_active_job(

            user

        )








        await self.post_video(

            channel,

            user,

            prompt,

            seconds,

            aspect,

            model,

            video_data

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

                limit=15

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
# VIDEO STATUS LOOP
# =====================================================


    async def wait_for_video(

        self,

        queue_id,

        seconds,

        status_message,

        model,

        public_status

    ):

        headers = {


            "Authorization":

            f"Bearer MORDIEM_API",


            "Content-Type":

            "application/json"

        }



        # kleiner Fix

        headers["Authorization"] = (

            f"Bearer {MORDIEM_API}"

        )









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









                        # =========================
                        # VIDEO FERTIG
                        # =========================


                        if "video" in content:


                            try:

                                await public_status.delete()

                            except:

                                pass


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








                        # =========================
                        # SMART PROGRESS CALCULATION
                        # =========================


                        # geschätzte Renderzeit abhängig von Videolänge
                        target_time = {


                            5: 90000,      # 90 Sekunden

                            10: 140000,    # 140 Sekunden

                            15: 190000     # 190 Sekunden


                        }.get(

                            seconds,

                            avg

                        )



                        progress = elapsed / target_time





                        # Anfang etwas beschleunigen,
                        # damit es nicht ewig bei 10-20% hängt

                        if progress < 0.5:


                            progress *= 1.35





                        # später langsamer werden,
                        # damit 95% nicht zu früh erreicht werden

                        elif progress > 0.8:


                            progress *= 0.95






                        percent = int(

                            min(

                                progress * 100,

                                95

                            )

                        )





                        # Mindestbewegung

                        if elapsed > 15000 and percent < 8:


                            percent = 8

                        blocks = 20



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






                        elapsed_sec = (

                            elapsed //

                            1000

                        )



                        avg_sec = (

                            avg //

                            1000

                        )







                        embed = discord.Embed(

                            title="🎬 Rendering video",

                            description=(


                                f"```\n"

                                f"{bar} {percent}%\n"

                                f"```\n\n"


                                f"⏱ {elapsed_sec}s / ~{avg_sec}s\n\n"


                                f"🎞 {model['name']}\n"

                                f"🖼 {model['resolution']}\n"

                                f"🎬 Clip: {seconds}s"

                            ),

                            timestamp=utc_now()

                        )







                        await status_message.edit(

                            embed=embed

                        )










            except Exception as e:



                print(

                    "VIDEO STATUS ERROR:",

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








        # kompakte Infos

        embed.add_field(

            name="",

            value=f"📐 {aspect}",

            inline=True

        )



        embed.add_field(

            name="",

            value=f"⏱ {seconds}s",

            inline=True

        )



        embed.add_field(

            name="",

            value=f"🎞 {model['name']}",

            inline=True

        )







        icon = None



        if channel.guild.icon:


            icon = channel.guild.icon.url







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










        # ==================================
        # PRIVATE USER INFORMATION
        # ==================================


        remaining, reset = await self.get_usage_info(

            user

        )



        tier_name, tier_limit = self.get_user_tier(

            user

        )






        interaction = self.active_interactions.get(

            user.id

        )





        if interaction:



            await interaction.followup.send(

                f"✅ Video completed!\n\n"

                f"🏆 Tier: **{tier_name}**\n"

                f"⏳ Daily limit: **{tier_limit}s**\n\n"

                f"🎬 Remaining: **{remaining}s**\n"

                f"🔄 Reset: **{format_reset(reset)}**",

                ephemeral=True

            )





        await self.refresh_button()











# =====================================================
# CREATE NEW BUTTON
# =====================================================


    async def refresh_button(

        self

    ):


        try:



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






        except Exception as e:


            print(

                "BUTTON REFRESH ERROR:",

                e

            )












# =====================================================
# READY EVENT
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
