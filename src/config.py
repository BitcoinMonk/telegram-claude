"""Configuration management for telegram-claude-bot."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Telegram settings
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Parse admin IDs (supports single ID or comma-separated list)
admin_ids_str = os.getenv("ADMIN_TELEGRAM_ID", "0")
ADMIN_TELEGRAM_IDS = [int(id.strip()) for id in admin_ids_str.split(",")]

# Restricted users (Telegram ID -> identifier mapping)
# Format: RESTRICTED_USERS=telegram_id:identifier,telegram_id:identifier
# Example: RESTRICTED_USERS=123456789:alice,987654321:bob
RESTRICTED_USERS = {}
restricted_users_str = os.getenv("RESTRICTED_USERS", "")
if restricted_users_str:
    for entry in restricted_users_str.split(","):
        entry = entry.strip()
        if ":" in entry:
            telegram_id_str, identifier = entry.split(":", 1)
            RESTRICTED_USERS[int(telegram_id_str.strip())] = identifier.strip()

# Claude settings
CLAUDE_SESSION_ID = os.getenv("CLAUDE_SESSION_ID", "telegram-bot")
CLAUDE_BIN_PATH = os.getenv("CLAUDE_BIN_PATH", str(Path.home() / ".local" / "bin" / "claude"))

# Validate required settings
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN must be set in .env file")
if not ADMIN_TELEGRAM_IDS or ADMIN_TELEGRAM_IDS == [0]:
    raise ValueError("ADMIN_TELEGRAM_ID must be set in .env file")

# Check Claude binary exists
if not Path(CLAUDE_BIN_PATH).exists():
    raise FileNotFoundError(f"Claude binary not found at {CLAUDE_BIN_PATH}")
