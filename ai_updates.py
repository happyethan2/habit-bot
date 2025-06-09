# ai_updates.py
import os
import json
import discord
from datetime import datetime, timedelta, date
from collections import defaultdict
from openai import OpenAI

from storage import load
from rank_storage import load as load_group_rank
from habits import HABITS
from ranks import RANKS
from helpers import (
    current_week_id, get_summary_for, get_all_streaks, 
    format_streak_display, LOCAL_TZ
)

# Initialize OpenAI client lazily
_client = None

def get_openai_client():
    """Get OpenAI client, initializing if needed"""
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables")
        _client = OpenAI(api_key=api_key)
    return _client

def ask_gpt(system_content, user_content, max_tokens=1000, temperature=0):
    """Make OpenAI API call"""
    client = get_openai_client()
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content}
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return response.choices[0].message.content.strip()

async def gather_team_context(bot=None):
    """Gather comprehensive team data for AI analysis"""
    data = load()
    current_week = current_week_id()
    current_rank = load_group_rank()
    
    # Get current week summary and raw data
    week_summary = get_summary_for(current_week)
    week_data = data.get(current_week, {})
    
    # Calculate days into week
    today = datetime.now(LOCAL_TZ).date()
    week_start = date.fromisoformat(current_week)
    days_elapsed = (today - week_start).days + 1  # +1 because we count today
    days_remaining = 7 - days_elapsed
    
    # Get current rank challenges
    rank_info = next(r for r in RANKS if r["level"] == current_rank)
    
    # Build habit targets map using same logic as progress command
    unlocked = []
    for r in RANKS[:current_rank]:
        for t in r["tasks"]:
            if t["habit"] not in unlocked:
                unlocked.append(t["habit"])

    # Compute weekly targets per habit from RANKS (same as progress command)
    habit_targets = {}
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
            habit_targets[h] = max(day_targets)
        else:
            habit_targets[h] = HABITS[h].get("weekly_target", 7)
    
    # Helper function to get username
    async def get_username(user_id):
        if not bot:
            return f"User_{user_id[:6]}"
        try:
            # Try to get from guild members first
            for guild in bot.guilds:
                member = guild.get_member(int(user_id))
                if member:
                    return member.display_name
            # Fallback to fetching user
            user = await bot.fetch_user(int(user_id))
            return user.display_name if user else f"User_{user_id[:6]}"
        except:
            return f"User_{user_id[:6]}"
    
    # Analyze each user's progress
    users_analysis = {}
    username_map = {}
    all_users = set(week_data.keys()) if week_data else set()
    
    for user_id in all_users:
        # Get username
        username = await get_username(user_id)
        username_map[user_id] = username
        
        user_days = week_data[user_id]
        user_summary = week_summary.get(user_id, {})
        
        # Get streak data
        streaks = get_all_streaks(user_id)
        
        # Analyze habit progress
        habit_analysis = {}
        for habit, target_num in habit_targets.items():
            completed = user_summary.get(habit, 0)
            
            # Determine if daily habit (target_num is now available)
            is_daily = (target_num == 7)  # Simple: if target is 7 times per week, it's daily
            
            # Risk assessment
            if is_daily:
                # For daily habits (must be done every day)
                if completed >= target_num:
                    risk = "NONE"  # Already completed all required days
                elif completed < (days_elapsed - 1):
                    risk = "HIGH"  # Missed previous days - truly behind
                elif completed < days_elapsed:
                    risk = "MEDIUM"  # Haven't done today yet - at risk
                else:
                    risk = "NONE"  # Up to date including today
            else:
                # For weekly habits (need X times per week)
                tasks_remaining = target_num - completed
                if completed >= target_num:
                    risk = "NONE"  # Already completed target
                elif tasks_remaining > days_remaining:
                    risk = "HIGH"  # Impossible to complete - behind
                elif tasks_remaining == days_remaining:
                    risk = "MEDIUM"  # Must do today or will fail - at risk
                else:
                    risk = "LOW"  # Have buffer days - tracking
            
            habit_analysis[habit] = {
                "completed": completed,
                "target": target_num,
                "is_daily": is_daily,
                "risk": risk
            }
        
        # Check for recent activity
        recent_checkins = sum(1 for day_data in user_days.values() if day_data)
        days_since_last = 0
        for i in range(days_elapsed):
            check_date = today - timedelta(days=i)
            if check_date.isoformat() in user_days:
                break
            days_since_last += 1
        
        users_analysis[username] = {  # Use username as key instead of user_id
            "user_id": user_id,  # Keep ID for reference
            "habits": habit_analysis,
            "recent_checkins": recent_checkins,
            "days_since_last": days_since_last,
            "streaks": streaks
        }
    
    # Overall team stats
    team_stats = {
        "total_users": len(all_users),
        "active_users": len([u for u in users_analysis.values() if u["recent_checkins"] > 0]),
        "users_behind": len([u for u in users_analysis.values() 
                           if any(h["risk"] in ["HIGH", "MEDIUM"] for h in u["habits"].values())]),
        "habits_at_risk": defaultdict(int)
    }
    
    # Count habits at risk across team
    for user_data in users_analysis.values():
        for habit, data in user_data["habits"].items():
            if data["risk"] in ["HIGH", "MEDIUM"]:
                team_stats["habits_at_risk"][habit] += 1
    
    # Filter out function objects from habits config
    habits_config_clean = {}
    for habit, config in HABITS.items():
        habits_config_clean[habit] = {
            k: v for k, v in config.items() 
            if not callable(v)  # Exclude function objects
        }
    
    return {
        "week_info": {
            "week_start": current_week,
            "days_elapsed": days_elapsed,
            "days_remaining": days_remaining,
            "current_date": today.isoformat()
        },
        "rank_info": {
            "current_rank": current_rank,
            "rank_name": rank_info["name"],
            "challenges": habit_targets
        },
        "users": users_analysis,  # Now keyed by username
        "username_map": username_map,  # ID to username mapping
        "team_stats": team_stats,
        "habits_config": habits_config_clean
    }

