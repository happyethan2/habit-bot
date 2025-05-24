import os
import discord
from discord.ext import commands
from discord import app_commands
from discord import Embed
from dotenv import load_dotenv
from pathlib import Path
from typing import List

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


class HabitBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
    
    async def setup_hook(self):
        await self.tree.sync()
        print(f"Synced {len(self.tree.get_commands())} command(s)")

bot = HabitBot()

# Load data AFTER creating the bot
GROUP_RANK = load_group_rank()
META = load_meta()
DATA = load()

# ------ channel restrictions -------
CHANNEL_CONFIG = {
    "check-ins": {
        "allowed_slash": ["checkin"],
        "allowed_traditional": ["checkin", "forcecheckin"],
        "message": "Please use #other-commands for non-checkin commands!"
    },
    "chat": {
        "allowed_slash": [],
        "allowed_traditional": [],
        "message": "Bot commands aren't allowed here. Try #check-ins or #other-commands!"
    },
    "other-commands": {
        "denied_slash": ["checkin"],
        "denied_traditional": ["checkin", "forcecheckin"],
        "message": "Please use #check-ins for checkin commands!"
    }
}


@bot.check
async def channel_check(ctx):
    """Channel check for traditional ! commands."""
    # Skip DMs and unconfigured channels
    if ctx.guild is None or ctx.channel.name not in CHANNEL_CONFIG:
        return True
    
    config = CHANNEL_CONFIG[ctx.channel.name]
    command_name = ctx.command.name if ctx.command else None
    
    # Check allowed list
    if "allowed_traditional" in config:
        if command_name in config["allowed_traditional"]:
            return True
        else:
            await ctx.send(f"❌ {config.get('message', 'Command not allowed in this channel.')}")
            return False
    
    # Check denied list
    if "denied_traditional" in config:
        if command_name in config["denied_traditional"]:
            await ctx.send(f"❌ {config.get('message', 'Command not allowed in this channel.')}")
            return False
        else:
            return True
    
    return True


def slash_channel_check():
    """Decorator for slash command channel restrictions."""
    async def predicate(interaction: discord.Interaction) -> bool:
        # Skip DMs and unconfigured channels
        if interaction.guild is None or interaction.channel.name not in CHANNEL_CONFIG:
            return True
        
        config = CHANNEL_CONFIG[interaction.channel.name]
        # Get command name from interaction data
        command_name = interaction.data.get("name") if interaction.data else None
        
        # Check allowed list
        if "allowed_slash" in config:
            if command_name in config["allowed_slash"]:
                return True
            else:
                await interaction.response.send_message(
                    f"❌ {config.get('message', 'Command not allowed in this channel.')}",
                    ephemeral=True
                )
                return False
        
        # Check denied list
        if "denied_slash" in config:
            if command_name in config["denied_slash"]:
                await interaction.response.send_message(
                    f"❌ {config.get('message', 'Command not allowed in this channel.')}",
                    ephemeral=True
                )
                return False
            else:
                return True
        
        return True
    
    return app_commands.check(predicate)


