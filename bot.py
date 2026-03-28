import os
import time
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

LOCAL_TZ = ZoneInfo("America/Toronto")

START_HOUR = 9
END_HOUR = 22

MAX_SIGNALS = 4
BONUS_LIMIT = 1
MIN_WAIT = 7200  # 2h

TP_PERCENT = 0.006
SL_PERCENT = 0.002
ENTRY_ZONE = 0.001  # 0.1%

signals_today = 0
bonus_sent = 0
last_time = 0
last_reset = None

open_trade = None
wins = 0
losses = 0

def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg},
        timeout=20,
    )

def get_data():
    r = requests.get(
        "https://api.kucoin.com/api/v1/market/candles",
        params={"type": "1min", "symbol": "BTC-USDT"},
        timeout=20,
    )
    data = r.json()["data"]

    df = pd.DataFrame(data, columns=[
        "time","open","close","high","low","volume","turnover"
    ])
    df = df.iloc[::-1]

    for col in ["open","high","low","close"]:
        df[col] = pd.to_numeric(df[col])

    return df

def ema(s,n):
    return s.ewm(span=n).mean()

def rsi(s,n=12):
    d = s.diff()
    gain = d.clip(lower=0)
    loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1/n).mean()
    al = loss.ewm(alpha=1/n).mean()
    rs = ag/al
    return 100-(100/(1+rs))

def reset_day(now):
    global last_reset, signals_today, bonus_sent
    if last_reset != now.date():
        last_reset = now.date()
        signals_today = 0
        bonus_sent = 0

def stats():
    total = wins + losses
    wr = round((wins/total)*100,1) if total else 0
    return f"WR: {wr}% | W:{wins} L:{losses}"

send("🚀 VIP BOT ACTIVE (FINAL VERSION)")
last_time = time.time() - MIN_WAIT

while True:
    try:
        now = datetime.now(LOCAL_TZ)
        reset_day(now)

        if not (START_HOUR <= now.hour < END_HOUR):
            time.sleep(60)
            continue

        df = get_data()
        df["ema9"] = ema(df["close"],9)
        df["ema21"] = ema(df["close"],21)
        df["ema50"] = ema(df["close"],50)
        df["rsi"] = rsi(df["close"])

        row = df.iloc[-1]
        price = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        rsi_val = float(row["rsi"])

        up = row["ema9"] > row["ema21"] > row["ema50"]
        down = row["ema9"] < row["ema21"] < row["ema50"]

        # ===== TRACK TRADE =====
        if open_trade:
            if open_trade["side"] == "LONG":
                if low <= open_trade["sl"]:
                    losses += 1
                    send(f"❌ SL HIT\n{stats()}")
                    open_trade = None
                elif high >= open_trade["tp"]:
                    wins += 1
                    send(f"✅ TP HIT\n{stats()}")
                    open_trade = None

            else:
                if high >= open_trade["sl"]:
                    losses += 1
                    send(f"❌ SL HIT\n{stats()}")
                    open_trade = None
                elif low <= open_trade["tp"]:
                    wins += 1
                    send(f"✅ TP HIT\n{stats()}")
                    open_trade = None

        # ===== MAIN SIGNAL =====
        if open_trade is None and signals_today < MAX_SIGNALS:
            if (time.time() - last_time) > MIN_WAIT:

                if up and rsi_val > 54:
                    side = "BUY LONG"
                    entry_low = price * (1 - ENTRY_ZONE)
                    entry_high = price
                    tp = price * (1 + TP_PERCENT)
                    sl = price * (1 - SL_PERCENT)

                elif down and rsi_val < 46:
                    side = "SELL SHORT"
                    entry_low = price
                    entry_high = price * (1 + ENTRY_ZONE)
                    tp = price * (1 - TP_PERCENT)
                    sl = price * (1 + SL_PERCENT)

                else:
                    time.sleep(60)
                    continue

                msg = (
                    f"🚨 VIP SIGNAL\n\n"
                    f"BTCUSDT.P | {side}\n\n"
                    f"Entry Zone: {round(entry_low,2)} - {round(entry_high,2)}\n"
                    f"TP: {round(tp,2)}\n"
                    f"SL: {round(sl,2)}\n\n"
                    f"{stats()}\n"
                    f"{signals_today+1}/{MAX_SIGNALS}"
                )

                send(msg)

                open_trade = {
                    "side": "LONG" if "BUY" in side else "SHORT",
                    "tp": tp,
                    "sl": sl
                }

                last_time = time.time()
                signals_today += 1

        # ===== BONUS LIMIT =====
        if bonus_sent < BONUS_LIMIT and signals_today >= 2:

            if up:
                entry = price * (1 - ENTRY_ZONE)
                tp = entry * (1 + TP_PERCENT)
                sl = entry * (1 - SL_PERCENT)
                side = "BUY LONG"

            elif down:
                entry = price * (1 + ENTRY_ZONE)
                tp = entry * (1 - TP_PERCENT)
                sl = entry * (1 + SL_PERCENT)
                side = "SELL SHORT"
            else:
                time.sleep(60)
                continue

            msg = (
                f"🎁 BONUS LIMIT\n\n"
                f"BTCUSDT.P | {side}\n\n"
                f"Entry: {round(entry,2)} (LIMIT)\n"
                f"TP: {round(tp,2)}\n"
                f"SL: {round(sl,2)}"
            )

            send(msg)
            bonus_sent += 1

    except Exception as e:
        try:
            send(f"ERROR: {str(e)}")
        except:
            pass

    time.sleep(60)