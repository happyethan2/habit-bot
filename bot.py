import os
import discord
from discord.ext import commands
from discord import Embed
from dotenv import load_dotenv
from pathlib import Path

import datetime as dt
from datetime import datetime, timezone, timedelta, date

from storage import load, save
from ranks import RANKS
from habits import HABITS
from rank_storage import load as load_group_rank, save as save_group_rank

from helpers import load_meta, current_week_id, display_name_for, get_week_summary, save_meta, evaluate_week
from helpers import LOCAL_TZ


load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DEV_USER_ID = 109596804374360064

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

GROUP_RANK = load_group_rank()
META = load_meta()
DATA = load()

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None       # removes default !help for override
)

# -------- bot commands -------------
@bot.command()
async def checkin(ctx, *args):
    """
    Log one or more habits for today (or a specified weekday).
    Allows default values for minute-based habits.

    Usage examples:
      !checkin meditation
      !checkin meditation 45
      !checkin reading 20 exercise
      !checkin walking 45 friday
    """
    if not args:
        return await ctx.reply("Try `!checkin meditation` or `!checkin reading 20 exercise`")

    # 1️⃣ Optional day-of-week override
    days = {d.lower(): i for i, d in enumerate(
        ["Monday","Tuesday","Wednesday","Thursday",
         "Friday","Saturday","Sunday"]
    )}
    override = None
    if args[-1].lower() in days:
        override = args[-1].lower()
        args = args[:-1]
    if not args:
        return await ctx.reply("You need at least one habit, e.g. `!checkin meditation`")

    # 2️⃣ Enforce only unlocked habits
    current_rank = load_group_rank()
    allowed = {t["habit"] for r in RANKS[:current_rank] for t in r["tasks"]}
    for arg in args:
        name = arg.lower()
        if name in HABITS and name not in allowed:
            levels = [rk["level"] for rk in RANKS if any(t["habit"] == name for t in rk["tasks"])]
            req = next(rk for rk in RANKS if rk["level"] == min(levels))
            return await ctx.reply(
                f"🚫 You can’t log **{name}** yet — it unlocks at "
                f"Rank {req['level']} ({req['name'].title()})."
            )

    # 3️⃣ Parse <habit> [value] pairs, with defaults
    parsed = []
    i = 0
    while i < len(args):
        name = args[i].lower()
        cfg = HABITS.get(name)
        if not cfg:
            return await ctx.reply(f"Unrecognised habit: {name}")

        if cfg["unit"] == "minutes":
            minutes = cfg.get("min", 0)
            # consume explicit number if provided
            if i + 1 < len(args) and args[i+1].isdigit():
                minutes = int(args[i+1])
                i += 1
            if minutes < cfg.get("min", 0):
                return await ctx.reply(f"{name} must be ≥ {cfg['min']} min.")
            if cfg.get("max") is not None and minutes > cfg["max"]:
                return await ctx.reply(f"{name} cannot exceed {cfg['max']} minutes per day.")
            parsed.append(f"{name}:{minutes}")
            i += 1

        elif cfg["unit"] == "bool":
            parsed.append(name)
            i += 1

        else:
            return await ctx.reply(f"Config error for habit: {name}")

    # 4️⃣ Determine which date to record
    if override:
        mon = date.fromisoformat(current_week_id())
        day_date = mon + timedelta(days=days[override])
    else:
        day_date = datetime.now(LOCAL_TZ).date()
    day_iso = day_date.isoformat()

    # 5️⃣ Merge into storage
    uid = str(ctx.author.id)
    week = current_week_id()
    user_days = DATA.setdefault(week, {}).setdefault(uid, {})
    existing = user_days.get(day_iso, [])
    to_replace = {tok.split(':',1)[0] for tok in parsed}
    filtered = [tok for tok in existing if tok.split(':',1)[0] not in to_replace]
    user_days[day_iso] = filtered + parsed
    save(DATA)

    # 6️⃣ Build a cleaner embed with “–” bullets
    lines = []
    for tok in parsed:
        name, *val = tok.split(":")
        label = name.capitalize()
        if HABITS[name]["unit"] == "minutes":
            unit_lbl = "pages" if name == "reading" else "min"
            lines.append(f"– **{label}:** {val[0]} {unit_lbl}")
        else:
            lines.append(f"– **{label}**")

    human_date = day_date.strftime("%A, %d %b %Y")
    embed = Embed(
        title="✅ Check-in Recorded",
        description=f"**{ctx.author.display_name}** logged on **{human_date}**",
        colour=0x2ecc71
    )
    embed.add_field(name="📝 Activities", value="\n".join(lines), inline=False)
    await ctx.send(embed=embed)


