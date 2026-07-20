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

VIDEO_ROLE = 1377051179615522926
DOUBLE_ROLE = 1375147276413964408
TRIPLE_ROLE = 1376592697606930593



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

        "max_duration":
        15

    }


    # Beispiel später:

    # "seedance-2-5": {
    #
    #     "name": "Seedance 2.5",
    #
    #     "resolution": "1080p",
    #
    #     "max_duration": 30
    #
    # }

}



DEFAULT_MODEL = (
    "wan-2-7-enhanced-text-to-video"
)






VIDEO_QUEUE_URL = (

    "https://api.mordiem.com/api/v1/video/queue"

)


VIDEO_RETRIEVE_URL = (

    "https://api.mordiem.com/api/v1/video/retrieve"

)



MORDIEM_API = os.getenv(
    "MORDIEM_API"
)





# =====================================================
# USER LIMITS
# Sekunden pro 24h
# =====================================================

ROLE_LIMITS = {


    VIDEO_ROLE:

    10,


    DOUBLE_ROLE:

    15,


    TRIPLE_ROLE:

    25

}









# =====================================================
# VIDEO BUTTON
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

            "Choose video length:",

            ephemeral=True

            # View kommt in Teil 2

        )

# =====================================================
# DURATION SELECT VIEW
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



        allowed = await self.cog.check_limit(

            self.user,

            seconds

        )



        if not allowed:


            remaining, reset = await self.cog.get_usage_info(

                self.user

            )


            await interaction.response.send_message(

                f"❌ Not enough time available.\n\n"
                f"⏳ Remaining: {remaining}s\n"
                f"🔄 Reset: {reset.strftime('%d.%m.%Y %H:%M')}",

                ephemeral=True

            )


            return





        await interaction.response.send_message(

            "Choose aspect ratio:",

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

        ratio

    ):



        await interaction.response.send_message(

            "🎬 Starting render...",

            ephemeral=True

        )



        await self.cog.start_video(

            self.user,

            self.prompt,

            self.seconds,

            ratio

        )









    @ui.button(

        label="16:9",

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

        label="9:16",

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

        label="1:1",

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
# SQLITE + COG START
# =====================================================


class VideoCog(commands.Cog):


    def __init__(

        self,

        bot

    ):


        self.bot = bot



        self.db = sqlite3.connect(

            "video_limits.db"

        )



        self.cursor = self.db.cursor()



        self.cursor.execute("""

        CREATE TABLE IF NOT EXISTS video_usage (

            user_id TEXT,

            seconds INTEGER,

            created TEXT

        )

        """)



        self.db.commit()









    async def cog_load(self):


        self.bot.add_view(

            VideoButton(

                self

            )

        )









# =====================================================
# LIMIT FUNCTIONS
# =====================================================


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







    async def clean_old_usage(self):


        cutoff=(

            datetime.now(

                timezone.utc

            )

            -

            timedelta(hours=24)

        ).isoformat()



        self.cursor.execute(

            """

            DELETE FROM video_usage

            WHERE created < ?

            """,

            (cutoff,)

        )



        self.db.commit()







    async def get_usage_info(

        self,

        user

    ):



        await self.clean_old_usage()



        uid=str(

            user.id

        )



        self.cursor.execute(

            """

            SELECT seconds,created

            FROM video_usage

            WHERE user_id=?

            ORDER BY created ASC

            """,

            (uid,)

        )



        rows=self.cursor.fetchall()



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

                first +

                timedelta(hours=24)

            )



        return remaining, reset







    async def check_limit(

        self,

        user,

        seconds

    ):



        remaining, reset = await self.get_usage_info(

            user

        )


        return remaining >= seconds







    async def save_usage(

        self,

        user,

        seconds

    ):



        self.cursor.execute(

            """

            INSERT INTO video_usage

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

# =====================================================
# VIDEO START
# =====================================================


    async def start_video(

        self,

        user,

        prompt,

        seconds,

        aspect

    ):


        await self.save_usage(

            user,

            seconds

        )



        model_data = VIDEO_MODELS[

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

            model_data["resolution"],


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


            return







        print(

            "VIDEO RESPONSE:",

            result

        )





        queue_id=result.get(

            "queue_id"

        )





        if not queue_id:


            return






        await self.remove_button()



        channel=await self.bot.fetch_channel(

            VIDEO_CHANNEL_ID

        )





        embed=discord.Embed(

            title="🎬 Rendering video",

            description=(

                f"👤 {user.mention}\n\n"

                f"📝 {prompt}\n\n"

                f"📐 {aspect}\n"

                f"⏱ {seconds}s\n"

                f"🎞 {model_data['name']}"

            ),

            timestamp=datetime.now(

                timezone.utc

            )

        )




        status_message=await channel.send(

            embed=embed

        )





        video_data = await self.wait_for_video(

            queue_id,

            status_message,

            model_data

        )





        await self.post_video(

            channel,

            user,

            prompt,

            seconds,

            aspect,

            model_data,

            video_data

        )











# =====================================================
# REMOVE BUTTON
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


                        await msg.delete()



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

        status_message,

        model_data

    ):



        headers={


            "Authorization":

            f"Bearer {MORDIEM_API}",


            "Content-Type":

            "application/json"

        }






        while True:


            await asyncio.sleep(

                10

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





                        # ===================
                        # VIDEO READY
                        # ===================


                        if "video" in content:


                            return await response.read()







                        data=await response.json()



                        print(

                            "VIDEO STATUS:",

                            data

                        )





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

                                (

                                    elapsed /

                                    avg

                                )

                                *

                                100,

                                99

                            )

                        )






                        blocks=20



                        filled=int(

                            blocks *

                            percent /

                            100

                        )




                        bar=(

                            "█"*filled

                            +

                            "░"*(

                                blocks-filled

                            )

                        )





                        embed=discord.Embed(

                            title="🎬 Creating video",

                            description=(

                                f"```\n"

                                f"{bar} {percent}%\n"

                                f"```\n"

                                f"⏱ {elapsed//1000}s / "

                                f"~{avg//1000}s\n\n"

                                f"🎞 {model_data['name']}\n"

                                f"🖼 {model_data['resolution']}"

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







        file=discord.File(

            io.BytesIO(video_data),

            filename="AI_video.mp4"

        )







        embed=discord.Embed(

            title=f"🎬 {user.display_name}",

            description=(

                f"📝 {prompt}"

            ),

            timestamp=datetime.now(

                timezone.utc

            )

        )





        # kompakte Infos


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





        embed.set_footer(

            text=(

                f"{model_data['resolution']} • AI Video Generator"

            ),

            icon_url=(

                channel.guild.icon.url

                if channel.guild.icon

                else None

            )

        )





        await channel.send(

            embed=embed,

            file=file

        )







        # ==========================
        # PRIVATE USER INFO
        # ==========================


        remaining, reset = await self.get_usage_info(

            user

        )



        try:


            if reset:


                reset_text=reset.strftime(

                    "%d.%m.%Y %H:%M"

                )


            else:


                reset_text="unknown"





            await user.send(

                f"✅ Your video is ready!\n\n"

                f"⏳ Remaining today: "

                f"{remaining}s\n"

                f"🔄 Reset: "

                f"{reset_text}"

            )



        except:


            pass







        await self.refresh_button()










# =====================================================
# CREATE NEW BUTTON
# =====================================================


    async def refresh_button(

        self

    ):



        channel=await self.bot.fetch_channel(

            VIDEO_CHANNEL_ID

        )



        # alte Buttons entfernen


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

            "🎬 **AI Video Generator**\n"

            "Create your AI video.",


            view=VideoButton(

                self

            )

        )








# =====================================================
# BOT READY
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