# -------- bot commands -------------
@bot.tree.command(name="checkin", description="Log your daily habits")
@slash_channel_check()
@app_commands.describe(
    habits="HABITS: meditation, reading, journaling, porn, exercise, walking, diet, bedtime, pmo, digitaldetox, streaming",
    day="optional: log for a different DOTW"
)
@app_commands.choices(day=[
    app_commands.Choice(name="today", value="today"),
    app_commands.Choice(name="monday", value="monday"),
    app_commands.Choice(name="Tuetuesdaysday", value="tuesday"),
    app_commands.Choice(name="wednesday", value="wednesday"),
    app_commands.Choice(name="thursday", value="thursday"),
    app_commands.Choice(name="friday", value="friday"),
    app_commands.Choice(name="saturday", value="saturday"),
    app_commands.Choice(name="sunday", value="sunday"),
])
async def checkin(interaction: discord.Interaction, habits: str, day: str = "today"):
    # Parse habits string
    args = habits.lower().split()
    
    # Reuse existing parsing logic but adapted for slash commands
    current_rank = load_group_rank()
    allowed = {t["habit"] for r in RANKS[:current_rank] for t in r["tasks"]}
    
    # Check for locked habits
    for arg in args:
        name = arg
        if name in HABITS and name not in allowed:
            levels = [rk["level"] for rk in RANKS if any(t["habit"] == name for t in rk["tasks"])]
            req = next(rk for rk in RANKS if rk["level"] == min(levels))
            return await interaction.response.send_message(
                f"🚫 You can't log **{name}** yet — it unlocks at "
                f"Rank {req['level']} ({req['name'].title()}).",
                ephemeral=True
            )
    
    # Parse habit/value pairs
    parsed = []
    i = 0
    while i < len(args):
        name = args[i]
        cfg = HABITS.get(name)
        if not cfg:
            return await interaction.response.send_message(
                f"Unrecognised habit: {name}", 
                ephemeral=True
            )
        
        if cfg["unit"] == "minutes":
            minutes = cfg.get("min", 0)
            if i + 1 < len(args) and args[i+1].isdigit():
                minutes = int(args[i+1])
                i += 1
            if minutes < cfg.get("min", 0):
                return await interaction.response.send_message(
                    f"{name} must be ≥ {cfg['min']} min.", 
                    ephemeral=True
                )
            if cfg.get("max") is not None and minutes > cfg["max"]:
                return await interaction.response.send_message(
                    f"{name} cannot exceed {cfg['max']} minutes per day.",
                    ephemeral=True
                )
            parsed.append(f"{name}:{minutes}")
            i += 1
        elif cfg["unit"] == "bool":
            parsed.append(name)
            i += 1
    
    # Determine date
    if day != "today":
        days = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, 
                "friday": 4, "saturday": 5, "sunday": 6}
        mon = date.fromisoformat(current_week_id())
        day_date = mon + timedelta(days=days[day.lower()])
    else:
        day_date = datetime.now(LOCAL_TZ).date()
    
    day_iso = day_date.isoformat()
    
    # Save to storage
    uid = str(interaction.user.id)
    week = current_week_id()
    user_days = DATA.setdefault(week, {}).setdefault(uid, {})
    existing = user_days.get(day_iso, [])
    to_replace = {tok.split(':',1)[0] for tok in parsed}
    filtered = [tok for tok in existing if tok.split(':',1)[0] not in to_replace]
    user_days[day_iso] = filtered + parsed
    save(DATA)
    
    # Build response embed
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
        description=f"**{interaction.user.display_name}** logged on **{human_date}**",
        colour=0x2ecc71
    )
    embed.add_field(name="📝 Activities", value="\n".join(lines), inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="progress", description="View weekly progress")
@slash_channel_check()
@app_commands.describe(member="View another member's progress (optional)")
async def progress(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.defer()  # For longer operations
    
    global DATA
    DATA = load()
    summary, week = get_week_summary()
    target = member or interaction.user
    uid = str(target.id)
    
    if uid not in summary:
        return await interaction.followup.send(
            f"No check-ins for {target.display_name} this week."
        )

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
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="ranks", description="Show all ranks and their challenges")
@slash_channel_check()
async def ranks(interaction: discord.Interaction):
    current = load_group_rank()
    
    FLAGS = {
        1: "🇮🇳", 2: "🇳🇬", 3: "🇮🇩", 4: "🇧🇷", 5: "🇿🇦",
        6: "🇲🇽", 7: "🇹🇷", 8: "🇨🇳", 9: "🇷🇺", 10: "🇰🇷",
        11: "🇩🇪", 12: "🇯🇵", 13: "🇺🇸",
    }
    
    lines = []
    for r in RANKS:
        flag = FLAGS.get(r["level"], "")
        tasks = ", ".join(f"{t['habit'].capitalize()}: {t['target']}" for t in r["tasks"])
        lines.append(f"{flag} **{r['level']}. {r['name'].title()}** — {tasks}")
    
    embed = Embed(title="🏅 Rank List", colour=0x00aaff)
    embed.add_field(name="\u200b", value="\n".join(lines), inline=False)
    
    curr = next(r for r in RANKS if r["level"] == current)
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
    
    embed.set_footer(text="Use /rank for details or /nextchallenge to preview what's next.")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="rank", description="Show current group rank and challenge")
