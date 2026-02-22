"""Main Telegram bot implementation."""
import logging
import os
import sys
import tempfile
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

from . import config
from .claude_client import ClaudeClient

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.ERROR,  # Errors only
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Silence noisy libraries
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


class TelegramClaudeBot:
    """Telegram bot that bridges messages to Claude CLI."""

    def __init__(self):
        self.admin_ids = config.ADMIN_TELEGRAM_IDS
        self.claude = ClaudeClient()

    def is_admin(self, user_id: int) -> bool:
        """Check if user is authorized admin."""
        return user_id in self.admin_ids

    def is_restricted_user(self, user_id: int) -> bool:
        """Check if user has restricted (scoped) access."""
        return user_id in config.RESTRICTED_USERS

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        user_id = update.effective_user.id
        if not self.is_admin(user_id):
            await update.message.reply_text(
                "⛔ Unauthorized. This bot is private."
            )
            logger.warning(f"Unauthorized access attempt from user {user_id}")
            return

        await update.message.reply_text(
            "🤖 **telegram-claude**\n\n"
            "Send me any message and I'll forward it to Claude.\n\n"
            "**Commands:**\n"
            "/help - Show this help message\n"
            "/clear - Clear Claude session and start fresh\n"
            "/status - Show bot and session info",
            parse_mode="Markdown"
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        if not self.is_admin(update.effective_user.id):
            return

        await update.message.reply_text(
            "**telegram-claude Help**\n\n"
            "**How it works:**\n"
            "• Send any text message → forwarded to Claude\n"
            "• Responses come back to Telegram\n"
            "• Session persists across messages\n\n"
            "**Commands:**\n"
            "• /help - Show this message\n"
            "• /clear - Clear Claude session (fresh start)\n"
            "• /status - Show bot and session info\n\n"
            "**Features:**\n"
            "• Full Claude access (no restrictions)\n"
            "• Persistent conversation context\n"
            "• Works with bills repo at ~/repos/bills",
            parse_mode="Markdown"
        )

    async def clear_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /clear command - clear Claude session."""
        if not self.is_admin(update.effective_user.id):
            return

        user_id = update.effective_user.id
        cleared = await self.claude.clear_session(user_id)

        if cleared:
            await update.message.reply_text(
                "✅ Claude session cleared! Next message will start a fresh conversation."
            )
        else:
            await update.message.reply_text(
                "ℹ️ No active session to clear. Next message will start fresh anyway."
            )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command - show bot info."""
        if not self.is_admin(update.effective_user.id):
            return

        from pathlib import Path
        session_file = Path.home() / ".claude" / "sessions" / f"{config.CLAUDE_SESSION_ID}.json"
        session_exists = session_file.exists()

        status_msg = (
            f"**Bot Status**\n\n"
            f"Session ID: `{config.CLAUDE_SESSION_ID}`\n"
            f"Session Active: {'Yes' if session_exists else 'No'}\n"
            f"Claude Binary: `{config.CLAUDE_BIN_PATH}`\n"
            f"Timeout: None (waits indefinitely)"
        )
        await update.message.reply_text(status_msg, parse_mode="Markdown")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle regular text messages - forward to Claude."""
        user_id = update.effective_user.id

        # Check authorization
        is_admin = self.is_admin(user_id)
        is_restricted = self.is_restricted_user(user_id)

        if not is_admin and not is_restricted:
            await update.message.reply_text(
                "⛔ Unauthorized. This bot is private."
            )
            logger.warning(f"Unauthorized message from user {user_id}: {update.message.text[:50]}")
            return

        message_text = update.message.text

        # Handle restricted users differently
        if is_restricted and not is_admin:
            resident_slug = config.RESTRICTED_USERS[user_id]

            # Build receipt processing prompt
            receipt_prompt = f"""You are a receipt processor for a shared household billing system.

User: {resident_slug}
Message: {message_text}

Task:
1. Extract the amount and description from the message
2. Create a receipt file in /bills/purchases/{resident_slug}/ with filename: YYYY-MM-DD_description_AMOUNT.txt
   - Use today's date
   - Replace spaces in description with underscores
   - Amount should be just the number (e.g., 25.50)
   - Example: 2026-02-15_groceries_25.50.txt
3. The file content can be empty (the filename IS the data)
4. Reply to the user confirming what was saved

If the message is unclear, ask for clarification."""

            # Send "typing..." indicator
            await update.message.chat.send_action("typing")

            try:
                response = await self.claude.send_message(receipt_prompt, user_id, use_continue=False)
                await update.message.reply_text(response)
            except Exception as e:
                error_msg = f"❌ Error processing receipt: {str(e)}"
                await update.message.reply_text(error_msg)
                logger.exception(f"Error processing receipt: {e}")

            return

        # Admin gets full Claude access with sessions

        # Send "typing..." indicator
        await update.message.chat.send_action("typing")

        try:
            # Forward to Claude with session continuity
            response = await self.claude.send_message(message_text, user_id, use_continue=True)

            # Send response back to Telegram
            # Telegram has a 4096 character limit, so split if needed
            if len(response) <= 4096:
                await update.message.reply_text(response)
            else:
                chunks = [response[i:i+4096] for i in range(0, len(response), 4096)]
                for chunk in chunks:
                    await update.message.reply_text(chunk)

        except RuntimeError as e:
            error_msg = f"❌ Error from Claude: {str(e)}"
            await update.message.reply_text(error_msg)
            logger.error(f"Claude error: {e}")

        except Exception as e:
            error_msg = f"❌ Unexpected error: {str(e)}"
            await update.message.reply_text(error_msg)
            logger.exception(f"Unexpected error handling message: {e}")

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle photo messages - download image and forward to Claude."""
        user_id = update.effective_user.id
        if not self.is_admin(user_id):
            await update.message.reply_text("⛔ Unauthorized. This bot is private.")
            return

        await update.message.chat.send_action("typing")

        temp_path = tempfile.mktemp(suffix=".jpg")
        try:
            photo = update.message.photo[-1]
            photo_file = await context.bot.get_file(photo.file_id)
            await photo_file.download_to_drive(temp_path)

            caption = update.message.caption or "Describe this image"

            response = await self.claude.send_message(caption, user_id, use_continue=True, image_path=temp_path)

            chunks = [response[i:i+4096] for i in range(0, len(response), 4096)]
            for chunk in chunks:
                await update.message.reply_text(chunk)

        except Exception as e:
            await update.message.reply_text(f"❌ Error processing photo: {str(e)}")
            logger.exception(f"Error handling photo: {e}")
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def run(self):
        """Start the bot."""
        application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("clear", self.clear_command))
        application.add_handler(CommandHandler("status", self.status_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))

        application.run_polling(allowed_updates=Update.ALL_TYPES)


def main():
    """Entry point for the bot."""
    try:
        bot = TelegramClaudeBot()
        bot.run()
    except KeyboardInterrupt:
        pass  # Clean shutdown
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
