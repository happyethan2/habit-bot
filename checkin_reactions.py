# checkin_reactions.py
# Daily reaction-based check-ins for HabitBot
from __future__ import annotations

import json
import pathlib
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import discord
from discord import Embed
from discord.ext import tasks

from helpers import LOCAL_TZ
from storage import load as load_store, save as save_store
from ranks import RANKS
from habits import HABITS
from rank_storage import load as load_group_rank

# Store mapping of date -> list[message_id] (for that day's check-in posts)
POSTS_FILE = pathlib.Path("data/checkin_posts.json")

# Map habit -> emoji (single unicode each)
EMOJI_MAP: Dict[str, str] = {
    "meditation": "🧘",
    "reading": "📖",
    "journaling": "📝",
    "exercise": "🏃",
    "walking": "🚶",
    "diet": "🍽️",
    "bedtime": "🌙",
    "digitaldetox": "📵",
    "porn": "🍑",         # no PMO
    "streaming": "📺",    # no streaming
}

def _build_reverse() -> Dict[str, str]:
    return {v: k for k, v in EMOJI_MAP.items()}

EMOJI_TO_HABIT = _build_reverse()

def _today_iso(d: Optional[datetime] = None) -> str:
    return (d or datetime.now(LOCAL_TZ)).date().isoformat()

def _load_posts() -> Dict[str, List[int]]:
    if POSTS_FILE.exists():
        try:
            return json.loads(POSTS_FILE.read_text())
        except Exception:
            return {}
    return {}

def _save_posts(data: Dict[str, List[int]]) -> None:
    POSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    POSTS_FILE.write_text(json.dumps(data, indent=2))

def _latest_targets_for_rank(rank: int) -> Dict[str, str]:
    """Return habit -> latest target string among ranks <= rank."""
    latest: Dict[str, str] = {}
    for r in RANKS[:max(0, rank)]:
        for t in r.get("tasks", []):
            latest[t["habit"]] = t["target"]
    return latest

def _default_token_for(habit: str, latest_target: Optional[str]) -> Tuple[str, Optional[int]]:
    """
    Return (token, default_numeric) for min/default check-in.
    token     -> 'habit' for boolean habits or 'habit:<min>' for timed ones
    default_numeric -> int minutes if applicable, else None
    """
    cfg = HABITS.get(habit, {})
    unit = cfg.get("unit")
    if unit == "bool":
        return habit, None

    # Prefer HABITS['min'] if present; otherwise parse from rank target like "30min"
    mins = None
    if isinstance(cfg.get("min"), int):
        mins = cfg["min"]
    elif latest_target:
        m = re.search(r'(\\d+)', latest_target)
        if m:
            mins = int(m.group(1))

    mins = mins or 0
    return f"{habit}:{mins}", mins

def _strip_name(token: str) -> str:
    return token.split(':', 1)[0]

async def post_for_date(bot: discord.Client, target_date_str: Optional[str] = None) -> List[int]:
    """
    Post the daily check-in embed to #check-ins for the given local date (ISO string).
    Returns list of message IDs posted.
    """
    target_date_str = target_date_str or _today_iso()
    guild = discord.utils.get(bot.guilds)  # single-server assumption OK for this bot
    if not guild:
        return []
    checkins_channel = discord.utils.get(guild.text_channels, name="check-ins")
    if not checkins_channel:
        return []

    rank = load_group_rank() or 7  # default to 7 if not set
    latest = _latest_targets_for_rank(rank)

    # Keep habits only if they exist at this rank and have an emoji mapping
    unlocked_habits = [h for h in EMOJI_MAP.keys() if h in latest]

    # Build embed description
    lines = []
    for h in unlocked_habits:
        token, mins = _default_token_for(h, latest.get(h))
        suffix = f" — min {mins} min" if mins is not None else ""
        lines.append(f"{EMOJI_MAP[h]}  **{h.capitalize()}**{suffix}")
    desc = "\\n".join(lines) if lines else "_No habits configured for this rank._"

    title = f"Daily Check-in — {datetime.fromisoformat(target_date_str).strftime('%a %d %b %Y')}"
    embed = Embed(title=title, description=desc, colour=0x2ecc71)
    embed.set_footer(text="React to log today. Use /checkin to log custom values.")

    msg = await checkins_channel.send(embed=embed)
    for h in unlocked_habits:
        try:
            await msg.add_reaction(EMOJI_MAP[h])
        except discord.HTTPException:
            pass

    # Remember message id under that date
    posts = _load_posts()
    posts.setdefault(target_date_str, [])
    posts[target_date_str].append(msg.id)
    _save_posts(posts)

    # Pin today's message; unpin older daily check-in pins by this bot
    try:
        await msg.pin(reason="Daily check-in")
        pins = await checkins_channel.pins()
        for p in pins:
            if p.id != msg.id and p.author.id == bot.user.id:
                # Unpin older check-in pins
                try:
                    await p.unpin(reason="Rotate daily check-in pin")
                except discord.HTTPException:
                    pass
    except discord.HTTPException:
        pass

    return [msg.id]

