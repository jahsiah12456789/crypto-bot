import os
import time
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

LOCAL_TZ = ZoneInfo("America/Toronto")
SYMBOL = "BTCUSDT"

last_side = None

def send(msg):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg},
        timeout=20,
    )
    print("SEND STATUS:", r.status_code)
    print("SEND RESPONSE:", r.text)
    r.raise_for_status()

def get_data():
    r = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": SYMBOL, "interval": "1m", "limit": 100},
        timeout=20,
    )
    r.raise_for_status()

    df = pd.DataFrame(r.json())
    df = df.iloc[:, :6]
    df.columns = ["time", "open", "high", "low", "close", "volume"]

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])

    return df

def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()

def atr(df, n=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()

send("🚀 SIGNAL BOT LIVE")

while True:
    try:
        now = datetime.now(LOCAL_TZ)
        print("LOOP:", now.strftime("%Y-%m-%d %H:%M:%S"))

        df = get_data()
        df["ema9"] = ema(df["close"], 9)
        df["ema21"] = ema(df["close"], 21)
        df["atr14"] = atr(df)

        row = df.iloc[-1]
        price = float(row["close"])
        atr_val = float(row["atr14"])

        if row["ema9"] > row["ema21"]:
            side = "LONG"
            tp = price + atr_val * 1.2
            sl = price - atr_val * 0.8
        else:
            side = "SHORT"
            tp = price - atr_val * 1.2
            sl = price + atr_val * 0.8

        if side != last_side:
            msg = (
                f"🚨 VIP SIGNAL\n\n"
                f"Symbol: {SYMBOL}\n"
                f"Signal: {'BUY LONG' if side == 'LONG' else 'SELL SHORT'}\n"
                f"Entry: {round(price, 2)}\n"
                f"TP: {round(tp, 2)}\n"
                f"SL: {round(sl, 2)}"
            )
            send(msg)
            last_side = side

    except Exception as e:
        print("ERROR:", e)

    time.sleep(60)
