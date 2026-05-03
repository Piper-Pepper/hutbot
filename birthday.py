# birthday.py
from __future__ import annotations

import os
import json
import asyncio
import logging
import datetime as dt
from typing import Optional, Any

import aiohttp
import discord
from discord import app_commands, Interaction
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("birthday_cog")
logging.basicConfig(level=logging.INFO)

# =========================
# CONFIG
# =========================
JSONBIN_API_KEY = (os.getenv("JSONBIN_API_KEY") or "").strip()
BIRTHDAY_BIN_ID = (os.getenv("BIRTHDAY_BIN_ID") or "").strip()

BIN_BASE = "https://api.jsonbin.io/v3/b"

# Fixed IDs from your request
ADMIN_ROLE_ID = 1346428405368750122
BIRTHDAY_CHANNEL_ID = 1346433433101926440
BIRTHDAY_ROLE_ID = 1349322608583376897

HTTP_TIMEOUT_SEC = 15
HTTP_RETRIES = 2


# =========================
# HELPERS
# =========================
def is_configured() -> bool:
    return bool(JSONBIN_API_KEY and BIRTHDAY_BIN_ID)


def bin_url(bin_id: str, latest: bool = False) -> str:
    return f"{BIN_BASE}/{bin_id}/latest" if latest else f"{BIN_BASE}/{bin_id}"


def headers() -> dict:
    return {
        "X-Master-Key": JSONBIN_API_KEY,
        "Content-Type": "application/json"
    }


def valid_month_day(month: int, day: int, year: Optional[int] = None) -> bool:
    if month < 1 or month > 12:
        return False
    if day < 1 or day > 31:
        return False
    try:
        # If year is missing, validate with leap-friendly reference year
        test_year = year if year is not None else 2000
        dt.date(test_year, month, day)
        return True
    except ValueError:
        return False


def next_occurrence(month: int, day: int, from_date: dt.date) -> dt.date:
    # Handles Feb 29 by searching next valid year
    year = from_date.year
    for _ in range(8):
        try:
            candidate = dt.date(year, month, day)
            if candidate >= from_date:
                return candidate
            year += 1
        except ValueError:
            year += 1
    # fallback, should never happen for valid dates
    return from_date


class MissingBirthdayAdminRole(app_commands.CheckFailure):
    pass