@slash_channel_check()
async def rank(interaction: discord.Interaction):
    level = load_group_rank()
    rank_entry = next((r for r in RANKS if r["level"] == level), None)
    if not rank_entry:
        return await interaction.response.send_message(
            "No rank data available.",
            ephemeral=True
        )
    
    # Build a map of habit → latest target (de-duplicating upgrades)
    task_map = {}
    for r in RANKS[:level]:
        for t in r["tasks"]:
            task_map[t["habit"]] = t["target"]
    
    # Preserve appearance order, but only once each habit
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
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="rankup", description="Promote the group's rank")
@slash_channel_check()
@app_commands.describe(target="Level number or rank name to promote to")
@app_commands.default_permissions(administrator=True)
async def rankup(interaction: discord.Interaction, target: str = None):
    old = load_group_rank()
    
    # Determine new level
    if target is None:
        new = old + 1
    else:
        try:
            lvl = int(target)
        except ValueError:
            match = next((r for r in RANKS if r["name"].lower() == target.lower()), None)
            if not match:
                return await interaction.response.send_message(
                    f"🚫 Invalid rank: `{target}`",
                    ephemeral=True
                )
            lvl = match["level"]
        new = lvl
    
    # Clamp and validate
    new = max(1, min(new, len(RANKS)))
    if new <= old:
        return await interaction.response.send_message(
            f"🚫 Cannot rank up to {new} (current is {old}). Use /rankdown to go down.",
            ephemeral=True
        )
    
    save_group_rank(new)
    
    # Build task list
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
    await interaction.response.send_message(embed=embed)

    # update command descriptions and re-sync
    await update_checkin_description()
    await bot.tree.sync()


@bot.tree.command(name="rankdown", description="Demote the group's rank")
@slash_channel_check()
@app_commands.describe(target="Level number or rank name to demote to")
@app_commands.default_permissions(administrator=True)
async def rankdown(interaction: discord.Interaction, target: str = None):
    old = load_group_rank()
    
    # Determine new level
    if target is None:
        new = old - 1
    else:
        try:
            lvl = int(target)
        except ValueError:
            match = next((r for r in RANKS if r["name"].lower() == target.lower()), None)
            if not match:
                return await interaction.response.send_message(
                    f"🚫 Invalid rank: `{target}`",
                    ephemeral=True
                )
            lvl = match["level"]
        new = lvl
    
    # Clamp within bounds
    new = max(1, min(new, len(RANKS)))
    if new >= old:
        return await interaction.response.send_message(
            f"🚫 Cannot rank down to {new} (current is {old}). Use /rankup to go up.",
            ephemeral=True
        )
    
    # Persist new rank
    save_group_rank(new)
    
    # Build cumulative task list
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
    
    # Announce
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
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="history", description="View check-in history")
@slash_channel_check()
@app_commands.describe(
    member="View another member's history (optional)",
    time_filter="Filter by specific day"
)
@app_commands.choices(time_filter=[
    app_commands.Choice(name="Today", value="today"),
    app_commands.Choice(name="Full Week", value="all"),
    app_commands.Choice(name="Monday", value="monday"),
    app_commands.Choice(name="Tuesday", value="tuesday"),
    app_commands.Choice(name="Wednesday", value="wednesday"),
    app_commands.Choice(name="Thursday", value="thursday"),
    app_commands.Choice(name="Friday", value="friday"),
    app_commands.Choice(name="Saturday", value="saturday"),
    app_commands.Choice(name="Sunday", value="sunday"),
])
async def history(interaction: discord.Interaction, member: discord.Member = None, time_filter: str = "all"):
    data = load()
    
    # Determine target member
    target = member or interaction.user
    
    # Load this week's data
    week_id = current_week_id()
    week_data = data.get(week_id, {})
    user_days = week_data.get(str(target.id), {})
    
    if not user_days:
        return await interaction.response.send_message(
            f"No check-ins for {target.display_name}.",
            ephemeral=True
        )
    
    # Build the subset of days to show
    entries = {}
    days_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                "friday": 4, "saturday": 5, "sunday": 6}
    
    if time_filter == "today":
        today = datetime.now(LOCAL_TZ).date()
        iso = today.isoformat()
        if iso in user_days:
            entries[iso] = user_days[iso]
        else:
            return await interaction.response.send_message(
                f"No check-ins for {target.display_name} today.",
                ephemeral=True
            )
    elif time_filter in days_map:
        mon = date.fromisoformat(week_id)
        target_dt = mon + timedelta(days=days_map[time_filter])
        iso = target_dt.isoformat()
        if iso in user_days:
            entries[iso] = user_days[iso]
        else:
            return await interaction.response.send_message(
                f"No check-ins for {target.display_name} on {time_filter.title()}.",
                ephemeral=True
            )
    else:  # "all"
        entries = user_days
    
    # Build embed
    embed = Embed(
        title=f"🕑 History for {target.display_name}",
        description=f"Week of {date.fromisoformat(week_id):%A %d %b %Y}",
        colour=0x9b59b6
    )
    
    for day_iso in sorted(entries):
        d = date.fromisoformat(day_iso)
        day_str = d.strftime("%A %d %b")
        lines = []
        for tok in entries[day_iso]:
            name, *val = tok.split(":")
            cfg = HABITS[name]
            if cfg["unit"] == "minutes":
                unit = "pages" if name == "reading" else "min"
                amt = val[0] if val else cfg.get("min", 0)
                lines.append(f"- **{name.capitalize()}:** {amt} {unit}")
            else:
                lines.append(f"- **{name.capitalize()}**")
        embed.add_field(name=day_str, value="\n".join(lines), inline=False)
    
    await interaction.response.send_message(embed=embed)


