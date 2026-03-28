import os
import time
import requests
import pandas as pd

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
SYMBOL = "BTCUSDT"

def send(msg):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg},
        timeout=20,
    )
    print("SEND STATUS:", r.status_code)
    print("SEND RESPONSE:", r.text)
    r.raise_for_status()

def get_price():
    r = requests.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": SYMBOL},
        timeout=20,
    )
    r.raise_for_status()
    return float(r.json()["price"])

print("BOT STARTING...")
send("✅ BTC BOT LIVE")

while True:
    try:
        price = get_price()
        msg = f"🚨 BTC SIGNAL\n\nSymbol: {SYMBOL}\nPrice: {round(price, 2)}\nStatus: BOT RUNNING"
        send(msg)
        print("MESSAGE SENT")
    except Exception as e:
        print("ERROR:", e)

    time.sleep(60)