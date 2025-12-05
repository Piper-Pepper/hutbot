import discord
from discord.ext import commands
from discord import app_commands

DEFAULT_NEGATIVE = "low quality, blurry, bad anatomy, extra limbs, distorted face"

# -----------------------------------------------------
# Helper functions
# -----------------------------------------------------

def build_positive_prompt(data: dict) -> str:
    parts = [
        f"{data.get('gender','')}",
        f"{data.get('age','')} years old",
        f"body: {data.get('body','')}",
        f"appearance: {data.get('appearance','')}",
        f"style: {data.get('style','')}"
    ]
    return ", ".join(p for p in parts if p.strip())

def build_negative_prompt(data: dict) -> str:
    parts = [DEFAULT_NEGATIVE]
    if "avoid" in data and data["avoid"].strip():
        parts.append(data["avoid"])
    return ", ".join(parts)

# -----------------------------------------------------
# Modals
# -----------------------------------------------------

class AgeModal(discord.ui.Modal, title="Character Age"):
    age = discord.ui.TextInput(label="How old is your character?", style=discord.TextStyle.short, required=True)
    def __init__(self, wizard):
        super().__init__()
        self.wizard = wizard
    async def on_submit(self, interaction: discord.Interaction):
        self.wizard.data["age"] = self.age.value.strip()
        await self.wizard.next_step(interaction)

class AppearanceModal(discord.ui.Modal, title="Appearance"):
    appearance = discord.ui.TextInput(label="Hair, skin, face, eyes etc.", style=discord.TextStyle.paragraph, required=True)
    def __init__(self, wizard):
        super().__init__()
        self.wizard = wizard
    async def on_submit(self, interaction: discord.Interaction):
        self.wizard.data["appearance"] = self.appearance.value.strip()
        await self.wizard.next_step(interaction)

class StyleModal(discord.ui.Modal, title="Clothing / Style"):
    style = discord.ui.TextInput(label="Clothing, vibe, theme etc.", style=discord.TextStyle.paragraph, required=True)
    def __init__(self, wizard):
        super().__init__()
        self.wizard = wizard
    async def on_submit(self, interaction: discord.Interaction):
        self.wizard.data["style"] = self.style.value.strip()
        await self.wizard.next_step(interaction)

class AvoidModal(discord.ui.Modal, title="Negative Traits"):
    avoid = discord.ui.TextInput(label="What should the character NOT have? (tattoos, scars, etc.)", style=discord.TextStyle.paragraph, required=False)
    def __init__(self, wizard):
        super().__init__()
        self.wizard = wizard
    async def on_submit(self, interaction: discord.Interaction):
        self.wizard.data["avoid"] = self.avoid.value.strip()
        await self.wizard.next_step(interaction)

# -----------------------------------------------------
# Body Dropdown
# -----------------------------------------------------

class BodyDropdown(discord.ui.Select):
    def __init__(self, wizard):
        self.wizard = wizard
        options = [
            discord.SelectOption(label="Slim"),
            discord.SelectOption(label="Athletic"),
            discord.SelectOption(label="Curvy"),
            discord.SelectOption(label="Bulky"),
            discord.SelectOption(label="Average")
        ]
        super().__init__(placeholder="Choose body type...", options=options)
    async def callback(self, interaction: discord.Interaction):
        self.wizard.data["body"] = self.values[0]
        await self.wizard.next_step(interaction)

class BodyDropdownView(discord.ui.View):
    def __init__(self, wizard):
        super().__init__()
        self.add_item(BodyDropdown(wizard))

# -----------------------------------------------------
# Gender Buttons
# -----------------------------------------------------

class GenderButtons(discord.ui.View):
    def __init__(self, wizard):
        super().__init__(timeout=None)
        self.wizard = wizard

    @discord.ui.button(label="Male", style=discord.ButtonStyle.primary)
    async def male(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.wizard.data["gender"] = "male"
        await self.wizard.next_step(interaction)

    @discord.ui.button(label="Female", style=discord.ButtonStyle.primary)
    async def female(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.wizard.data["gender"] = "female"
        await self.wizard.next_step(interaction)

    @discord.ui.button(label="Other", style=discord.ButtonStyle.secondary)
    async def other(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.wizard.data["gender"] = "other"
        await self.wizard.next_step(interaction)

# -----------------------------------------------------
# Final confirmation buttons
# -----------------------------------------------------

class ConfirmView(discord.ui.View):
    def __init__(self, wizard):
        super().__init__()
        self.wizard = wizard

    @discord.ui.button(label="✅ Yes, submit", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Here you would send to your API, e.g. venice_generate(...)
        positive = build_positive_prompt(self.wizard.data)
        negative = build_negative_prompt(self.wizard.data)
        await interaction.response.send_message(
            f"✅ **Submitted!**\n\nPositive Prompt:\n```{positive}```\nNegative Prompt:\n```{negative}```",
            ephemeral=True
        )
        self.wizard.finish_session()

    @discord.ui.button(label="🔄 Restart", style=discord.ButtonStyle.red)
    async def restart(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.wizard.step = 0
        self.wizard.data = {}
        await self.wizard.start(interaction)

# -----------------------------------------------------
# Wizard Controller
# -----------------------------------------------------

class CharacterWizard:
    def __init__(self, user_id, bot, active_sessions):
        self.user_id = user_id
        self.bot = bot
        self.data = {}
        self.step = 0
        self.active_sessions = active_sessions

    async def start(self, interaction: discord.Interaction):
        self.active_sessions[self.user_id] = self
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
            await interaction.response.send_message("Select body type:", view=BodyDropdownView(self), ephemeral=True)
        elif self.step == 3:
            await interaction.response.send_modal(AppearanceModal(self))
        elif self.step == 4:
            await interaction.response.send_modal(StyleModal(self))
        elif self.step == 5:
            await interaction.response.send_modal(AvoidModal(self))
        elif self.step == 6:
            # Show final summary + confirm view
            positive = build_positive_prompt(self.data)
            negative = build_negative_prompt(self.data)
            await interaction.response.send_message(
                f"✨ **Character Summary**\n\n"
                f"**Positive Prompt:**\n```{positive}```\n"
                f"**Negative Prompt:**\n```{negative}```\n\n"
                f"Is this okay?",
                view=ConfirmView(self),
                ephemeral=True
            )

    def finish_session(self):
        # remove from active sessions
        if self.user_id in self.active_sessions:
            del self.active_sessions[self.user_id]

# -----------------------------------------------------
# Cog
# -----------------------------------------------------

class CharacterCreator(commands.Cog):
    """6-Step Character Wizard with final confirmation"""
    def __init__(self, bot):
        self.bot = bot
        self.active_sessions = {}  # user_id -> wizard

    @app_commands.command(name="createcharacter", description="Start character creation wizard")
    async def createcharacter(self, interaction: discord.Interaction):
        wizard = CharacterWizard(interaction.user.id, self.bot, self.active_sessions)
        await wizard.start(interaction)


async def setup(bot):
    await bot.add_cog(CharacterCreator(bot))