async def habit_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    current_rank = load_group_rank()
    allowed = []
    for r in RANKS[:current_rank]:
        for t in r["tasks"]:
            habit = t["habit"]
            if habit not in [h.value for h in allowed]:
                if current.lower() in habit:
                    allowed.append(
                        app_commands.Choice(name=habit.capitalize(), value=habit)
                    )
    return allowed[:25]  # Discord limit

@bot.tree.command(name="delete", description="Delete a logged habit")
@slash_channel_check()
@app_commands.describe(
    habit="The habit to delete",
    day="Day to delete from"
)
@app_commands.autocomplete(habit=habit_autocomplete)
@app_commands.choices(day=[
    app_commands.Choice(name="Today", value="today"),
    app_commands.Choice(name="Monday", value="monday"),
    app_commands.Choice(name="Tuesday", value="tuesday"),
    app_commands.Choice(name="Wednesday", value="wednesday"),
    app_commands.Choice(name="Thursday", value="thursday"),
    app_commands.Choice(name="Friday", value="friday"),
    app_commands.Choice(name="Saturday", value="saturday"),
    app_commands.Choice(name="Sunday", value="sunday"),
])
async def delete(interaction: discord.Interaction, habit: str, day: str = "today"):
    global DATA
    DATA = load()
    
    habit_key = habit.lower()
    if habit_key not in HABITS:
        return await interaction.response.send_message(
            f"Unrecognized habit: {habit_key}",
            ephemeral=True
        )
    
    # Determine date
    if day != "today":
        days_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                    "friday": 4, "saturday": 5, "sunday": 6}
        mon = date.fromisoformat(current_week_id())
        day_date = mon + timedelta(days=days_map[day])
    else:
        day_date = datetime.now(LOCAL_TZ).date()
    
    day_iso = day_date.isoformat()
    human_date = day_date.strftime("%A, %d %b")
    
    # Locate and remove entry
    week = current_week_id()
    uid = str(interaction.user.id)
    week_data = DATA.get(week, {})
    user_days = week_data.get(uid, {})
    
    tokens = user_days.get(day_iso, [])
    filtered = [tok for tok in tokens if tok.split(":",1)[0] != habit_key]
    
    if len(filtered) == len(tokens):
        return await interaction.response.send_message(
            f"No `{habit_key}` entry found on {human_date}.",
            ephemeral=True
        )
    
    if filtered:
        user_days[day_iso] = filtered
    else:
        user_days.pop(day_iso)
    save(DATA)
    
    embed = Embed(
        title="🗑 Entry Deleted",
        description=f"Removed **{habit_key}** on {human_date}.",
        colour=0xe67e22
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="nextchallenge", description="Preview the next rank's challenges")
@slash_channel_check()
async def nextchallenge(interaction: discord.Interaction):
    next_level = load_group_rank() + 1
    if next_level > len(RANKS):
        return await interaction.response.send_message(
            "🎉 The group is already at the highest rank!",
            ephemeral=True
        )
    
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
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="mychallenge", description="Show your current weekly challenges")
@slash_channel_check()
async def mychallenge(interaction: discord.Interaction):
    """Display current challenges organized by daily vs other."""
    current_rank = load_group_rank()
    rank_entry = next((r for r in RANKS if r["level"] == current_rank), None)
    
    if not rank_entry:
        return await interaction.response.send_message(
            "No rank data available.", 
            ephemeral=True
        )
    
    # Build map of all current requirements (latest version of each habit)
    habit_targets = {}
    for r in RANKS[:current_rank]:
        for t in r["tasks"]:
            habit_targets[t["habit"]] = t["target"]
    
    # Categorize habits
    daily_habits = []
    other_habits = []
    
    for habit, target in habit_targets.items():
        cfg = HABITS.get(habit, {})
        
        # Determine if daily
        is_daily = (
            target == "7days" or 
            (cfg.get("unit") == "bool" and cfg.get("weekly_target", 0) == 7)
        )
        
        # Format the display
        if cfg.get("unit") == "minutes":
            if habit == "reading":
                display = f"**{habit}** - {target}"
            else:
                display = f"**{habit}** - {target}"
        elif cfg.get("unit") == "bool":
            display = f"**{habit}** - {target}"
        else:
            display = f"**{habit}** - {target}"
        
        if is_daily:
            daily_habits.append(display)
        else:
            other_habits.append(display)
    
    # Sort for consistent display
    daily_habits.sort()
    other_habits.sort()
    
    embed = Embed(
        title=f"📋 Your Weekly Challenges",
        description=f"**Rank {current_rank}: {rank_entry['name'].title()}**",
        colour=0x00aaff
    )
    
    if daily_habits:
        embed.add_field(
            name="DAILY HABITS (Every Day)",
            value="\n".join(daily_habits),
            inline=False
        )
    
    if other_habits:
        embed.add_field(
            name="\nOTHER HABITS",
            value="\n".join(other_habits),
            inline=False
        )
    
    embed.set_footer(text="Use /progress to see your current week status")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="leaderboard", description="Show cumulative habit totals")