@bot.command()
async def progress(ctx, member: commands.MemberConverter = None):
    """
    Show a member’s progress for this week.
    """
    global DATA
    DATA = load()
    summary, week = get_week_summary()
    target = member or ctx.author
    uid    = str(target.id)

    if uid not in summary:
        return await ctx.reply(f"No check-ins for {target.display_name} this week.")

    habits_done = summary[uid]

    # Determine which habits are unlocked
    current_rank = load_group_rank()
    unlocked = []
    for r in RANKS[:current_rank]:
        for t in r["tasks"]:
            if t["habit"] not in unlocked:
                unlocked.append(t["habit"])

    # Compute weekly targets per habit from RANKS (days-based tasks override default)
    weekly_targets = {}
    for h in unlocked:
        day_targets = []
        for r in RANKS[:current_rank]:
            for t in r["tasks"]:
                if t["habit"] == h and t["target"].endswith("days"):
                    try:
                        val = int(t["target"].rstrip("days"))
                        day_targets.append(val)
                    except ValueError:
                        pass
        if day_targets:
            weekly_targets[h] = max(day_targets)
        else:
            weekly_targets[h] = HABITS[h].get("weekly_target", 7)

    # Overall percentage
    total_done = sum(min(habits_done.get(h, 0), weekly_targets[h]) for h in unlocked)
    total_target = sum(weekly_targets[h] for h in unlocked)
    pct = (total_done / total_target * 100) if total_target else 100
    pct_str = f"{pct:.1f}%"

    # Progress bars
    BAR_LEN = 14
    def make_bar(done, targ):
        if targ <= 0:
            return "░" * BAR_LEN
        filled = round(done / targ * BAR_LEN)
        filled = max(0, min(BAR_LEN, filled))
        return "█" * filled + "░" * (BAR_LEN - filled)

    max_len = max(len(h) for h in unlocked) if unlocked else 0
    lines = []
    for h in unlocked:
        done = habits_done.get(h, 0)
        targ = weekly_targets[h]
        bar = make_bar(done, targ)
        lines.append(f"{h.ljust(max_len)}  {bar}  {done}/{targ}")

    # Build and send embed
    week_dt = dt.date.fromisoformat(week)
    table = "```" + "\n".join(lines) + "```"
    embed = Embed(
        title=f"📊 Progress for {target.display_name}",
        description=f"Week of {week_dt:%A %d %b %Y}\n➡️ Total: **{pct_str}**",
        colour=0x3498db
    )
    embed.add_field(name="📋 Habit Breakdown", value=table, inline=False)
    await ctx.send(embed=embed)


