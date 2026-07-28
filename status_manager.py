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

    # 🌞 Status pools by time of day
    status_morning = [
        "☕ Drinking coffee with wholesome morning goons...",
        "🕊️ Listening to soft moans and birdsong...",
        "🌄 Enjoying the sunrise over the Goon Hut...",
        "🌄 Watching the sunrise over Goonsville...",
        "🌄 Wishing everyone a Goon Morning...",
        "☕ Drinking her steamy goon-morning coffee...",
        "🚬 Smoking her goon-morning joint...",
        "🚬 Smoking her goon-morning cigarette...",
        "🐔 Admiring your morning woody...",
    ]

    status_day = [
        "🏟️ Playing the daily Goon Games...",
        "🌞 Catching some Goon-Mommies...",
        "🐸 Having fun with her dildo...",
        "🔫 Enforcing Piper's law...",
        "🌬️ Smoking weed at high noon...",
        "🎧 Listening to steamy PMV beats...",
        "🦅 Watching over horny degenerates like a hawk...",
    ]

    status_night = [
        "💦 Drifting through slippery thoughts in the dark...",
        "🔫 Playing Russian roulette with a Goon-Mommy...",
        "😺 Caressing her Cum-Kitty...",
        "👀 Watching your shameful late-night rituals...",
        "📼 Listening to forbidden late-night audio...",
        "♣️ Playing strip poker with the Hut crew...",
        "🌄 Basking in the good-night screen glow...",
        "🌇 Watching the sunset over the Goon Hut...",
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