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


# ---------- streak functions ----------
def calculate_streak(user_id: str, habit: str):
    """Calculate current and best streaks for a habit"""
    data = load()
    current_streak = 0
    best_streak = 0
    temp_streak = 0
    
    # Check last 90 days in reverse chronological order
    today = datetime.now(LOCAL_TZ).date()
    
    for i in range(90):
        check_date = today - timedelta(days=i)
        week_start = check_date - timedelta(days=check_date.weekday())
        week_id = week_start.isoformat()
        day_iso = check_date.isoformat()
        
        # Check if habit was completed on this day
        completed = False
        if week_id in data and user_id in data[week_id]:
            day_data = data[week_id][user_id].get(day_iso, [])
            completed = any(token.split(":")[0] == habit for token in day_data)
        
        if completed:
            temp_streak += 1
            best_streak = max(best_streak, temp_streak)
            if i == 0:  # Most recent day (today or yesterday depending on time)
                current_streak = temp_streak
        else:
            if i == 0:
                current_streak = 0
            temp_streak = 0
    
    return current_streak, best_streak

def get_all_streaks(user_id: str):
    """Get streaks for all habits a user has logged"""
    data = load()
    user_habits = set()
    
    # Find all habits this user has ever logged
    for week_data in data.values():
        if user_id in week_data:
            for day_data in week_data[user_id].values():
                for token in day_data:
                    habit = token.split(":")[0]
                    user_habits.add(habit)
    
    streaks = {}
    for habit in user_habits:
        current, best = calculate_streak(user_id, habit)
        if current > 0 or best > 0:  # Only include habits with streaks
            streaks[habit] = {"current": current, "best": best}
    
    return streaks

def format_streak_display(current: int, best: int) -> str:
    """Format streak for display"""
    if current == 0:
        return f"💔 {best} best"
    elif current == best:
        return f"🔥 {current} (PB!)"
    else:
        return f"🔥 {current} (best: {best})"


# ---------- reminder functions ----------
def load_reminder_prefs():
    """Load user reminder preferences from meta.json"""
    meta = load_meta()
    return meta.get("reminder_users", [])

def save_reminder_prefs(user_list):
    """Save user reminder preferences to meta.json"""
    meta = load_meta()
    meta["reminder_users"] = user_list
    save_meta(meta)

def toggle_user_reminders(user_id: str):
    """Toggle reminder preference for a user"""
    prefs = load_reminder_prefs()
    if user_id in prefs:
        prefs.remove(user_id)
        save_reminder_prefs(prefs)
        return False  # Now disabled
    else:
        prefs.append(user_id)
        save_reminder_prefs(prefs)
        return True  # Now enabled

def get_users_needing_reminders():
    """Get list of users who want reminders and haven't checked in today"""
    reminder_users = load_reminder_prefs()
    if not reminder_users:
        return []
    
    # Get today's check-ins
    today = datetime.now(LOCAL_TZ).date()
    week = current_week_id()
    today_iso = today.isoformat()
    
    data = load()
    week_data = data.get(week, {})
    
    users_needing_reminders = []
    for user_id in reminder_users:
        # Check if user has checked in today
        user_days = week_data.get(user_id, {})
        if not user_days.get(today_iso):
            users_needing_reminders.append(user_id)
    
    return users_needing_reminders

# Streak tracking functions
def calculate_streak(user_id: str, habit: str):
    """Calculate current and best streaks for a habit"""
    data = load()
    
    # Check last 90 days in reverse chronological order
    today = datetime.now(LOCAL_TZ).date()
    
    # Build list of completed days (True/False for each day)
    completed_days = []
    for i in range(90):
        check_date = today - timedelta(days=i)
        week_start = check_date - timedelta(days=check_date.weekday())
        week_id = week_start.isoformat()
        day_iso = check_date.isoformat()
        
        # Check if habit was completed on this day
        completed = False
        if week_id in data and user_id in data[week_id]:
            day_data = data[week_id][user_id].get(day_iso, [])
            completed = any(token.split(":")[0] == habit for token in day_data)
        
        completed_days.append(completed)
    
    # Calculate current streak (consecutive days from most recent)
    current_streak = 0
    for completed in completed_days:
        if completed:
            current_streak += 1
        else:
            break  # Stop at first non-completed day
    
    # Calculate best streak (longest consecutive sequence)
    best_streak = 0
    temp_streak = 0
    for completed in completed_days:
        if completed:
            temp_streak += 1
            best_streak = max(best_streak, temp_streak)
        else:
            temp_streak = 0
    
    return current_streak, best_streak

def get_all_streaks(user_id: str):
    """Get streaks for all habits a user has logged"""
    data = load()
    user_habits = set()
    
    # Find all habits this user has ever logged
    for week_data in data.values():
        if user_id in week_data:
            for day_data in week_data[user_id].values():
                for token in day_data:
                    habit = token.split(":")[0]
                    user_habits.add(habit)
    
    streaks = {}
    for habit in user_habits:
        current, best = calculate_streak(user_id, habit)
        if current > 0 or best > 0:  # Only include habits with streaks
            streaks[habit] = {"current": current, "best": best}
    
    return streaks

def format_streak_display(current: int, best: int) -> str:
    """Format streak for display"""
    if current == 0:
        return f"💔 {best} best"
    elif current == best:
        return f"🔥 {current} (PB!)"
    else:
        return f"🔥 {current} (best: {best})"