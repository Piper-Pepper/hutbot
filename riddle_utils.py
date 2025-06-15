import json
import os
import random
import string
from typing import Dict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RIDDLE_DB_PATH = os.path.join(BASE_DIR, "riddles.json")
USER_STATS_PATH = os.path.join(BASE_DIR, "user_stats.json")

# Erstelle leere JSON-Dateien, wenn sie nicht existieren
for path in [RIDDLE_DB_PATH, USER_STATS_PATH]:
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump({}, f)

def load_riddles(path: str) -> Dict:
    with open(path, 'r') as f:
        return json.load(f)

def save_riddles(path: str, data: Dict):
    with open(path, 'w') as f:
        json.dump(data, f, indent=4)

def generate_riddle_id(length: int = 6) -> str:
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

