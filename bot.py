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



load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

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
    Usage examples
      !checkin meditation            → logs today (30m default)
      !checkin meditation 45         → logs today for 45m
      !checkin exercise              → logs exercise today
      !checkin exercise Friday       → logs exercise this Friday
      !checkin meditation 50 Monday  → logs meditation Monday 50m
    """
    if not args:
        await ctx.reply("Try `!checkin meditation 40` or `!checkin exercise Friday`")
        return

    # 1️⃣  Optional DOTW override?
    days = {d.lower(): i for i, d in enumerate(
        ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    )}
    override = None
    if args[-1].lower() in days:
        override = args[-1].lower()
        args = args[:-1]

    if not args:
        await ctx.reply("You need to specify a habit, e.g. `!checkin meditation Monday`")
        return

    # 2️⃣  Parse the habits+values
    parsed = []
    i = 0
    while i < len(args):
        task = args[i].lower()
        cfg  = HABITS.get(task)
        if not cfg:
            await ctx.reply(f"Unrecognised habit: {task}")
            return

        if cfg["unit"] == "minutes":
            minutes = cfg["min"]
            if i + 1 < len(args) and args[i+1].isdigit():
                minutes = int(args[i+1]); i += 1
            if minutes < cfg["min"]:
                await ctx.reply(f"{task} must be ≥ {cfg['min']} min.")
                return
            parsed.append(f"{task}:{minutes}")

        elif cfg["unit"] == "bool":
            parsed.append(task)

        else:
            await ctx.reply(f"Config error for habit: {task}")
            return

        i += 1

    # 3️⃣  Determine which date to record
    if override:
        mon      = date.fromisoformat(current_week_id())
        day_date = mon + timedelta(days=days[override])
    else:
        day_date = datetime.now(timezone.utc).date()

    day_iso = day_date.isoformat()

    uid  = str(ctx.author.id)
    week = current_week_id()
    
    # 4️⃣  Store in JSON (merge with any existing entries for that day)
    user_days = DATA.setdefault(week, {}).setdefault(uid, {})
    existing = user_days.get(day_iso, [])

    # remove any old tokens for the same habit(s) we’re logging now
    parsed_names = {tok.split(":",1)[0] for tok in parsed}
    filtered = [tok for tok in existing
                if tok.split(":",1)[0] not in parsed_names]

    # combine: keep others + add new/updated tokens
    user_days[day_iso] = filtered + parsed
    save(DATA)

    # 5️⃣  Human-friendly reply
    pretty = []
    for tok in parsed:
        name, *val = tok.split(":")
        pretty.append(HABITS[name]["reply"](val[0] if val else None))

    human_date = day_date.strftime("%A, %d %b")

    # build an embed
    embed = Embed(
        title="✅ Check-in Recorded",
        description=f"{ctx.author.display_name} logged:",
        colour=0x2ecc71
    )
    # add each task as its own field
    for desc in pretty:
        embed.add_field(name=desc, value=f"🗓 {human_date}", inline=False)

    await ctx.send(embed=embed)


    # 6️⃣  Auto-evaluate week-boundary
    current   = current_week_id()
    last_eval = META.get("last_eval")
    if last_eval and last_eval != current:
        await evaluate_week(last_eval, ctx)
    META["last_eval"] = current
    save_meta(META)



@bot.command()
async def progress(ctx):
    # 🔄 reload the freshest progress.json
    global DATA
    DATA = load()

    summary, week = get_week_summary()
    if not summary:
        return await ctx.reply("No check-ins recorded for this week yet.")

    # Determine which habits to show based on the current group rank
    relevant = [r["habit"] for r in RANKS[:GROUP_RANK]]
    seen = set()
    relevant_unique = []
    for h in relevant:
        if h not in seen:
            seen.add(h)
            relevant_unique.append(h)

    # Build the embed
    week_dt = dt.date.fromisoformat(week)
    embed = Embed(
        title=f"📊 Week Starting — {week_dt:%A %d %b %Y}",
        colour=0x3498db
    )

    for uid, habits in summary.items():
        name = await display_name_for(uid, ctx)

        # Compute total completion % for only the relevant habits
        total_done = sum(
            min(habits.get(h, 0), HABITS[h].get("weekly_target", 7))
            for h in relevant_unique
        )
        total_target = sum(
            HABITS[h].get("weekly_target", 7)
            for h in relevant_unique
        )
        pct_str = f"{(total_done / total_target * 100):.1f}%"

        # Build the value lines
        lines = [f"total-completion: **{pct_str}**"]
        for h in relevant_unique:
            done   = habits.get(h, 0)
            target = HABITS[h].get("weekly_target", 7)
            lines.append(f"{h}: {done}/{target}")

        embed.add_field(name=name, value="\n".join(lines), inline=False)

    await ctx.send(embed=embed)



@bot.command()
async def ranks(ctx):
    # 1️⃣ Build the “All Ranks” embed section
    embed = Embed(title="🏅 All Ranks", colour=0x00aaff)
    for r in RANKS:
        level = r["level"]
        name  = r["name"].title()
        habit = r["habit"].capitalize()
        target= r["target"]
        embed.add_field(
            name=f"{level}. {name}",
            value=f"{habit} • {target}",
            inline=False
        )

    # 2️⃣ Add a blank line separator
    embed.add_field(name="\u200b", value="\u200b", inline=False)

    # 3️⃣ Show the group’s current rank
    level = GROUP_RANK
    rank  = next((x for x in RANKS if x["level"] == level), None)
    if rank:
        embed.add_field(
            name="🎖 Current Group Rank",
            value=f"**{rank['level']}. {rank['name'].title()}**\n" +
                  f"Challenge: {rank['habit'].capitalize()} • {rank['target']}",
            inline=False
        )
    else:
        embed.add_field(
            name="🎖 Current Group Rank",
            value="No rank assigned yet.",
            inline=False
        )

    await ctx.send(embed=embed)


@bot.command()
async def rank(ctx):
    """
    Show the group’s current rank and its full cumulative challenge.
    """
    # current rank info
    level = GROUP_RANK
    rank  = next((r for r in RANKS if r["level"] == level), None)
    if not rank:
        return await ctx.reply("No rank data available.")

    # build embed
    embed = Embed(
        title=f"🎖 Current Group Rank: {level} – {rank['name'].title()}",
        colour=0x00aaff
    )

    # list all tasks up to current level
    tasks = "\n".join(
        f"- **{r['habit'].capitalize()}:** {r['target']}"
        for r in RANKS[:level]
    )
    embed.add_field(
        name="🗒️ Current Challenge",
        value=f"Complete all of the following:\n{tasks}",
        inline=False
    )

    await ctx.send(embed=embed)


@bot.command()
async def rankup(ctx):
    """
    Manually bump the group’s rank by 1 and show the cumulative challenge.
    """
    global GROUP_RANK
    old = GROUP_RANK
    if old >= len(RANKS):
        return await ctx.reply("The group is already at the highest rank.")

    # bump and persist
    GROUP_RANK += 1
    save_group_rank(GROUP_RANK)

    # find the new rank entry
    new_rank = next(r for r in RANKS if r["level"] == GROUP_RANK)

    # build an embed
    embed = Embed(
        title=f"🎉 Group Promoted to Rank {GROUP_RANK}: {new_rank['name'].title()}",
        colour=0x2ecc71
    )

    # list all cumulative tasks up to this rank
    tasks = "\n".join(
        f"- {r['habit'].capitalize()} {r['target']}"
        for r in RANKS[:GROUP_RANK]
    )
    embed.add_field(
        name="🆕 Current Challenge",
        value=f"Complete all of the following:\n{tasks}",
        inline=False
    )

    await ctx.send(embed=embed)


@bot.command()
async def rankdown(ctx):
    """
    Manually drop the group’s rank by 1 and show the cumulative challenge.
    """
    global GROUP_RANK
    old = GROUP_RANK
    if old <= 1:
        return await ctx.reply("The group is already at the lowest rank.")

    # derank and persist
    GROUP_RANK -= 1
    save_group_rank(GROUP_RANK)

    # find the new rank entry
    new_rank = next(r for r in RANKS if r["level"] == GROUP_RANK)

    # build an embed
    embed = Embed(
        title=f"⚠️ Group Demoted to Rank {GROUP_RANK}: {new_rank['name'].title()}",
        colour=0xe74c3c
    )

    # list all cumulative tasks up to this rank
    tasks = "\n".join(
        f"- {r['habit'].capitalize()} {r['target']}"
        for r in RANKS[:GROUP_RANK]
    )
    embed.add_field(
        name="🔽 Current Challenge",
        value=f"Complete all of the following:\n{tasks}",
        inline=False
    )

    await ctx.send(embed=embed)


@bot.command()
async def history(ctx, *args):
    """
    Show a member’s check-in history for a week.
    Usage:
      !history                        → your current week
      !history 2025-04-28             → your specified week
      !history @Friend                → friend’s current week
      !history @Friend 2025-04-28     → friend’s specified week
    """
    # 1️⃣ reload data
    data = load()

    # 2️⃣ determine which member
    if ctx.message.mentions:
        member = ctx.message.mentions[0]
    else:
        member = ctx.author

    # 3️⃣ strip mention tokens from args
    mention_ids = {f"<@{member.id}>", f"<@!{member.id}>"}
    args = [a for a in args if a not in mention_ids]

    # 4️⃣ determine week_id
    week_id = args[0] if args else current_week_id()

    # 5️⃣ parse the week’s Monday
    week_dt = dt.date.fromisoformat(week_id)

    # 6️⃣ fetch that member’s entries
    uid = str(member.id)
    week_data = data.get(week_id, {})
    user_days = week_data.get(uid, {})

    if not user_days:
        return await ctx.reply(
            f"No check-ins for {member.display_name} for week of {week_dt:%A %d %b %Y}."
        )

    # 7️⃣ build embed
    embed = Embed(
        title=f"🕑 History for {member.display_name}",
        description=f"Week of {week_dt:%A %d %b %Y}",
        colour=0x9b59b6
    )

    for day_iso in sorted(user_days):
        day_date = dt.date.fromisoformat(day_iso)
        day_name = day_date.strftime("%A %d %b")
        tasks    = user_days[day_iso]

        pretty = []
        for tok in tasks:
            name, *val = tok.split(":")
            if val:
                num    = val[0]
                target = next((r["target"] for r in RANKS if r["habit"] == name), "")
                unit   = "".join(ch for ch in target if ch.isalpha())
                pretty.append(f"- **{name}:** {num}{unit}")
            else:
                pretty.append(f"- **{name}**")

        embed.add_field(
            name=f"__{day_name}__",    # underlined day header
            value="\n".join(pretty),
            inline=False
        )

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
        day_date = datetime.now(timezone.utc).date()
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
    Preview the next rank’s cumulative challenge for the group.
    """
    # determine the upcoming rank
    next_level = GROUP_RANK + 1
    if next_level > len(RANKS):
        return await ctx.reply("🎉 The group is already at the highest rank!")

    next_rank = next(r for r in RANKS if r["level"] == next_level)

    # build an embed
    embed = Embed(
        title=f"🔮 Next Challenge: Rank {next_level} – {next_rank['name'].title()}",
        colour=0x8e44ad
    )

    # list all tasks up to next_level
    tasks = "\n".join(
        f"- **{r['habit'].capitalize()}:** {r['target']}"
        for r in RANKS[:next_level]
    )
    embed.add_field(
        name="Tasks to Complete",
        value=f"Complete all of the following:\n{tasks}",
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
        description="Here’s a list of everything you can do with HabitBot:",
        colour=0x95a5a6
    )

    embed.add_field(
        name="!ping",
        value="Check bot responsiveness.",
        inline=False
    )
    embed.add_field(
        name="!checkin <habit> [value] [weekday]",
        value=(
            "Log a habit for today (default) or another day of this week.\n"
            "- e.g. `!checkin meditation`\n"
            "- e.g. `!checkin meditation 45 Tuesday`\n"
            "- e.g. `!checkin exercise Friday`"
        ),
        inline=False
    )
    embed.add_field(
        name="!progress",
        value="Show the group’s progress for the current week.",
        inline=False
    )
    embed.add_field(
        name="!history [@User] [week]",
        value=(
            "Show check-ins for you or another member for a week.\n"
            "- Default: your current week\n"
            "- e.g. `!history @Friend`\n"
            "- e.g. `!history 2025-04-28`\n"
            "- e.g. `!history @Friend 2025-04-28`"
        ),
        inline=False
    )
    embed.add_field(
        name="!delete <habit> [weekday]",
        value=(
            "Remove an entry you logged.\n"
            "- e.g. `!delete meditation`\n"
            "- e.g. `!delete meditation Saturday`"
        ),
        inline=False
    )
    embed.add_field(
        name="!nextchallenge",
        value="Preview the upcoming rank’s cumulative challenge.",
        inline=False
    )
    embed.add_field(
        name="!rank",
        value="Show the group’s current rank and its cumulative challenge.",
        inline=False
    )
    embed.add_field(
        name="!ranks",
        value="List all possible ranks and show the current group rank.",
        inline=False
    )
    embed.add_field(
        name="!rankup",
        value="Manually promote the group’s rank by 1.",
        inline=False
    )
    embed.add_field(
        name="!rankdown",
        value="Manually demote the group’s rank by 1.",
        inline=False
    )

    await ctx.send(embed=embed)



@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id: {bot.user.id})")

# simple ping-pong sanity check
@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

if __name__ == "__main__":
    print("Loaded token is:", TOKEN[:10] + "...")  # should show first chars, not 'None'
    bot.run(TOKEN)