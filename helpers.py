# helpers.py
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta, date
from collections import defaultdict

from storage import load
from rank_storage import load as load_ranks, save as save_ranks
from habits import HABITS
from ranks import RANKS

# — persistent state —
# We reload DATA each call to ensure fresh progress data
USER_RANKS = load_ranks()

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
    """Return ISO date string for this week’s Monday."""
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat()


def get_week_summary():
    """Tallies days each user logged each habit this week."""
    data = load()
    week = current_week_id()
    week_data = data.get(week, {})
    summary = defaultdict(lambda: defaultdict(int))
    for uid, days in week_data.items():
        for tokens in days.values():
            for tok in tokens:
                name = tok.split(":", 1)[0]
                summary[uid][name] += 1
    return summary, week



def get_summary_for(week_id: str):
    """Tallies days each user logged each habit for a given week."""
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
    """Return a readable name for a Discord user ID."""
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
    Evaluate performance for week_id, adjust USER_RANKS up/down, announce.
    """
    summary = get_summary_for(week_id)
    lines = [f"🏁 Weekly evaluation for week starting {week_id}"]

    for uid, habits in summary.items():
        name = await display_name_for(uid, ctx)
        old = USER_RANKS.get(uid, 1)
        new = old

        all_met = all(
            habits.get(h, 0) >= HABITS[h].get("weekly_target", 7)
            for h in HABITS
        )
        none_met = all(
            habits.get(h, 0) == 0
            for h in HABITS
        )

        if all_met and old < len(RANKS):
            new += 1
            status = f"🎉 {name} ranked up to **{new}**!"
        elif none_met and old > 1:
            new -= 1
            status = f"⚠️ {name} ranked down to **{new}**."
        else:
            status = f"— {name} stays at **{old}**."

        USER_RANKS[uid] = new
        lines.append(status)

    save_ranks(USER_RANKS)
    await ctx.send("\n".join(lines))