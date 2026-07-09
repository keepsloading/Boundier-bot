-- Represents watched channels (long-term workspaces)
CREATE TABLE IF NOT EXISTS channels (
    channel_id INTEGER PRIMARY KEY,
    category_id INTEGER,
    category_name TEXT,
    channel_name TEXT NOT NULL,
    channel_summary TEXT DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Represents active Discord threads mapping to ChatGPT chat sessions
CREATE TABLE IF NOT EXISTS threads (
    thread_id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    chatgpt_chat_id TEXT NOT NULL,
    thread_title TEXT,
    thread_summary TEXT DEFAULT '',
    message_count INTEGER DEFAULT 0,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id) ON DELETE CASCADE
);
