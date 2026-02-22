# telegram-claude

A Telegram bot that bridges messages to [Claude Code](https://claude.ai/claude-code) CLI. Send a message on Telegram, get a response from Claude with full tool access (files, bash, MCP servers, web search, etc).

## Features

- **Admin mode**: Full conversational Claude with session persistence
- **Receipt mode**: Restricted users can submit expenses (e.g. `$25.50 groceries`) — Claude saves structured receipt files
- **Photo support**: Send images, Claude analyzes them
- **Message history**: SQLite-backed history with `/history` and `/stats` commands

## Requirements

- [Claude Code CLI](https://claude.ai/claude-code) installed and authenticated
- Python 3.11+
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- Your Telegram user ID (from [@userinfobot](https://t.me/userinfobot))

## Setup

```bash
# Clone
git clone https://github.com/yourusername/telegram-claude.git
cd telegram-claude

# Create venv and install deps
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your bot token and Telegram user ID

# Test it
python telegram-claude.py
```

## Run as a Service (Linux)

```bash
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/telegram-claude.service << EOF
[Unit]
Description=Telegram Claude Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/venv/bin/python telegram-claude.py
Restart=on-failure
RestartSec=10
EnvironmentFile=$(pwd)/.env

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now telegram-claude.service

# Survive logout / start on boot
sudo loginctl enable-linger $(whoami)
```

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | Show help |
| `/clear` | Reset Claude session |
| `/status` | Session info |
| `/history` | Recent messages |
| `/stats` | Usage statistics |

## Receipt Mode

Add restricted users to `.env` to give them limited access for submitting expenses:

```
RESTRICTED_USERS=123456789:alice,987654321:bob
```

They can send messages like `$25.50 groceries` and Claude will save a structured receipt file. They cannot access full Claude features.

## How It Works

The bot is a thin Telegram interface. When you send a message:

1. Telegram bot receives it
2. Bot calls `claude -p "your message" --continue` as a subprocess
3. Claude runs with full access to your machine (files, bash, MCP servers, etc)
4. Response is sent back to Telegram

Session persistence is handled by Claude CLI `--continue` flag — each message continues the previous conversation.

## License

MIT