async def generate_daily_update(bot=None):
    """Generate AI-powered daily update with structured data"""
    context = await gather_team_context(bot)
    
    # Generate user status ourselves (we control this logic)
    user_status_lines = []
    for username in sorted(context['users'].keys()):  # Sort alphabetically
        user_data = context['users'][username]
        high_risk_count = sum(1 for h in user_data['habits'].values() if h['risk'] == 'HIGH')
        medium_risk_count = sum(1 for h in user_data['habits'].values() if h['risk'] == 'MEDIUM')
        
        if high_risk_count > 0:
            status = "❌ behind"
        elif medium_risk_count > 0:
            status = "⚠️ at risk"
        else:
            status = "✅ tracking"
        
        user_status_lines.append(f"{username}: {status}")
    
    user_status = "\n".join(user_status_lines)
    
    # Simple AI prompt - just ask for summary text
    system_prompt = """You are an AI assistant for a Discord habit tracking bot. Analyze the team data and provide a **very concise** 3-sentence summary covering team performance, trends and insights. Be honest about challenges."""
    
    user_prompt = f"""Analyze this team's habit tracking data:

WEEK: Day {context['week_info']['days_elapsed']}/7, {context['week_info']['days_remaining']} days remaining
RANK: {context['rank_info']['current_rank']} ({context['rank_info']['rank_name'].title()})
CHALLENGES: {context['rank_info']['challenges']}
TEAM: {context['team_stats']['active_users']}/{context['team_stats']['total_users']} active

DETAILED ANALYSIS:
{json.dumps(context['users'], indent=2)}

Provide a very concise 3-sentence maximum analysis covering overall team performance and trends. Don't provide a final supportive sentence, and don't reference the team's rank.

Summary guidelines:
- Be honest about potential challenges
- Focus on any insights, trends or weekly challenges
- Call out accountability issues diplomatically

IMPORTANT: Be very concise and succinct with a **STRICT** character limit of 180!"""
    
    summary = ask_gpt(system_prompt, user_prompt, max_tokens=500, temperature=0.6)
    
    return {
        "user_status": user_status,
        "summary": summary.strip()
    }


