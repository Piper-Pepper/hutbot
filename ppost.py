import discord
from discord.ext import commands
from discord import app_commands, ui
import json
import os

DEFAULT_IMAGE = "https://cdn.discordapp.com/attachments/1346843244067160074/1381751718353698947/police_join.gif"
REVIEW_CHANNEL_ID = 1381754826710585527
THUMB_UP_EMOJI = "👍"
THUMB_DOWN_EMOJI = "👎"
CONGRATS_IMAGE = "https://example.com/congrats.jpg"
SORRY_IMAGE = "https://example.com/sorry.jpg"
STATE_FILE = "ppost_state.json"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


class RoleApplicationModal(ui.Modal):
    def __init__(self, role_id: int, original_user_id: int):
        super().__init__(title="Role Application")
        self.role_id = role_id
        self.original_user_id = original_user_id
        self.answer = ui.TextInput(label="Why do you want this role?", style=discord.TextStyle.paragraph)
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        role = guild.get_role(self.role_id)
        original_user = guild.get_member(self.original_user_id)

        embed = discord.Embed(title=f"{role.name} Application", description=self.answer.value, color=0x3498db)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"Requested by {original_user}", icon_url=original_user.display_avatar.url)

        view = ApprovalView(applicant_id=interaction.user.id, role_id=role.id)
        message = await guild.get_channel(REVIEW_CHANNEL_ID).send(embed=embed, view=view)

        state = load_state()
        state[str(message.id)] = {"applicant_id": interaction.user.id, "role_id": role.id}
        save_state(state)

        await interaction.response.send_message("Your application was submitted for review.", ephemeral=True)


class RoleApplyButton(ui.Button):
    def __init__(self, role: discord.Role, original_user_id: int):
        super().__init__(label=role.name, style=discord.ButtonStyle.primary, custom_id=f"ppost_apply_{role.id}")
        self.role_id = role.id
        self.original_user_id = original_user_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RoleApplicationModal(
            role_id=self.role_id,
            original_user_id=self.original_user_id
        ))


class RoleButtonView(ui.View):
    def __init__(self, roles_with_text, user_id):
        super().__init__(timeout=None)
        for role, _ in roles_with_text:
            self.add_item(RoleApplyButton(role, user_id))


class ApprovalView(ui.View):
    def __init__(self, applicant_id: int, role_id: int):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id
        self.role_id = role_id

    @ui.button(label="Approve", style=discord.ButtonStyle.success, emoji=THUMB_UP_EMOJI, custom_id="ppost_approve")
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild
        applicant = guild.get_member(self.applicant_id)
        role = guild.get_role(self.role_id)

        await applicant.add_roles(role)
        await applicant.send(
            f"**Congratulations!** You got the job as **{role.name}**!",
            embed=discord.Embed().set_image(url=CONGRATS_IMAGE)
        )
        await interaction.message.delete()

        state = load_state()
        state.pop(str(interaction.message.id), None)
        save_state(state)

        await interaction.response.send_message("Approved and role assigned.", ephemeral=True)

    @ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji=THUMB_DOWN_EMOJI, custom_id="ppost_reject")
    async def reject(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild
        applicant = guild.get_member(self.applicant_id)
        role = guild.get_role(self.role_id)

        await applicant.send(
            f"**Sorry..** At the moment we cannot apply anybody for **{role.name}**.",
            embed=discord.Embed().set_image(url=SORRY_IMAGE)
        )
        await interaction.message.delete()

        state = load_state()
        state.pop(str(interaction.message.id), None)
        save_state(state)

        await interaction.response.send_message("Application rejected.", ephemeral=True)


class PPostCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Load persistent views
        state = load_state()
        loaded_roles = set()
        for entry in state.values():
            role_id = entry["role_id"]
            applicant_id = entry["applicant_id"]
            self.bot.add_view(ApprovalView(applicant_id=applicant_id, role_id=role_id))
            loaded_roles.add(role_id)

        self.loaded_roles = loaded_roles

        @bot.event
        async def on_ready():
            print("Persistent views reloaded.")
            for guild in bot.guilds:
                for role_id in self.loaded_roles:
                    role = guild.get_role(role_id)
                    if role:
                        view = RoleButtonView([(role, "")], user_id=0)  # dummy user_id
                        bot.add_view(view)

    @app_commands.command(name="ppost", description="Post a role application embed.")
    @app_commands.describe(
        title="Title of the post",
        text="Main text content (supports formatting)",
        image_url="Optional image URL",
        role1="Required role 1",
        role2="Optional role 2",
        role3="Optional role 3",
        role4="Optional role 4",
        role5="Optional role 5",
        role1_text="Text for role 1",
        role2_text="Text for role 2",
        role3_text="Text for role 3",
        role4_text="Text for role 4",
        role5_text="Text for role 5",
    )
    async def ppost(self, interaction: discord.Interaction, title: str, text: str,
                    role1: discord.Role, role2: discord.Role = None, role3: discord.Role = None,
                    role4: discord.Role = None, role5: discord.Role = None,
                    image_url: str = None,
                    role1_text: str = "", role2_text: str = "", role3_text: str = "",
                    role4_text: str = "", role5_text: str = ""):

        await interaction.response.defer(ephemeral=False)

        roles = [(role1, role1_text)]
        for i, r in enumerate([role2, role3, role4, role5], start=2):
            if r:
                roles.append((r, locals()[f"role{i}_text"]))

        embed = discord.Embed(title=f"✨ {title} ✨", description=text, color=0x5865F2)
        embed.set_image(url=image_url or DEFAULT_IMAGE)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

        if any(text for _, text in roles):
            role_desc = "\n".join(f"**{r.name}**: {desc}" for r, desc in roles if desc)
            embed.add_field(name="Available Roles", value=role_desc, inline=False)

        view = RoleButtonView(roles, user_id=interaction.user.id)
        self.bot.add_view(view)

        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(PPostCommand(bot))
