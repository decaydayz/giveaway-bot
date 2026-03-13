# 🎉 Discord Giveaway Bot (Python)

A Discord giveaway bot built with `discord.py`, featuring Steam ID verification, SQLite storage, and full giveaway lifecycle management.

---

## Features

- **Steam ID Verification** — Users register via modal before entering; unregistered users are prompted automatically
- **SQLite Database** — Built-in to Python, auto-created on first run
- **Role-Restricted Commands** — Only admins or configured role IDs can manage giveaways
- **Full Lifecycle** — Start, end early, reroll, and inspect giveaways
- **Persistent Buttons** — Entry buttons survive bot restarts
- **Timer Recovery** — Active giveaway timers are restored on bot restart

---

## Setup

### 1. Prerequisites
- Python 3.10+
- A Discord bot application ([create one here](https://discord.com/developers/applications))

### 2. Bot Settings (Discord Developer Portal)
- **Privileged Gateway Intents**: Enable `Server Members Intent`
- **OAuth2 Scopes**: `bot`, `applications.commands`
- **Bot Permissions**: `Send Messages`, `Embed Links`, `Read Message History`

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment
```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Your bot token |
| `ALLOWED_ROLE_IDS` | Comma-separated role IDs allowed to manage giveaways |

### 5. Run
```bash
python bot.py
```

The `giveaway.db` SQLite file is created automatically on first run.

---

## Commands

### Anyone
| Command | Description |
|---|---|
| `/registersteam` | Register or update your Steam ID via modal |

### Admin / Allowed Roles
| Command | Description |
|---|---|
| `/gstart prize duration winners` | Start a new giveaway |
| `/gend id` | End a giveaway early |
| `/greroll id [winners]` | Reroll winners (optionally override count) |
| `/ginfo id` | View giveaway details, entries, and winners |
| `/getusersteam user` | Look up a user's registered Steam ID |

### Duration Format
| Input | Meaning |
|---|---|
| `30s` | 30 seconds |
| `10m` | 10 minutes |
| `2h` | 2 hours |
| `1d` | 1 day |

---

## How It Works

1. Admin runs `/gstart prize:"Steam Gift Card" duration:1d winners:1`
2. Bot posts a giveaway embed with an **Enter Giveaway** button
3. User clicks the button:
   - **Has Steam ID** → entered immediately
   - **No Steam ID** → modal pops up, they register and are entered in one step
4. Timer expires → bot picks random winner(s), announces them, and locks the embed
5. Admin can `/greroll` at any time to pick new winners from the same entry pool

---

## File Structure
```
giveaway-bot/
├── bot.py            # Entire bot — single file
├── requirements.txt  # discord.py + python-dotenv
├── .env.example      # Copy to .env and configure
└── giveaway.db       # Auto-created SQLite database
```

---

## Finding Role IDs
Enable Developer Mode in Discord: **User Settings → Advanced → Developer Mode**
Then right-click any role in **Server Settings → Roles** → **Copy Role ID**