@bot.command()
async def ranks(ctx):
    """
    Show all ranks in one column, using country-flag emojis ordered
    from lower to higher GDP per capita.
    """
    current = load_group_rank()

    FLAGS = {
        1:  "🇮🇳", 2:  "🇳🇬", 3:  "🇮🇩", 4:  "🇧🇷", 5:  "🇿🇦",
        6:  "🇲🇽", 7:  "🇹🇷", 8:  "🇨🇳", 9:  "🇷🇺", 10: "🇰🇷",
        11: "🇩🇪", 12: "🇯🇵", 13: "🇺🇸",
    }

    lines = []
    for r in RANKS:
        flag  = FLAGS.get(r["level"], "")
        tasks = ", ".join(f"{t['habit'].capitalize()}: {t['target']}" for t in r["tasks"])
        lines.append(f"{flag} **{r['level']}. {r['name'].title()}** — {tasks}")

    embed = Embed(title="🏅 Rank List", colour=0x00aaff)
    embed.add_field(name="\u200b", value="\n".join(lines), inline=False)

    curr = next(r for r in RANKS if r["level"] == current)
    # current rank + its tasks
    embed.add_field(
        name="🎖 Current Group Rank",
        value=f"**{current}. {curr['name'].title()}**",
        inline=False
    )
    embed.add_field(
        name="🏁 Current Challenge",
        value=", ".join(f"{t['habit'].capitalize()}: {t['target']}" for t in curr["tasks"]),
        inline=False
    )

    embed.set_footer(text="Use !rank for details or !nextchallenge to preview what’s next.")
    await ctx.send(embed=embed)


@bot.command()
async def rank(ctx):
    """
    Show the group’s current rank and its full cumulative challenge,
    de-duplicating any “upgraded” habit volume.
    """
    level = load_group_rank()
    rank_entry = next((r for r in RANKS if r["level"] == level), None)
    if not rank_entry:
        return await ctx.reply("No rank data available.")

    # build a map of habit → latest target
    task_map = {}
    for r in RANKS[:level]:
        for t in r["tasks"]:
            task_map[t["habit"]] = t["target"]

    # preserve appearance order, but only once each habit
    seen = set()
    lines = []
    for r in RANKS[:level]:
        for t in r["tasks"]:
            h = t["habit"]
            if h in task_map and h not in seen:
                lines.append(f"- **{h.capitalize()}:** {task_map[h]}")
                seen.add(h)

    embed = Embed(
        title=f"🎖 Current Group Rank: {level} – {rank_entry['name'].title()}",
        colour=0x00aaff
    )
    embed.add_field(
        name="🗒️ Current Challenge",
        value="Complete all of the following:\n" + "\n".join(lines),
        inline=False
    )
    await ctx.send(embed=embed)


@bot.command()
async def rankup(ctx, target: str = None):
    """
    Promote the group’s rank.
    Usage:
      !rankup               → bump up by 1
      !rankup <level>       → set rank to that level
      !rankup <name>        → set rank to that named rank
    """
    old = load_group_rank()

    # determine new level
    if target is None:
        new = old + 1
    else:
        try:
            lvl = int(target)
        except ValueError:
            match = next((r for r in RANKS if r["name"].lower() == target.lower()), None)
            if not match:
                return await ctx.reply(f"🚫 Invalid rank: `{target}`")
            lvl = match["level"]
        new = lvl

    # clamp within bounds
    new = max(1, min(new, len(RANKS)))
    if new <= old:
        return await ctx.reply(f"🚫 Cannot rank up to {new} (current is {old}). Use `!rankdown` to go down.")

    # persist new rank
    save_group_rank(new)

    # build cumulative task list up to the new rank, de-duplicating by habit
    task_map = {}
    for r in RANKS[:new]:
        for t in r["tasks"]:
            task_map[t["habit"]] = t["target"]

    seen = set()
    lines = []
    for r in RANKS[:new]:
        for t in r["tasks"]:
            h = t["habit"]
            if h in task_map and h not in seen:
                lines.append(f"- **{h.capitalize()}:** {task_map[h]}")
                seen.add(h)

    # announce
    rank = next(r for r in RANKS if r["level"] == new)
    embed = Embed(
        title=f"🎉 Group Promoted to Rank {new}: {rank['name'].title()}",
        colour=0x2ecc71
    )
    embed.add_field(
        name="🆕 New Cumulative Challenge",
        value="Complete all of the following:\n" + "\n".join(lines),
        inline=False
    )
    await ctx.send(embed=embed)


