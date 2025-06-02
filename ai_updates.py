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

def ask_gpt(system_content, user_content, max_tokens=1000, temperature=0.7):
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
    
    # Build habit targets map
    habit_targets = {}
    for r in RANKS[:current_rank]:
        for t in r["tasks"]:
            habit_targets[t["habit"]] = t["target"]
    
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
        for habit, target in habit_targets.items():
            completed = user_summary.get(habit, 0)
            
            # Determine if daily habit
            is_daily = (target == "7days" or 
                       (HABITS[habit].get("unit") == "bool" and 
                        HABITS[habit].get("weekly_target", 0) == 7))
            
            # Calculate target number
            if target.endswith("days"):
                target_num = int(target.rstrip("days"))
            else:
                target_num = HABITS[habit].get("weekly_target", 7)
            
            # Risk assessment
            if is_daily:
                if completed < (days_elapsed - 1):
                    risk = "HIGH"  # Missing previous days - truly behind
                elif completed < days_elapsed:
                    risk = "MEDIUM"  # Haven't done today yet - at risk
                else:
                    risk = "NONE"  # Up to date including today - tracking
            else:
                # For non-daily habits
                if completed == 0 and days_remaining < target_num:
                    risk = "HIGH"  # No progress and not enough days left
                elif completed < target_num - days_remaining:
                    risk = "MEDIUM"  # Behind pace but still possible
                elif completed >= target_num:
                    risk = "NONE"  # Already completed
                else:
                    risk = "LOW"  # On pace
            
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
    
    system_prompt = """You are an AI assistant for a Discord habit tracking bot called HabitBot. You provide daily team updates in a structured format.

Analyze the team data and respond with ONLY a valid JSON object with these exact keys:
- "user_status": An enumerated list showing each user's overall status for the week
- "summary": A concise 3-sentence maximum paragraph covering overall team performance, trends, and actionable insights

For user_status format:
- Use format: "username: ✅ tracking" or "username: ⚠️ at risk" or "username: ❌ behind"
- ✅ tracking = on pace to meet all weekly targets
- ⚠️ at risk = some habits behind pace but still recoverable  
- ❌ behind = significantly behind on daily habits or unlikely to meet targets
- Separate each user with \\n

For summary guidelines:
- Be encouraging but honest about challenges
- Focus on any insights, trends or weekly challenges
- Call out accountability issues diplomatically
- Keep to 3 sentences maximum

IMPORTANT: Respond with ONLY valid JSON, no other text or formatting."""

    user_prompt = f"""Analyze this team's habit tracking data:

WEEK: Day {context['week_info']['days_elapsed']}/7, {context['week_info']['days_remaining']} days remaining
RANK: {context['rank_info']['current_rank']} ({context['rank_info']['rank_name'].title()})
CHALLENGES: {context['rank_info']['challenges']}
TEAM: {context['team_stats']['active_users']}/{context['team_stats']['total_users']} active, {context['team_stats']['users_behind']} behind pace

USER ANALYSIS (by username):
{json.dumps(context['users'], indent=2)}

Return JSON with "user_status" and "summary" keys only."""

    response = ask_gpt(system_prompt, user_prompt, max_tokens=1000, temperature=0.5)
    
    try:
        # Clean response and attempt JSON parse
        cleaned_response = response.strip()
        
        # Remove any markdown code blocks if present
        if cleaned_response.startswith('```'):
            lines = cleaned_response.split('\n')
            lines = [line for line in lines if not line.strip().startswith('```')]
            cleaned_response = '\n'.join(lines)
        
        data = json.loads(cleaned_response)
        
        # Validate required keys
        required_keys = ["user_status", "summary"]
        for key in required_keys:
            if key not in data:
                data[key] = ""
        
        return data
        
    except json.JSONDecodeError as e:
        print(f"❌ JSON parsing failed: {e}")
        print(f"Raw response: {response}")
        
        # Enhanced fallback - try to extract content manually
        return parse_fallback_response_new(response, context)

def parse_fallback_response_new(response, context):
    """Parse non-JSON response as fallback for new format"""
    try:
        # Generate user status list
        user_status_lines = []
        for username, user_data in context['users'].items():
            # Determine status based on habits
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