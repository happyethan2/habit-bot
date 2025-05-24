# helpers.py
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta, date
from collections import defaultdict

from storage import load
from rank_storage import load as load_group_rank, save as save_group_rank
from habits import HABITS
from ranks import RANKS

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# timezone definition
LOCAL_TZ = ZoneInfo("Australia/Adelaide")

# meta for last‐evaluated week
META_FILE = Path("meta.json")

def load_meta():
    if META_FILE.exists():
        return json.loads(META_FILE.read_text())
    return {}
def save_meta(m):
    META_FILE.write_text(json.dumps(m, indent=2))

# — week helpers —
def current_week_id():
    today = datetime.now(LOCAL_TZ).date()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat()

def get_week_summary():
    """For the current week only."""
    data = load()
    week = current_week_id()
    return get_summary_for(week), week

def get_summary_for(week_id: str):
    """For any ISO‐week key."""
    data = load()
    week_data = data.get(week_id, {})
    summary = defaultdict(lambda: defaultdict(int))
    for uid, days in week_data.items():
        for tokens in days.values():
            for tok in tokens:
                name = tok.split(":", 1)[0]
                summary[uid][name] += 1
    return summary

# — Discord helper —
async def display_name_for(uid: str, ctx):
    member = ctx.guild.get_member(int(uid)) if ctx.guild else None
    if member:
        return member.display_name
    try:
        user = await ctx.bot.fetch_user(int(uid))
        return user.display_name
    except:
        return uid[:6]

# — rank evaluator —
async def evaluate_week(week_id: str, ctx):
    """
    Evaluate group performance for week_id:
      • rank up if EVERYONE met all targets
      • rank down if EVERYONE missed at least one target
    """
    summary = get_summary_for(week_id)
    lines = [f"🏁 Weekly evaluation for week starting {week_id}"]

    # load the current group rank
    old_rank = load_group_rank()
    new_rank = old_rank

    # check if all passed or all failed, otherwise no rank change
    reqs = get_relevant_challenges(old_rank)
    all_met_all = all(
        summary.get(uid, {}).get(h, 0) >= HABITS[h].get("weekly_target",7)
        for uid in summary
        for h in (r["habit"] for r in reqs)
    )
    all_missed_one = all(
        any(
            summary.get(uid, {}).get(h, 0) < HABITS[h].get("weekly_target",7)
            for h in (r["habit"] for r in reqs)
        )
        for uid in summary
    )


    if all_met_all and old_rank < len(RANKS):
        new_rank += 1
        lines.append(f"🎉 Group ranked up to **{new_rank}**!")
    elif all_missed_one and old_rank > 1:
        new_rank -= 1
        lines.append(f"⚠️ Group ranked down to **{new_rank}**.")
    else:
        lines.append(f"— Group stays at **{old_rank}**.")

    # persist the updated rank
    save_group_rank(new_rank)

    # announce
    await ctx.send("\n".join(lines))


def get_relevant_challenges(level: int):
    """
    Return a list of task dicts for every challenge up to `level`, 
    deduping overridden older targets.
    """
    seen = {}
    for rank in RANKS[:level]:
        for task in rank["tasks"]:
            # always overwrite older same-habit entries
            seen[task["habit"]] = task
    # preserve the original RANK order for uniqueness
    return [seen[task["habit"]] for rank in RANKS[:level] for task in rank["tasks"]
            if task["habit"] in seen and seen[task["habit"]] is task]