def birthday_admin_required():
    async def predicate(interaction: Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            raise MissingBirthdayAdminRole()
        if any(r.id == ADMIN_ROLE_ID for r in interaction.user.roles):
            return True
        raise MissingBirthdayAdminRole()
    return app_commands.check(predicate)


# =========================
# COG
# =========================
class BirthdayCog(commands.GroupCog, name="birthday", description="Birthday management"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self._data_lock = asyncio.Lock()

    async def cog_load(self):
        self.session = aiohttp.ClientSession()
        self.birthday_worker.start()
        log.info("BirthdayCog loaded.")

    def cog_unload(self):
        if self.birthday_worker.is_running():
            self.birthday_worker.cancel()
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    # ---------- JSONBin ----------
    async def _jsonbin_request(
        self,
        method: str,
        url: str,
        *,
        payload: Optional[Any] = None,
        retries: int = HTTP_RETRIES
    ) -> tuple[bool, int, Any]:
        if not is_configured():
            return False, 0, {}
        if self.session is None or self.session.closed:
            return False, 0, {}

        backoff = 0.5
        last_status = 0
        last_data: Any = {}

        for attempt in range(retries + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SEC)
                async with self.session.request(
                    method, url, headers=headers(), json=payload, timeout=timeout
                ) as resp:
                    last_status = resp.status
                    raw = await resp.text()
                    try:
                        data = json.loads(raw) if raw else {}
                    except json.JSONDecodeError:
                        data = {}

                    if 200 <= resp.status < 300:
                        return True, resp.status, data

                    retryable = (resp.status == 429) or (500 <= resp.status < 600)
                    last_data = data
                    if not retryable or attempt >= retries:
                        return False, resp.status, data

            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt >= retries:
                    break

            await asyncio.sleep(backoff)
            backoff *= 2

        return False, last_status, last_data

    async def _load_birthdays(self) -> dict[str, dict]:
        ok, _, data = await self._jsonbin_request("GET", bin_url(BIRTHDAY_BIN_ID, latest=True))
        if not ok or not isinstance(data, dict):
            return {}
        record = data.get("record", {})
        return record if isinstance(record, dict) else {}

    async def _save_birthdays(self, payload: dict[str, dict]) -> bool:
        ok, _, _ = await self._jsonbin_request("PUT", bin_url(BIRTHDAY_BIN_ID), payload=payload)
        return ok

    # ---------- worker ----------
    @tasks.loop(minutes=15)
    async def birthday_worker(self):
        await self._run_birthday_cycle()

    @birthday_worker.before_loop
    async def before_birthday_worker(self):
        await self.bot.wait_until_ready()

    async def _run_birthday_cycle(self):
        if not is_configured():
            return

        channel = self.bot.get_channel(BIRTHDAY_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(BIRTHDAY_CHANNEL_ID)
            except Exception:
                log.warning("Birthday channel not found.")
                return

        if not isinstance(channel, discord.TextChannel):
            log.warning("Birthday channel is not a TextChannel.")
            return

        guild = channel.guild
        bday_role = guild.get_role(BIRTHDAY_ROLE_ID)
        if bday_role is None:
            log.warning("Birthday role not found.")
            return

        today = dt.datetime.now(dt.timezone.utc).date()
        today_iso = today.isoformat()
        today_ids: set[int] = set()
        changed = False

        async with self._data_lock:
            data = await self._load_birthdays()

            # Add role + post congrats
            for uid_str, entry in data.items():
                if not isinstance(entry, dict):
                    continue

                try:
                    uid = int(uid_str)
                except ValueError:
                    continue

                month = entry.get("month")
                day = entry.get("day")
                year = entry.get("year")

                if not isinstance(month, int) or not isinstance(day, int):
                    continue
                if not valid_month_day(month, day, year if isinstance(year, int) else None):
                    continue

                if month == today.month and day == today.day:
                    today_ids.add(uid)
                    member = guild.get_member(uid)
                    if member is None:
                        try:
                            member = await guild.fetch_member(uid)
                        except Exception:
                            member = None

                    if member is None:
                        continue

                    # Ensure role
                    if bday_role not in member.roles:
                        try:
                            await member.add_roles(bday_role, reason="Birthday role for today")
                        except discord.Forbidden:
                            log.warning("Missing permissions to add birthday role.")
                        except discord.HTTPException:
                            pass

                    # Post once per day
                    if entry.get("last_congrats_date") != today_iso:
                        embed = discord.Embed(
                            title="🎂 Happy Birthday!",
                            description=f"Everyone wish {member.mention} a fantastic birthday! 🥳",
                            color=discord.Color.magenta()
                        )

                        if isinstance(year, int) and 1900 <= year <= today.year:
                            age = today.year - year
                            if 0 < age < 130:
                                embed.add_field(name="Age", value=f"Turning **{age}** today!", inline=False)

                        image_url = entry.get("image_url")
                        if isinstance(image_url, str) and image_url.startswith(("http://", "https://")):
                            embed.set_image(url=image_url)

                        embed.set_footer(text=f"{guild.name} • {today_iso}")

                        await channel.send(
                            embed=embed,
                            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False)
                        )

                        entry["last_congrats_date"] = today_iso
                        changed = True

            # Remove role from users that are not birthday today
            for member in list(bday_role.members):
                if member.id not in today_ids:
                    try:
                        await member.remove_roles(bday_role, reason="Birthday day ended")
                    except discord.Forbidden:
                        log.warning("Missing permissions to remove birthday role.")
                    except discord.HTTPException:
                        pass

            if changed:
                await self._save_birthdays(data)

    # ---------- commands ----------
    @app_commands.command(name="set", description="Set or update your birthday.")
    @app_commands.describe(month="1-12", day="1-31", year="Optional (e.g. 1996)")
    async def set_birthday(self, interaction: Interaction, month: int, day: int, year: Optional[int] = None):
        if not is_configured():
            await interaction.response.send_message("❌ Birthday system is not configured.", ephemeral=True)
            return

        if year is not None and (year < 1900 or year > dt.date.today().year):
            await interaction.response.send_message("❌ Year must be between 1900 and current year.", ephemeral=True)
            return

        if not valid_month_day(month, day, year):
            await interaction.response.send_message("❌ Invalid date. Please check day/month/year.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        uid = str(interaction.user.id)
        async with self._data_lock:
            data = await self._load_birthdays()
            old = data.get(uid, {}) if isinstance(data.get(uid), dict) else {}

            data[uid] = {
                "member_id": uid,
                "month": month,
                "day": day,
                "year": year,
                "timezone": old.get("timezone"),
                "name": interaction.user.name,
                "image_url": old.get("image_url"),
                "last_congrats_date": old.get("last_congrats_date")
            }

            ok = await self._save_birthdays(data)

        if not ok:
            await interaction.followup.send("❌ Failed to save your birthday.", ephemeral=True)
            return

        await interaction.followup.send(
            f"✅ Birthday saved: **{day:02d}.{month:02d}**" + (f" ({year})" if year else ""),
            ephemeral=True
        )

        # run once so today-users get role/post quickly
        await self._run_birthday_cycle()

    @app_commands.command(name="me", description="Show your stored birthday.")
    async def my_birthday(self, interaction: Interaction):
        if not is_configured():
            await interaction.response.send_message("❌ Birthday system is not configured.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        uid = str(interaction.user.id)
        data = await self._load_birthdays()
        entry = data.get(uid)

        if not isinstance(entry, dict):
            await interaction.followup.send("You have no birthday saved yet. Use `/birthday set`.", ephemeral=True)
            return

        month = entry.get("month")
        day = entry.get("day")
        year = entry.get("year")

        if not isinstance(month, int) or not isinstance(day, int):
            await interaction.followup.send("❌ Your birthday entry is invalid.", ephemeral=True)
            return

        embed = discord.Embed(title="🎉 Your Birthday", color=discord.Color.blurple())
        embed.add_field(name="Date", value=f"{day:02d}.{month:02d}" + (f".{year}" if isinstance(year, int) else ""), inline=False)
        tz = entry.get("timezone") or "Not set"
        embed.add_field(name="Timezone", value=str(tz), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="remove", description="Remove your birthday entry.")
    async def remove_birthday(self, interaction: Interaction):
        if not is_configured():
            await interaction.response.send_message("❌ Birthday system is not configured.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        uid = str(interaction.user.id)
        async with self._data_lock:
            data = await self._load_birthdays()
            if uid in data:
                del data[uid]
                ok = await self._save_birthdays(data)
                if not ok:
                    await interaction.followup.send("❌ Failed to remove your birthday.", ephemeral=True)
                    return
            else:
                await interaction.followup.send("You have no birthday entry to remove.", ephemeral=True)
                return

        await interaction.followup.send("✅ Your birthday entry was removed.", ephemeral=True)

    @app_commands.command(name="show", description="Show a member's birthday.")
    async def show_birthday(self, interaction: Interaction, member: discord.Member):
        if not is_configured():
            await interaction.response.send_message("❌ Birthday system is not configured.", ephemeral=True)
            return

        data = await self._load_birthdays()
        entry = data.get(str(member.id))
        if not isinstance(entry, dict):
            await interaction.response.send_message(f"No birthday saved for {member.mention}.", ephemeral=True)
            return

        month = entry.get("month")
        day = entry.get("day")
        year = entry.get("year")
        if not isinstance(month, int) or not isinstance(day, int):
            await interaction.response.send_message("Stored birthday is invalid.", ephemeral=True)
            return

        text = f"🎂 {member.mention}: **{day:02d}.{month:02d}**"
        if isinstance(year, int):
            text += f".{year}"

        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="upcoming", description="Show upcoming birthdays.")
    @app_commands.describe(limit="How many users to show (1-25)")
    async def upcoming_birthdays(self, interaction: Interaction, limit: Optional[int] = 10):
        if not is_configured():
            await interaction.response.send_message("❌ Birthday system is not configured.", ephemeral=True)
            return

        limit = max(1, min(limit or 10, 25))
        data = await self._load_birthdays()
        today = dt.datetime.now(dt.timezone.utc).date()

        rows = []
        for uid_str, entry in data.items():
            if not isinstance(entry, dict):
                continue
            try:
                uid = int(uid_str)
            except ValueError:
                continue

            month = entry.get("month")
            day = entry.get("day")
            year = entry.get("year")

            if not isinstance(month, int) or not isinstance(day, int):
                continue
            if not valid_month_day(month, day, year if isinstance(year, int) else None):
                continue

            nxt = next_occurrence(month, day, today)
            rows.append((nxt, uid, month, day, year))

        rows.sort(key=lambda x: x[0])
        rows = rows[:limit]

        if not rows:
            await interaction.response.send_message("No birthdays stored yet.", ephemeral=True)
            return

        embed = discord.Embed(title="📅 Upcoming Birthdays", color=discord.Color.green())
        lines = []
        for nxt, uid, month, day, year in rows:
            delta = (nxt - today).days
            date_part = f"{day:02d}.{month:02d}"
            if isinstance(year, int):
                lines.append(f"<@{uid}> — {date_part}.{year} *(in {delta} day(s))*")
            else:
                lines.append(f"<@{uid}> — {date_part} *(in {delta} day(s))*")

        embed.description = "\n".join(lines)
        await interaction.response.send_message(
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False)
        )

    # ---------- admin ----------
    @app_commands.command(name="admin_set", description="Admin: set birthday for another member.")
    @birthday_admin_required()
    @app_commands.describe(member="Target user", month="1-12", day="1-31", year="Optional year")
    async def admin_set(
        self,
        interaction: Interaction,
        member: discord.Member,
        month: int,
        day: int,
        year: Optional[int] = None
    ):
        if not is_configured():
            await interaction.response.send_message("❌ Birthday system is not configured.", ephemeral=True)
            return

        if year is not None and (year < 1900 or year > dt.date.today().year):
            await interaction.response.send_message("❌ Year must be between 1900 and current year.", ephemeral=True)
            return
        if not valid_month_day(month, day, year):
            await interaction.response.send_message("❌ Invalid date.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        uid = str(member.id)
        async with self._data_lock:
            data = await self._load_birthdays()
            old = data.get(uid, {}) if isinstance(data.get(uid), dict) else {}

            data[uid] = {
                "member_id": uid,
                "month": month,
                "day": day,
                "year": year,
                "timezone": old.get("timezone"),
                "name": member.name,
                "image_url": old.get("image_url"),
                "last_congrats_date": old.get("last_congrats_date")
            }

            ok = await self._save_birthdays(data)

        if not ok:
            await interaction.followup.send("❌ Save failed.", ephemeral=True)
            return

        await interaction.followup.send(
            f"✅ Saved birthday for {member.mention}: **{day:02d}.{month:02d}**" + (f" ({year})" if year else ""),
            ephemeral=True
        )

        await self._run_birthday_cycle()

    @app_commands.command(name="admin_remove", description="Admin: remove birthday for another member.")
    @birthday_admin_required()
    async def admin_remove(self, interaction: Interaction, member: discord.Member):
        if not is_configured():
            await interaction.response.send_message("❌ Birthday system is not configured.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        uid = str(member.id)
        async with self._data_lock:
            data = await self._load_birthdays()
            if uid not in data:
                await interaction.followup.send("No birthday entry found for that user.", ephemeral=True)
                return
            del data[uid]
            ok = await self._save_birthdays(data)

        if not ok:
            await interaction.followup.send("❌ Remove failed.", ephemeral=True)
            return

        await interaction.followup.send(f"✅ Removed birthday entry for {member.mention}.", ephemeral=True)

    @app_commands.command(name="admin_run", description="Admin: run birthday check now.")
    @birthday_admin_required()
    async def admin_run(self, interaction: Interaction):
        if not is_configured():
            await interaction.response.send_message("❌ Birthday system is not configured.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self._run_birthday_cycle()
        await interaction.followup.send("✅ Birthday cycle executed.", ephemeral=True)

    # ---------- errors ----------
    async def cog_app_command_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, MissingBirthdayAdminRole):
            msg = f"🚫 You need <@&{ADMIN_ROLE_ID}> to use this admin birthday command."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return

        log.exception("Birthday command error: %s", error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Command failed.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Command failed.", ephemeral=True)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(BirthdayCog(bot))