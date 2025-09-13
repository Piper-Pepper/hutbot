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
    "<:011:1346549711817146400>",           # index 3
]

# Channels, die die Reaktionen repräsentieren (gleiche Indices wie REACTIONS)
REACTION_CHANNELS = [
    1416267309399670917,  # für REACTIONS[0]
    1416267352378572820,  # für REACTIONS[1]
    1416267383160442901,  # für REACTIONS[2]
    1416276593709420544   # für REACTIONS[3]
]

# Scan-Parameter
SCAN_LIMIT = 20
SCAN_INTERVAL = 30  # alle 30 Sekunden


# ---------- Cog ----------
class AutoReactCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Runtime-Store:
        # key = original message id (die id der Original-Nachricht vor dem Löschen)
        # value = {
        #   "origin_channel": int,
        #   "orig_msg_id": int,
        #   "content": str,
        #   "attachments": [ {"filename": str, "bytes": bytes}, ... ],
        #   "mirrored": { reaction_channel_id: mirrored_message_id, ... },
        #   "origin_deleted": bool
        # }
        self.store: dict[int, dict] = {}

        # Tasks
        self.bot.loop.create_task(self.initial_scan())
        self.bot.loop.create_task(self.background_monitor())

    # ---------------- Helpers ----------------
    async def _get_channel(self, channel_id: int) -> discord.TextChannel | None:
        ch = self.bot.get_channel(channel_id)
        if ch:
            return ch
        try:
            ch = await self.bot.fetch_channel(channel_id)
            if isinstance(ch, discord.TextChannel):
                return ch
        except Exception:
            return None
        return None

    async def _snapshot_message(self, msg: discord.Message) -> dict:
        """Snapshot (content + attachments) einer Nachricht aufnehmen."""
        snapshot = {
            "origin_channel": msg.channel.id,
            "orig_msg_id": msg.id,
            "content": msg.content or "",
            "attachments": []
        }
        for att in msg.attachments:
            try:
                data = await att.read()
                snapshot["attachments"].append({"filename": att.filename or "file", "bytes": data})
                await asyncio.sleep(0.03)
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
        for orig_id, rec in self.store.items():
            for mid in rec.get("mirrored", {}).values():
                if mid == mirrored_msg_id:
                    return orig_id
        return None

    async def ensure_reactions_on_msg(self, msg: discord.Message):
        """Stellt sicher, dass die 4 REACTIONS an einer Nachricht hängen (falls möglich)."""
        try:
            existing = {str(r.emoji) for r in msg.reactions}
        except Exception:
            existing = set()
        for r in REACTIONS:
            if r not in existing:
                try:
                    await msg.add_reaction(discord.PartialEmoji.from_str(r))
                    await asyncio.sleep(0.08)
                except Exception:
                    # z.B. wenn Custom-Emoji nicht verfügbar ist für den Bot
                    pass

    # -------- initial scan (beim Start) --------
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
                    await self.ensure_reactions_on_msg(msg)
                    # Führt einmal die Logik aus, sodass beim Start alles korrekt steht
                    await self.process_message(msg)
                    await asyncio.sleep(0.12)
            except Exception as e:
                print(f"⚠️ Fehler beim Initial-Scan von {ch_id}: {e}")

    # -------- background monitor (alle 30s) --------
    async def background_monitor(self):
        await self.bot.wait_until_ready()
        print("⏱️ Background monitor gestartet (überwacht SOURCE + REACTION channels)")
        monitored = SOURCE_CHANNELS + REACTION_CHANNELS
        while True:
            try:
                for ch_id in monitored:
                    ch = await self._get_channel(ch_id)
                    if not ch:
                        continue
                    async for msg in ch.history(limit=SCAN_LIMIT):
                        if not msg.attachments:
                            continue
                        # ensure reactions only for source messages (so each source has the 4 emojis)
                        if msg.channel.id in SOURCE_CHANNELS:
                            await self.ensure_reactions_on_msg(msg)
                        await self.process_message(msg)
                        await asyncio.sleep(0.08)
            except Exception as e:
                print(f"⚠️ Fehler im Background Monitor: {e}")
            await asyncio.sleep(SCAN_INTERVAL)

    # -------- Aggregation helper --------
    async def _aggregate_counts_for_record(self, orig_id: int, record: dict) -> list:
        """Aggregiert Reaktionen über alle existierenden Kopien (mirrors + ggf. origin)."""
        counts = [0] * len(REACTIONS)

        # origin message (falls noch vorhanden)
        if not record.get("origin_deleted", False) and record.get("orig_msg_id") and record.get("origin_channel"):
            try:
                origin_ch = await self._get_channel(record["origin_channel"])
                if origin_ch:
                    origin_msg = await origin_ch.fetch_message(record["orig_msg_id"])
                    for i, r in enumerate(REACTIONS):
                        react = discord.utils.get(origin_msg.reactions, emoji=discord.PartialEmoji.from_str(r))
                        if react:
                            counts[i] += react.count
            except Exception:
                # not found or no permission
                pass

        # mirrored messages
        for ch_id, m_id in record.get("mirrored", {}).items():
            try:
                ch = await self._get_channel(ch_id)
                if not ch:
                    continue
                m = await ch.fetch_message(m_id)
                for i, r in enumerate(REACTIONS):
                    react = discord.utils.get(m.reactions, emoji=discord.PartialEmoji.from_str(r))
                    if react:
                        counts[i] += react.count
            except Exception:
                pass

        return counts

    # -------- Core logic ----------
    async def process_message(self, msg: discord.Message):
        """Entscheidet, ob verschoben/gelöscht/erstellt werden muss."""
        # Wenn es eine Nachricht ohne attachments ist -> nichts tun
        if not msg.attachments:
            return

        # Bestimme counts für diese einzelne Nachricht (Schnelles Signal)
        counts_local = []
        for r in REACTIONS:
            react = discord.utils.get(msg.reactions, emoji=discord.PartialEmoji.from_str(r))
            counts_local.append(react.count if react else 0)

        # Quick-check: if this is a source message and has no relevant reactions
        if msg.channel.id in SOURCE_CHANNELS:
            # If local max <= 1, ensure it isn't mirrored (cleanup), otherwise move it.
            local_max = max(counts_local)
            if local_max <= 1:
                # ensure no leftover mirrors exist for this original (cleanup)
                # If store contains this orig (maybe from a previous run), delete its mirrors
                if msg.id in self.store:
                    await self._delete_all_mirrored(msg.id)
                    # leave original in place
                return

            # local_max >= 2 -> move from source to reaction channels according to local counts
            selected_indices = [i for i, c in enumerate(counts_local) if c == max(counts_local)]
            target_channel_ids = {REACTION_CHANNELS[i] for i in selected_indices}

            # Snapshot original
            orig_id = msg.id
            snapshot = await self._snapshot_message(msg)
            snapshot["mirrored"] = {}
            snapshot["origin_deleted"] = False

            # Post copies into target channels
            for t_id in target_channel_ids:
                t_ch = await self._get_channel(t_id)
                if not t_ch:
                    continue
                try:
                    files = self._files_from_snapshot(snapshot)
                    mirrored_msg = await t_ch.send(content=snapshot["content"], files=files)
                    # ensure reactions on the mirror
                    await self.ensure_reactions_on_msg(mirrored_msg)
                    snapshot["mirrored"][t_id] = mirrored_msg.id
                    await asyncio.sleep(0.08)
                except Exception as e:
                    print(f"⚠️ Fehler beim Senden in Reaction-Channel {t_id}: {e}")

            # Delete original (-> move)
            try:
                await msg.delete()
                snapshot["origin_deleted"] = True
            except Exception as e:
                print(f"⚠️ Konnte Original-Nachricht {orig_id} nicht löschen: {e}")

            self.store[orig_id] = snapshot
            return

        # If message is inside a reaction channel:
        if msg.channel.id in REACTION_CHANNELS:
            # Find the original record by mirrored id
            orig = self._find_store_by_mirrored_id(msg.id)
            if orig is None:
                # Orphan mirrored message (no store) -> best-effort: ensure reactions present and try to dedupe
                await self.ensure_reactions_on_msg(msg)
                # try to delete any identical messages in source channels (best-effort)
                await self._ensure_unique_for_orphan(msg)
                return

            record = self.store.get(orig)
            if not record:
                return

            # Aggregate counts across all copies (mirrors + origin if present)
            agg_counts = await self._aggregate_counts_for_record(orig, record)
            max_count = max(agg_counts)
            if max_count <= 1:
                # restore to origin (if we have snapshot) and delete mirrors
                origin_ch = await self._get_channel(record.get("origin_channel")) or await self._get_channel(SOURCE_CHANNELS[0])
                if origin_ch:
                    try:
                        files = self._files_from_snapshot(record)
                        new_msg = await origin_ch.send(content=record.get("content", ""), files=files)
                        await self.ensure_reactions_on_msg(new_msg)
                        # cleanup
                        await self._delete_all_mirrored(orig)
                        if orig in self.store:
                            del self.store[orig]
                        await asyncio.sleep(0.08)
                    except Exception as e:
                        print(f"⚠️ Fehler beim Wiederherstellen Nachricht in {origin_ch.id}: {e}")
                else:
                    # fallback: just delete mirrors
                    await self._delete_all_mirrored(orig)
                    if orig in self.store:
                        del self.store[orig]
                return

            # Otherwise max_count >= 2 -> ensure mirrors in exactly the channels for highest reactions
            selected_indices = [i for i, c in enumerate(agg_counts) if c == max_count]
            target_channel_ids = {REACTION_CHANNELS[i] for i in selected_indices}

            existing_channels = set(record.get("mirrored", {}).keys())

            # create missing mirrors
            for t_id in target_channel_ids - existing_channels:
                t_ch = await self._get_channel(t_id)
                if not t_ch:
                    continue
                try:
                    files = self._files_from_snapshot(record)
                    new_m = await t_ch.send(content=record.get("content", ""), files=files)
                    await self.ensure_reactions_on_msg(new_m)
                    record["mirrored"][t_id] = new_m.id
                    await asyncio.sleep(0.08)
                except Exception as e:
                    print(f"⚠️ Fehler beim Erstellen Kopie in {t_id}: {e}")

            # delete mirrors that are no longer needed
            for t_id in existing_channels - target_channel_ids:
                mid = record["mirrored"].get(t_id)
                if not mid:
                    continue
                ch = await self._get_channel(t_id)
                if ch:
                    try:
                        m = await ch.fetch_message(mid)
                        await m.delete()
                    except Exception:
                        pass
                record["mirrored"].pop(t_id, None)

            # ensure origin is deleted (we moved before)
            if not record.get("origin_deleted", True):
                origin_ch = await self._get_channel(record.get("origin_channel"))
                if origin_ch:
                    try:
                        om = await origin_ch.fetch_message(record.get("orig_msg_id"))
                        await om.delete()
                    except Exception:
                        pass
                    record["origin_deleted"] = True

            # persist record in store
            self.store[orig] = record
            return

    # ---------- Orphan helper ----------
    async def _ensure_unique_for_orphan(self, msg: discord.Message):
        """Wenn eine Nachricht manuell in Reaction-Channel gepostet wurde (kein store),
        versuchen wir, gleiche Inhalte in SOURCE_CHANNELS oder anderen reaction channels zu löschen."""
        # delete identical messages in SOURCE_CHANNELS (best-effort)
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
                            await asyncio.sleep(0.05)
                        except Exception:
                            pass
            except Exception:
                pass

        # delete identical in other REACTION channels (except current)
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
                            await asyncio.sleep(0.05)
                        except Exception:
                            pass
            except Exception:
                pass

    # ---------- delete all mirrors ----------
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
                await asyncio.sleep(0.05)
            except Exception:
                pass
        rec["mirrored"].clear()


# ---------- setup ----------
async def setup(bot: commands.Bot):
    await bot.add_cog(AutoReactCog(bot))
