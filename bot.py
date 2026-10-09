import os
import sqlite3
import time
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# Load token from .env file
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

if not TOKEN:
    raise ValueError("ERROR: Bot token not found in .env file!")

# Default Role Thresholds (XP roles only)
DEFAULT_THRESHOLDS = [
    (150, "Officer"),
    (100, "Senior"),
    (50, "Cadet")
]

# Permanent non-XP roles managed by verification/application
VERIFIED_ROLE_NAME = "Verified"
UNVERIFIED_ROLE_NAME = "Unverified"

# Cooldown tracking dictionary: {user_id: timestamp_of_last_request}
cooldowns = {}
COOLDOWN_SECONDS = 600  # 10 minutes

# Database Initialization
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
            mod_channel_id INTEGER,
            log_channel_id INTEGER
        )
    """)
    try:
        cursor.execute("ALTER TABLE config ADD COLUMN promo_channel_id INTEGER")
    except sqlite3.OperationalError:
        pass  # Column already exists

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS thresholds (
            xp_req INTEGER PRIMARY KEY,
            role_name TEXT NOT NULL
        )
    """)
    cursor.execute("SELECT COUNT(*) FROM thresholds")
    if cursor.fetchone()[0] == 0:
        for req, rname in DEFAULT_THRESHOLDS:
            cursor.execute("INSERT INTO thresholds (xp_req, role_name) VALUES (?, ?)", (req, rname))
    conn.commit()
    conn.close()

init_db()

def get_thresholds():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT xp_req, role_name FROM thresholds ORDER BY xp_req DESC")
    rows = cursor.fetchall()
    conn.close()
    return rows if rows else DEFAULT_THRESHOLDS

def set_threshold_db(xp_req: int, role_name: str):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO thresholds (xp_req, role_name) VALUES (?, ?) ON CONFLICT(xp_req) DO UPDATE SET role_name = ?", (xp_req, role_name, role_name))
    conn.commit()
    conn.close()

def get_config(guild_id: int):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT mod_channel_id, log_channel_id, promo_channel_id FROM config WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()
    default_mod = 1557784812079808582
    if row:
        return (row[0] if row[0] is not None else default_mod), row[1], row[2]
    return default_mod, None, None

