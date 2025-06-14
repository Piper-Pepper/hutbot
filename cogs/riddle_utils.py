# riddle_utils.py
import json
import os
import random
import string
from typing import Dict

def load_riddles(path: str) -> Dict:
    if not os.path.exists(path):
        with open(path, 'w') as f:
            json.dump({}, f)
        return {}
    with open(path, 'r') as f:
        return json.load(f)

def save_riddles(path: str, data: Dict):
    with open(path, 'w') as f:
        json.dump(data, f, indent=4)

def generate_riddle_id(length: int = 6) -> str:
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))
