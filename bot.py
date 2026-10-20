import os
import sqlite3
import asyncio
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("ERROR: DISCORD_TOKEN is missing!")

def init_db():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            xp INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config (
            guild_id INTEGER PRIMARY KEY,
            log_channel_id INTEGER
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_config(guild_id: int):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT log_channel_id FROM config WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def set_log_channel_db(guild_id: int, channel_id: int):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO config (guild_id, log_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET log_channel_id = ?", (guild_id, channel_id, channel_id))
    conn.commit()
    conn.close()

def get_xp(user_id: int) -> int:
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT xp FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0

def set_xp(user_id: int, amount: int):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT xp FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        cursor.execute("UPDATE users SET xp = ? WHERE user_id = ?", (amount, user_id))
    else:
        cursor.execute("INSERT INTO users (user_id, xp) VALUES (?, ?)", (user_id, amount))
    conn.commit()
    conn.close()

def add_xp(user_id: int, amount: int) -> int:
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT xp FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        new_xp = max(0, row[0] + amount)
        cursor.execute("UPDATE users SET xp = ? WHERE user_id = ?", (new_xp, user_id))
    else:
        new_xp = max(0, amount)
        cursor.execute("INSERT INTO users (user_id, xp) VALUES (?, ?)", (user_id, new_xp))
    conn.commit()
    conn.close()
    return new_xp

async def log_action(guild: discord.Guild, message: str):
    log_channel_id = get_config(guild.id)
    if log_channel_id:
        log_chan = guild.get_channel(log_channel_id)
        if log_chan:
            try:
                embed = discord.Embed(title="XP Audit Log", description=message, color=discord.Color.dark_blue(), timestamp=discord.utils.utcnow())
                await log_chan.send(embed=embed)
            except discord.HTTPException:
                pass

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

class PTFSBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()

bot = PTFSBot()

@bot.event
async def on_ready():
    print(f"Bot is online as: {bot.user.name} ({bot.user.id})")

@bot.tree.command(name="balance", description="Check your current XP balance.")
async def balance(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user_xp = get_xp(interaction.user.id)
    await interaction.followup.send(f"Your current balance is: **{user_xp} XP**", ephemeral=True)

@bot.tree.command(name="profile", description="View user's XP profile.")
@app_commands.describe(member="The member whose profile you want to check (leave empty for yours)")
async def profile(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.defer(ephemeral=True)
    target = member or interaction.user
    user_xp = get_xp(target.id)

    embed = discord.Embed(title=f"XP Profile - {target.display_name}", color=discord.Color.gold())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Current XP", value=f"**{user_xp} XP**", inline=True)

    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="xp-add", description="[Admin] Add XP to a member.")
@app_commands.describe(member="The member to give XP to", amount="Amount of XP", reason="Reason for adding XP")
@app_commands.checks.has_permissions(administrator=True)
async def xp_add(interaction: discord.Interaction, member: discord.Member, amount: int, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)
    if amount <= 0:
        await interaction.followup.send("Amount must be greater than 0.", ephemeral=True)
        return
    new_xp = add_xp(member.id, amount)
    await interaction.followup.send(f"Successfully added {amount} XP to {member.mention}. New total: **{new_xp} XP**", ephemeral=True)
    await log_action(interaction.guild, f"➕ Admin {interaction.user.mention} added **{amount} XP** to {member.mention}.\n**Reason:** {reason}\nNew total: **{new_xp} XP**.")

@bot.tree.command(name="xp-remove", description="[Admin] Remove XP from a member.")
@app_commands.describe(member="The member to take XP from", amount="Amount of XP", reason="Reason for removing XP")
@app_commands.checks.has_permissions(administrator=True)
async def xp_remove(interaction: discord.Interaction, member: discord.Member, amount: int, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)
    if amount <= 0:
        await interaction.followup.send("Amount must be greater than 0.", ephemeral=True)
        return
    new_xp = add_xp(member.id, -amount)
    await interaction.followup.send(f"Successfully removed {amount} XP from {member.mention}. New total: **{new_xp} XP**", ephemeral=True)
    await log_action(interaction.guild, f"➖ Admin {interaction.user.mention} removed **{amount} XP** from {member.mention}.\n**Reason:** {reason}\nNew total: **{new_xp} XP**.")

@bot.tree.command(name="xp-set", description="[Admin] Set exact XP balance for a member.")
@app_commands.describe(member="The member to update", amount="Exact amount of XP", reason="Reason")
@app_commands.checks.has_permissions(administrator=True)
async def xp_set(interaction: discord.Interaction, member: discord.Member, amount: int, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)
    if amount < %0:  # opravené preistotu
    if amount < 0:
        await interaction.followup.send("XP cannot be negative.", ephemeral=True)
        return
    set_xp(member.id, amount)
    await interaction.followup.send(f"Successfully set XP balance for {member.mention} to **{amount} XP**.", ephemeral=True)
    await log_action(interaction.guild, f"⚙️ Admin {interaction.user.mention} set {member.mention}'s balance to **{amount} XP**.\n**Reason:** {reason}")

@bot.tree.command(name="set-log-channel", description="[Admin] Set the channel for XP audit logs.")
@app_commands.describe(channel="The logging channel")
@app_commands.checks.has_permissions(administrator=True)
async def set_log_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    set_log_channel_db(interaction.guild.id, channel.id)
    await interaction.followup.send(f"Audit log channel has been set to {channel.mention}.", ephemeral=True)

# --- MINI WEB SERVER PRE FLY.IO (UDRŽIAVA BOTA ŽIVÉHO 24/7) ---
async def handle(request):
    return web.Response(text="Bot is running 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()

async def main():
    await asyncio.gather(
        bot.start(TOKEN),
        start_web_server()
    )

if __name__ == "__main__":
    asyncio.run(main())
