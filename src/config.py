"""Configuration management for telegram-claude-bot."""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# --- Legacy globals ---

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

admin_ids_str = os.getenv("ADMIN_TELEGRAM_ID", "0")
ADMIN_TELEGRAM_IDS = [int(id.strip()) for id in admin_ids_str.split(",")]

RESTRICTED_USERS = {}
restricted_users_str = os.getenv("RESTRICTED_USERS", "")
if restricted_users_str:
    for entry in restricted_users_str.split(","):
        entry = entry.strip()
        if ":" in entry:
            telegram_id_str, identifier = entry.split(":", 1)
            RESTRICTED_USERS[int(telegram_id_str.strip())] = identifier.strip()

CLAUDE_SESSION_ID = os.getenv("CLAUDE_SESSION_ID", "telegram-bot")
CLAUDE_BIN_PATH = os.getenv("CLAUDE_BIN_PATH", str(Path.home() / ".local" / "bin" / "claude"))

# --- Channel config ---

@dataclass
class ChannelConfig:
    """Configuration for a single bot channel."""
    name: str
    token: str
    handler: str
    admin_ids: list[int] = field(default_factory=list)
    users: dict[int, str] = field(default_factory=dict)  # telegram_id -> slug
    working_dir: Optional[str] = None
    session_id: Optional[str] = None


def load_channels(config_path: Optional[str] = None) -> list[ChannelConfig]:
    """Load channel configs from channels.json."""
    if config_path is None:
        config_path = Path(__file__).parent.parent / "channels.json"
    else:
        config_path = Path(config_path)

    with open(config_path) as f:
        data = json.load(f)

    channels = []
    for ch in data["channels"]:
        token = os.getenv(ch["token_env"])
        if not token:
            raise ValueError(f"Missing env var {ch['token_env']} for channel {ch['name']}")

        users = {}
        for u in ch.get("users", []):
            users[u["telegram_id"]] = u["slug"]

        channels.append(ChannelConfig(
            name=ch["name"],
            token=token,
            handler=ch["handler"],
            admin_ids=ch.get("admin_ids", []),
            users=users,
            working_dir=ch.get("working_dir"),
            session_id=ch.get("session_id"),
        ))

    return channels


# Validate required settings
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN must be set in .env file")
if not ADMIN_TELEGRAM_IDS or ADMIN_TELEGRAM_IDS == [0]:
    raise ValueError("ADMIN_TELEGRAM_ID must be set in .env file")
