# storage.py
import json
from pathlib import Path
DATA_FILE = Path("data/progress.json")

def load():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {}          # {week_id: {user_id: {day_iso: [tasks]}}}

def save(data: dict):
    DATA_FILE.write_text(json.dumps(data, indent=2))