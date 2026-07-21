import os
import json
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

# DB-Pfad robust auflösen
db_env = os.getenv("RIDDLE_DB_PATH", "data/riddle.sqlite3")
db_path = Path(db_env)
if not db_path.is_absolute():
    db_path = (BASE / db_path).resolve()
db_path.parent.mkdir(parents=True, exist_ok=True)

guild_raw = os.getenv("GUILD_ID", "0")
GUILD_ID = int(guild_raw) if guild_raw.isdigit() else 0
if GUILD_ID <= 0:
    raise ValueError("GUILD_ID fehlt oder ungültig in .env")

# Fallback: falls keine solved_legacy.json vorhanden ist, hier inline nutzen
SOLVED_INLINE = {
  "796602617047285770": {"solved_riddles": 6, "xp": 14500},
  "1348821831510921286": {"solved_riddles": 5, "xp": 10500},
  "1269066266094604382": {"solved_riddles": 1, "xp": 500},
  "314134226981224458": {"solved_riddles": 14, "xp": 34169},
  "210794612535590912": {"solved_riddles": 7, "xp": 14069},
  "1386284154219659305": {"solved_riddles": 1, "xp": 1000},
  "916814537288745011": {"solved_riddles": 3, "xp": 7000},
  "354370475280826379": {"solved_riddles": 15, "xp": 26319},
  "1057496485312331807": {"solved_riddles": 1, "xp": 2000},
  "1202364141310840859": {"solved_riddles": 2, "xp": 2500},
  "231468839475478529": {"solved_riddles": 1, "xp": 1750},
  "322956631963074561": {"solved_riddles": 2, "xp": 5000},
  "1292194320786522223": {"solved_riddles": 1, "xp": 2000},
  "1155135234732474378": {"solved_riddles": 1, "xp": 2000},
  "713790851264020551": {"solved_riddles": 1, "xp": 4000},
  "1149708230524669993": {"solved_riddles": 1, "xp": 3500},
  "236999659229413377": {"solved_riddles": 2, "xp": 4500},
  "656539065838993408": {"solved_riddles": 2, "xp": 3500}
}

def load_solved() -> dict:
    p = BASE / "solved_legacy.json"
    if p.exists():
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    print("[WARN] solved_legacy.json nicht gefunden -> nutze SOLVED_INLINE.")
    return SOLVED_INLINE

def main():
    solved = load_solved()

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_stats (
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        solved_riddles INTEGER NOT NULL DEFAULT 0,
        xp INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(guild_id, user_id)
    )
    """)

    for uid, stats in solved.items():
        cur.execute("""
        INSERT INTO user_stats (guild_id, user_id, solved_riddles, xp)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, user_id)
        DO UPDATE SET solved_riddles=excluded.solved_riddles, xp=excluded.xp
        """, (
            GUILD_ID,
            int(uid),
            max(0, int(stats.get("solved_riddles", 0))),
            max(0, int(stats.get("xp", 0)))
        ))

    conn.commit()

    cur.execute("""
    SELECT COUNT(*), COALESCE(SUM(solved_riddles),0), COALESCE(SUM(xp),0)
    FROM user_stats
    WHERE guild_id=?
    """, (GUILD_ID,))
    users, solved_total, xp_total = cur.fetchone()

    print(f"OK: users={users}, solved={solved_total}, xp={xp_total}")
    conn.close()
    print("DONE")

if __name__ == "__main__":
    main()