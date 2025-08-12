# HabitBot

HabitBot is a Discord bot that helps a group of friends track daily habits, levels up the group through ranks as they complete challenges, and enforces accountability by ranking down when targets are missed.

---

## Table of Contents

1. [Features](#features)
2. [Rules & Mechanics](#rules--mechanics)
3. [Installation & Setup](#installation--setup)
4. [Configuration](#configuration)
5. [Usage & Commands](#usage--commands)
6. [Developer Command](#developer-command)
7. [Accessing the EC2 Instance](#accessing-the-ec2-instance)
8. [Contributing](#contributing)
9. [License](#license)

---

## Features

* **Habit Tracking**: Log multiple habits per day with `!checkin`.
* **Progress Visualization**: View weekly completion bars with `!progress`.
* **Rank System**: Unlock new challenges each week via predefined ranks.
* **Automatic Task Upgrades**: When a habit’s target increases (e.g., reading 10→20 pages), the new target replaces the old one in cumulative lists.
* **Manual Rank Control**: Promote or demote group rank with `!rankup`/`!rankdown`.
* **History Viewing**: Review past week’s check-ins with `!history`.
* **Developer Override**: Force a check-in for any user with `!forcecheckin` (restricted to bot owner).

---

## Rules & Mechanics

1. **Weekly Cycle**: Weeks run Monday–Sunday. Each Monday the group may rank up or down based on performance.
2. **Rank Up**: To rank up, **every** member must meet **all** weekly targets.
3. **Rank Down**: If **all** members miss **at least one** target, the group ranks down.
4. **Stasis**: If neither condition is met (partial success), the group stays at its current rank.
5. **Unlocks**: Each rank unlocks one or two habits. You can only `!checkin` habits unlocked at or below the current group rank.
6. **Task Replacement**: When a habit’s volume increases at a later rank (e.g., reading 10→20 pages), the higher-volume target replaces the lower one in cumulative lists.

---

## Installation & Setup

1. **Clone the repo**

   ```bash
   git clone https://github.com/your-org/habit-bot.git
   cd habit-bot
   ```
2. **Create a virtual environment & install dependencies**

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Environment Variables**
   Create a `.env` file in the project root:

   ```ini
   DISCORD_TOKEN=your_bot_token_here
   # (Optional) If using developer override, set your user ID:
   DEV_USER_ID=123456789012345678
   ```
4. **Run the bot**

   ```bash
   python bot.py
   ```

---

## Configuration

* **Data Files** (do not commit live data):

  * `progress.json` — habit logs per week.
  * `rank.json` — current group rank.
  * `meta.json` — last evaluation timestamp.
* **skip-worktree** is recommended to keep these files local:

  ```bash
  git update-index --skip-worktree progress.json rank.json meta.json
  ```

---

## Usage & Commands

Below is a summary of all commands. Use `!help` in Discord to view this list anytime.

### General Commands

* **`!ping`** — Check bot responsiveness.

### Check-In

* **`!checkin <habit> [value] [habit2] [value2] ... [weekday]`**

  * Log one or more habits on the specified day (default: today).
  * Minute‑based habits default to their minimum if no `value` is given.
  * **Examples:**

    ```
    !checkin meditation
    !checkin meditation 45
    !checkin reading 20 exercise
    !checkin walking 30 meditation 45 friday
    ```

### Progress & History

* **`!progress [@User]`** — Show weekly progress bars for you or a mentioned user.
* **`!history [@User] [week]`** — Display daily logs for the given week (ISO date `YYYY-MM-DD`).

### Rank & Challenges

* **`!ranks`** — List all ranks and their tasks in a single column.
* **`!rank`** — Show your current group rank and cumulative challenge.
* **`!nextchallenge`** — Preview the next rank’s challenge(s).
* **`!rankup [level|name]`** — Promote group by one rank or to a specified level/name.
* **`!rankdown [level|name]`** — Demote group by one rank or to a specified level/name.

### Deletion

* **`!delete <habit> [weekday]`** — Remove a logged habit for today or specified day.

---

## Developer Command

* **`!forcecheckin @User <habit> [value] ... [weekday]`** — Force‑log habits for any user (bot owner only). Replies with a minimal, inline confirmation.

---

## Accessing the EC2 Instance

Use your SSH key and the instance’s public DNS or IP:

```bash
ssh -i ~/ssh_keys/habit-bot-key.pem ec2-user@INSTANCE_PUBLIC_IPV4
```

Replace:

* `/path/to/your/key.pem` with your private key path.
* `ec2-user` with the appropriate username (e.g., `ubuntu` or `ec2-user`).
* `INSTANCE_PUBLIC_IPV4` with your instance’s public ipv4 address.

---

## Making Manual Progress Changes

Use SCP to pull and push files to and from the remote instance to make changes to `progress.json`.

Pull it down to your local machine:
```bash
scp -i ~/ssh_keys/habit-bot-key.pem ec2-user@3.26.26.202:/home/ec2-user/habit-bot/progress.json .
```

Push it back up after completing edits:
```bash
scp -i ~/ssh_keys/habit-bot-key.pem ./progress.json ec2-user@3.26.26.202:/home/ec2-user/habit-bot/progress.json
```

## Contributing

1. Fork this repository.
2. Create a feature branch (`git checkout -b feature/xyz`).
3. Commit your changes (`git commit -m "Add new feature"`).
4. Push to your branch (`git push origin feature/xyz`).
5. Open a Pull Request.

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

### New: Reaction‑based Daily Check‑ins (6:00 Adelaide)

- Every morning at **06:00 Australia/Adelaide**, the bot posts a **Daily Check‑in** embed in `#check-ins` with one emoji per habit unlocked at the current rank.
- **Click a reaction to log the minimum/default** for that habit (e.g., 🧘 logs meditation for 30 min at Rank 7).  
  - Want to log **more than the minimum**? Use `/checkin` as usual (custom values take precedence over reactions).
- **Undo**: remove your reaction to un‑check the default value for that day. If you logged a **custom** value via `/checkin`, removing the reaction does **nothing** (we don’t override custom entries).
- **Backfill**: React on **yesterday’s** (or any day’s) check‑in message to write to **that date**.
- Confirmations go to **`#check-in-logs`** to avoid cluttering `#check-ins`.

#### Channels & permissions
- `#check-ins`: Only reactions and the `/checkin` command should be used here (configure channel perms in Discord; the bot already warns if other commands are used).
- `#check-in-logs`: A read‑only channel for confirmation messages (“✅ Ethan checked meditation for 2025‑08‑11”). Create this channel if it doesn’t exist.
- Bot requires: **View Channel**, **Send Messages**, **Add Reactions**, **Read Message History**, optional **Manage Messages** (to remove unknown reactions).

#### Admin
- `/postcheckin <date>`: Re‑post the check‑in embed for a given date (`YYYY-MM-DD`, `today`, `yesterday`). Admin‑only.
