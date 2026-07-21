import os
import json
import sqlite3
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("RIDDLE_DB_PATH", "data/riddle.sqlite3")
GUILD_ID = int(os.getenv("GUILD_ID"))  # kommt aus .env

with open("solved_legacy.json", "r", encoding="utf-8") as f:
    solved = json.load(f)

conn = sqlite3.connect(DB_PATH)
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
    """, (GUILD_ID, int(uid), int(stats.get("solved_riddles", 0)), int(stats.get("xp", 0))))

conn.commit()
cur.execute("SELECT COUNT(*), COALESCE(SUM(solved_riddles),0), COALESCE(SUM(xp),0) FROM user_stats WHERE guild_id=?", (GUILD_ID,))
print(cur.fetchone())
conn.close()