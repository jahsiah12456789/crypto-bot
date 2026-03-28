import os
import time
import requests

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

def send(msg):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg},
        timeout=20,
    )
    print("STATUS:", r.status_code)
    print("RESPONSE:", r.text)
    r.raise_for_status()

print("BOT STARTING")
send("✅ BOT WORKING")

while True:
    print("LOOP RUNNING")
    time.sleep(60)