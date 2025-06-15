import json

# === File paths ===
RIDDLES_FILE = "riddles.json"
USER_STATS_FILE = "user_stats.json"

# === Constants ===
LOG_CHANNEL_ID = 1381754826710585527
DEFAULT_SOLUTION_IMAGE = "https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg"
RIDDLE_ADD_PERMISSION_ROLE_ID = 1380610400416043089
DEFAULT_RIDDLE_IMAGE = "https://cdn.discordapp.com/attachments/1346843244067160074/1382408027122172085/riddle_logo.jpg"

# === JSON helpers ===
def load_json(filename):
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)