@slash_channel_check()
async def leaderboard(interaction: discord.Interaction):
    from collections import defaultdict
    
    # Defer since this might take a moment
    await interaction.response.defer()
    
    # Aggregate totals across every week
    data = load()
    totals = defaultdict(lambda: defaultdict(int))
    
    for week_data in data.values():
        for uid, days in week_data.items():
            for tokens in days.values():
                for tok in tokens:
                    name, *val = tok.split(":", 1)
                    cfg = HABITS.get(name)
                    if cfg and cfg["unit"] == "minutes":
                        amt = int(val[0]) if val else cfg.get("min", 0)
                        totals[uid][name] += amt
    
    # Build an embed with one field per user
    embed = Embed(
        title="🏆 Leaderboard",
        description="Because statistics are awesome...",
        colour=0xf1c40f
    )
    
    # Sort users by total sum descending
    def user_sum(hdict): 
        return sum(hdict.values())
    
    # Get display names for all users
    for uid, habit_dict in sorted(totals.items(), key=lambda kv: -user_sum(kv[1])):
        # Try to get member from guild
        member = interaction.guild.get_member(int(uid)) if interaction.guild else None
        if member:
            display = member.display_name
        else:
            # Try to fetch user
            try:
                user = await bot.fetch_user(int(uid))
                display = user.display_name if user else uid[:6]
            except:
                display = uid[:6]
        
        lines = []
        for habit, amount in sorted(habit_dict.items()):
            unit = "pages" if habit == "reading" else "min"
            lines.append(f"– **{habit.capitalize()}:** {amount} {unit}")
        
        # Skip if someone has no minute-based entries
        if not lines:
            continue
        
        embed.add_field(
            name=display,
            value="\n".join(lines),
            inline=False
        )
    
    # Handle case where no one has logged minute-based habits
    if len(embed.fields) == 0:
        embed.add_field(
            name="No Data",
            value="No minute-based habits have been logged yet.",
            inline=False
        )
    
    await interaction.followup.send(embed=embed)
    

