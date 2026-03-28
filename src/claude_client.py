"""Claude CLI subprocess client for telegram-claude."""
import asyncio
import json
import logging
import os
from typing import Optional

from . import config

logger = logging.getLogger(__name__)


class ClaudeClient:
    """Manages communication with Claude via the claude -p subprocess."""

    def __init__(self):
        self._session_ids: dict[str, str] = {}  # session_key → session_id

    async def send_message(
        self,
        message: str,
        user_id: int,
        conversation_history: Optional[list] = None,  # unused, kept for compat
        use_continue: bool = True,
        image_path: Optional[str] = None,
        working_dir: Optional[str] = None,
        session_key: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Send a message to Claude via CLI and return the response."""
        if system_prompt:
            message = f"[System instructions: {system_prompt}]\n\n{message}"

        if image_path:
            message = f"Use your Read tool to read the image at this path: {image_path}\n\nThen respond to: {message}"

        cmd = [
            config.CLAUDE_BIN_PATH,
            "-p", message,
            "--output-format", "json",
        ]

        # Resume existing session for this channel if we have one
        if use_continue and session_key and session_key in self._session_ids:
            cmd.extend(["--resume", self._session_ids[session_key]])

        cwd = os.path.expanduser(working_dir) if working_dir else None

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            # If we used --resume and it failed, the session may be stale/expired.
            # Clear it and retry without resuming.
            if use_continue and session_key and "--resume" in cmd:
                stale_id = self._session_ids.pop(session_key, None)
                logger.warning(f"Session resume failed for {session_key} (session {stale_id}), retrying fresh")
                cmd_no_resume = [c for i, c in enumerate(cmd) if c != "--resume" and (i == 0 or cmd[i-1] != "--resume")]
                process2 = await asyncio.create_subprocess_exec(
                    *cmd_no_resume,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
                stdout, stderr = await process2.communicate()
                if process2.returncode != 0:
                    error_msg = stderr.decode().strip()
                    logger.error(f"Claude CLI error on retry: {error_msg}")
                    raise RuntimeError(f"Claude CLI failed: {error_msg}")
            else:
                error_msg = stderr.decode().strip()
                logger.error(f"Claude CLI error: {error_msg}")
                raise RuntimeError(f"Claude CLI failed: {error_msg}")

        try:
            response_data = json.loads(stdout.decode())
            session_id = response_data.get("session_id")
            if session_id and session_key and use_continue:
                self._session_ids[session_key] = session_id
            content = response_data.get("result", "")
            return content or "*(Claude returned an empty response)*"
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude JSON response: {e}")
            raw = stdout.decode().strip()
            return raw or "*(Unable to parse Claude response)*"

    async def send_voice_message(
        self,
        audio_b64: str,
        user_id: int,
        working_dir: Optional[str] = None,
        session_key: Optional[str] = None,
    ) -> str:
        """Voice messages are not supported in CLI mode."""
        return "Voice messages aren't supported. Send text instead."

    async def clear_session(self, user_id: int, session_key: Optional[str] = None) -> bool:
        """Clear conversation history for a session."""
        try:
            if session_key:
                self._session_ids.pop(session_key, None)
            else:
                self._session_ids.clear()
            return True
        except Exception as e:
            logger.error(f"Error clearing session: {e}")
            return False
