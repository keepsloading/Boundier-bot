import os
import yaml
from pydantic import BaseModel

class ChatGPTSelectors(BaseModel):
    chat_input: str
    submit_button: str
    new_chat_button: str
    response_containers: str
    streaming_indicators: str
    sidebar_history_items: str
    profile_menu_button: str
    file_input: str
    image_download_button: str

def load_selectors(selectors_path: str = "selectors.yaml") -> ChatGPTSelectors:
    """Loads and validates CSS/XPath selectors from selectors.yaml for ChatGPT."""
    if not os.path.exists(selectors_path):
        raise FileNotFoundError(f"Selectors file not found at: {selectors_path}")
        
    with open(selectors_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
        
    chatgpt_data = raw.get("chatgpt") if raw else None
    if not chatgpt_data:
        raise ValueError("Root key 'chatgpt' missing or empty in selectors.yaml")
        
    return ChatGPTSelectors.model_validate(chatgpt_data)