def parse_fallback_response_new(response, context):
    """Parse non-JSON response as fallback for new format"""
    try:
        print("🔄 Using fallback - generating status from risk analysis")
        
        # Generate user status list using same logic as main analysis
        user_status_lines = []
        for username, user_data in context['users'].items():
            # Count risk levels
            high_risk_count = sum(1 for h in user_data['habits'].values() if h['risk'] == 'HIGH')
            medium_risk_count = sum(1 for h in user_data['habits'].values() if h['risk'] == 'MEDIUM')
            low_risk_count = sum(1 for h in user_data['habits'].values() if h['risk'] == 'LOW')
            none_risk_count = sum(1 for h in user_data['habits'].values() if h['risk'] == 'NONE')
            
            print(f"🔍 {username}: HIGH={high_risk_count}, MEDIUM={medium_risk_count}, LOW={low_risk_count}, NONE={none_risk_count}")
            
            # Determine overall user status (same logic as AI should use)
            if high_risk_count > 0:
                status = "❌ behind"
            elif medium_risk_count > 0:
                status = "⚠️ at risk"
            elif low_risk_count > 0:
                status = "✅ tracking"  # LOW means tracking with buffer
            else:
                status = "✅ tracking"  # All NONE means perfect
            
            user_status_lines.append(f"{username}: {status}")
        
        user_status = "\n".join(user_status_lines)
        
        # Generate summary
        active_users = context['team_stats']['active_users']
        total_users = context['team_stats']['total_users']
        behind_count = context['team_stats']['users_behind']
        days_elapsed = context['week_info']['days_elapsed']
        
        if behind_count == 0:
            summary = f"The team is performing excellently with all {active_users} members on track for their weekly targets. "
            summary += f"With {7 - days_elapsed} days remaining, maintaining current momentum will ensure successful rank completion. "
            summary += "Keep up the consistent daily logging and mutual accountability!"
        else:
            summary = f"Team is {days_elapsed} days into the week with {behind_count} members needing to catch up on their habit targets. "
            summary += "Daily habits require immediate attention as missed days cannot be recovered. "
            summary += "Focus on consistent check-ins and supporting teammates who have fallen behind."
        
        return {
            "user_status": user_status,
            "summary": summary
        }
        
    except Exception as e:
        print(f"❌ Fallback parsing failed: {e}")
        return {
            "user_status": "Status generation failed - check logs",
            "summary": f"Day {context['week_info']['days_elapsed']}/7 of the current week. AI update system needs attention."
        }
    

async def send_daily_update(bot):
    """Send daily update to #updates channel as formatted embed"""
    # Find the updates channel
    updates_channel = None
    for guild in bot.guilds:
        channel = discord.utils.get(guild.channels, name="updates")
        if channel:
            updates_channel = channel
            break
    
    if not updates_channel:
        print("❌ Updates channel not found")
        return
    
    try:
        update_data = await generate_daily_update(bot)
        context = await gather_team_context(bot)
        
        # Calculate team performance percentage
        week_info = context['week_info']
        team_stats = context['team_stats']
        
        # Create embed
        embed = discord.Embed(
            title="📊 Daily Team Update",
            color=0x3498db,
            timestamp=datetime.now(LOCAL_TZ)
        )
        
        # Add week info to description
        week_start = datetime.fromisoformat(week_info['week_start']).strftime("%A %d %b %Y")
        description = f"**Week of {week_start}**\n"
        description += f"📅 Day {week_info['days_elapsed']}/7 • {week_info['days_remaining']} days remaining\n"
        description += f"🎖️ **Rank {context['rank_info']['current_rank']}: {context['rank_info']['rank_name'].title()}**"
        
        embed.description = description
        
        # user status updates
        if update_data['user_status'].strip():
            embed.add_field(name="👤 Individual Status", value=f"```{update_data['user_status']}```", inline=False)
        
        # ai summary
        today_weekday = datetime.now(LOCAL_TZ).weekday()
        summary_title = "📋 Weekly Summary" if today_weekday == 6 else "📋 Status Update"
        if update_data['summary'].strip():
            embed.add_field(name=summary_title, value=update_data['summary'], inline=False)
        
        # footer with motivation
        embed.set_footer(text="🔥 Keep building those habits! Use /progress to see your status.")
        
        await updates_channel.send(embed=embed)
        print("✅ Daily update sent successfully")
        
    except Exception as e:
        print(f"❌ Failed to generate/send daily update: {e}")
        # Send a simple error message to updates channel if possible
        try:
            await updates_channel.send("⚠️ Daily update failed to generate. Check logs.")
        except:
            pass