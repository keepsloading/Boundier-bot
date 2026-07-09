import os
import yaml
from pydantic import BaseModel

class GeminiSelectors(BaseModel):
    chat_input: str
    submit_button: str
    new_chat_button: str
    response_containers: str
    streaming_indicators: str
    sidebar_history_items: str

def load_selectors(selectors_path: str = "selectors.yaml") -> GeminiSelectors:
    """Loads and validates CSS/XPath selectors from selectors.yaml."""
    if not os.path.exists(selectors_path):
        raise FileNotFoundError(f"Selectors file not found at: {selectors_path}")
        
    with open(selectors_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
        
    gemini_data = raw.get("gemini") if raw else None
    if not gemini_data:
        raise ValueError("Root key 'gemini' missing or empty in selectors.yaml")
        
    return GeminiSelectors.model_validate(gemini_data)