@bot.tree.command(name="help", description="Show all available commands")
@slash_channel_check()
async def help_slash(interaction: discord.Interaction):
    """Show all available slash commands and their usage."""
    embed = Embed(
        title="📋 HabitBot Commands",
        description="All commands now use slash (/) format!",
        colour=0x95a5a6
    )

    embed.add_field(
        name="🔹 `/checkin`",
        value=(
            "Log one or more habits for today or another day.\n"
            "• habits: List like 'meditation reading 20 exercise'\n"
            "• day: Choose from dropdown (optional)"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 `/progress`",
        value=(
            "View weekly progress for you or another member.\n"
            "• member: Select a member (optional)"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 `/history`",
        value=(
            "View check-in history.\n"
            "• member: Select a member (optional)\n"
            "• time_filter: Choose 'Full Week', 'Today', or a specific day"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 `/delete`",
        value=(
            "Delete a logged habit.\n"
            "• habit: Start typing to see available habits\n"
            "• day: Choose from dropdown (optional)"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 `/rank`",
        value="Show the group's current rank and cumulative challenge.",
        inline=False
    )

    embed.add_field(
        name="🔹 `/ranks`",
        value="List all ranks and their challenges.",
        inline=False
    )

    embed.add_field(
        name="🔹 `/nextchallenge`",
        value="Preview the next rank's challenges.",
        inline=False
    )

    embed.add_field(
        name="🔹 `/mychallenge`",
        value="Show your current weekly habit challenges organized by type.",
        inline=False
    )

    embed.add_field(
        name="🔹 `/leaderboard`",
        value="Show cumulative totals for minute-based habits.",
        inline=False
    )

    embed.add_field(
        name="🔹 `/ping`",
        value="Check if the bot is responsive.",
        inline=False
    )

    embed.add_field(
        name="📌 Admin Commands",
        value=(
            "• `/rankup` - Promote group rank (admin only)\n"
            "• `/rankdown` - Demote group rank (admin only)"
        ),
        inline=False
    )

    embed.add_field(
        name="💡 Tips",
        value=(
            "• Slash commands show hints as you type!\n"
            "• Use Tab to autocomplete\n"
            "• Red error messages only you can see"
        ),
        inline=False
    )

    embed.set_footer(text="Legacy ! commands still work during transition")
    await interaction.response.send_message(embed=embed)


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


@bot.command()
async def forcedelete(ctx, member: commands.MemberConverter, *args):
    """
    [DEV ONLY] Force‐delete one or more habits for another user.
    Usage:
      !forcedelete @User <habit> [habit ...] [weekday]
    """
    # only you can run this
    if ctx.author.id != DEV_USER_ID:
        return

    if not args:
        return await ctx.send("Usage: `!forcedelete @User <habit> [habit ...] [weekday]`")

    # 1️⃣ Optional day‐of‐week override
    days = {d.lower(): i for i, d in enumerate(
        ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    )}
    override = None
    if args[-1].lower() in days:
        override = args[-1].lower()
        args = args[:-1]

    if not args:
        return await ctx.send("You must specify at least one habit to delete.")

    # 2️⃣ Normalize habit names
    habits_to_delete = [h.lower() for h in args if h.lower() in HABITS]
    if not habits_to_delete:
        return await ctx.send("No valid habits provided to delete.")

    # 3️⃣ Determine the target date
    if override:
        mon      = date.fromisoformat(current_week_id())
        day_date = mon + timedelta(days=days[override])
    else:
        day_date = datetime.now(LOCAL_TZ).date()
    day_iso    = day_date.isoformat()
    human_date = day_date.strftime("%d %b")

    # 4️⃣ Load and modify storage
    uid       = str(member.id)
    week      = current_week_id()
    user_days = DATA.setdefault(week, {}).setdefault(uid, {})

    tokens = user_days.get(day_iso, [])
    if not tokens:
        return await ctx.send(f"No entries found for {member.display_name} on {human_date}.")

    # remove any tokens matching the specified habits
    filtered = [tok for tok in tokens
                if tok.split(":",1)[0] not in habits_to_delete]

    if len(filtered) == len(tokens):
        # nothing was removed
        return await ctx.send(f"No matching entries for {member.display_name} on {human_date}.")

    # save back
    if filtered:
        user_days[day_iso] = filtered
    else:
        user_days.pop(day_iso)
    save(DATA)

    # 5️⃣ Minimal feedback
    await ctx.send(
        f"🗑 Deleted for {member.display_name} on {human_date}: "
        + ", ".join(habits_to_delete)
    )


@bot.command()
async def sync(ctx):
    """Force sync slash commands - DEV ONLY"""
    if ctx.author.id != DEV_USER_ID:
        return
    
    try:
        await update_command_descriptions()
        synced = await bot.tree.sync()
        await ctx.send(f"Synced {len(synced)} commands globally")
    except Exception as e:
        await ctx.send(f"Failed to sync: {e}")

# Exempt sync from channel checks
sync.checks = []

# simple ping-pong sanity check
@bot.tree.command(name="ping", description="Check bot responsiveness")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong!")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print(f"Slash commands synced: {len(bot.tree.get_commands())}")

if __name__ == "__main__":
    print("Loaded token is:", TOKEN[:10] + "...")
    bot.run(TOKEN)