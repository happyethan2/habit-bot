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
    
    # ─── 1.1 Enforce only unlocked habits ───
    current_rank   = load_group_rank()
    allowed        = {r["habit"] for r in RANKS[:current_rank]}
    # if they try to log a habit that exists but isn’t in allowed
    for arg in args:
        name = arg.lower()
        if name in HABITS and name not in allowed:
            # find the rank that unlocks it
            req = next(r for r in RANKS if r["habit"] == name)
            return await ctx.reply(
                f"🚫 You can’t log **{name}** yet — it unlocks at "
                f"Rank {req['level']} ({req['name'].title()})."
            )

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
            # use .get so streaming picks up min=0
            minutes = cfg.get("min", 0)
            # consume an explicit number if provided
            if i + 1 < len(args) and args[i+1].isdigit():
                minutes = int(args[i+1])
                i += 1

            # enforce minimum (for other habits)
            if minutes < cfg.get("min", 0):
                await ctx.reply(f"{task} must be ≥ {cfg['min']} min.")
                return

            # enforce maximum (for streaming)
            if cfg.get("max") is not None and minutes > cfg["max"]:
                await ctx.reply(f"{task} cannot exceed {cfg['max']} minutes per day.")
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
        day_date = datetime.now(LOCAL_TZ).date()

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
async def progress(ctx, member: commands.MemberConverter = None):
    """
    Show a member’s progress for this week.
    """
    # reload & pick user
    global DATA
    DATA = load()
    summary, week = get_week_summary()
    target = member or ctx.author
    uid    = str(target.id)

    if uid not in summary:
        return await ctx.reply(f"No check-ins for {target.display_name} this week.")

    habits = summary[uid]

    # which habits unlocked?
    unlocked = [r["habit"] for r in RANKS[:load_group_rank()]]
    seen, relevant = set(), []
    for h in unlocked:
        if h not in seen:
            seen.add(h)
            relevant.append(h)

    # overall %
    total_done   = sum(min(habits.get(h,0), HABITS[h].get("weekly_target",7)) for h in relevant)
    total_target = sum(  HABITS[h].get("weekly_target",7)              for h in relevant)
    pct = (total_done/total_target*100) if total_target else 100
    pct_str = f"{pct:.1f}%"

    # fixed bar length
    BAR_LEN = 14
    def make_bar(done, targ):
        if targ <= 0:
            return "░" * BAR_LEN
        filled = round(done / targ * BAR_LEN)
        filled = max(0, min(BAR_LEN, filled))
        return "█" * filled + "░" * (BAR_LEN - filled)

    # build code-block table
    max_len = max(len(h) for h in relevant)
    lines = []
    for h in relevant:
        done = habits.get(h, 0)
        targ = HABITS[h].get("weekly_target", 7)
        bar  = make_bar(done, targ)
        lines.append(f"{h.ljust(max_len)}  {bar}  {done}/{targ}")

    table = "```\n" + "\n".join(lines) + "\n```"

    # send embed
    week_dt = dt.date.fromisoformat(week)
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
    Show all ranks in two columns, with each habit in “Habit: target” format.
    """
    current = load_group_rank()

    EMOJI = {
        1:  "🥫", 2:  "🧲", 3: "🥉", 4: "🔩", 5: "⚙️",
        6:  "🥈", 7:  "🥇", 8: "💿", 9: "💎", 10: "🪐",
        11: "☢️", 12: "🎓", 13: "🏆",
    }

    half  = (len(RANKS) + 1) // 2
    left  = RANKS[:half]
    right = RANKS[half:]

    left_text = "\n\n".join(
        f"{EMOJI[r['level']]} **{r['level']}. {r['name'].title()}**\n"
        f"  {r['habit'].capitalize()}: {r['target']}"
        for r in left
    )
    right_text = "\n\n".join(
        f"{EMOJI[r['level']]} **{r['level']}. {r['name'].title()}**\n"
        f"  {r['habit'].capitalize()}: {r['target']}"
        for r in right
    )

    embed = Embed(title="🏅 Ranks Overview", colour=0x00aaff)
    embed.add_field(name="\u200b", value=left_text,  inline=True)
    embed.add_field(name="\u200b", value=right_text, inline=True)

    # blank spacer
    embed.add_field(name="\u200b", value="\u200b", inline=False)

        # — current group rank —
    curr = next(r for r in RANKS if r["level"] == current)
    embed.add_field(
        name="🎖 Current Group Rank",
        value=f"**{current}. {curr['name'].title()}**",
        inline=False
    )

    # — current challenge —
    embed.add_field(
        name="🏁 Current Challenge",
        value=f"{curr['habit'].capitalize()}: {curr['target']}",
        inline=False
    )

    # footer hint
    embed.set_footer(text="Use !rank for details or !nextchallenge to preview what’s next.")

    await ctx.send(embed=embed)



@bot.command()
async def rank(ctx):
    """
    Show the group’s current rank and its full cumulative challenge.
    """
    # current rank info
    level = load_group_rank()
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
        # try parsing as integer level
        try:
            lvl = int(target)
        except ValueError:
            # fallback: look up by rank name (case-insensitive)
            match = next((r for r in RANKS if r["name"].lower() == target.lower()), None)
            if not match:
                return await ctx.reply(f"🚫 Invalid rank: `{target}`")
            lvl = match["level"]
        new = lvl

    # clamp within bounds
    new = max(1, min(new, len(RANKS)))

    if new <= old:
        return await ctx.reply(f"🚫 Cannot rank up to {new} (current is {old}). Use `!rankdown` to go down.")

    save_group_rank(new)
    rank = next(r for r in RANKS if r["level"] == new)

    embed = Embed(
        title=f"🎉 Group Promoted to Rank {new}: {rank['name'].title()}",
        colour=0x2ecc71
    )
    tasks = "\n".join(
        f"- **{r['habit'].capitalize()}:** {r['target']}"
        for r in RANKS[:new]
    )
    embed.add_field(
        name="🆕 New Cumulative Challenge",
        value=f"Complete all of the following:\n{tasks}",
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

    save_group_rank(new)
    rank = next(r for r in RANKS if r["level"] == new)

    embed = Embed(
        title=f"⚠️ Group Demoted to Rank {new}: {rank['name'].title()}",
        colour=0xe74c3c
    )
    tasks = "\n".join(
        f"- **{r['habit'].capitalize()}:** {r['target']}"
        for r in RANKS[:new]
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
    Show a member’s check‐in history for a week.
    Usage:
      !history                → your current week
      !history 2025-05-05     → your specified week
      !history @Friend        → Friend’s current week
      !history @Friend 2025-05-05
    """
    data = load()

    # 1️⃣ Pick target member
    if ctx.message.mentions:
        member = ctx.message.mentions[0]
    else:
        member = ctx.author

    # Strip mention out of args
    mention_ids = {f"<@{member.id}>", f"<@!{member.id}>"}
    args = [a for a in args if a not in mention_ids]

    # 2️⃣ Determine week
    week_id = args[0] if args else current_week_id()
    week_dt = dt.date.fromisoformat(week_id)

    # 3️⃣ Fetch that user’s days
    uid       = str(member.id)
    week_data = data.get(week_id, {})
    user_days = week_data.get(uid, {})

    if not user_days:
        return await ctx.reply(
            f"No check‐ins for {member.display_name} in week of {week_dt:%A %d %b %Y}."
        )

    # 4️⃣ Build embed
    embed = Embed(
        title=f"🕑 History for {member.display_name}",
        description=f"Week of {week_dt:%A %d %b %Y}",
        colour=0x9b59b6
    )

    # 5️⃣ One field per day, with bullet‐list of habits
    for day_iso in sorted(user_days):
        d = dt.date.fromisoformat(day_iso)
        day_str = d.strftime("%A %d %b")

        lines = []
        for tok in user_days[day_iso]:
            name, *val = tok.split(":")
            if val:
                # infer unit label from HABITS config
                cfg = HABITS[name]
                unit = "min" if cfg["unit"]=="minutes" else ""
                # special case reading→pages
                if name=="reading": unit = "pages"
                lines.append(f"- **{name}**: {val[0]} {unit}".rstrip())
            else:
                lines.append(f"- **{name}**")

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
    Preview the next rank’s cumulative challenge for the group.
    """
    # determine the upcoming rank
    next_level = load_group_rank() + 1
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
        description="Here’s what you can do with HabitBot:",
        colour=0x95a5a6
    )

    embed.add_field(
        name="🔹 `!ping`",
        value="Check bot responsiveness.",
        inline=False
    )

    embed.add_field(
        name="🔹 `!checkin <habit> [value] [weekday]`",
        value=(
            "Log a habit for today (default) or another day this week.\n"
            "• `!checkin meditation`\n"
            "• `!checkin meditation 45 Tuesday`\n"
            "• `!checkin exercise Friday`\n"
            "• `!checkin bedtime`"
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
        name="🔹 `!history [@User] [week]`",
        value=(
            "Show check-ins for you or another member for a week.\n"
            "• Default: current week\n"
            "• `!history @Friend`\n"
            "• `!history 2025-04-28`\n"
            "• `!history @Friend 2025-04-28`"
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
        value="List all possible ranks and show the current group rank.",
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
            "Demote the group’s rank by 1, or jump to a specific rank.\n"
            "• `!rankdown` → drop down by 1\n"
            "• `!rankdown 2` → set rank to level 2\n"
            "• `!rankdown bronze` → jump to Bronze"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 `!help`",
        value="Display this help message.",
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