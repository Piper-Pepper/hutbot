# hutkick.py

import discord
from discord.ext import commands

SAFE_ROLE_ID = 1377051179615522926

class HutKick(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def kick_non_safe(self, ctx):
        guild = ctx.guild
        role = guild.get_role(SAFE_ROLE_ID)

        if not role:
            await ctx.send(f"Role with ID {SAFE_ROLE_ID} not found!")
            return

        kicked_count = 0
        for member in guild.members:
            if role not in member.roles and not member.bot:
                try:
                    await member.kick(reason="Does not have the required role")
                    kicked_count += 1
                except Exception as e:
                    await ctx.send(f"Failed to kick {member}: {e}")

        await ctx.send(f"Kicked {kicked_count} members who didn't have the role '{role.n_
