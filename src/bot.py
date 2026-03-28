"""General-purpose Telegram bot handler."""
import asyncio
import json
import logging
import os
import sys
import re
import subprocess
import tempfile
import base64
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TimedOut, NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

from . import config
from .config import ChannelConfig
from .claude_client import ClaudeClient

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.ERROR,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


class GeneralHandler:
    """General-purpose handler — full Claude access for admins, restricted for others."""

    def __init__(self, channel_config: ChannelConfig):
        self.config = channel_config
        self.claude = ClaudeClient()

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.config.admin_ids

    def is_restricted_user(self, user_id: int) -> bool:
        return user_id in self.config.users

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_admin(user_id):
            await update.message.reply_text("Unauthorized. This bot is private.")
            return

        await update.message.reply_text(
            "**telegram-claude**\n\n"
            "Send me any message and I'll forward it to Claude.\n\n"
            "**Commands:**\n"
            "/help - Show this help message\n"
            "/clear - Clear Claude session and start fresh\n"
            "/status - System status\n"
            "/review <url> - Review a URL for credibility and relevance",
            parse_mode="Markdown"
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            return

        await update.message.reply_text(
            "**telegram-claude Help**\n\n"
            "Send any text message → forwarded to Claude\n"
            "Session persists across messages\n\n"
            "**Commands:**\n"
            "/help - Show this message\n"
            "/clear - Clear Claude session\n"
            "/status - System status\n"
            "/review <url> [context] - Review a URL",
            parse_mode="Markdown"
        )

    async def clear_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            return

        chat_id = update.effective_chat.id
        had_session = str(chat_id) in self.claude._session_ids
        await self.claude.clear_session(update.effective_user.id, session_key=str(chat_id))
        if had_session:
            await update.message.reply_text("Conversation cleared. Next message starts fresh.")
        else:
            await update.message.reply_text("No active conversation to clear.")

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            return

        gw_result = subprocess.run(
            ["systemctl", "--user", "is-active", "telegram-claude"],
            capture_output=True, text=True
        )
        gw_status = gw_result.stdout.strip()

        stuck_result = subprocess.run(
            ["pgrep", "-fc", "claude.*--output-format"],
            capture_output=True, text=True
        )
        claude_procs = stuck_result.stdout.strip() or "0"

        status_msg = (
            f"**System Status**\n\n"
            f"Gateway: `{gw_status}`\n"
            f"Claude processes: {claude_procs}"
        )
        await update.message.reply_text(status_msg, parse_mode="Markdown")

    async def review_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /review <url> [context] — fetch, analyze, and optionally save to notes."""
        if not self.is_admin(update.effective_user.id):
            return

        args = update.message.text.split(maxsplit=1)
        if len(args) < 2 or not args[1].strip():
            await update.message.reply_text(
                "Usage: `/review <url> [context]`\nExample: `/review https://x.com/post this is about bitcoin mining`",
                parse_mode="Markdown"
            )
            return

        parts = args[1].strip().split(maxsplit=1)
        url = parts[0]
        user_context = parts[1] if len(parts) > 1 else ""

        scout_dir = os.path.expanduser("~/repos/scout")
        venv_python = os.path.join(scout_dir, ".venv", "bin", "python3")
        python = venv_python if os.path.exists(venv_python) else "python3"

        await update.message.reply_text(f"Reviewing `{url[:60]}`...", parse_mode="Markdown")

        cmd = [python, "review.py", url, "--json"]
        if user_context:
            cmd.extend(["--context", user_context])

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True, text=True, cwd=scout_dir, timeout=120
            )
            if result.returncode != 0:
                await update.message.reply_text(f"Review failed: {result.stderr.strip()[:500]}")
                return

            data = json.loads(result.stdout.strip())
        except Exception as e:
            await update.message.reply_text(f"Error during review: {str(e)[:400]}")
            return

        lines = [f"*{data['summary']}*\n"]
        lines.append(f"*Credible:* {data['credible']}")
        lines.append(f"*Relevant:* {data['relevant']}")
        if data.get("reason"):
            lines.append(f"*Why useful:* {data['reason']}")
        lines.append(f"*Tags:* {', '.join(data['tags'])}")
        text = "\n".join(lines)

        review_key = f"review:{update.effective_chat.id}:{update.message.message_id}"
        context.bot_data[review_key] = {"data": data, "url": url}

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Save", callback_data=f"scout_save:{review_key}"),
                InlineKeyboardButton("Skip", callback_data=f"scout_skip:{review_key}"),
            ]
        ])
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)

    async def review_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle Save/Skip button presses from /review."""
        query = update.callback_query
        await query.answer()

        action, review_key = query.data.split(":", 1)

        if action == "scout_skip":
            context.bot_data.pop(review_key, None)
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("Skipped.")
            return

        stored = context.bot_data.pop(review_key, None)
        if not stored:
            await query.message.reply_text("Review data expired — run /review again.")
            return

        data = stored["data"]
        url = stored["url"]
        scout_dir = os.path.expanduser("~/repos/scout")
        venv_python = os.path.join(scout_dir, ".venv", "bin", "python3")
        python = venv_python if os.path.exists(venv_python) else "python3"

        save_input = json.dumps({**data, "url": url})
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [python, "save.py"],
                input=save_input, capture_output=True, text=True, cwd=scout_dir, timeout=30
            )
            if result.returncode != 0:
                await query.message.reply_text(f"Save failed: {result.stderr.strip()[:400]}")
                return
            saved_path = result.stdout.strip()
        except Exception as e:
            await query.message.reply_text(f"Save error: {str(e)[:400]}")
            return

        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"Saved to `{saved_path}`", parse_mode="Markdown")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        is_admin = self.is_admin(user_id)
        is_restricted = self.is_restricted_user(user_id)

        if not is_admin and not is_restricted:
            await update.message.reply_text("Unauthorized. This bot is private.")
            logger.warning(f"Unauthorized message from user {user_id}")
            return

        message_text = update.message.text

        if is_restricted and not is_admin:
            restricted_slug = self.config.users[user_id]
            restricted_prompt = (
                f"You are a restricted assistant for user: {restricted_slug}\n\n"
                f"User message: {message_text}\n\n"
                "You have read-only access. Do NOT create, modify, or delete any files."
            )
            try:
                response = await self._with_typing(
                    update.message.chat,
                    self.claude.send_message(restricted_prompt, user_id, use_continue=False),
                )
                await self._send_response(update, response)
            except Exception as e:
                await update.message.reply_text(f"Error: {str(e)}")
            return

        chat_id = update.effective_chat.id
        try:
            response = await self._with_typing(
                update.message.chat,
                self.claude.send_message(
                    message_text, user_id,
                    session_key=str(chat_id),
                    working_dir=self.config.working_dir,
                ),
            )
            await self._send_response(update, response)
        except Exception as e:
            await update.message.reply_text(f"Error: {str(e)}")
            logger.exception(f"Error handling message: {e}")

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_admin(user_id):
            await update.message.reply_text("Unauthorized. This bot is private.")
            return

        chat_id = update.effective_chat.id
        temp_path = tempfile.mktemp(suffix=".jpg")
        try:
            photo = update.message.photo[-1]
            photo_file = await context.bot.get_file(photo.file_id)
            await photo_file.download_to_drive(temp_path)
            caption = update.message.caption or "Describe this image"
            response = await self._with_typing(
                update.message.chat,
                self.claude.send_message(
                    caption, user_id,
                    session_key=str(chat_id),
                    image_path=temp_path,
                    working_dir=self.config.working_dir,
                ),
            )
            await self._send_response(update, response)
        except Exception as e:
            await update.message.reply_text(f"Error processing photo: {str(e)}")
            logger.exception(f"Error handling photo: {e}")
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Download voice message and forward audio data to Claude."""
        user_id = update.effective_user.id
        if not self.is_admin(user_id):
            await update.message.reply_text("Unauthorized. This bot is private.")
            return

        chat_id = update.effective_chat.id
        ogg_path = tempfile.mktemp(suffix=".ogg")
        try:
            voice_file = await context.bot.get_file(update.message.voice.file_id)
            await voice_file.download_to_drive(ogg_path)

            with open(ogg_path, "rb") as audio_file:
                audio_data = base64.standard_b64encode(audio_file.read()).decode()

            response = await self._with_typing(
                update.message.chat,
                self.claude.send_voice_message(
                    audio_data, user_id,
                    session_key=str(chat_id),
                    working_dir=self.config.working_dir,
                ),
            )
            await self._send_response(update, response)

        except Exception as e:
            await update.message.reply_text(f"Voice error: {str(e)}")
            logger.exception(f"Error handling voice: {e}")
        finally:
            if os.path.exists(ogg_path):
                os.unlink(ogg_path)

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        error = context.error
        if isinstance(error, (TimedOut, NetworkError)):
            logger.warning(f"Network error: {error}")
        else:
            logger.exception(f"Unhandled error: {error}", exc_info=error)

        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "Something went wrong talking to Telegram. Try again."
                )
            except Exception:
                pass

    async def _with_typing(self, chat, coro):
        """Run a coroutine while sending typing indicators every 5s."""
        async def keep_typing():
            try:
                while True:
                    try:
                        await chat.send_action("typing")
                    except Exception:
                        pass
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                pass
        typing_task = asyncio.create_task(keep_typing())
        try:
            return await coro
        finally:
            typing_task.cancel()

    async def _send_response(self, update: Update, response: str):
        """Send response, splitting if over Telegram's 4096 char limit."""
        if len(response) <= 4096:
            await update.message.reply_text(response)
        else:
            for i in range(0, len(response), 4096):
                await update.message.reply_text(response[i:i+4096])

    def register(self, app):
        """Register all handlers on the given Application."""
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("help", self.help_command))
        app.add_handler(CommandHandler("clear", self.clear_command))
        app.add_handler(CommandHandler("status", self.status_command))
        app.add_handler(CommandHandler("review", self.review_command))
        app.add_handler(CallbackQueryHandler(self.review_callback, pattern=r"^scout_(save|skip):"))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        app.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        app.add_handler(MessageHandler(filters.VOICE, self.handle_voice))
        app.add_error_handler(self.error_handler)


def main():
    """Entry point — builds a ChannelConfig from env vars and runs the bot."""
    channel_config = config.ChannelConfig(
        name="general",
        token=config.TELEGRAM_BOT_TOKEN,
        handler="GeneralHandler",
        admin_ids=config.ADMIN_TELEGRAM_IDS,
        users=config.RESTRICTED_USERS,
        working_dir=None,
        session_id=config.CLAUDE_SESSION_ID,
    )
    handler = GeneralHandler(channel_config)
    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .read_timeout(600)
        .write_timeout(600)
        .connect_timeout(30)
        .pool_timeout(60)
        .build()
    )
    handler.register(application)
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)
