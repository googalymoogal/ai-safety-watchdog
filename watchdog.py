import os
import requests

def test_telegram():
    token = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    
    print(f"Testing Telegram connection...")
    print(f"Token (first 5 chars): {token[:5]}...")
    print(f"Chat ID: {chat_id}")
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": "✅ **SYSTEM TEST**: If you can read this, your AI Watchdog is connected!", "parse_mode": "Markdown"}
    
    response = requests.post(url, json=payload)
    
    print(f"Telegram Response Code: {response.status_code}")
    print(f"Telegram Response Body: {response.text}")

if __name__ == "__main__":
    test_telegram()
