# reminder.py - Reminder system helper functions for HabitBot

import discord
from discord.ext import tasks
from discord import Embed
import datetime as dt
from datetime import datetime, timedelta

from helpers import get_users_needing_reminders, LOCAL_TZ

# Global reference to bot - will be set when imported
bot = None

def setup_reminders(bot_instance):
    """Initialize the reminder system with bot instance"""
    global bot
    bot = bot_instance
    
    # Start the daily task
    if not daily_reminder_task.is_running():
        daily_reminder_task.start()
        print("Daily reminder task started")

# Scheduled task for daily reminders
@tasks.loop(time=dt.time(hour=20, minute=30, tzinfo=LOCAL_TZ))  # 8:30 PM Adelaide Time
async def daily_reminder_task():
    """Send daily reminders to users who haven't checked in"""
    try:
        await send_daily_reminders()
    except Exception as e:
        print(f"Error in daily reminder task: {e}")

async def send_daily_reminders():
    """Send reminders to users who need them"""
    if not bot:
        print("Bot not initialized for reminders")
        return
        
    users_needing_reminders = get_users_needing_reminders()
    if not users_needing_reminders:
        print("No users need reminders today")
        return
    
    # Get the main guild (assuming single server bot)
    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        print("No guild found for reminders")
        return
    
    # Send reminders via DM
    reminder_count = 0
    for user_id in users_needing_reminders:
        try:
            member = guild.get_member(int(user_id))
            if member:
                embed = Embed(
                    title="🔔 Daily Check-in Reminder",
                    description=f"Hey {member.display_name}! You haven't checked in today yet.",
                    colour=0x3498db
                )
                embed.add_field(
                    name="Quick Check-in",
                    value="Head to the check-ins channel and use `/checkin` to log your habits!",
                    inline=False
                )
                embed.set_footer(text="💡 Use /reminders to turn these off if you don't want them")
                
                try:
                    await member.send(embed=embed)
                    reminder_count += 1
                    print(f"Sent reminder to {member.display_name}")
                except discord.Forbidden:
                    print(f"Cannot DM {member.display_name} - DMs disabled")
                except Exception as dm_error:
                    print(f"Error DMing {member.display_name}: {dm_error}")
                
        except Exception as e:
            print(f"Error processing reminder for {user_id}: {e}")
    
    print(f"Sent {reminder_count} daily reminders")

def stop_reminder_task():
    """Stop the reminder task (for cleanup)"""
    if daily_reminder_task.is_running():
        daily_reminder_task.stop()
        print("Daily reminder task stopped")