@bot.command()
async def rankdown(ctx, target: str = None):
    """
    Demote the group’s rank.
    Usage:
      !rankdown           → drop down by 1
      !rankdown <level>   → set rank to that level
      !rankdown <name>    → set rank to that named rank
    """
    old = load_group_rank()

    # determine new level
    if target is None:
        new = old - 1
    else:
        try:
            lvl = int(target)
        except ValueError:
            match = next((r for r in RANKS if r["name"].lower() == target.lower()), None)
            if not match:
                return await ctx.reply(f"🚫 Invalid rank: `{target}`")
            lvl = match["level"]
        new = lvl

    # clamp within bounds
    new = max(1, min(new, len(RANKS)))
    if new >= old:
        return await ctx.reply(f"🚫 Cannot rank down to {new} (current is {old}). Use `!rankup` to go up.")

    # persist new rank
    save_group_rank(new)

    # build cumulative task list up to the new rank, de-duplicating by habit
    task_map = {}
    for r in RANKS[:new]:
        for t in r["tasks"]:
            task_map[t["habit"]] = t["target"]

    seen = set()
    lines = []
    for r in RANKS[:new]:
        for t in r["tasks"]:
            h = t["habit"]
            if h in task_map and h not in seen:
                lines.append(f"- **{h.capitalize()}:** {task_map[h]}")
                seen.add(h)

    # announce
    rank = next(r for r in RANKS if r["level"] == new)
    embed = Embed(
        title=f"⚠️ Group Demoted to Rank {new}: {rank['name'].title()}",
        colour=0xe74c3c
    )
    embed.add_field(
        name="🔽 Current Challenge",
        value="Complete all of the following:\n" + "\n".join(lines),
        inline=False
    )
    await ctx.send(embed=embed)


@bot.command()
async def history(ctx, *args):
    """
    Show a member’s check-in history.

    Usage:
      !history               → full week for you
      !history today         → today’s entries for you
      !history monday        → entries this week on Monday for you
    """
    data = load()
    # Determine target user (always you in this variant)
    member = ctx.author
    uid = str(member.id)

    # Day-of-week mapping
    days = {d.lower(): i for i, d in enumerate(
        ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    )}

    # Handle "today" or specific weekday
    if args:
        key = args[0].lower()
        # Today's history
        if key == 'today':
            day_date = datetime.now(LOCAL_TZ).date()
            week_id = current_week_id()
            week_data = data.get(week_id, {})
            user_days = week_data.get(uid, {})
            tokens = user_days.get(day_date.isoformat(), [])
            if not tokens:
                return await ctx.reply(f"No check-ins for today ({day_date.strftime('%A %d %b')}).")
            # Build embed
            embed = Embed(
                title=f"🕑 Today's History for {member.display_name}",
                description=f"{day_date.strftime('%A %d %b %Y')}",
                colour=0x9b59b6
            )
            lines = []
            for tok in tokens:
                name, *val = tok.split(":")
                if val:
                    unit = HABITS[name]["unit"]
                    label = "pages" if name == "reading" else "min"
                    lines.append(f"- **{name.capitalize()}:** {val[0]} {label}")
                else:
                    lines.append(f"- **{name.capitalize()}**")
            embed.add_field(name=day_date.strftime('%A %d %b'), value="\n".join(lines), inline=False)
            return await ctx.send(embed=embed)

        # Specific weekday
        if key in days:
            offset = days[key]
            mon = date.fromisoformat(current_week_id())
            day_date = mon + timedelta(days=offset)
            week_id = current_week_id()
            week_data = data.get(week_id, {})
            user_days = week_data.get(uid, {})
            tokens = user_days.get(day_date.isoformat(), [])
            if not tokens:
                return await ctx.reply(f"No check-ins for {key.title()} ({day_date.strftime('%d %b')}).")
            # Build embed
            embed = Embed(
                title=f"🕑 {key.title()}'s History for {member.display_name}",
                description=f"{day_date.strftime('%A %d %b %Y')}",
                colour=0x9b59b6
            )
            lines = []
            for tok in tokens:
                name, *val = tok.split(":")
                if val:
                    unit = HABITS[name]["unit"]
                    label = "pages" if name == "reading" else "min"
                    lines.append(f"- **{name.capitalize()}:** {val[0]} {label}")
                else:
                    lines.append(f"- **{name.capitalize()}**")
            embed.add_field(name=day_date.strftime('%A %d %b'), value="\n".join(lines), inline=False)
            return await ctx.send(embed=embed)

    # Default: full weekly history
    week_id = current_week_id()
    week_dt = date.fromisoformat(week_id)
    week_data = data.get(week_id, {})
    user_days = week_data.get(uid, {})
    if not user_days:
        return await ctx.reply(f"No check-ins for week of {week_dt:%A %d %b %Y}.")

    embed = Embed(
        title=f"🕑 History for {member.display_name}",
        description=f"Week of {week_dt:%A %d %b %Y}",
        colour=0x9b59b6
    )
    # One field per day
    for day_iso in sorted(user_days):
        d = date.fromisoformat(day_iso)
        day_str = d.strftime("%A %d %b")
        lines = []
        for tok in user_days[day_iso]:
            name, *val = tok.split(":")
            if val:
                unit = HABITS[name]["unit"]
                label = "pages" if name == "reading" else "min"
                lines.append(f"- **{name.capitalize()}:** {val[0]} {label}")
            else:
                lines.append(f"- **{name.capitalize()}**")
        embed.add_field(name=day_str, value="\n".join(lines), inline=False)
    await ctx.send(embed=embed)


