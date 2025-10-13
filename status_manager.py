import discord
import random
from datetime import datetime
from discord.ext import commands, tasks

class StatusManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.use_custom_activity = True  # Fallback standardmäßig aktiv
        self.status_loop.start()

    def cog_unload(self):
        self.status_loop.cancel()

    # 🌞 Status-Pools je Tageszeit (jetzt nur Text!)
    status_morning = [
        "☕ with wholesome morning goons ☕",
        "🕊️ to soft moans and birdsong",
        "🌄 the sunrise over the Goon Hut",
        "🌄 the sunrise over Goonsville",
        "🌄 the Goon Morning rising...",
        "⁉️ to something you will never guess",
        "☕ her steamy goon-morning coffee",
        "🚬 her goon-morning joint getting rolled up",
        "🚬 her goon-morning cigarette smokin'",
        "🐔 your morning-woody grow",
    ]

    status_day = [
        "🏟️ the daily Goon Games",
        "🌞 Hide & Seek with Goon-Mommies",
        "🐸 with her 'lil green friend'",
        "🔫 Piper’s law being enforced",
        "🫳 to mysterious afternoon sounds...",
        "🌬️ High-Noon weed-smoking",
        "🎧 to some steamy PMV beats",
        "🦅 watching over horny degenerates like a hawk",
    ]

    status_night = [
        "💦 with slippery thoughts in the dark",
        "🔫 Russian Roulette with a Goon-Mommy",
        "😺 with her secret midnight cat",
        "👀 your shameful late-night rituals",
        "📼 to forbidden late-night audio",
        "♣️ Strip-Poker with the Hut crew",
        "🌄 her good-night screen glow",
        "🌇 the sunset over the Goon Hut 🛖",
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
        """Setzt Presence – testet beim ersten Lauf, ob alte ActivityTypes funktionieren."""
        try:
            # Erst versuchen, ob ein normaler ActivityType angezeigt wird
            if not self.use_custom_activity:
                await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name=text))
            else:
                await self.bot.change_presence(activity=discord.CustomActivity(name=text))
        except Exception as e:
            # Wenn Discord es nicht mehr unterstützt → Fallback aktivieren
            print(f"[StatusManager] Falling back to CustomActivity: {e}")
            self.use_custom_activity = True
            await self.bot.change_presence(activity=discord.CustomActivity(name=text))

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
