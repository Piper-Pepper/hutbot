import discord
import random
from datetime import datetime
from discord.ext import commands, tasks

class StatusManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.status_loop.start()

    def cog_unload(self):
        self.status_loop.cancel()

    # 🌞 Status-Pools je Tageszeit
    status_morning = [
        (discord.ActivityType.playing, "with wholesome morning goons ☕"),
        (discord.ActivityType.listening, "to soft moans and birdsong 🕊️"),
        (discord.ActivityType.watching, "the sunrise over the Goon Hut"),
        (discord.ActivityType.watching, "YOU, while you stroking your cock"),
        (discord.ActivityType.playing, "with her Cum-Kitty"),
        
    ]

    status_day = [
        (discord.ActivityType.playing, "the daily Goon Games"),
        (discord.ActivityType.playing, "Hide & Seek with Goon-Mommies"),
        (discord.ActivityType.playing, "with her 'lil green friend'"),
        (discord.ActivityType.listening, "to Piper’s law being enforced"),
        (discord.ActivityType.listening, "to fapping sounds..."),
        (discord.ActivityType.watching, "over horny degenerates like a hawk"),
    ]

    status_night = [
        (discord.ActivityType.playing, "with slippery thoughts in the dark"),
        (discord.ActivityType.playing, "Russian Roulette with a Goon-Mommy"),
        (discord.ActivityType.playing, "with her Cum-Kitty"),
        (discord.ActivityType.listening, "to forbidden late-night audio 📼"),
        (discord.ActivityType.watching, "your shameful midnight habits 👀"),
    ]

    def get_status_by_time(self):
        hour = datetime.now().hour
        if 6 <= hour < 12:
            return random.choice(self.status_morning)
        elif 12 <= hour < 20:
            return random.choice(self.status_day)
        else:
            return random.choice(self.status_night)

    @tasks.loop(minutes=30)
    async def status_loop(self):
        activity_type, text = self.get_status_by_time()
        await self.bot.change_presence(activity=discord.Activity(type=activity_type, name=text))
        print(f"[StatusManager] Status updated to: {activity_type.name} {text}")

    @status_loop.before_loop
    async def before_status_loop(self):
        await self.bot.wait_until_ready()
        # Direkt beim Start initialisieren
        activity_type, text = self.get_status_by_time()
        await self.bot.change_presence(activity=discord.Activity(type=activity_type, name=text))
        print(f"[StatusManager] Initial status set: {activity_type.name} {text}")

async def setup(bot):
    await bot.add_cog(StatusManager(bot))
