# HabitBot

A Discord bot that tracks group habits, ranks up/down weekly, and provides progress reports.

## Commands

- `!checkin <habit> [value] [weekday]`
- `!progress`
- `!history [@User] [week]`
- `!delete <habit> [weekday]`
- `!nextchallenge`
- `!rank`
- `!ranks`
- `!rankup`
- `!rankdown`
- `!help`

## Setup

1. Copy `.env.example` to `.env` and fill in your Discord token.
2. Install dependencies: `pip install -r requirements.txt`
3. Run: `python bot.py`