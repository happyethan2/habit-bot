# rank_storage.py
import json
from pathlib import Path

FILE = Path("rank.json")

def load():
    if FILE.exists():
        return json.loads(FILE.read_text()).get("rank", 1)
    return 1

def save(rank: int):
    FILE.write_text(json.dumps({"rank": rank}, indent=2))