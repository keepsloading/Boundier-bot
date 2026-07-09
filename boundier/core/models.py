from enum import Enum
from datetime import datetime

class SessionStatus(str, Enum):
    IDLE = "idle"
    PROCESSING = "processing"
    SUMMARIZING = "summarizing"
    ARCHIVED = "archived"

class Session:
    def __init__(self, thread_id: int, chatgpt_chat_id: str, channel_id: int):
        self.discord_thread_id: int = thread_id
        self.chatgpt_chat_id: str = chatgpt_chat_id
        self.channel_id: int = channel_id
        self.chatgpt_url: str = f"https://chatgpt.com/c/{chatgpt_chat_id}"
        self.conversation_title: str = ""
        self.last_active: datetime = datetime.now()
        self.status: SessionStatus = SessionStatus.IDLE
        self.cached_summary: str = ""
        self.message_count: int = 0
        self.rename_parent: bool = False

    def update_activity(self):
        self.last_active = datetime.now()
        self.message_count += 1