def set_mod_channel_db(guild_id: int, channel_id: int):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO config (guild_id, mod_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET mod_channel_id = ?", (guild_id, channel_id, channel_id))
    conn.commit()
    conn.close()

def set_log_channel_db(guild_id: int, channel_id: int):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO config (guild_id, log_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET log_channel_id = ?", (guild_id, channel_id, channel_id))
    conn.commit()
    conn.close()

def set_promo_channel_db(guild_id: int, channel_id: int):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO config (guild_id, promo_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET promo_channel_id = ?", (guild_id, channel_id, channel_id))
    conn.commit()
    conn.close()

def get_xp(user_id: int) -> int:
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT xp FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0

def set_xp(user_id: int, amount: int) -> int:
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
    return amount

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
    _, log_channel_id, _ = get_config(guild.id)
    if log_channel_id:
        log_chan = guild.get_channel(log_channel_id)
        if log_chan:
            try:
                embed = discord.Embed(title="XP Audit Log", description=message, color=discord.Color.dark_blue(), timestamp=discord.utils.utcnow())
                await log_chan.send(embed=embed)
            except discord.HTTPException:
                pass

async def announce_rank_change(member: discord.Member, new_role_name: str, old_role_name: str, promoter: discord.abc.User, reason: str = None):
    _, _, promo_channel_id = get_config(member.guild.id)
    if promo_channel_id:
        promo_chan = member.guild.get_channel(promo_channel_id)
        if promo_chan:
            try:
                role_obj = discord.utils.get(member.guild.roles, name=new_role_name)
                role_mention = role_obj.mention if role_obj else f"@{new_role_name}"
                
                thresholds = get_thresholds()
                old_index = -1
                new_index = -1
                for idx, (_, rname) in enumerate(reversed(thresholds)):
                    if rname == old_role_name:
                        old_index = idx
                    if rname == new_role_name:
                        new_index = idx

                action_word = "promoted" if new_index >= old_index else "demoted"
                
                msg = f"{promoter.mention} {action_word} {member.mention} to {role_mention}"
                if reason and action_word == "demoted":
                    msg += f" ({reason})"
                
                await promo_chan.send(msg)
            except discord.HTTPException:
                pass

async def update_roles(member: discord.Member, new_xp: int, promoter: discord.abc.User, reason: str = None):
    guild = member.guild
    thresholds = get_thresholds()
    all_xp_role_names = [role_name for _, role_name in thresholds]
    
    # 1. Handle Verified & Unverified roles (Permanent verification upon getting XP/participating)
    verified_role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)
    unverified_role = discord.utils.get(guild.roles, name=UNVERIFIED_ROLE_NAME)
    
    try:
        if verified_role and verified_role not in member.roles:
            await member.add_roles(verified_role)
        if unverified_role and unverified_role in member.roles:
            await member.remove_roles(unverified_role)
    except discord.HTTPException:
        pass

    # 2. Handle XP rank thresholds
    target_role_name = None
    for xp_req, role_name in thresholds:
        if new_xp >= xp_req:
            target_role_name = role_name
            break

    current_xp_role = None
    for role in member.roles:
        if role.name in all_xp_role_names:
            current_xp_role = role.name
            break

    roles_to_remove = []
    role_to_add = None

    for role in guild.roles:
        if role.name in all_xp_role_names:
            if target_role_name and role.name == target_role_name:
                role_to_add = role
            elif role in member.roles:
                roles_to_remove.append(role)

    if roles_to_remove:
        try:
            await member.remove_roles(*roles_to_remove)
        except discord.HTTPException:
            pass

    if role_to_add and role_to_add not in member.roles:
        try:
            await member.add_roles(role_to_add)
            if current_xp_role and current_xp_role != role_to_add.name:
                await announce_rank_change(member, role_to_add.name, current_xp_role, promoter, reason)
            elif not current_xp_role:
                await announce_rank_change(member, role_to_add.name, "None", promoter, reason)
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

class RejectReasonModal(discord.ui.Modal, title="Reject XP Request"):
    reason = discord.ui.TextInput(
        label="Reason for rejection",
        style=discord.TextStyle.paragraph,
        placeholder="Type the reason why this request is rejected...",
        required=True
    )

    def __init__(self, view: "XPRequestView"):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        for child in self.view.children:
            child.disabled = True

        rejection_reason = self.reason.value

        applicant = interaction.guild.get_member(self.view.applicant_id)
        if applicant:
            try:
                await applicant.send(
                    f"Your request for {self.view.xp_amount} XP has been **rejected**.\n"
                    f"**Reason:** {rejection_reason}"
                )
            except discord.HTTPException:
                pass

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.add_field(name="Status", value=f"Rejected by {interaction.user.mention}", inline=False)
        embed.add_field(name="Rejection Reason", value=rejection_reason, inline=False)

        await interaction.response.edit_message(embed=embed, view=self.view)
        await log_action(interaction.guild, f"❌ XP Request of **{self.view.xp_amount} XP** for <@{self.view.applicant_id}> was **rejected** by {interaction.user.mention}.\n**Reason:** {rejection_reason}")

class XPRequestView(discord.ui.View):
    def __init__(self, applicant_id: int, xp_amount: int):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id
        self.xp_amount = xp_amount

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, custom_id="approve_xp")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        
        new_xp = add_xp(self.applicant_id, self.xp_amount)
        
        applicant = interaction.guild.get_member(self.applicant_id)
        if applicant:
            await update_roles(applicant, new_xp, promoter=interaction.user, reason="Training approved")
            try:
                await applicant.send(f"Your request for {self.xp_amount} XP has been **approved**! Your new balance is **{new_xp} XP**.")
            except discord.HTTPException:
                pass

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(name="Status", value=f"Approved by {interaction.user.mention}", inline=False)
        
        await interaction.response.edit_message(embed=embed, view=self)
        await log_action(interaction.guild, f"✅ XP Request of **{self.xp_amount} XP** for <@{self.applicant_id}> was **approved** by {interaction.user.mention}. New total: **{new_xp} XP**.")

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.red, custom_id="reject_xp")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RejectReasonModal(view=self)
        await interaction.response.send_modal(modal)

