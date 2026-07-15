import os
import sqlite3
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger("boundier.storage")

class SQLiteStore:
    def __init__(self, db_path: str = "boundier.db", schema_path: str = "schema.sql", memory_dir: str = "memory"):
        self.db_path = db_path
        self.schema_path = schema_path
        self.memory_dir = memory_dir
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initializes database schema if missing and creates memory backup folders."""
        logger.info(f"Initializing SQLite database at: {self.db_path}")
        os.makedirs(self.memory_dir, exist_ok=True)
        
        conn = self._get_conn()
        try:
            with conn:
                if os.path.exists(self.schema_path):
                    with open(self.schema_path, "r", encoding="utf-8") as f:
                        conn.executescript(f.read())
                    logger.info("Schema applied successfully.")
                else:
                    logger.warning(f"Schema file not found at: {self.schema_path}")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}", exc_info=True)
        finally:
            conn.close()

    def sync_markdown_files(self):
        """Performs bi-directional sync between SQLite summaries and memory/*.md files during boot."""
        logger.info("Syncing Markdown summaries with database...")
        conn = self._get_conn()
        try:
            # 1. Update SQLite from edited markdown files (markdown -> DB)
            for file_name in os.listdir(self.memory_dir):
                if file_name.endswith(".md"):
                    channel_name = file_name[:-3]
                    file_path = os.path.join(self.memory_dir, file_name)
                    mtime = os.path.getmtime(file_path)
                    last_mod_time = datetime.fromtimestamp(mtime)
                    
                    with open(file_path, "r", encoding="utf-8") as f:
                        markdown_content = f.read().strip()
                        
                    cursor = conn.cursor()
                    cursor.execute("SELECT channel_id, channel_summary, updated_at FROM channels WHERE channel_name = ?", (channel_name,))
                    row = cursor.fetchone()
                    
                    if row:
                        db_updated_str = row["updated_at"]
                        try:
                            # Parse SQLite format YYYY-MM-DD HH:MM:SS
                            # Strip out spaces or Z if present
                            clean_time = db_updated_str.replace("Z", "").replace("T", " ")
                            db_updated = datetime.strptime(clean_time.split(".")[0], "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            db_updated = datetime.min
                            
                        # If markdown file has been touched more recently than SQLite table write, ingest it
                        if last_mod_time > db_updated and markdown_content != row["channel_summary"]:
                            logger.info(f"Markdown file '{file_name}' is newer than DB. Updating DB summary.")
                            with conn:
                                conn.execute(
                                    "UPDATE channels SET channel_summary = ?, updated_at = CURRENT_TIMESTAMP WHERE channel_id = ?",
                                    (markdown_content, row["channel_id"])
                                )
                    else:
                        logger.warning(f"Markdown summary exists for channel '{channel_name}', but channel is not registered in database.")
                        
            # 2. Update markdown files from DB (DB -> markdown)
            cursor = conn.cursor()
            cursor.execute("SELECT channel_name, channel_summary FROM channels")
            for row in cursor.fetchall():
                ch_name = row["channel_name"]
                ch_summary = row["channel_summary"] or ""
                file_path = os.path.join(self.memory_dir, f"{ch_name}.md")
                
                # Check if file needs to be updated
                write_file = True
                if os.path.exists(file_path):
                    with open(file_path, "r", encoding="utf-8") as f:
                        if f.read().strip() == ch_summary.strip():
                            write_file = False
                            
                if write_file:
                    logger.info(f"Writing DB channel summary to file: {file_path}")
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(ch_summary)
        except Exception as e:
            logger.error(f"Failed to sync markdown: {e}", exc_info=True)
        finally:
            conn.close()

    def save_channel(self, channel_id: int, channel_name: str, category_id: Optional[int] = None, category_name: Optional[str] = None, summary: str = ""):
        """Saves a channel log and writes/updates its backup Markdown mirror file."""
        conn = self._get_conn()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO channels (channel_id, channel_name, category_id, category_name, channel_summary, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(channel_id) DO UPDATE SET
                        channel_name = excluded.channel_name,
                        category_id = COALESCE(excluded.category_id, channels.category_id),
                        category_name = COALESCE(excluded.category_name, channels.category_name),
                        channel_summary = excluded.channel_summary,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (channel_id, channel_name, category_id, category_name, summary)
                )
            # Sync directly to local markdown file
            file_path = os.path.join(self.memory_dir, f"{channel_name}.md")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(summary)
        finally:
            conn.close()

    def get_channel(self, channel_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM channels WHERE channel_id = ?", (channel_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def save_thread(self, thread_id: int, channel_id: int, chatgpt_chat_id: str, title: Optional[str] = None, summary: str = "", message_count: int = 0):
        """Saves or updates a thread record mapping to ChatGPT chat ID."""
        conn = self._get_conn()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO threads (thread_id, channel_id, chatgpt_chat_id, thread_title, thread_summary, message_count, last_active)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(thread_id) DO UPDATE SET
                        chatgpt_chat_id = excluded.chatgpt_chat_id,
                        thread_title = COALESCE(excluded.thread_title, threads.thread_title),
                        thread_summary = excluded.thread_summary,
                        message_count = excluded.message_count,
                        last_active = CURRENT_TIMESTAMP
                    """,
                    (thread_id, channel_id, chatgpt_chat_id, title, summary, message_count)
                )
        finally:
            conn.close()

    def get_thread(self, thread_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM threads WHERE thread_id = ?", (thread_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def delete_thread(self, thread_id: int):
        conn = self._get_conn()
        try:
            with conn:
                conn.execute("DELETE FROM threads WHERE thread_id = ?", (thread_id,))
        finally:
            conn.close()

    def list_active_threads(self) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM threads ORDER BY last_active DESC")
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

    def list_channels(self) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM channels")
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

    def check_or_register_user(self, user_id: int, username: str, max_users: int = 5) -> bool:
        """
        Checks if a user is registered. If not, attempts to register them
        if the total registered users count is less than max_users.
        Returns True if the user is registered/allowed, False if the limit is exceeded.
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            # Check if user is already registered
            cursor.execute("SELECT 1 FROM registered_users WHERE user_id = ?", (user_id,))
            if cursor.fetchone():
                return True
                
            # Count registered users
            cursor.execute("SELECT COUNT(*) FROM registered_users")
            count = cursor.fetchone()[0]
            
            if count < max_users:
                with conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO registered_users (user_id, username) VALUES (?, ?)",
                        (user_id, username)
                    )
                logger.info(f"Registered user {username} ({user_id}). Total registered: {count + 1}/{max_users}")
                return True
                
            logger.warning(f"Registration rejected for user {username} ({user_id}). Max {max_users} users limit reached.")
            return False
        finally:
            conn.close()