@bot.command()
async def delete(ctx, *args):
    """
    Delete a logged habit for a given day.
    Usage:
      !delete meditation         → deletes today’s meditation entry
      !delete meditation Friday  → deletes Friday’s meditation entry
    """
    # Reload data
    global DATA
    DATA = load()

    # 1️⃣ parse optional DOTW override
    days = {d.lower(): i for i, d in enumerate(
        ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    )}
    override = None
    if args and args[-1].lower() in days:
        override = args[-1].lower()
        args = args[:-1]

    if not args:
        return await ctx.reply("Please specify the habit to delete, e.g. `!delete meditation`.")

    # habit key
    habit_key = " ".join(args).lower()
    if habit_key not in HABITS:
        return await ctx.reply(f"Unrecognized habit: {habit_key}")

    # Determine date
    if override:
        mon = date.fromisoformat(current_week_id())
        day_date = mon + timedelta(days=days[override])
    else:
        day_date = datetime.now(LOCAL_TZ).date()
    day_iso = day_date.isoformat()
    human_date = day_date.strftime("%A, %d %b")

    # Locate entry
    week = current_week_id()
    uid = str(ctx.author.id)
    week_data = DATA.get(week, {})
    user_days = week_data.get(uid, {})

    tokens = user_days.get(day_iso, [])
    # filter out this habit
    filtered = [tok for tok in tokens if tok.split(":",1)[0] != habit_key]

    if len(filtered) == len(tokens):
        return await ctx.reply(f"No `{habit_key}` entry found on {human_date}.")

    # Save back
    if filtered:
        user_days[day_iso] = filtered
    else:
        user_days.pop(day_iso)
    save(DATA)

    # Confirm
    embed = Embed(
        title="🗑 Entry Deleted",
        description=f"Removed **{habit_key}** on {human_date}.",
        colour=0xe67e22
    )
    await ctx.send(embed=embed)


