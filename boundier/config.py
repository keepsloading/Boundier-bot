import os
import yaml
from pydantic import BaseModel, Field
from typing import List

class DiscordConfig(BaseModel):
    token: str
    admin_channel_id: int
    command_prefix: str = "/"
    watched_categories: List[int] = Field(default_factory=list)

class ViewportConfig(BaseModel):
    width: int = 1280
    height: int = 720

class PlaywrightConfig(BaseModel):
    headless: bool = True
    user_data_dir: str = "./.chrome_profile"
    timeout_ms: int = 30000
    viewport: ViewportConfig = Field(default_factory=ViewportConfig)
    max_pages: int = 3

class MemoryConfig(BaseModel):
    max_thread_messages: int = 15
    channel_summary_limit: int = 500
    system_instructions: str = "You are Boundier, a helpful coding assistant."

class BoundierConfig(BaseModel):
    discord: DiscordConfig
    playwright: PlaywrightConfig = Field(default_factory=PlaywrightConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    max_users: int = 5  # How many Discord users can access this bot (1 = only you, 5 = you + 4 others)

def load_config(config_path: str = "config.yaml") -> BoundierConfig:
    # Load .env file manually if it exists to avoid external dependency issues
    env_file = ".env"
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip().strip('"').strip("'")

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found at: {config_path}")
    
    with open(config_path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)
    
    # Override token if set in environment or .env file
    if "DISCORD_TOKEN" in os.environ:
        if "discord" not in raw_config:
            raw_config["discord"] = {}
        raw_config["discord"]["token"] = os.environ["DISCORD_TOKEN"]
        
    # Override headless mode if set in environment or running in a headless Linux/Docker container
    import sys
    is_headless = False
    if "PLAYWRIGHT_HEADLESS" in os.environ:
        is_headless = os.environ["PLAYWRIGHT_HEADLESS"].lower() in ("true", "1", "yes")
    elif sys.platform != "win32" and "DISPLAY" not in os.environ:
        is_headless = True
    elif os.environ.get("RENDER") == "true" or os.path.exists("/.dockerenv"):
        is_headless = True
        
    if is_headless:
        if "playwright" not in raw_config:
            raw_config["playwright"] = {}
        raw_config["playwright"]["headless"] = True
    
    return BoundierConfig.model_validate(raw_config)