@bot.event
async def on_ready():
    print(f"Bot logged in as: {bot.user.name} ({bot.user.id})")

@bot.tree.command(name="balance", description="Check your current XP balance.")
async def balance(interaction: discord.Interaction):
    user_xp = get_xp(interaction.user.id)
    await interaction.response.send_message(f"Your current balance is: **{user_xp} XP**", ephemeral=True)

@bot.tree.command(name="profile", description="View user XP profile and rank progress.")
@app_commands.describe(member="The member whose profile you want to check (leave blank for yours)")
async def profile(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    user_xp = get_xp(target.id)
    thresholds = get_thresholds()

    current_role = "None"
    next_role = thresholds[0][1] if thresholds else "Officer"
    next_threshold = thresholds[-1][0] if thresholds else 50

    for req, rname in thresholds:
        if user_xp >= req:
            current_role = rname
            break

    for req, rname in reversed(thresholds):
        if user_xp < req:
            next_threshold = req
            next_role = rname
            break
    else:
        next_role = "Max Rank Reached"
        next_threshold = user_xp

    embed = discord.Embed(title=f"XP Profile - {target.display_name}", color=discord.Color.gold())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Current XP", value=f"**{user_xp} XP**", inline=True)
    embed.add_field(name="Current Rank", value=f"**{current_role}**", inline=True)
    
    if next_role != "Max Rank Reached":
        needed = next_threshold - user_xp
        embed.add_field(name="Next Rank", value=f"{next_role} (Needs {needed} more XP)", inline=False)
    else:
        embed.add_field(name="Next Rank", value="You have reached the highest rank!", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="leaderboard", description="Show the server XP leaderboard (Top 10).")
async def leaderboard(interaction: discord.Interaction):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, xp FROM users ORDER BY xp DESC LIMIT 10")
    rows = cursor.fetchall()
    conn.close()

    embed = discord.Embed(title="🏆 Server XP Leaderboard (Top 10)", color=discord.Color.blurple())
    
    if not rows:
        embed.description = "No users found in the leaderboard yet."
    else:
        desc = ""
        for idx, (uid, xp_val) in enumerate(rows, start=1):
            medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"`#{idx}`"
            desc += f"{medal} <@{uid}> — **{xp_val} XP**\n"
        embed.description = desc

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="xp-request", description="Request XP for completed training.")
@app_commands.describe(
    xp="Amount of XP requested",
    reason="Reason for XP request (mandatory)",
    proof_link="Link to screenshot/video proof (mandatory if no file uploaded)",
    proof_file="Upload screenshot/video proof file (mandatory if no link provided)"
)
async def xp_request(
    interaction: discord.Interaction, 
    xp: int, 
    reason: str, 
    proof_link: str = None, 
    proof_file: discord.Attachment = None
):
    is_privileged = interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild
    if not is_privileged:
        last_time = cooldowns.get(interaction.user.id, 0)
        current_time = time.time()
        elapsed = current_time - last_time
        if elapsed < COOLDOWN_SECONDS:
            remaining = int((COOLDOWN_SECONDS - elapsed) / 60) + 1
            await interaction.response.send_message(f"⏳ Cooldown active! You must wait about **{remaining} more minutes** before submitting another XP request.", ephemeral=True)
            return
        cooldowns[interaction.user.id] = current_time

    await interaction.response.defer(ephemeral=True)

    if xp <= 0:
        await interaction.followup.send("XP amount must be greater than 0.", ephemeral=True)
        return

    if not proof_link and not proof_file:
        await interaction.followup.send("Error: You must provide either a proof link or upload a proof file!", ephemeral=True)
        return

    mod_channel_id, _, _ = get_config(interaction.guild.id)
    mod_channel = interaction.guild.get_channel(mod_channel_id)
    if not mod_channel:
        await interaction.followup.send("Error: Moderator channel not found. Please contact an admin.", ephemeral=True)
        return

    embed = discord.Embed(
        title="XP Request",
        description=f"User {interaction.user.mention} requested **{xp} XP**.",
        color=discord.Color.blue()
    )
    embed.add_field(name="Reason", value=reason, inline=False)

    if proof_link:
        embed.add_field(name="Proof (Link)", value=proof_link, inline=False)

    if proof_file:
        if proof_file.content_type and proof_file.content_type.startswith("image/"):
            embed.set_image(url=proof_file.url)
        else:
            embed.add_field(name="Proof (File)", value=f"[{proof_file.filename}]({proof_file.url})", inline=False)

    embed.set_footer(text=f"Applicant ID: {interaction.user.id}")

    view = XPRequestView(applicant_id=interaction.user.id, xp_amount=xp)
    
    try:
        await mod_channel.send(embed=embed, view=view)
        await interaction.followup.send("Your XP request has been successfully submitted to the moderators.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("Error: Bot lacks permission to send messages to the moderator channel.", ephemeral=True)

@bot.tree.command(name="xp-add", description="[Admin] Manually add XP to a member.")
@app_commands.describe(member="Member to add XP to", amount="Amount of XP to add", reason="Reason for adding XP (optional)")
@app_commands.checks.has_permissions(administrator=True)
async def xp_add(interaction: discord.Interaction, member: discord.Member, amount: int, reason: str = "No reason provided"):
    if amount <= 0:
        await interaction.response.send_message("Amount must be greater than 0.", ephemeral=True)
        return
    new_xp = add_xp(member.id, amount)
    await update_roles(member, new_xp, promoter=interaction.user, reason=reason)
    await interaction.response.send_message(f"Successfully added {amount} XP to {member.mention}. New total: **{new_xp} XP**", ephemeral=True)
    await log_action(interaction.guild, f"➕ Admin {interaction.user.mention} added **{amount} XP** to {member.mention}.\n**Reason:** {reason}\nNew total: **{new_xp} XP**.")

@bot.tree.command(name="xp-remove", description="[Admin] Manually remove XP from a member.")
@app_commands.describe(member="Member to remove XP from", amount="Amount of XP to remove", reason="Reason for removing XP (optional)")
@app_commands.checks.has_permissions(administrator=True)
async def xp_remove(interaction: discord.Interaction, member: discord.Member, amount: int, reason: str = "No reason provided"):
    if amount <= 0:
        await interaction.response.send_message("Amount must be greater than 0.", ephemeral=True)
        return
    new_xp = add_xp(member.id, -amount)
    await update_roles(member, new_xp, promoter=interaction.user, reason=reason)
    await interaction.response.send_message(f"Successfully removed {amount} XP from {member.mention}. New total: **{new_xp} XP**", ephemeral=True)
    await log_action(interaction.guild, f"➖ Admin {interaction.user.mention} removed **{amount} XP** from {member.mention}.\n**Reason:** {reason}\nNew total: **{new_xp} XP**.")

@bot.tree.command(name="xp-set", description="[Admin] Set exact XP balance for a member.")
@app_commands.describe(member="Member to set XP for", amount="Exact XP amount", reason="Reason for setting XP (optional)")
@app_commands.checks.has_permissions(administrator=True)
async def xp_set(interaction: discord.Interaction, member: discord.Member, amount: int, reason: str = "No reason provided"):
    if amount < 0:
        await interaction.response.send_message("XP amount cannot be negative.", ephemeral=True)
        return
    set_xp(member.id, amount)
    await update_roles(member, amount, promoter=interaction.user, reason=reason)
    await interaction.response.send_message(f"Successfully set {member.mention}'s XP balance to **{amount} XP**.", ephemeral=True)
    await log_action(interaction.guild, f"⚙️ Admin {interaction.user.mention} set {member.mention}'s XP balance to **{amount} XP**.\n**Reason:** {reason}")

@bot.tree.command(name="set-mod-channel", description="[Admin] Set the channel where XP requests will arrive.")
@app_commands.describe(channel="The channel for moderator requests")
@app_commands.checks.has_permissions(administrator=True)
async def set_mod_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    set_mod_channel_db(interaction.guild.id, channel.id)
    await interaction.followup.send(f"Moderator request channel successfully set to {channel.mention}.", ephemeral=True)

@bot.tree.command(name="set-log-channel", description="[Admin] Set the channel for XP audit logs.")
@app_commands.describe(channel="The channel for audit logs")
@app_commands.checks.has_permissions(administrator=True)
async def set_log_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    set_log_channel_db(interaction.guild.id, channel.id)
    await interaction.followup.send(f"XP audit log channel successfully set to {channel.mention}.", ephemeral=True)

@bot.tree.command(name="set-promo-channel", description="[Admin] Set the channel for promotion announcements.")
@app_commands.describe(channel="The channel for promotions")
@app_commands.checks.has_permissions(administrator=True)
async def set_promo_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    set_promo_channel_db(interaction.guild.id, channel.id)
    await interaction.followup.send(f"Promotion announcement channel successfully set to {channel.mention}.", ephemeral=True)

@bot.tree.command(name="set-threshold", description="[Admin] Set XP requirement for a role (creates role if it doesn't exist).")
@app_commands.describe(xp_required="Required XP amount", role_name="Name of the Discord role")
@app_commands.checks.has_permissions(administrator=True)
async def set_threshold(interaction: discord.Interaction, xp_required: int, role_name: str):
    await interaction.response.defer(ephemeral=True)

    if xp_required < 0:
        await interaction.followup.send("XP threshold cannot be negative.", ephemeral=True)
        return

    guild = interaction.guild
    existing_role = discord.utils.get(guild.roles, name=role_name)
    role_created = False
    
    if not existing_role:
        try:
            await guild.create_role(name=role_name, reason=f"Created automatically by XP bot for {xp_required} XP threshold")
            role_created = True
        except discord.Forbidden:
            await interaction.followup.send("Error: Bot lacks permission to create roles on this server!", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.followup.send("Error: Failed to create the role due to a Discord error.", ephemeral=True)
            return

    set_threshold_db(xp_required, role_name)
    
    msg = f"Successfully set role **{role_name}** requirement to **{xp_required} XP**."
    if role_created:
        msg += f" *(Note: Role '{role_name}' did not exist, so it was automatically created on the server!)*"

    await interaction.followup.send(msg, ephemeral=True)
    await log_action(guild, f"⚙️ Admin {interaction.user.mention} updated XP threshold: **{role_name}** is now set to **{xp_required} XP**{'(Role was newly created)' if role_created else ''}.")

if __name__ == "__main__":
    bot.run(TOKEN)

@bot.command(name="balance", help="Pozri si svoje XP alebo XP iného člena (admini/moderátori môžu pozerať aj iných).")
async def balance(ctx, member: discord.Member = None):
    # Ak nezadal člena, pozerá svoje vlastné XP
    if member is None:
        target_user = ctx.author
    else:
        # Ak chce pozrieť niekoho iného, overíme, či je admin alebo moderátor
        is_admin = ctx.author.guild_permissions.administrator
        is_mod = any(role.name in ["Moderátor", "Admin", "Mod"] for role in ctx.author.roles)
        
        if not is_admin and not is_mod:
            await ctx.send("Nemáš oprávnenie pozerať zostatok iných členov!", delete_after=5)
            return
        target_user = member

    # Pripojenie k SQLite databáze a vyťahovanie XP
    import sqlite3
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # Predpokladáme tabuľku 'users' so stĺpcami 'user_id' a 'xp' (uprav názvy, ak ich máš iné)
    cursor.execute("SELECT xp FROM users WHERE user_id = ?", (target_user.id,))
    result = cursor.fetchone()
    
    if result:
        xp_zostatok = result[0]
    else:
        xp_zostatok = 0  # Ak užívateľ ešte nemá záznam v databáze
        
    conn.close()

    # Odpoveď do chatu
    await ctx.send(f"Užívateur **{target_user.display_name}** má aktuálne **{xp_zostatok} XP**.")

# Ošetrenie chyby, ak by nastala
@balance.error
async def balance_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Zadal si nesprávny formát príkazu.", delete_after=5)