@bot.command()
async def nextchallenge(ctx):
    """
    Preview the next rank’s challenge(s): supports multiple tasks.
    """
    next_level = load_group_rank() + 1
    if next_level > len(RANKS):
        return await ctx.reply("🎉 The group is already at the highest rank!")

    nr = next(r for r in RANKS if r["level"] == next_level)
    tasks = "\n".join(f"- **{t['habit'].capitalize()}:** {t['target']}" for t in nr["tasks"])

    embed = Embed(
        title=f"🔮 Next Challenge: Rank {next_level} – {nr['name'].title()}",
        colour=0x8e44ad
    )
    embed.add_field(
        name="Tasks to Complete",
        value=f"Complete all of the following:\n{tasks}",
        inline=False
    )
    await ctx.send(embed=embed)


@bot.command()
async def leaderboard(ctx):
    """
    Show cumulative totals for all minute-based habits in a mobile-friendly format.
    """
    from collections import defaultdict

    # 1️⃣ Aggregate totals across every week
    data = load()  # your storage.load import
    totals = defaultdict(lambda: defaultdict(int))

    for week_data in data.values():
        for uid, days in week_data.items():
            for tokens in days.values():
                for tok in tokens:
                    name, *val = tok.split(":", 1)
                    cfg = HABITS.get(name)
                    if cfg and cfg["unit"] == "minutes":
                        amt = int(val[0])
                        totals[uid][name] += amt

    # 2️⃣ Build an embed with one field per user
    embed = Embed(
        title="🏆 Leaderboard",
        description="Because statistics are awesome...",
        colour=0xf1c40f
    )

    # Sort users by total sum descending
    def user_sum(hdict): 
        return sum(hdict.values())

    for uid, habit_dict in sorted(totals.items(), key=lambda kv: -user_sum(kv[1])):
        display = await display_name_for(uid, ctx)
        lines = []
        for habit, amount in habit_dict.items():
            unit = "pages" if habit == "reading" else "min"
            lines.append(f"– **{habit.capitalize()}:** {amount} {unit}")
        # If someone has no minute-based entries, skip
        if not lines:
            continue

        embed.add_field(
            name=display,
            value="\n".join(lines),
            inline=False
        )

    await ctx.send(embed=embed)
    

