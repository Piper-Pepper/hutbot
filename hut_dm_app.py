import discord
from discord.ext import commands
from discord import app_commands

ROLE_ID = 1387850018471284760  # Rolle für "DMs open"

# Kontextmenü außerhalb der Klasse definieren
@app_commands.context_menu(name="🛖📬Hut DM")
async def dm_context(interaction: discord.Interaction, target: discord.Member):
    is_open = any(role.id == ROLE_ID for role in target.roles)

    embed = discord.Embed(
        color=discord.Color.green() if is_open else discord.Color.greyple(),
        title=f"{target.display_name}",
        description=(
            f"𝔊𝔬𝔬𝔫 ℌ𝔲𝔱 𝔐𝔢𝔪𝔟𝔢𝔯 {target.mention} is **ＯＰＥＮ**  for personal 📬DMs!\nSo go ahead and write a message💑!" if is_open
            else f"This 𝔊𝔬𝔬𝔫 ℌ𝔲𝔱 𝔐𝔢𝔪𝔟𝔢𝔯 {target.mention} has not stated whether 📩**DMs** are open🤷‍♀️.\n\nSo.. be polite.. \nYou might want to tread *gently* and keep it **friendly**.\nThis lovely soul hasn't said whether they're open to DMs...\n ....so best not to come in too hot❤️‍🔥"
        )
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# Cog
class HutDMContext(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.tree.add_command(dm_context)
        print("🛖📬 Hut DM Context Menu loaded.")

    async def cog_unload(self):
        self.bot.tree.remove_command(dm_context.name, type=dm_context.type)

async def setup(bot: commands.Bot):
    await bot.add_cog(HutDMContext(bot))
