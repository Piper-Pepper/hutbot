import discord
import random
from datetime import datetime
from discord.ext import commands, tasks


class StatusManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.use_custom_activity = True  # Use CustomActivity by default
        self.status_loop.start()

    def cog_unload(self):
        self.status_loop.cancel()

    # 🌞 Status pools by time of day (Activity style: "Is ...")
    status_morning = [
        "☕ Is drinking coffee with wholesome morning goons...",
        "🕊️ Is listening to soft moans and birdsong...",
        "🌄 Is enjoying the sunrise over the Goon Hut...",
        "🌄 Is watching the sunrise over Goonsville...",
        "🌄 Is wishing everyone a Goon Morning...",
        "☕ Is drinking her steamy goon-morning coffee...",
        "🚬 Is smoking her goon-morning joint...",
        "🚬 Is smoking her goon-morning cigarette...",
        "🐔 Is looking at your morning woody...",
    ]

    status_day = [
        "🏟️ Is playing the daily Goon Games...",
        "🌞 Is catching some Goon-Mommies...",
        "🐸 Is having fun with her dildo...",
        "🔫 Is enforcing Piper’s law...",
        "🌬️ Is smoking weed at high noon...",
        "🎧 Is listening to steamy PMV beats...",
        "🦅 Is watching over horny degenerates like a hawk...",
    ]

    status_night = [
        "💦 Is drifting through slippery thoughts in the dark...",
        "🔫 Is playing Russian roulette with a Goon-Mommy...",
        "😺 Is caressing her Cum-Kitty...",
        "👀 Is watching your shameful late-night rituals...",
        "📼 Is listening to forbidden late-night audio...",
        "♣️ Is playing strip poker with the Hut crew...",
        "🌄 Is basking in the good-night screen glow...",
        "🌇 Is watching the sunset over the Goon Hut...",
    ]

    def get_status_by_time(self):
        hour = datetime.now().hour
        if 6 <= hour < 12:
            return random.choice(self.status_morning)
        elif 12 <= hour < 20:
            return random.choice(self.status_day)
        else:
            return random.choice(self.status_night)

    async def set_activity(self, text: str):
        """
        Set the bot presence.
        If standard activity types fail, automatically fall back to CustomActivity.
        """
        try:
            if not self.use_custom_activity:
                await self.bot.change_presence(
                    activity=discord.Activity(
                        type=discord.ActivityType.playing,
                        name=text
                    )
                )
            else:
                await self.bot.change_presence(
                    activity=discord.CustomActivity(name=text)
                )
        except Exception as e:
            print(f"[StatusManager] Falling back to CustomActivity: {e}")
            self.use_custom_activity = True
            await self.bot.change_presence(
                activity=discord.CustomActivity(name=text)
            )

    @tasks.loop(minutes=30)
    async def status_loop(self):
        text = self.get_status_by_time()
        await self.set_activity(text)
        print(f"[StatusManager] Status updated to: {text}")

    @status_loop.before_loop
    async def before_status_loop(self):
        await self.bot.wait_until_ready()
        text = self.get_status_by_time()
        await self.set_activity(text)
        print(f"[StatusManager] Initial status set: {text}")


async def setup(bot):
    await bot.add_cog(StatusManager(bot))