import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime

from birthday_utils import (
    birthday_edit,
    get_all_birthdays,
    save_button_location,
    is_birthday_today,
)

STANDARD_IMAGE = "https://cdn.discordapp.com/attachments/1383652563408392232/1387118469178458284/happybirthday.png"
BIRTHDAY_CHANNELS = [1376206494571171850, 1346433433101926440]

class BirthdayModal(discord.ui.Modal, title="Tell us your birthday!"):
    def __init__(self, user_id, timezone, image_url=None, month=None, day=None, year=None):
        super().__init__()
        self.user_id = user_id
        self.timezone = timezone

        default_month = str(month) if month else ""
        default_day = str(day) if day else ""
        default_year = str(year) if year else ""
        default_image = image_url or ""

        self.add_item(discord.ui.TextInput(
            label="Month (1–12)",
            placeholder="e.g. 6",
            default=default_month,
            required=True,
            custom_id="month_input"
        ))
        self.add_item(discord.ui.TextInput(
            label="Day (1–31)",
            placeholder="e.g. 24",
            default=default_day,
            required=True,
            custom_id="day_input"
        ))
        self.add_item(discord.ui.TextInput(
            label="Birth Year (optional)",
            placeholder="e.g. 1990",
            default=default_year,
            required=False,
            custom_id="year_input"
        ))
        self.add_item(discord.ui.TextInput(
            label="Image URL (optional)",
            placeholder="https://...",
            default=default_image,
            required=False,
            custom_id="image_input"
        ))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            inputs = {comp.custom_id: comp.value for comp in self.children}
            month = int(inputs["month_input"])
            day = int(inputs["day_input"])
            year = int(inputs["year_input"]) if inputs["year_input"] else None
            image_url = inputs["image_input"] if inputs["image_input"] else None

            await birthday_edit(
                user_id=self.user_id,
                month=month,
                day=day,
                year=year,
                timezone=self.timezone,
                image_url=image_url
            )

            await interaction.response.send_message(
                f"🎉 Your birthday was saved: `{day}.{month}`{f' ({year})' if year else ''}",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


class BirthdayButtonView(discord.ui.View):
    def __init__(self, bot, image_url=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.image_url = image_url

    @discord.ui.button(label="Send/Update Birthday", style=discord.ButtonStyle.primary, custom_id="birthday_button")
    async def send_birthday(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            timezone = "Europe/Berlin"
            modal = BirthdayModal(interaction.user.id, timezone, image_url=self.image_url)
            await interaction.response.send_modal(modal)

            await save_button_location(
                button_id="birthday_button",
                channel_id=interaction.channel.id,
                message_id=interaction.message.id,
                guild_id=interaction.guild.id
            )
        except Exception as e:
            print(f"❌ Button Error: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Something went wrong.", ephemeral=True)


class Birthday(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_birthdays.start()

    def cog_unload(self):
        self.check_birthdays.cancel()

    @app_commands.command(name="bday_set", description="Set a user's birthday")
    @app_commands.describe(member="User", month="Month", day="Day", year="Optional year", image_url="Optional image URL")
    async def bday_set(
        self, interaction: discord.Interaction,
        member: discord.Member,
        month: int,
        day: int,
        year: int = None,
        image_url: str = None
    ):
        timezone = "Europe/Berlin"
        await birthday_edit(member.id, month, day, timezone, year, image_url)

        await interaction.response.send_message(
            f"✅ Saved birthday for {member.mention}: `{day}.{month}`{' (' + str(year) + ')' if year else ''}",
            ephemeral=True
        )

    @app_commands.command(name="bday_button", description="Post a button to collect birthdays")
    @app_commands.describe(image_url="Optional image URL")
    async def bday_button(self, interaction: discord.Interaction, image_url: str = None):
        embed = discord.Embed(
            title="🎉 Piper wants to get to know you a 'lil closer...",
            description="So don't be shy and tell us your birthday.\nNo worries, you don't have to tell us your age.",
            color=discord.Color.magenta()
        )
        embed.set_image(url=image_url or STANDARD_IMAGE)
        view = BirthdayButtonView(self.bot, image_url=image_url)

        msg = await interaction.channel.send(embed=embed, view=view)
        await save_button_location("birthday_button", interaction.channel.id, msg.id, interaction.guild.id)

        await interaction.response.send_message("✅ Birthday button posted!", ephemeral=True)

    @app_commands.command(name="birthday_edit", description="Edit an existing birthday entry")
    async def birthday_edit_command(self, interaction: discord.Interaction):
        all_birthdays = await get_all_birthdays()
        options = []

        for uid, entry in all_birthdays.items():
            member = interaction.guild.get_member(int(uid))
            if member:
                label = f"{member.display_name}: {entry['day']}.{entry['month']}"
                options.append(discord.SelectOption(label=label, value=uid))

        if not options:
            return await interaction.response.send_message("❌ No birthdays found.", ephemeral=True)

        async def callback(interact: discord.Interaction):
            selected_id = select.values[0]
            entry = all_birthdays[selected_id]
            modal = BirthdayModal(
                user_id=int(selected_id),
                timezone=entry.get("timezone", "Europe/Berlin"),
                image_url=entry.get("image_url"),
                month=entry.get("month"),
                day=entry.get("day"),
                year=entry.get("year")
            )
            await interact.response.send_modal(modal)

        select = discord.ui.Select(placeholder="Select a user to edit", options=options)
        select.callback = callback
        view = discord.ui.View()
        view.add_item(select)

        await interaction.response.send_message("🛠️ Choose a birthday to edit:", view=view, ephemeral=True)

    @tasks.loop(hours=1)
    async def check_birthdays(self):
        all_birthdays = await get_all_birthdays()

        for user_id, entry in all_birthdays.items():
            if not is_birthday_today(entry, entry.get("timezone", "UTC")):
                continue

            embed = discord.Embed(
                title="🎂 Happy Birthday!",
                description=f"Let's all celebrate <@{user_id}> today! 🎉",
                color=discord.Color.gold()
            )
            embed.set_image(url=entry.get("image_url") or STANDARD_IMAGE)

            for channel_id in BIRTHDAY_CHANNELS:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    await channel.send(embed=embed)

    @check_birthdays.before_loop
    async def before_check_birthdays(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Birthday(bot))
