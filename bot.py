import os
import random
import asyncio
import sqlite3
import re
from datetime import datetime, timezone
from dotenv import load_dotenv

import discord
from discord import app_commands
from discord.ext import commands

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
ALLOWED_ROLE_IDS = [
    int(r.strip())
    for r in os.getenv("ALLOWED_ROLE_IDS", "").split(",")
    if r.strip().isdigit()
]

# ── Database ─────────────────────────────────────────────────────────────────


def get_db():
    conn = sqlite3.connect("./data/giveaway.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                discord_id   TEXT PRIMARY KEY,
                steam_id     TEXT NOT NULL,
                registered_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS giveaways (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id      TEXT NOT NULL,
                channel_id    TEXT NOT NULL,
                message_id    TEXT,
                prize         TEXT NOT NULL,
                winners_count INTEGER NOT NULL DEFAULT 1,
                ends_at       TEXT NOT NULL,
                ended         INTEGER NOT NULL DEFAULT 0,
                created_by    TEXT NOT NULL,
                ping_role_id  TEXT,
                created_at    TEXT DEFAULT (datetime('now'))
            );

            -- migrate: add ping_role_id if upgrading from older schema
            CREATE TABLE IF NOT EXISTS _migrations (key TEXT PRIMARY KEY);
        """)
        already = conn.execute(
            "SELECT 1 FROM _migrations WHERE key='add_ping_role'"
        ).fetchone()
        if not already:
            try:
                conn.execute("ALTER TABLE giveaways ADD COLUMN ping_role_id TEXT")
            except Exception:
                pass
            conn.execute("INSERT OR IGNORE INTO _migrations VALUES ('add_ping_role')")
        already2 = conn.execute(
            "SELECT 1 FROM _migrations WHERE key='add_custom_text'"
        ).fetchone()
        if not already2:
            try:
                conn.execute("ALTER TABLE giveaways ADD COLUMN custom_text TEXT")
            except Exception:
                pass
            conn.execute("INSERT OR IGNORE INTO _migrations VALUES ('add_custom_text')")
        conn.executescript("""

            CREATE TABLE IF NOT EXISTS giveaway_entries (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                giveaway_id  INTEGER NOT NULL,
                discord_id   TEXT NOT NULL,
                entered_at   TEXT DEFAULT (datetime('now')),
                UNIQUE(giveaway_id, discord_id),
                FOREIGN KEY (giveaway_id) REFERENCES giveaways(id)
            );

            CREATE TABLE IF NOT EXISTS giveaway_winners (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                giveaway_id  INTEGER NOT NULL,
                discord_id   TEXT NOT NULL,
                won_at       TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (giveaway_id) REFERENCES giveaways(id)
            );
        """)


# ── Helpers ───────────────────────────────────────────────────────────────────


def parse_duration(s: str) -> int | None:
    """Return milliseconds or None if invalid. Accepts e.g. 10m, 2h, 1d, 30s."""
    match = re.fullmatch(r"(\d+)([smhd])", s.strip().lower())
    if not match:
        return None
    n, unit = int(match.group(1)), match.group(2)
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def is_allowed(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(role.id in ALLOWED_ROLE_IDS for role in member.roles)


def pick_winners(entries: list[str], count: int) -> list[str]:
    pool = entries.copy()
    random.shuffle(pool)
    return pool[: min(count, len(pool))]


def build_giveaway_embed(
    giveaway: sqlite3.Row, entry_count: int, ended: bool = False
) -> discord.Embed:
    ends_dt = datetime.fromisoformat(giveaway["ends_at"]).replace(tzinfo=timezone.utc)
    ts = int(ends_dt.timestamp())

    if ended:
        desc = "This giveaway has ended."
    else:
        base = "Click **Enter Giveaway** below!\n> You must have a registered Steam ID to enter."
        custom = giveaway["custom_text"] if "custom_text" in giveaway.keys() else None
        desc = f"{custom}\n\n{base}" if custom else base

    embed = discord.Embed(
        title=f"🎉 GIVEAWAY: {giveaway['prize']}",
        color=0x888888 if ended else 0xF1C40F,
        description=desc,
    )
    embed.add_field(
        name="🏆 Winners", value=str(giveaway["winners_count"]), inline=True
    )
    embed.add_field(name="👥 Entries", value=str(entry_count), inline=True)
    embed.add_field(
        name="⏰ " + ("Ended" if ended else "Ends"), value=f"<t:{ts}:R>", inline=True
    )
    embed.set_footer(text=f"Giveaway ID: {giveaway['id']}")
    return embed


# ── Bot Setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ── Giveaway End Logic ────────────────────────────────────────────────────────


async def end_giveaway(giveaway_id: int):
    with get_db() as conn:
        giveaway = conn.execute(
            "SELECT * FROM giveaways WHERE id = ?", (giveaway_id,)
        ).fetchone()
        if not giveaway or giveaway["ended"]:
            return

        conn.execute("UPDATE giveaways SET ended = 1 WHERE id = ?", (giveaway_id,))

        entries = [
            r["discord_id"]
            for r in conn.execute(
                "SELECT discord_id FROM giveaway_entries WHERE giveaway_id = ?",
                (giveaway_id,),
            ).fetchall()
        ]
        winners = pick_winners(entries, giveaway["winners_count"])

        conn.execute(
            "DELETE FROM giveaway_winners WHERE giveaway_id = ?", (giveaway_id,)
        )
        for w in winners:
            conn.execute(
                "INSERT INTO giveaway_winners (giveaway_id, discord_id) VALUES (?, ?)",
                (giveaway_id, w),
            )

    try:
        channel = bot.get_channel(
            int(giveaway["channel_id"])
        ) or await bot.fetch_channel(int(giveaway["channel_id"]))
        msg = await channel.fetch_message(int(giveaway["message_id"]))

        embed = build_giveaway_embed(giveaway, len(entries), ended=True)
        winner_mentions = (
            ", ".join(f"<@{w}>" for w in winners) if winners else "No valid entries."
        )
        embed.add_field(name="🎊 Winners", value=winner_mentions)

        await msg.edit(embed=embed, view=None)
        await channel.send(
            f"🎉 Congratulations {winner_mentions}! You won **{giveaway['prize']}**!"
        )
    except Exception as e:
        print(f"[Error] Failed to finalize giveaway #{giveaway_id}: {e}")


# ── Steam ID Modal ─────────────────────────────────────────────────────────────


class SteamModal(discord.ui.Modal, title="Steam ID Verification"):
    steam_id = discord.ui.TextInput(
        label="Enter your Steam ID (17-digit number)",
        placeholder="76561198XXXXXXXXX",
        min_length=17,
        max_length=17,
        required=True,
    )

    def __init__(self, giveaway_id: int | None = None):
        super().__init__()
        self.giveaway_id = giveaway_id

    async def on_submit(self, interaction: discord.Interaction):
        sid = self.steam_id.value.strip()
        if not re.fullmatch(r"\d{17}", sid):
            await interaction.response.send_message(
                "❌ Invalid Steam ID — it must be exactly 17 digits.", ephemeral=True
            )
            return

        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO users (discord_id, steam_id) VALUES (?, ?)",
                (str(interaction.user.id), sid),
            )

        if self.giveaway_id is not None:
            with get_db() as conn:
                giveaway = conn.execute(
                    "SELECT * FROM giveaways WHERE id = ?", (self.giveaway_id,)
                ).fetchone()

            if giveaway and not giveaway["ended"]:
                with get_db() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO giveaway_entries (giveaway_id, discord_id) VALUES (?, ?)",
                        (self.giveaway_id, str(interaction.user.id)),
                    )
                    entry_count = conn.execute(
                        "SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id = ?",
                        (self.giveaway_id,),
                    ).fetchone()[0]

                try:
                    channel = interaction.channel
                    msg = await channel.fetch_message(int(giveaway["message_id"]))
                    embed = build_giveaway_embed(giveaway, entry_count)
                    await msg.edit(embed=embed)
                except Exception:
                    pass

                await interaction.response.send_message(
                    f"✅ Steam ID `{sid}` registered and you've been entered into the giveaway! 🎉",
                    ephemeral=True,
                )
                return

        await interaction.response.send_message(
            f"✅ Steam ID `{sid}` registered successfully!", ephemeral=True
        )


# ── Giveaway Entry Button ─────────────────────────────────────────────────────


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: int):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.enter_button.custom_id = f"enter_giveaway_{giveaway_id}"

    @discord.ui.button(
        label="🎉 Enter Giveaway",
        style=discord.ButtonStyle.success,
        custom_id="enter_giveaway_placeholder",
    )
    async def enter_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        giveaway_id = int(button.custom_id.split("_")[2])

        with get_db() as conn:
            giveaway = conn.execute(
                "SELECT * FROM giveaways WHERE id = ?", (giveaway_id,)
            ).fetchone()

        if not giveaway or giveaway["ended"]:
            await interaction.response.send_message(
                "❌ This giveaway has already ended.", ephemeral=True
            )
            return

        with get_db() as conn:
            user_row = conn.execute(
                "SELECT * FROM users WHERE discord_id = ?", (str(interaction.user.id),)
            ).fetchone()

        if not user_row:
            await interaction.response.send_modal(SteamModal(giveaway_id=giveaway_id))
            return

        with get_db() as conn:
            already = conn.execute(
                "SELECT 1 FROM giveaway_entries WHERE giveaway_id = ? AND discord_id = ?",
                (giveaway_id, str(interaction.user.id)),
            ).fetchone()

            if already:
                await interaction.response.send_message(
                    "✅ You are already entered in this giveaway!", ephemeral=True
                )
                return

            conn.execute(
                "INSERT OR IGNORE INTO giveaway_entries (giveaway_id, discord_id) VALUES (?, ?)",
                (giveaway_id, str(interaction.user.id)),
            )
            entry_count = conn.execute(
                "SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id = ?",
                (giveaway_id,),
            ).fetchone()[0]

        try:
            embed = build_giveaway_embed(giveaway, entry_count)
            await interaction.message.edit(embed=embed)
        except Exception:
            pass

        await interaction.response.send_message(
            "🎉 You've been entered into the giveaway! Good luck!", ephemeral=True
        )


# ── on_ready ──────────────────────────────────────────────────────────────────


@bot.event
async def on_ready():
    init_db()
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")

    # Re-register persistent views
    with get_db() as conn:
        active = conn.execute("SELECT * FROM giveaways WHERE ended = 0").fetchall()

    for g in active:
        bot.add_view(GiveawayView(g["id"]))
        ends_at = datetime.fromisoformat(g["ends_at"]).replace(tzinfo=timezone.utc)
        delay = (ends_at - datetime.now(timezone.utc)).total_seconds()
        if delay <= 0:
            asyncio.create_task(end_giveaway(g["id"]))
        else:
            asyncio.create_task(_schedule_end(g["id"], delay))
            print(f"⏱ Restored timer for giveaway #{g['id']} ({delay:.0f}s remaining)")

    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"[Error] Failed to sync commands: {e}")


async def _schedule_end(giveaway_id: int, delay: float):
    await asyncio.sleep(delay)
    await end_giveaway(giveaway_id)


# ── Slash Commands ────────────────────────────────────────────────────────────


@bot.tree.command(name="registersteam", description="Register or update your Steam ID")
async def registersteam(interaction: discord.Interaction):
    await interaction.response.send_modal(SteamModal())


@bot.tree.command(
    name="getusersteam", description="Look up a user's registered Steam ID"
)
@app_commands.describe(user="The Discord user to look up")
async def getusersteam(interaction: discord.Interaction, user: discord.Member):
    if not is_allowed(interaction.user):
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.", ephemeral=True
        )
        return

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE discord_id = ?", (str(user.id),)
        ).fetchone()

    if not row:
        await interaction.response.send_message(
            f"❌ No Steam ID registered for {user.mention}.", ephemeral=True
        )
        return

    embed = discord.Embed(title="Steam ID Lookup", color=0x1B2838)
    embed.add_field(name="Discord User", value=f"{user.mention} ({user})", inline=True)
    embed.add_field(name="Steam ID", value=f"`{row['steam_id']}`", inline=True)
    embed.add_field(name="Registered At", value=row["registered_at"], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="gstart", description="Start a new giveaway")
@app_commands.describe(
    prize="What is being given away?",
    duration="Duration e.g. 10m, 2h, 1d",
    winners="Number of winners",
    ping_role="Role to ping when the giveaway is posted (optional)",
    description="Custom text shown on the giveaway embed (optional)",
)
async def gstart(
    interaction: discord.Interaction,
    prize: str,
    duration: str,
    winners: int,
    ping_role: discord.Role = None,
    description: str = None,
):
    if not is_allowed(interaction.user):
        await interaction.response.send_message(
            "❌ You don't have permission to start giveaways.", ephemeral=True
        )
        return

    if winners < 1 or winners > 20:
        await interaction.response.send_message(
            "❌ Winners must be between 1 and 20.", ephemeral=True
        )
        return

    delay = parse_duration(duration)
    if delay is None:
        await interaction.response.send_message(
            "❌ Invalid duration. Use e.g. `10m`, `2h`, `1d`.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    ends_at = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + delay, tz=timezone.utc
    ).isoformat()

    ping_role_id = str(ping_role.id) if ping_role else None

    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO giveaways (guild_id, channel_id, prize, winners_count, ends_at, created_by, ping_role_id, custom_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(interaction.guild_id),
                str(interaction.channel_id),
                prize,
                winners,
                ends_at,
                str(interaction.user.id),
                ping_role_id,
                description,
            ),
        )
        giveaway_id = cursor.lastrowid
        giveaway = conn.execute(
            "SELECT * FROM giveaways WHERE id = ?", (giveaway_id,)
        ).fetchone()

    view = GiveawayView(giveaway_id)
    embed = build_giveaway_embed(giveaway, 0)

    # Send role ping as a separate message first, then the embed
    if ping_role:
        await interaction.channel.send(
            f"{ping_role.mention} 🎉 A new giveaway has started!"
        )
    msg = await interaction.channel.send(embed=embed, view=view)

    with get_db() as conn:
        conn.execute(
            "UPDATE giveaways SET message_id = ? WHERE id = ?",
            (str(msg.id), giveaway_id),
        )

    asyncio.create_task(_schedule_end(giveaway_id, delay))

    ts = int(datetime.fromisoformat(ends_at).timestamp())
    ping_note = f" Pinged {ping_role.mention}." if ping_role else ""
    await interaction.followup.send(
        f"✅ Giveaway **#{giveaway_id}** started! Ends <t:{ts}:R>.{ping_note}",
        ephemeral=True,
    )


@bot.tree.command(name="gend", description="End a giveaway early")
@app_commands.describe(id="Giveaway ID")
async def gend(interaction: discord.Interaction, id: int):
    if not is_allowed(interaction.user):
        await interaction.response.send_message(
            "❌ You don't have permission to end giveaways.", ephemeral=True
        )
        return

    with get_db() as conn:
        giveaway = conn.execute(
            "SELECT * FROM giveaways WHERE id = ?", (id,)
        ).fetchone()

    if not giveaway:
        await interaction.response.send_message(
            f"❌ Giveaway #{id} not found.", ephemeral=True
        )
        return
    if giveaway["ended"]:
        await interaction.response.send_message(
            f"❌ Giveaway #{id} has already ended.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    await end_giveaway(id)
    await interaction.followup.send(
        f"✅ Giveaway **#{id}** ended early.", ephemeral=True
    )


@bot.tree.command(name="greroll", description="Reroll winners for a finished giveaway")
@app_commands.describe(id="Giveaway ID", winners="Override winner count (optional)")
async def greroll(interaction: discord.Interaction, id: int, winners: int = None):
    if not is_allowed(interaction.user):
        await interaction.response.send_message(
            "❌ You don't have permission to reroll giveaways.", ephemeral=True
        )
        return

    with get_db() as conn:
        giveaway = conn.execute(
            "SELECT * FROM giveaways WHERE id = ?", (id,)
        ).fetchone()

    if not giveaway:
        await interaction.response.send_message(
            f"❌ Giveaway #{id} not found.", ephemeral=True
        )
        return
    if not giveaway["ended"]:
        await interaction.response.send_message(
            f"❌ Giveaway #{id} hasn't ended yet. Use `/gend` first.", ephemeral=True
        )
        return

    await interaction.response.defer()

    with get_db() as conn:
        entries = [
            r["discord_id"]
            for r in conn.execute(
                "SELECT discord_id FROM giveaway_entries WHERE giveaway_id = ?", (id,)
            ).fetchall()
        ]

    winner_count = winners if winners is not None else giveaway["winners_count"]
    new_winners = pick_winners(entries, winner_count)

    with get_db() as conn:
        conn.execute("DELETE FROM giveaway_winners WHERE giveaway_id = ?", (id,))
        for w in new_winners:
            conn.execute(
                "INSERT INTO giveaway_winners (giveaway_id, discord_id) VALUES (?, ?)",
                (id, w),
            )

    winner_mentions = (
        ", ".join(f"<@{w}>" for w in new_winners)
        if new_winners
        else "No valid entries."
    )

    try:
        channel = interaction.channel
        msg = await channel.fetch_message(int(giveaway["message_id"]))
        embed = build_giveaway_embed(giveaway, len(entries), ended=True)
        embed.add_field(name="🎊 Winners (Rerolled)", value=winner_mentions)
        await msg.edit(embed=embed, view=None)
    except Exception:
        pass

    await interaction.followup.send(
        f"🔄 Rerolled giveaway **#{id}**! New winners: {winner_mentions} — Congratulations!"
    )


@bot.tree.command(name="ginfo", description="Show info about a giveaway")
@app_commands.describe(id="Giveaway ID")
async def ginfo(interaction: discord.Interaction, id: int):
    with get_db() as conn:
        giveaway = conn.execute(
            "SELECT * FROM giveaways WHERE id = ?", (id,)
        ).fetchone()
        if not giveaway:
            await interaction.response.send_message(
                f"❌ Giveaway #{id} not found.", ephemeral=True
            )
            return

        entry_count = conn.execute(
            "SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id = ?", (id,)
        ).fetchone()[0]
        winner_rows = conn.execute(
            "SELECT discord_id FROM giveaway_winners WHERE giveaway_id = ?", (id,)
        ).fetchall()

    ends_dt = datetime.fromisoformat(giveaway["ends_at"]).replace(tzinfo=timezone.utc)
    ts = int(ends_dt.timestamp())
    winners_val = ", ".join(f"<@{r['discord_id']}>" for r in winner_rows) or "None yet"

    embed = discord.Embed(
        title=f"Giveaway #{id} Info",
        color=0x888888 if giveaway["ended"] else 0xF1C40F,
    )
    embed.add_field(name="Prize", value=giveaway["prize"], inline=True)
    embed.add_field(
        name="Status",
        value="✅ Ended" if giveaway["ended"] else "🟢 Active",
        inline=True,
    )
    embed.add_field(name="Winners", value=str(giveaway["winners_count"]), inline=True)
    embed.add_field(name="Entries", value=str(entry_count), inline=True)
    embed.add_field(name="Ends/Ended", value=f"<t:{ts}:F>", inline=True)
    embed.add_field(
        name="Started By", value=f"<@{giveaway['created_by']}>", inline=True
    )
    if giveaway["ended"]:
        embed.add_field(name="🏆 Winners", value=winners_val, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="liveentrants",
    description="View the current entrant list for an active giveaway (admin only)",
)
@app_commands.describe(id="Giveaway ID")
async def liveentrants(interaction: discord.Interaction, id: int):
    if not is_allowed(interaction.user):
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.", ephemeral=True
        )
        return

    with get_db() as conn:
        giveaway = conn.execute(
            "SELECT * FROM giveaways WHERE id = ?", (id,)
        ).fetchone()
        if not giveaway:
            await interaction.response.send_message(
                f"❌ Giveaway #{id} not found.", ephemeral=True
            )
            return

        rows = conn.execute(
            "SELECT discord_id, entered_at FROM giveaway_entries WHERE giveaway_id = ? ORDER BY entered_at ASC",
            (id,),
        ).fetchall()

    entry_count = len(rows)
    ends_dt = datetime.fromisoformat(giveaway["ends_at"]).replace(tzinfo=timezone.utc)
    ts = int(ends_dt.timestamp())
    status = "✅ Ended" if giveaway["ended"] else f"🟢 Active — ends <t:{ts}:R>"

    embed = discord.Embed(
        title=f"👥 Live Entrants — Giveaway #{id}",
        description=f"**Prize:** {giveaway['prize']}\n**Status:** {status}\n**Total Entries:** {entry_count}",
        color=0x5865F2,
    )

    if entry_count == 0:
        embed.add_field(name="Entrants", value="No entries yet.", inline=False)
    else:
        # Discord embed field value cap is 1024 chars; paginate into chunks of 20
        chunk_size = 20
        for i in range(0, entry_count, chunk_size):
            chunk = rows[i : i + chunk_size]
            lines = [
                f"`{i + j + 1}.` <@{r['discord_id']}> — <t:{int(datetime.fromisoformat(r['entered_at']).replace(tzinfo=timezone.utc).timestamp())}:t>"
                for j, r in enumerate(chunk)
            ]
            field_name = f"Entrants {i + 1}–{min(i + chunk_size, entry_count)}"
            embed.add_field(name=field_name, value="\n".join(lines), inline=False)
            if len(embed.fields) >= 25:  # Discord max fields
                embed.set_footer(
                    text=f"Showing first {i + chunk_size} of {entry_count} entrants (Discord limit reached)"
                )
                break

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Run ───────────────────────────────────────────────────────────────────────

bot.run(TOKEN)
