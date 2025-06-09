import os
import discord
import asyncio
import reminder
import ai_updates
from discord.ext import commands
from discord import app_commands
from discord.ext import tasks
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

from helpers import load_meta, current_week_id, display_name_for, get_week_summary, save_meta, evaluate_week, get_all_streaks, format_streak_display
from helpers import LOCAL_TZ


load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DEV_USER_IDS = [109596804374360064, 241453518567768064]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True


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
        "allowed_slash": ["checkin", "delete", "clear"],
        "allowed_traditional": ["checkin", "forcecheckin", "forcedelete"],
        "message": "Please use #other-commands for non-checkin commands!"
    },
    "chat": {
        "allowed_slash": [],
        "allowed_traditional": [],
        "message": "Bot commands aren't allowed here. Try #check-ins or #other-commands!"
    },
    "other-commands": {
        "denied_slash": ["checkin", "delete", "clear"],
        "denied_traditional": ["checkin", "forcecheckin", "forcedelete"],
        "message": "Please use #check-ins for checkin-related commands!"
    },
    "updates": {
        "allowed_slash": ["dailyupdate", "testupdate"],
        "allowed_traditional": [],
        "message": "This channel is for AI-generated team updates."
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
    day="optional: log for a different DOTW",
    week="Week offset (0=current, -1=last week, -2=two weeks ago, etc.)"
)
@app_commands.choices(day=[
    app_commands.Choice(name="Today", value="today"),
    app_commands.Choice(name="Yesterday", value="yesterday"),
    app_commands.Choice(name="Monday", value="monday"),
    app_commands.Choice(name="Tuesday", value="tuesday"),
    app_commands.Choice(name="Wednesday", value="wednesday"),
    app_commands.Choice(name="Thursday", value="thursday"),
    app_commands.Choice(name="Friday", value="friday"),
    app_commands.Choice(name="Saturday", value="saturday"),
    app_commands.Choice(name="Sunday", value="sunday"),
])
async def checkin(interaction: discord.Interaction, habits: str, day: str = "today", week: int = 0):
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
    
    # Calculate target week
    current_monday = date.fromisoformat(current_week_id())
    target_monday = current_monday + timedelta(weeks=week)
    target_week_id = target_monday.isoformat()
    
    # Determine date within the target week
    if day == "yesterday":
        if week == 0:
            # Yesterday relative to current week
            day_date = datetime.now(LOCAL_TZ).date() - timedelta(days=1)
        else:
            # Yesterday doesn't make sense for past weeks, default to Sunday of target week
            day_date = target_monday + timedelta(days=6)  # Sunday
    elif day != "today":
        # Specific day of the target week
        days = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, 
                "friday": 4, "saturday": 5, "sunday": 6}
        day_date = target_monday + timedelta(days=days[day.lower()])
    else:
        # "today" 
        if week == 0:
            # Current week's today
            day_date = datetime.now(LOCAL_TZ).date()
        else:
            # For past weeks, "today" defaults to the equivalent day of week in that week
            current_date = datetime.now(LOCAL_TZ).date()
            current_weekday = current_date.weekday()
            day_date = target_monday + timedelta(days=current_weekday)

    # Use the target week for storage
    day_iso = day_date.isoformat()
    week_for_storage = target_week_id

    # Save to storage
    uid = str(interaction.user.id)
    user_days = DATA.setdefault(week_for_storage, {}).setdefault(uid, {})
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
    
    # Create embed with warning for non-current weeks
    if week == 0:
        embed = Embed(
            title="✅ Check-in Recorded",
            description=f"**{interaction.user.display_name}** logged on **{human_date}**",
            colour=0x2ecc71
        )
    else:
        week_desc = f"{abs(week)} week{'s' if abs(week) != 1 else ''} ago"
        embed = Embed(
            title="⚠️ Previous Week Check-in Recorded",
            description=f"**{interaction.user.display_name}** logged on **{human_date}** ({week_desc})",
            colour=0xf39c12  # Orange warning color
        )
    
    embed.add_field(name="📝 Activities", value="\n".join(lines), inline=False)
    
    # Add week context footer for non-current weeks
    if week != 0:
        embed.set_footer(text=f"💡 This was logged for week starting {target_monday:%d %b %Y}")
    
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
    time_filter="Filter by specific day",
    week="Week offset (0=current, -1=last week, -2=two weeks ago, etc.)"
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
async def history(interaction: discord.Interaction, member: discord.Member = None, time_filter: str = "all", week: int = 0):
    data = load()
    
    # Determine target member
    target = member or interaction.user
    
    # Calculate target week
    current_monday = date.fromisoformat(current_week_id())
    target_monday = current_monday + timedelta(weeks=week)
    week_id = target_monday.isoformat()
    
    # Load target week's data
    week_data = data.get(week_id, {})
    user_days = week_data.get(str(target.id), {})
    
    if not user_days:
        week_desc = "this week" if week == 0 else f"{abs(week)} week{'s' if abs(week) != 1 else ''} {'ago' if week < 0 else 'in the future'}"
        return await interaction.response.send_message(
            f"No check-ins for {target.display_name} in {week_desc}.",
            ephemeral=True
        )
    
    # Build the subset of days to show
    entries = {}
    days_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                "friday": 4, "saturday": 5, "sunday": 6}
    
    if time_filter == "today" and week == 0:
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
        target_dt = target_monday + timedelta(days=days_map[time_filter])
        iso = target_dt.isoformat()
        if iso in user_days:
            entries[iso] = user_days[iso]
        else:
            return await interaction.response.send_message(
                f"No check-ins for {target.display_name} on {time_filter.title()} that week.",
                ephemeral=True
            )
    else:  # "all" or "today" for past weeks
        entries = user_days
    
    # Build embed
    week_desc = "This Week" if week == 0 else f"Week {week:+d}"
    embed = Embed(
        title=f"🕑 History for {target.display_name}",
        description=f"{week_desc} - {target_monday:%A %d %b %Y}",
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
    
    # Add navigation hint
    if week == 0:
        embed.set_footer(text="💡 Use week:-1 to see last week, week:-2 for two weeks ago, etc.")
    else:
        embed.set_footer(text=f"💡 Use week:0 to return to current week")
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="streaks", description="View habit streaks")
@slash_channel_check()
@app_commands.describe(member="View another member's streaks (optional)")
async def streaks(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.defer()  # For longer operations
    
    target = member or interaction.user
    uid = str(target.id)
    
    streaks_data = get_all_streaks(uid)
    
    if not streaks_data:
        return await interaction.followup.send(
            f"No streak data found for {target.display_name}."
        )
    
    # Sort by current streak descending, then by habit name
    sorted_habits = sorted(
        streaks_data.items(), 
        key=lambda x: (-x[1]["current"], x[0])
    )
    
    embed = Embed(
        title=f"🔥 Streaks for {target.display_name}",
        colour=0xff6b35
    )
    
    # Group into active (current > 0) and broken (current = 0)
    active_streaks = []
    broken_streaks = []
    
    for habit, data in sorted_habits:
        streak_display = format_streak_display(data["current"], data["best"])
        line = f"**{habit.capitalize()}:** {streak_display}"
        
        if data["current"] > 0:
            active_streaks.append(line)
        else:
            broken_streaks.append(line)
    
    if active_streaks:
        embed.add_field(
            name="🔥 Active Streaks",
            value="\n".join(active_streaks),
            inline=False
        )
    
    if broken_streaks:
        embed.add_field(
            name="💔 Broken Streaks",
            value="\n".join(broken_streaks[:10]),  # Limit to avoid embed size issues
            inline=False
        )
    
    # Add some motivational footer
    total_active = len(active_streaks)
    if total_active > 0:
        embed.set_footer(text=f"🚀 {total_active} active streak{'s' if total_active != 1 else ''} - keep it up!")
    else:
        embed.set_footer(text="💪 Start building those streaks!")
    
    await interaction.followup.send(embed=embed)


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
    day="Day to delete from",
    week="Week offset (0=current, -1=last week, -2=two weeks ago, etc.)"
)
@app_commands.autocomplete(habit=habit_autocomplete)
@app_commands.choices(day=[
    app_commands.Choice(name="Today", value="today"),
    app_commands.Choice(name="Yesterday", value="yesterday"),
    app_commands.Choice(name="Monday", value="monday"),
    app_commands.Choice(name="Tuesday", value="tuesday"),
    app_commands.Choice(name="Wednesday", value="wednesday"),
    app_commands.Choice(name="Thursday", value="thursday"),
    app_commands.Choice(name="Friday", value="friday"),
    app_commands.Choice(name="Saturday", value="saturday"),
    app_commands.Choice(name="Sunday", value="sunday"),
])
async def delete(interaction: discord.Interaction, habit: str, day: str = "today", week: int = 0):
    global DATA
    DATA = load()
    
    habit_key = habit.lower()
    if habit_key not in HABITS:
        return await interaction.response.send_message(
            f"Unrecognized habit: {habit_key}",
            ephemeral=True
        )
    
    # Calculate target week
    current_monday = date.fromisoformat(current_week_id())
    target_monday = current_monday + timedelta(weeks=week)
    target_week_id = target_monday.isoformat()
    
    # Determine date within the target week
    if day == "yesterday":
        if week == 0:
            # Yesterday relative to current week
            day_date = datetime.now(LOCAL_TZ).date() - timedelta(days=1)
        else:
            # Yesterday doesn't make sense for past weeks, default to Sunday of target week
            day_date = target_monday + timedelta(days=6)  # Sunday
    elif day != "today":
        # Specific day of the target week
        days_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                    "friday": 4, "saturday": 5, "sunday": 6}
        day_date = target_monday + timedelta(days=days_map[day])
    else:
        # "today"
        if week == 0:
            # Current week's today
            day_date = datetime.now(LOCAL_TZ).date()
        else:
            # For past weeks, "today" defaults to the equivalent day of week in that week
            current_date = datetime.now(LOCAL_TZ).date()
            current_weekday = current_date.weekday()
            day_date = target_monday + timedelta(days=current_weekday)
    
    day_iso = day_date.isoformat()
    human_date = day_date.strftime("%A, %d %b")
    
    # Locate and remove entry from target week
    uid = str(interaction.user.id)
    week_data = DATA.get(target_week_id, {})
    user_days = week_data.get(uid, {})
    
    tokens = user_days.get(day_iso, [])
    filtered = [tok for tok in tokens if tok.split(":",1)[0] != habit_key]
    
    if len(filtered) == len(tokens):
        # No entry found to delete
        week_context = ""
        if week != 0:
            week_desc = f"{abs(week)} week{'s' if abs(week) != 1 else ''} {'ago' if week < 0 else 'in the future'}"
            week_context = f" ({week_desc})"
        
        return await interaction.response.send_message(
            f"No `{habit_key}` entry found on {human_date}{week_context}.",
            ephemeral=True
        )
    
    # Update storage
    if filtered:
        user_days[day_iso] = filtered
    else:
        user_days.pop(day_iso, None)
    save(DATA)
    
    # Build response embed
    if week == 0:
        embed = Embed(
            title="🗑 Entry Deleted",
            description=f"Removed **{habit_key}** on {human_date}.",
            colour=0xe67e22
        )
    else:
        week_desc = f"{abs(week)} week{'s' if abs(week) != 1 else ''} {'ago' if week < 0 else 'in the future'}"
        embed = Embed(
            title="⚠️ Previous Week Entry Deleted",
            description=f"Removed **{habit_key}** on {human_date} ({week_desc}).",
            colour=0xf39c12  # Orange warning color
        )
    
    # Add week context footer for non-current weeks
    if week != 0:
        embed.set_footer(text=f"💡 This was deleted from week starting {target_monday:%d %b %Y}")
    
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="clear", description="Clear ALL habits for a specific day")
@slash_channel_check()
@app_commands.describe(day="Day to clear all habits from")
@app_commands.choices(day=[
    app_commands.Choice(name="Today", value="today"),
    app_commands.Choice(name="Yesterday", value="yesterday"),
    app_commands.Choice(name="Monday", value="monday"),
    app_commands.Choice(name="Tuesday", value="tuesday"),
    app_commands.Choice(name="Wednesday", value="wednesday"),
    app_commands.Choice(name="Thursday", value="thursday"),
    app_commands.Choice(name="Friday", value="friday"),
    app_commands.Choice(name="Saturday", value="saturday"),
    app_commands.Choice(name="Sunday", value="sunday"),
])
async def clear_day(interaction: discord.Interaction, day: str = "today"):
    global DATA
    DATA = load()
    
    # Determine date
    if day == "yesterday":
        day_date = datetime.now(LOCAL_TZ).date() - timedelta(days=1)
    elif day != "today":
        days_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                    "friday": 4, "saturday": 5, "sunday": 6}
        mon = date.fromisoformat(current_week_id())
        day_date = mon + timedelta(days=days_map[day])
    else:
        day_date = datetime.now(LOCAL_TZ).date()
    
    day_iso = day_date.isoformat()
    human_date = day_date.strftime("%A, %d %b")
    
    # Determine correct week for the date
    target_monday = day_date - timedelta(days=day_date.weekday())
    week = target_monday.isoformat()
    
    # Check if data exists
    uid = str(interaction.user.id)
    week_data = DATA.get(week, {})
    user_days = week_data.get(uid, {})
    
    if day_iso not in user_days or not user_days[day_iso]:
        return await interaction.response.send_message(
            f"No check-ins found for {human_date}.",
            ephemeral=True
        )
    
    # Store what we're clearing for the response
    cleared_habits = []
    for token in user_days[day_iso]:
        habit_name = token.split(":", 1)[0]
        cleared_habits.append(habit_name.capitalize())
    
    # Clear the day
    user_days.pop(day_iso)
    save(DATA)
    
    embed = Embed(
        title="🗑️ Day Cleared",
        description=f"Cleared all check-ins for **{human_date}**",
        colour=0xe67e22
    )
    embed.add_field(
        name="Removed Habits",
        value=", ".join(cleared_habits) if cleared_habits else "None",
        inline=False
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


@bot.tree.command(name="dailyupdate", description="Generate AI team update (manual)")
@slash_channel_check()
@app_commands.default_permissions(administrator=True)
async def manual_daily_update(interaction: discord.Interaction):
    """Manually trigger a daily update"""
    await interaction.response.defer()
    
    try:
        await ai_updates.send_daily_update(bot)
        await interaction.followup.send("✅ Daily update sent to #updates channel!")
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to send update: {str(e)}", ephemeral=True)

@bot.tree.command(name="testupdate", description="Test AI update generation (no send)")
@slash_channel_check()
@app_commands.default_permissions(administrator=True)
async def test_update_generation(interaction: discord.Interaction):
    """Test update generation without sending to channel"""
    await interaction.response.defer()
    
    try:
        update_data = await ai_updates.generate_daily_update(bot)
        context = await ai_updates.gather_team_context(bot)
        
        # Create test embed similar to what would be sent
        embed = discord.Embed(
            title="🧪 Test Daily Update",
            color=0x9b59b6,
            timestamp=datetime.now(ai_updates.LOCAL_TZ)
        )
        
        week_info = context['week_info']
        week_start = datetime.fromisoformat(week_info['week_start']).strftime("%A %d %b %Y")
        description = f"**Week of {week_start}**\n"
        description += f"📅 Day {week_info['days_elapsed']}/7 • {week_info['days_remaining']} days remaining\n"
        description += f"🎖️ **Rank {context['rank_info']['current_rank']}: {context['rank_info']['rank_name'].title()}**"
        
        embed.description = description
        
        # Add user status list
        if update_data['user_status'].strip():
            embed.add_field(name="👤 Individual Status", value=f"```{update_data['user_status']}```", inline=False)
        
        # Add AI summary
        today_weekday = datetime.now(LOCAL_TZ).weekday()
        summary_title = "📋 Weekly Summary" if today_weekday == 6 else "📋 Status Update"
        if update_data['summary'].strip():
            embed.add_field(name=summary_title, value=update_data['summary'], inline=False)
        
        embed.set_footer(text="This is a test - no message sent to #updates")
        
        await interaction.followup.send(embed=embed)
            
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to generate update: {str(e)}", ephemeral=True)


@tasks.loop(time=dt.time(hour=22, minute=00, tzinfo=LOCAL_TZ))  # 10:00 PM Adelaide
async def daily_update_task():
    """Send daily updates at 9 AM Adelaide time"""
    await ai_updates.send_daily_update(bot)

@daily_update_task.before_loop
async def before_daily_update():
    await bot.wait_until_ready()
    

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
            "View check-in history with week navigation.\n"
            "• member: Select a member (optional)\n"
            "• time_filter: Choose day or 'Full Week'\n"
            "• week: 0=current, -1=last week, -2=two weeks ago"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 `/streaks`",
        value=(
            "View current and best habit streaks.\n"
            "• member: Select a member (optional)"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 `/delete`",
        value=(
            "Delete a specific logged habit.\n"
            "• habit: Start typing to see available habits\n"
            "• day: Choose from dropdown (optional)"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 `/clear`",
        value=(
            "Clear ALL habits for a specific day.\n"
            "• day: Choose from dropdown (optional)"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 `/reminders`",
        value="Toggle daily check-in reminders at 10 PM Adelaide time.",
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
            "• `/rankup` - Promote group rank (anyone)\n"
            "• `/rankdown` - Demote group rank (anyone)\n"
            "• `/dailyupdate` - Generate AI team update (admin only)\n"
            "• `/testupdate` - Test AI update generation (admin only)\n"
            "• `/testreminder` - Test reminder system (admin only)\n"
            "• `!forcecheckin` - Force check-ins for @user (admin only)\n"
            "• `!forcedelete` - Force delete for @user (admin only)"
        ),
        inline=False
    )

    embed.add_field(
        name="💡 New Features",
        value=(
            "• **Week Navigation**: Use `week:-1` in `/history` for past weeks\n"
            "• **Streak Tracking**: See your habit streaks with `/streaks`\n"
            "• **Smart Reminders**: Get DM reminders if you haven't checked in\n"
            "• **Bulk Clear**: Clear entire days with `/clear`"
        ),
        inline=False
    )

    embed.set_footer(text="🔥 Streak tracking now available! • 🔔 Set up reminders with /reminders")
    await interaction.response.send_message(embed=embed)


@bot.command()
async def forcecheckin(ctx, member: commands.MemberConverter, *args):
    """
    [DEV ONLY] Force‐log one or more habits for another user.
    Usage: !forcecheckin @User <habit> [value] [habit] [value] ... [weekday] [week:N]
    Examples: 
      !forcecheckin @User meditation exercise
      !forcecheckin @User reading 20 monday
      !forcecheckin @User meditation week:-1
      !forcecheckin @User exercise monday week:-2
    """
    # only you can run this
    if ctx.author.id not in DEV_USER_IDS:
        return

    if not args:
        return await ctx.send("Usage: `!forcecheckin @User <habit> [value] ... [weekday] [week:N]`")

    # Parse week parameter first
    week_offset = 0
    remaining_args = list(args)
    
    # Check if last argument is week:N
    if remaining_args and remaining_args[-1].startswith("week:"):
        try:
            week_offset = int(remaining_args[-1].split(":", 1)[1])
            remaining_args = remaining_args[:-1]
        except (ValueError, IndexError):
            return await ctx.send("Invalid week format. Use week:N (e.g., week:-1)")

    # Calculate target week
    current_monday = date.fromisoformat(current_week_id())
    target_monday = current_monday + timedelta(weeks=week_offset)
    target_week_id = target_monday.isoformat()

    # Parse day-of-week override
    days = {d.lower(): i for i, d in enumerate(
        ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    )}
    day_override = None
    if remaining_args and remaining_args[-1].lower() in days:
        day_override = remaining_args[-1].lower()
        remaining_args = remaining_args[:-1]
    
    if not remaining_args:
        return await ctx.send("You must specify at least one habit after the user mention.")

    # Parse & validate habits (existing logic)
    parsed = []
    i = 0
    while i < len(remaining_args):
        name = remaining_args[i].lower()
        cfg  = HABITS.get(name)
        if not cfg:
            return await ctx.send(f"Unrecognised habit: `{name}`")

        if cfg["unit"] == "minutes":
            # next token may be a number, else default
            minutes = cfg.get("min", 0)
            if i + 1 < len(remaining_args) and remaining_args[i+1].isdigit():
                minutes = int(remaining_args[i+1])
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

    # Determine date within target week
    if day_override:
        day_date = target_monday + timedelta(days=days[day_override])
    else:
        if week_offset == 0:
            # Current week, use today
            day_date = datetime.now(LOCAL_TZ).date()
        else:
            # Past/future week, use equivalent weekday
            current_date = datetime.now(LOCAL_TZ).date()
            current_weekday = current_date.weekday()
            day_date = target_monday + timedelta(days=current_weekday)

    day_iso = day_date.isoformat()

    # Write into storage using target week
    uid = str(member.id)
    user_days = DATA.setdefault(target_week_id, {}).setdefault(uid, {})
    existing = user_days.get(day_iso, [])
    to_replace = {tok.split(":",1)[0] for tok in parsed}
    filtered = [tok for tok in existing if tok.split(":",1)[0] not in to_replace]
    user_days[day_iso] = filtered + parsed
    save(DATA)

    # Build feedback message
    short = []
    for tok in parsed:
        h, *v = tok.split(":")
        if v:
            short.append(f"{h}:{v[0]}")
        else:
            short.append(h)
    
    human_date = day_date.strftime("%d %b")
    week_context = ""
    if week_offset != 0:
        week_desc = f"{abs(week_offset)} week{'s' if abs(week_offset) != 1 else ''} {'ago' if week_offset < 0 else 'in future'}"
        week_context = f" ({week_desc})"
    
    await ctx.send(f"✅ Successfully forced for {member.display_name} on {human_date}{week_context}: " +
                   ", ".join(short))


@bot.command()
async def forcedelete(ctx, member: commands.MemberConverter, *args):
    """
    [DEV ONLY] Force‐delete one or more habits for another user.
    Usage: !forcedelete @User <habit> [habit ...] [weekday] [week:N]
    Examples:
      !forcedelete @User meditation exercise
      !forcedelete @User reading monday  
      !forcedelete @User porn week:-1
      !forcedelete @User exercise monday week:-2
    """
    # only you can run this
    if ctx.author.id not in DEV_USER_IDS:
        return

    if not args:
        return await ctx.send("Usage: `!forcedelete @User <habit> [habit ...] [weekday] [week:N]`")

    # Parse week parameter first
    week_offset = 0
    remaining_args = list(args)
    
    # Check if last argument is week:N
    if remaining_args and remaining_args[-1].startswith("week:"):
        try:
            week_offset = int(remaining_args[-1].split(":", 1)[1])
            remaining_args = remaining_args[:-1]
        except (ValueError, IndexError):
            return await ctx.send("Invalid week format. Use week:N (e.g., week:-1)")

    # Calculate target week
    current_monday = date.fromisoformat(current_week_id())
    target_monday = current_monday + timedelta(weeks=week_offset)
    target_week_id = target_monday.isoformat()

    # Parse day-of-week override
    days = {d.lower(): i for i, d in enumerate(
        ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    )}
    day_override = None
    if remaining_args and remaining_args[-1].lower() in days:
        day_override = remaining_args[-1].lower()
        remaining_args = remaining_args[:-1]

    if not remaining_args:
        return await ctx.send("You must specify at least one habit to delete.")

    # Normalize habit names and validate
    habits_to_delete = [h.lower() for h in remaining_args if h.lower() in HABITS]
    if not habits_to_delete:
        return await ctx.send("No valid habits provided to delete.")

    # Determine date within target week
    if day_override:
        day_date = target_monday + timedelta(days=days[day_override])
    else:
        if week_offset == 0:
            # Current week, use today
            day_date = datetime.now(LOCAL_TZ).date()
        else:
            # Past/future week, use equivalent weekday
            current_date = datetime.now(LOCAL_TZ).date()
            current_weekday = current_date.weekday()
            day_date = target_monday + timedelta(days=current_weekday)

    day_iso = day_date.isoformat()
    human_date = day_date.strftime("%d %b")

    # Load and modify storage using target week
    uid = str(member.id)
    user_days = DATA.setdefault(target_week_id, {}).setdefault(uid, {})

    tokens = user_days.get(day_iso, [])
    if not tokens:
        week_context = ""
        if week_offset != 0:
            week_desc = f"{abs(week_offset)} week{'s' if abs(week_offset) != 1 else ''} {'ago' if week_offset < 0 else 'in future'}"
            week_context = f" ({week_desc})"
        return await ctx.send(f"No entries found for {member.display_name} on {human_date}{week_context}.")

    # Remove any tokens matching the specified habits
    filtered = [tok for tok in tokens
                if tok.split(":",1)[0] not in habits_to_delete]

    if len(filtered) == len(tokens):
        # nothing was removed
        week_context = ""
        if week_offset != 0:
            week_desc = f"{abs(week_offset)} week{'s' if abs(week_offset) != 1 else ''} {'ago' if week_offset < 0 else 'in future'}"
            week_context = f" ({week_desc})"
        return await ctx.send(f"No matching entries for {member.display_name} on {human_date}{week_context}.")

    # Save back to storage
    if filtered:
        user_days[day_iso] = filtered
    else:
        user_days.pop(day_iso)
    save(DATA)

    # Build feedback message
    week_context = ""
    if week_offset != 0:
        week_desc = f"{abs(week_offset)} week{'s' if abs(week_offset) != 1 else ''} {'ago' if week_offset < 0 else 'in future'}"
        week_context = f" ({week_desc})"

    await ctx.send(
        f"🗑 Deleted for {member.display_name} on {human_date}{week_context}: "
        + ", ".join(habits_to_delete)
    )


@bot.tree.command(name="reminders", description="Toggle daily check-in reminders")
@slash_channel_check()
async def toggle_reminders(interaction: discord.Interaction):
    from helpers import toggle_user_reminders
    
    user_id = str(interaction.user.id)
    enabled = toggle_user_reminders(user_id)
    
    if enabled:
        embed = Embed(
            title="🔔 Reminders Enabled",
            description="You'll get a private reminder at 10 PM Adelaide time if you haven't checked in.",
            colour=0x2ecc71
        )
    else:
        embed = Embed(
            title="🔕 Reminders Disabled", 
            description="You won't receive daily check-in reminders anymore.",
            colour=0xe74c3c
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="testreminder", description="Test the reminder system (dev only)")
@slash_channel_check()
@app_commands.default_permissions(administrator=True)
async def test_reminder(interaction: discord.Interaction):
    """Test reminder functionality - dev only"""
    if interaction.user.id not in DEV_USER_IDS:
        return await interaction.response.send_message("Dev only command.", ephemeral=True)
    
    await interaction.response.send_message("Testing reminder system...", ephemeral=True)
    await reminder.send_daily_reminders()


# simple ping-pong sanity check
@bot.tree.command(name="ping", description="Check bot responsiveness")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong!")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print(f"Slash commands synced: {len(bot.tree.get_commands())}")
    
    # Initialize the reminder system
    reminder.setup_reminders(bot)
    print("Reminder system initialized")
    
    # Start daily update task
    if not daily_update_task.is_running():
        daily_update_task.start()
        print("Daily update scheduler started")

if __name__ == "__main__":
    print("Loaded token is:", TOKEN[:10] + "...")
    bot.run(TOKEN)