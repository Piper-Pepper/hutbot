import asyncio
import io
import discord
from discord.ext import commands

# ---------- Konfiguration ----------
SOURCE_CHANNELS = [
    1415769909874524262,
    1415769966573260970
]

# Reaktions-Emojis (in genau dieser Reihenfolge)
REACTIONS = [
    "<:01hotlips:1347157151616995378>",     # index 0
    "<:01smile_piper:1387083454575022213>", # index 1
    "<:01scream:1377706250690625576>",      # index 2
    "<:011:1346549711817146400>",           # index 3 (bonus)
]

# Channels, die die Reaktionen repräsentieren (gleiche Indices wie REACTIONS)
REACTION_CHANNELS = [
    1416267309399670917,  # für REACTIONS[0]
    1416267352378572820,  # für REACTIONS[1]
    1416267383160442901,  # für REACTIONS[2]
    1416276593709420544   # für REACTIONS[3]
]

# Scan-Parameter
SCAN_LIMIT = 20      # letzte X Nachrichten pro Channel beobachten
SCAN_INTERVAL = 10   # Wartezeit (Sekunden) zwischen Scans


# ---------- Cog ----------
class AutoReactCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Speicherung:
        # key = original message id (die id, die beim ersten Fund existierte)
        # value = {
        #   "origin_channel": int,
        #   "content": str,
        #   "attachments": [ {"filename": str, "bytes": bytes} , ...],
        #   "mirrored": { reaction_channel_id: mirrored_message_id, ... },
        #   "origin_deleted": bool
        # }
        self.store: dict[int, dict] = {}

        # Start tasks (initial scan + laufender Monitor)
        self.bot.loop.create_task(self.initial_scan())
        self.bot.loop.create_task(self.background_monitor())

    # ---------- Hilfsfunktionen ----------
    async def _get_channel(self, channel_id: int) -> discord.TextChannel | None:
        ch = self.bot.get_channel(channel_id)
        if ch:
            return ch
        try:
            ch = await self.bot.fetch_channel(channel_id)
            if isinstance(ch, discord.TextChannel):
                return ch
        except discord.NotFound:
            return None
        except discord.Forbidden:
            print(f"⚠️ Keine Berechtigung, um Channel {channel_id} zu lesen/schreiben")
            return None
        except Exception as e:
            print(f"⚠️ Fehler beim Laden Channel {channel_id}: {e}")
            return None

    async def _snapshot_message(self, msg: discord.Message) -> dict:
        """Liest Inhalt und Attachments (als bytes) und gibt Snapshot zurück."""
        snapshot = {
            "origin_channel": msg.channel.id,
            "content": msg.content or "",
            "attachments": []
        }
        for att in msg.attachments:
            try:
                data = await att.read()
                snapshot["attachments"].append({"filename": att.filename or "file", "bytes": data})
                # kleine Pause, um Rate-Limits zu mindern
                await asyncio.sleep(0.05)
            except Exception as e:
                print(f"⚠️ Fehler beim Lesen Attachment {att.url}: {e}")
        return snapshot

    def _files_from_snapshot(self, snapshot: dict) -> list:
        files = []
        for a in snapshot.get("attachments", []):
            bio = io.BytesIO(a["bytes"])
            bio.seek(0)
            files.append(discord.File(bio, filename=a["filename"]))
        return files

    def _find_store_by_mirrored_id(self, mirrored_msg_id: int) -> int | None:
        """Gibt original msg id zurück, wenn mirrored_msg_id in einem store vorhanden ist."""
        for orig_id, rec in self.store.items():
            for mid in rec.get("mirrored", {}).values():
                if mid == mirrored_msg_id:
                    return orig_id
        return None

    async def ensure_reactions_on_msg(self, msg: discord.Message):
        """Stellt sicher, dass die 4 REACTIONS an einer gegebenen Nachricht vorhanden sind."""
        try:
            existing = {str(r.emoji) for r in msg.reactions}
        except Exception:
            existing = set()
        for r in REACTIONS:
            if r not in existing:
                try:
                    await msg.add_reaction(discord.PartialEmoji.from_str(r))
                    await asyncio.sleep(0.12)
                except Exception:
                    # falls fehlgeschlagen (z.B. Emoji nicht verfügbar), weiter
                    pass

    # ---------- initial scan (nur SOURCE_CHANNELS) ----------
    async def initial_scan(self):
        await self.bot.wait_until_ready()
        print("🔍 Initial-Scan: prüfe letzte Nachrichten in SOURCE_CHANNELS und füge ggf. Reactions hinzu")
        for ch_id in SOURCE_CHANNELS:
            ch = await self._get_channel(ch_id)
            if not ch:
                print(f"⚠️ SOURCE channel {ch_id} nicht verfügbar")
                continue
            try:
                async for msg in ch.history(limit=SCAN_LIMIT):
                    if not msg.attachments:
                        continue
                    await self.ensure_all_reactions(msg)
                    await asyncio.sleep(0.25)
            except Exception as e:
                print(f"⚠️ Fehler beim Initial-Scan von {ch_id}: {e}")

    # ---------- fügt alle 4 Reactions hinzu, falls fehlen (für SOURCE messages) ----------
    async def ensure_all_reactions(self, msg: discord.Message):
        existing = {str(r.emoji) for r in msg.reactions}
        for r in REACTIONS:
            if r not in existing:
                try:
                    await msg.add_reaction(discord.PartialEmoji.from_str(r))
                    await asyncio.sleep(0.12)
                except discord.HTTPException:
                    print(f"⚠️ Konnte Reaction {r} nicht hinzufügen bei msg {msg.id}")

    # ---------- Background Monitor (kontinuierlich) ----------
    async def background_monitor(self):
        await self.bot.wait_until_ready()
        print("⏱️ Background monitor gestartet: überwache SOURCE + REACTION channels")
        monitored = SOURCE_CHANNELS + REACTION_CHANNELS
        while True:
            try:
                # Für jeden Channel die letzten SCAN_LIMIT Nachrichten prüfen
                for ch_id in monitored:
                    ch = await self._get_channel(ch_id)
                    if not ch:
                        continue
                    async for msg in ch.history(limit=SCAN_LIMIT):
                        if not msg.attachments:
                            continue
                        # Stelle sicher, dass in SOURCE_CHANNELS die Reactions vorhanden sind
                        if msg.channel.id in SOURCE_CHANNELS:
                            await self.ensure_all_reactions(msg)
                        # Update-Logik (verschieben/restore/etc.)
                        await self.process_message(msg)
                        await asyncio.sleep(0.2)
            except Exception as e:
                print(f"⚠️ Fehler im Background Monitor: {e}")
            await asyncio.sleep(SCAN_INTERVAL)

    # ---------- Kernlogik: entscheiden, wo die Nachricht stehen soll ----------
    async def process_message(self, msg: discord.Message):
        """Entscheidet anhand der Reactions, wohin die Nachricht gehört, und führt Move/Restore durch."""
        # Zähle die relevanten Reaktionen
        counts = []
        for r in REACTIONS:
            reaction = discord.utils.get(msg.reactions, emoji=discord.PartialEmoji.from_str(r))
            counts.append(reaction.count if reaction else 0)

        max_count = max(counts)
        # Wenn <=1 => Nachricht soll in einem SOURCE_CHANNEL sein (nicht in Reaction-Channels)
        if max_count <= 1:
            # Wenn die Nachricht aktuell in a reaction channel -> restore in source and delete mirrors
            if msg.channel.id in REACTION_CHANNELS:
                orig = self._find_store_by_mirrored_id(msg.id)
                if orig is not None:
                    record = self.store.get(orig)
                    if record:
                        origin_ch = await self._get_channel(record.get("origin_channel")) or await self._get_channel(SOURCE_CHANNELS[0])
                        if origin_ch:
                            files = self._files_from_snapshot(record)
                            try:
                                new_msg = await origin_ch.send(content=record.get("content", ""), files=files)
                                # ensure reactions on restored source message
                                await self.ensure_reactions_on_msg(new_msg)
                                # cleanup: lösche alle mirrored messages, entferne store
                                await self._delete_all_mirrored(orig)
                                if orig in self.store:
                                    del self.store[orig]
                                await asyncio.sleep(0.15)
                            except Exception as e:
                                print(f"⚠️ Fehler beim Wiederherstellen Nachricht in {origin_ch.id}: {e}")
                else:
                    # keine Zuordnung: entferne die message aus reaction channel (weil sie <=1 hat)
                    try:
                        await msg.delete()
                        await asyncio.sleep(0.15)
                    except Exception:
                        pass
            else:
                # msg in SOURCE_CHANNEL und <=1 -> nichts tun (bleibt dort).
                return
            return

        # Sonst max_count >= 2 -> Nachricht muss in Reaktions-channel(s) sein.
        selected_indices = [i for i, c in enumerate(counts) if c == max_count]
        target_channel_ids = {REACTION_CHANNELS[i] for i in selected_indices}

        # Falls die message aktuell in SOURCE_CHANNELS -> move it to target channels
        if msg.channel.id in SOURCE_CHANNELS:
            orig_id = msg.id
            snapshot = await self._snapshot_message(msg)
            snapshot["origin_channel"] = msg.channel.id
            snapshot["content"] = snapshot.get("content", "")
            snapshot["mirrored"] = {}
            snapshot["origin_deleted"] = False

            # Poste in alle target channels (kann mehrere sein, falls Gleichstand)
            for t_id in target_channel_ids:
                t_ch = await self._get_channel(t_id)
                if not t_ch:
                    continue
                try:
                    files = self._files_from_snapshot(snapshot)
                    mirrored_msg = await t_ch.send(content=snapshot["content"], files=files)
                    # ensure reactions on mirrored message
                    await self.ensure_reactions_on_msg(mirrored_msg)
                    snapshot["mirrored"][t_id] = mirrored_msg.id
                    await asyncio.sleep(0.15)
                except Exception as e:
                    print(f"⚠️ Fehler beim Senden in Reaction-Channel {t_id}: {e}")

            # Jetzt lösche die Originalnachricht aus SOURCE (-> "verschoben")
            try:
                await msg.delete()
                snapshot["origin_deleted"] = True
            except Exception as e:
                print(f"⚠️ Konnte Original-Nachricht {orig_id} nicht löschen: {e}")

            # speichere snapshot in store
            self.store[orig_id] = snapshot
            return

        # Falls die message aktuell in a REACTION_CHANNEL
        if msg.channel.id in REACTION_CHANNELS:
            orig = self._find_store_by_mirrored_id(msg.id)
            if orig is None:
                # Orphan: best-effort: ensure reactions exist on this message
                await self.ensure_reactions_on_msg(msg)
                # und entferne gleiche posts in SOURCE/anderen reaction channels
                await self._ensure_unique_for_orphan(msg, target_channel_ids)
                return

            record = self.store.get(orig)
            if not record:
                return

            # Ziel: die message soll in genau target_channel_ids stehen.
            existing_mirrored = set(record.get("mirrored", {}).keys())

            # Erstelle fehlende Kopien in target channels
            missing = target_channel_ids - existing_mirrored
            for t_id in missing:
                t_ch = await self._get_channel(t_id)
                if not t_ch:
                    continue
                try:
                    files = self._files_from_snapshot(record)
                    new_m = await t_ch.send(content=record.get("content", ""), files=files)
                    await self.ensure_reactions_on_msg(new_m)
                    record["mirrored"][t_id] = new_m.id
                    await asyncio.sleep(0.12)
                except Exception as e:
                    print(f"⚠️ Fehler beim Erstellen Kopie in {t_id}: {e}")

            # Lösche Kopien aus reaction channels, die NICHT mehr in target_channel_ids liegen
            to_delete = existing_mirrored - target_channel_ids
            for t_id in to_delete:
                mid = record["mirrored"].get(t_id)
                if not mid:
                    continue
                ch = await self._get_channel(t_id)
                if not ch:
                    del record["mirrored"][t_id]
                    continue
                try:
                    m = await ch.fetch_message(mid)
                    await m.delete()
                    await asyncio.sleep(0.12)
                except discord.NotFound:
                    pass
                except Exception as e:
                    print(f"⚠️ Fehler beim Löschen Spiegelung in {t_id}: {e}")
                record["mirrored"].pop(t_id, None)

            # Falls die origin noch existiert in SOURCE, lösche sie, weil jetzt highest >=2
            origin_ch = await self._get_channel(record.get("origin_channel"))
            if origin_ch and not record.get("origin_deleted"):
                try:
                    orig_msg = await origin_ch.fetch_message(orig)
                    await orig_msg.delete()
                    record["origin_deleted"] = True
                    await asyncio.sleep(0.12)
                except discord.NotFound:
                    record["origin_deleted"] = True
                except Exception:
                    pass

            # speichere record zurück
            self.store[orig] = record
            return

    # ---------- Hilfsroutine: Falls eine 'orphan' message (keine Zuordnung) existiert ----------
    async def _ensure_unique_for_orphan(self, msg: discord.Message, target_channel_ids: set):
        """Wenn eine Nachricht in Reaction-Channel auftaucht, die nicht in store ist,
        stelle sicher, dass keine Kopie in SOURCE_CHANNELS existiert (lösche sie), und
        lösche ggf. Kopien in anderen Reaction-Channels, die nicht zu target_channel_ids gehören."""
        # Lösche gleiche Inhalte in SOURCE_CHANNELS (falls vorhanden)
        for src in SOURCE_CHANNELS:
            ch = await self._get_channel(src)
            if not ch:
                continue
            try:
                async for m in ch.history(limit=SCAN_LIMIT):
                    if not m.attachments:
                        continue
                    if (m.content or "") == (msg.content or "") and len(m.attachments) == len(msg.attachments):
                        try:
                            await m.delete()
                            await asyncio.sleep(0.12)
                        except Exception:
                            pass
            except Exception:
                pass

        # prüfe andere reaction-channels: falls gleiche content vorhanden, lösche dort (außer msg.channel)
        for t_id in REACTION_CHANNELS:
            if t_id == msg.channel.id:
                continue
            ch = await self._get_channel(t_id)
            if not ch:
                continue
            try:
                async for m in ch.history(limit=SCAN_LIMIT):
                    if (m.content or "") == (msg.content or "") and len(m.attachments) == len(msg.attachments):
                        try:
                            await m.delete()
                            await asyncio.sleep(0.12)
                        except Exception:
                            pass
            except Exception:
                pass

    # ---------- löscht alle mirrored messages für orig ----------
    async def _delete_all_mirrored(self, orig_id: int):
        rec = self.store.get(orig_id)
        if not rec:
            return
        for ch_id, mid in list(rec.get("mirrored", {}).items()):
            ch = await self._get_channel(ch_id)
            if not ch:
                continue
            try:
                m = await ch.fetch_message(mid)
                await m.delete()
                await asyncio.sleep(0.12)
            except discord.NotFound:
                pass
            except Exception as e:
                print(f"⚠️ Fehler beim Löschen mirror {mid} in {ch_id}: {e}")
        # remove mapping
        rec["mirrored"].clear()


# ---------- setup ----------
async def setup(bot: commands.Bot):
    await bot.add_cog(AutoReactCog(bot))
