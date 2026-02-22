"""Message history tracking with SQLite for telegram-claude-bot."""
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class HistoryManager:
    """Manages conversation history in SQLite database."""

    def __init__(self, db_path: str = None):
        """
        Initialize history manager.

        Args:
            db_path: Path to SQLite database file
        """
        if db_path is None:
            db_path = str(Path(__file__).resolve().parent.parent / "data" / "history.db")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()
        logger.info(f"History manager initialized: {self.db_path}")

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Return rows as dicts
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def _init_database(self):
        """Create database tables if they don't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Sessions table - groups related conversations
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    last_activity TIMESTAMP NOT NULL,
                    message_count INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT 1
                )
            """)

            # Messages table - individual messages and responses
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    direction TEXT NOT NULL,  -- 'user' or 'bot'
                    message_text TEXT NOT NULL,
                    char_count INTEGER,
                    tokens_estimated INTEGER,
                    claude_model TEXT,
                    error_occurred BOOLEAN DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
            """)

            # Create indexes for common queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_timestamp
                ON messages(timestamp)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_user
                ON messages(user_id)
            """)

            logger.info("Database tables initialized")

    def _estimate_tokens(self, text: str) -> int:
        """
        Rough token estimation (4 chars ≈ 1 token).

        Args:
            text: Text to estimate tokens for

        Returns:
            Estimated token count
        """
        return len(text) // 4

    def log_message(
        self,
        session_id: str,
        user_id: int,
        username: Optional[str],
        direction: str,
        message_text: str,
        error_occurred: bool = False
    ):
        """
        Log a message (user or bot) to history.

        Args:
            session_id: Claude session UUID
            user_id: Telegram user ID
            username: Telegram username (optional)
            direction: 'user' or 'bot'
            message_text: The message content
            error_occurred: Whether this message resulted in an error
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Ensure session exists
                cursor.execute(
                    "SELECT session_id FROM sessions WHERE session_id = ?",
                    (session_id,)
                )
                if not cursor.fetchone():
                    # Create new session
                    cursor.execute("""
                        INSERT INTO sessions
                        (session_id, user_id, started_at, last_activity)
                        VALUES (?, ?, ?, ?)
                    """, (session_id, user_id, datetime.now(), datetime.now()))

                # Update session activity
                cursor.execute("""
                    UPDATE sessions
                    SET last_activity = ?,
                        message_count = message_count + 1
                    WHERE session_id = ?
                """, (datetime.now(), session_id))

                # Insert message
                char_count = len(message_text)
                tokens_estimated = self._estimate_tokens(message_text)

                cursor.execute("""
                    INSERT INTO messages
                    (session_id, timestamp, user_id, username, direction,
                     message_text, char_count, tokens_estimated, error_occurred)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_id,
                    datetime.now(),
                    user_id,
                    username,
                    direction,
                    message_text,
                    char_count,
                    tokens_estimated,
                    error_occurred
                ))

                logger.debug(f"Logged {direction} message: {char_count} chars")

        except Exception as e:
            logger.error(f"Failed to log message: {e}")

    def get_recent_messages(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get recent messages across all sessions.

        Args:
            limit: Maximum number of messages to return

        Returns:
            List of message dictionaries
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT
                        id, session_id, timestamp, user_id, username,
                        direction, substr(message_text, 1, 100) as preview,
                        char_count, tokens_estimated
                    FROM messages
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))

                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get recent messages: {e}")
            return []

    def get_session_messages(
        self,
        session_id: str,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all messages for a specific session.

        Args:
            session_id: Claude session UUID
            limit: Optional limit on number of messages

        Returns:
            List of message dictionaries
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                query = """
                    SELECT *
                    FROM messages
                    WHERE session_id = ?
                    ORDER BY timestamp ASC
                """
                if limit:
                    query += f" LIMIT {limit}"

                cursor.execute(query, (session_id,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get session messages: {e}")
            return []

    def get_stats(self) -> Dict[str, Any]:
        """
        Get overall usage statistics.

        Returns:
            Dictionary with stats (total messages, sessions, etc.)
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Total messages
                cursor.execute("SELECT COUNT(*) as count FROM messages")
                total_messages = cursor.fetchone()['count']

                # Total sessions
                cursor.execute("SELECT COUNT(*) as count FROM sessions")
                total_sessions = cursor.fetchone()['count']

                # Active sessions
                cursor.execute("""
                    SELECT COUNT(*) as count
                    FROM sessions
                    WHERE is_active = 1
                """)
                active_sessions = cursor.fetchone()['count']

                # Total tokens (estimated)
                cursor.execute("""
                    SELECT SUM(tokens_estimated) as total
                    FROM messages
                """)
                total_tokens = cursor.fetchone()['total'] or 0

                # Most recent activity
                cursor.execute("""
                    SELECT MAX(timestamp) as latest
                    FROM messages
                """)
                latest_activity = cursor.fetchone()['latest']

                return {
                    'total_messages': total_messages,
                    'total_sessions': total_sessions,
                    'active_sessions': active_sessions,
                    'total_tokens_estimated': total_tokens,
                    'latest_activity': latest_activity
                }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {}

    def clear_old_sessions(self, days: int = 30):
        """
        Mark old sessions as inactive.

        Args:
            days: Consider sessions older than this inactive
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE sessions
                    SET is_active = 0
                    WHERE last_activity < datetime('now', '-{} days')
                """.format(days))

                count = cursor.rowcount
                logger.info(f"Marked {count} old sessions as inactive")
                return count
        except Exception as e:
            logger.error(f"Failed to clear old sessions: {e}")
            return 0
