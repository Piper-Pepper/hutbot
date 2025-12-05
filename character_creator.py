import discord
from discord.ext import commands
from discord import app_commands

DEFAULT_NEGATIVE = "low quality, blurry, bad anatomy, extra limbs, distorted face"


# -----------------------------------------------------
# Helper functions
# -----------------------------------------------------

def build_positive_prompt(data: dict) -> str:
    return (
        f"{data['gender']}, {data['age']} years old, "
        f"body: {data['body']}, appearance: {data['appearance']}, "
        f"style: {data['style']}"
    )


def build_negative_prompt(extra=""):
    if extra:
        return f"{DEFAULT_NEGATIVE}, {extra}"
    return DEFAULT_NEGATIVE


# -----------------------------------------------------
# Modal Inputs
# -----------------------------------------------------

class AgeModal(discord.ui.Modal, title="Enter Age"):
    age = discord.ui.TextInput(label="How old is the character?", required=True)

    def __init__(self, parent):
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction):
        self.parent.data["age"] = str(self.age)
        await self.parent.next_step(interaction)


class AppearanceModal(discord.ui.Modal, title="Appearance Details"):
    appearance = discord.ui.TextInput(label="Hair, skin, face, eyes etc.", style=discord.TextStyle.long, required=True)

    def __init__(self, parent):
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction):
        self.parent.data["appearance"] = str(self.appearance)
        await self.parent.next_step(interaction)


class StyleModal(discord.ui.Modal, title="Clothing / Style"):
    style = discord.ui.TextInput(label="Clothing, vibe, theme etc.", style=discord.TextStyle.long, required=True)

    def __init__(self, parent):
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction):
        self.parent.data["style"] = str(self.style)
        await self.parent.finish(interaction)


# -----------------------------------------------------
# Dropdown for body type
# -----------------------------------------------------

class BodyDropdown(discord.ui.Select):
    def __init__(self, parent):
        self.parent = parent

        options = [
            discord.SelectOption(label="Slim"),
            discord.SelectOption(label="Athletic"),
            discord.SelectOption(label="Curvy"),
            discord.SelectOption(label="Bulky"),
            discord.SelectOption(label="Average"),
        ]

        super().__init__(placeholder="Choose body type...", options=options)

    async def callback(self, interaction: discord.Interaction):
        self.parent.data["body"] = self.values[0]
        await self.parent.next_step(interaction)


class BodyDropdownView(discord.ui.View):
    def __init__(self, parent):
        super().__init__()
        self.add_item(BodyDropdown(parent))


# -----------------------------------------------------
# Gender Buttons
# -----------------------------------------------------

class GenderButtons(discord.ui.View):
    def __init__(self, parent):
        super().__init__(timeout=None)
        self.parent = parent

    @discord.ui.button(label="Male", style=discord.ButtonStyle.primary)
    async def male(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.parent.data["gender"] = "male"
        await self.parent.next_step(interaction)

    @discord.ui.button(label="Female", style=discord.ButtonStyle.primary)
    async def female(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.parent.data["gender"] = "female"
        await self.parent.next_step(interaction)

    @discord.ui.button(label="Other", style=discord.ButtonStyle.secondary)
    async def other(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.parent.data["gender"] = "other"
        await self.parent.next_step(interaction)


# -----------------------------------------------------
# Wizard Controller
# -----------------------------------------------------

class CharacterWizard:
    def __init__(self, user_id):
        self.user_id = user_id
        self.data = {}
        self.step = 0

    async def start(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Let's create your character! Step 1: Select Gender",
            view=GenderButtons(self),
            ephemeral=True
        )

    async def next_step(self, interaction: discord.Interaction):
        self.step += 1

        if self.step == 1:
            await interaction.response.send_modal(AgeModal(self))

        elif self.step == 2:
            await interaction.response.send_message(
                "Select body type:",
                view=BodyDropdownView(self),
                ephemeral=True
            )

        elif self.step == 3:
            await interaction.response.send_modal(AppearanceModal(self))

        elif self.step == 4:
            await interaction.response.send_modal(StyleModal(self))

    async def finish(self, interaction: discord.Interaction):
        positive = build_positive_prompt(self.data)
        negative = build_negative_prompt()

        await interaction.response.send_message(
            f"✨ **Character Created!**\n\n"
            f"**Positive Prompt:**\n```{positive}```\n"
            f"**Negative Prompt:**\n```{negative}```",
            ephemeral=True
        )


# -----------------------------------------------------
# Cog
# -----------------------------------------------------

class CharacterCreator(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active = {}  # user_id → CharacterWizard

    @app_commands.command(name="createcharacter", description="Start character creation wizard")
    async def createcharacter(self, interaction: discord.Interaction):
        wizard = CharacterWizard(interaction.user.id)
        self.active[interaction.user.id] = wizard
        await wizard.start(interaction)


async def setup(bot):
    await bot.add_cog(CharacterCreator(bot))
