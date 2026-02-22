"""Claude CLI subprocess wrapper for telegram-claude-bot."""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from . import config

logger = logging.getLogger(__name__)


class ClaudeClient:
    """Manages communication with Claude CLI via subprocess."""

    def __init__(self):
        self.claude_bin = config.CLAUDE_BIN_PATH
        # When True, skip --continue once (next message starts fresh after /clear)
        self.cleared = False

    async def send_message(self, message: str, user_id: int, conversation_history: Optional[list] = None, use_continue: bool = True, image_path: Optional[str] = None) -> str:
        """
        Send a message to Claude and return the response.

        Args:
            message: User message to send to Claude
            user_id: Telegram user ID (for session isolation)
            image_path: Optional path to an image file to include

        Returns:
            Claude's response text

        Raises:
            RuntimeError: If Claude CLI returns an error
        """
        try:
            # Claude CLI has no --image flag. Instead, we tell Claude
            # explicitly to use its Read tool (which supports images)
            # to load the file, then respond to the user's message.
            if image_path:
                message = f"Use your Read tool to read the image at this path: {image_path}\n\nThen respond to: {message}"

            # Build Claude command (must happen after message is finalized)
            cmd = [
                self.claude_bin,
                "-p", message,
                "--output-format", "json"
            ]

            # Always use --continue so Claude resumes the last session automatically.
            # If there's no prior session, CLI starts fresh anyway.
            # Skip once after /clear so the next message starts a new session.
            if use_continue and not self.cleared:
                cmd.append("--continue")
            self.cleared = False

            # Run Claude subprocess (Docker mounts isolated .claude directory)
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # Wait indefinitely for Claude to respond (no timeout)
            stdout, stderr = await process.communicate()

            # Check for errors
            if process.returncode != 0:
                error_msg = stderr.decode().strip()
                logger.error(f"Claude CLI error: {error_msg}")
                raise RuntimeError(f"Claude CLI failed: {error_msg}")

            # Parse JSON response
            try:
                raw_stdout = stdout.decode()
                response_data = json.loads(raw_stdout)

                # Claude CLI returns {"result": "...", "session_id": "..."}
                content = response_data.get("result", "")
                if not content:
                    return "*(Claude returned an empty response)*"

                return content
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse Claude JSON response: {e}")
                # Fallback: return raw stdout if JSON parsing fails
                raw_output = stdout.decode().strip()
                return raw_output if raw_output else "*(Unable to parse Claude response)*"

        except Exception as e:
            logger.exception(f"Error communicating with Claude: {e}")
            raise

    async def clear_session(self, user_id: int) -> bool:
        """
        Clear the current Claude session.
        Next message will start a fresh conversation (skips --continue once).

        Args:
            user_id: Telegram user ID (kept for API compatibility)

        Returns:
            True if cleared successfully
        """
        try:
            self.cleared = True
            return True
        except Exception as e:
            logger.error(f"Error clearing session: {e}")
            return False