_bot_ref: Optional[discord.Client] = None

@tasks.loop(minutes=1)
async def _checkin_poster():
    now = datetime.now(LOCAL_TZ)
    if now.hour == 6 and now.minute == 0:
        await post_for_date(_bot_ref, _today_iso(now))

@_checkin_poster.before_loop
async def _before_loop():
    await _bot_ref.wait_until_ready()

def setup(bot: discord.Client):
    """Call from bot.py to start the 06:00 poster loop."""
    global _bot_ref
    _bot_ref = bot
    if not _checkin_poster.is_running():
        _checkin_poster.start()

async def _log(bot: discord.Client, guild_id: int, text: str):
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    log_chan = discord.utils.get(guild.text_channels, name="check-in-logs")
    if not log_chan:
        return
    try:
        await log_chan.send(text)
    except discord.HTTPException:
        pass

async def handle_reaction(bot: discord.Client, payload: discord.RawReactionActionEvent, added: bool):
    """
    Handle raw reaction add/remove for check-in posts.
    - Add: log default/min token IF the user didn't already log a custom value via /checkin.
    - Remove: undo only the default/min token (keep custom values).
    - Reactions map to the date of the original post (backfill enabled).
    """
    # Only operate on our check-in messages
    posts = _load_posts()
    target_date = None
    for d, ids in posts.items():
        if payload.message_id in ids:
            target_date = d
            break
    if not target_date:
        return

    # Identify habit from emoji; remove any unknown reacts
    habit = EMOJI_TO_HABIT.get(str(payload.emoji))
    if not habit:
        # Auto-remove non-mapped reactions to keep the message tidy
        try:
            channel = bot.get_channel(payload.channel_id)
            if channel:
                msg = await channel.fetch_message(payload.message_id)
                user = bot.get_user(payload.user_id)
                if user:
                    await msg.remove_reaction(payload.emoji, user)
        except Exception:
            pass
        return

    # Prepare storage
    data = load_store()
    dt_obj = datetime.fromisoformat(target_date)
    monday = dt_obj - timedelta(days=dt_obj.weekday())
    week_id = monday.date().isoformat()

    user_id = str(payload.user_id)
    day_tasks: List[str] = data.setdefault(week_id, {}).setdefault(user_id, {}).setdefault(target_date, [])

    rank = load_group_rank() or 7
    latest = _latest_targets_for_rank(rank)
    default_token, _ = _default_token_for(habit, latest.get(habit))

    # Respect custom values
    existing_for_habit = [t for t in day_tasks if _strip_name(t) == habit]
    has_custom = any(t != default_token for t in existing_for_habit)

    if added:
        if has_custom:
            # Command-entered custom value wins; do nothing.
            return
        # Replace any previous entries for this habit with the default token
        day_tasks[:] = [t for t in day_tasks if _strip_name(t) != habit] + [default_token]
        save_store(data)
        await _log(bot, payload.guild_id, f"✅ <@{payload.user_id}> checked **{habit}** for **{target_date}**.")
    else:
        # Remove only the default token; keep custom values
        if default_token in day_tasks and not has_custom:
            day_tasks.remove(default_token)
            save_store(data)
            await _log(bot, payload.guild_id, f"↩️ <@{payload.user_id}> unchecked **{habit}** for **{target_date}**.")
