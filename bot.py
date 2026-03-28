import os
import requests

print("BOT.PY STARTED")

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

r = requests.post(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    json={
        "chat_id": CHAT_ID,
        "text": "🚨 FORCED TEST FROM BOT.PY"
    },
    timeout=20,
)

print("STATUS:", r.status_code)
print("RESPONSE:", r.text)
r.raise_for_status()

print("DONE")