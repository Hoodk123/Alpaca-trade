import os
import requests

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
def send_discord_message(content: str):
    if not DISCORD_WEBHOOK_URL:
        print("[discord] Warning: DISCORD_WEBHOOK_URL is not set.")
        return
    payload = {"content": content}
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        response.raise_for_status()
    except Exception as e:
        print(f"[discord] failed to send notification: {e}")