@bot.command(name="help")
async def help_command(ctx):
    """
    Show all available commands and their usage.
    """
    embed = Embed(
        title="📋 HabitBot Commands",
        description="Here’s what you can do with HabitBot:",
        colour=0x95a5a6
    )

    embed.add_field(
        name="🔹 `!ping`",
        value="Check bot responsiveness.",
        inline=False
    )

    embed.add_field(
        name="🔹 `!checkin <habit> [value] [habit] [value] ... [weekday]`",
        value=(
            "Log one or more habits in a single command (all on the same day).\n"
            "• `!checkin meditation`\n"
            "• `!checkin meditation 45`\n"
            "• `!checkin reading 20 exercise`\n"
            "• `!checkin walking 30 meditation 45 friday`"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 `!progress [@User]`",
        value=(
            "Show a single member’s progress for the current week.\n"
            "• `!progress` → your progress\n"
            "• `!progress @Friend` → Friend’s progress"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 `!history [@User] [today|<weekday>]`",
        value=(
            "Show check-ins for you or another member.\n"
            "• `!history` → entire current week\n"
            "• `!history today` → today’s entries\n"
            "• `!history monday` → Monday’s entries this week\n"
            "• `!history @Friend friday` → Friend’s Friday entries"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 `!delete <habit> [weekday]`",
        value=(
            "Remove an entry you logged.\n"
            "• `!delete meditation`\n"
            "• `!delete bedtime Saturday`"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 `!nextchallenge`",
        value="Preview the upcoming rank’s cumulative challenge.",
        inline=False
    )

    embed.add_field(
        name="🔹 `!rank`",
        value="Show the group’s current rank and its full cumulative challenge.",
        inline=False
    )

    embed.add_field(
        name="🔹 `!ranks`",
        value="List all ranks and their tasks in one column.",
        inline=False
    )

    embed.add_field(
        name="🔹 `!rankup [level|name]`",
        value=(
            "Promote the group’s rank by 1, or jump to a specific rank.\n"
            "• `!rankup` → bump up by 1\n"
            "• `!rankup 5` → set rank to level 5\n"
            "• `!rankup platinum` → jump to Platinum"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 `!rankdown [level|name]`",
        value=(
            "Demote the group’s rank by 1, or drop to a specific rank.\n"
            "• `!rankdown` → drop down by 1\n"
            "• `!rankdown 2` → set rank to level 2\n"
            "• `!rankdown bronze` → jump to Bronze"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 `!leaderboard`",
        value="Show cumulative totals for all minute-based habits across all weeks.",
        inline=False
    )

    embed.set_footer(text="Use !help to see this list any time.")
    await ctx.send(embed=embed)


@bot.command()
async def forcecheckin(ctx, member: commands.MemberConverter, *args):
    """
    [DEV ONLY] Force‐log one or more habits for another user.
    Usage: !forcecheckin @User <habit> [value] [habit] [value] ... [weekday]
    """
    # only you can run this
    if ctx.author.id != DEV_USER_ID:
        return

    if not args:
        return await ctx.send("Usage: `!forcecheckin @User <habit> [value] ... [weekday]`")

    # 1️⃣ Optional day‐of‐week override
    days = {d.lower(): i for i, d in enumerate(
        ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    )}
    override = None
    if args[-1].lower() in days:
        override = args[-1].lower()
        args = args[:-1]
    if not args:
        return await ctx.send("You must specify at least one habit after the user mention.")

    # 2️⃣ Parse & validate exactly as in !checkin
    parsed = []
    i = 0
    while i < len(args):
        name = args[i].lower()
        cfg  = HABITS.get(name)
        if not cfg:
            return await ctx.send(f"Unrecognised habit: `{name}`")

        if cfg["unit"] == "minutes":
            # next token may be a number, else default
            minutes = cfg.get("min", 0)
            if i + 1 < len(args) and args[i+1].isdigit():
                minutes = int(args[i+1])
                i += 1
            if minutes < cfg.get("min", 0):
                return await ctx.send(f"`{name}` must be ≥ {cfg['min']} min.")
            if cfg.get("max") is not None and minutes > cfg["max"]:
                return await ctx.send(f"`{name}` cannot exceed {cfg['max']} min.")
            parsed.append(f"{name}:{minutes}")
            i += 1

        elif cfg["unit"] == "bool":
            parsed.append(name)
            i += 1

        else:
            return await ctx.send(f"Config error for habit: `{name}`")

    # 3️⃣ Determine date
    if override:
        mon      = date.fromisoformat(current_week_id())
        day_date = mon + timedelta(days=days[override])
    else:
        day_date = datetime.now(LOCAL_TZ).date()
    day_iso    = day_date.isoformat()

    # 4️⃣ Write into storage as if they ran !checkin themselves
    uid        = str(member.id)
    week       = current_week_id()
    user_days  = DATA.setdefault(week, {}).setdefault(uid, {})
    existing   = user_days.get(day_iso, [])
    to_replace = {tok.split(":",1)[0] for tok in parsed}
    filtered   = [tok for tok in existing if tok.split(":",1)[0] not in to_replace]
    user_days[day_iso] = filtered + parsed
    save(DATA)

    # 5️⃣ Minimal feedback
    short = []
    for tok in parsed:
        h, *v = tok.split(":")
        if v:
            short.append(f"{h}:{v[0]}")
        else:
            short.append(h)
    human_date = day_date.strftime("%d %b")
    await ctx.send(f"successfully forced for {member.display_name} on {human_date}: " +
                   ", ".join(short))


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id: {bot.user.id})")

# simple ping-pong sanity check
@bot.command()
async def ping(ctx):
    await ctx.send("pong!")


if __name__ == "__main__":
    print("Loaded token is:", TOKEN[:10] + "...")  # should show first chars, not 'None'
    bot.run(TOKEN)