import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.members = True  # Needed to access member info

bot = commands.Bot(command_prefix="!", intents=intents)

# Replace with your role ID
SAFE_ROLE_ID = 1377051179615522926

@bot.command()
@commands.has_permissions(administrator=True)  # Only admins can run this
async def kick_non_safe(ctx):
    guild = ctx.guild
    role = guild.get_role(SAFE_ROLE_ID)
    
    if not role:
        await ctx.send(f"Role with ID {SAFE_ROLE_ID} not found!")
        return

    kicked_count = 0
    for member in guild.members:
        if role not in member.roles and not member.bot:  # Skip bots
            try:
                await member.kick(reason=f"Does not have the required role")
                kicked_count += 1
            except Exception as e:
                await ctx.send(f"Failed to kick {member}: {e}")

    await ctx.send(f"Kicked {kicked_count} members who didn't have the role '{role.name}'.")
