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
BONUS_SIGNAL_LIMIT = 1
MIN_WAIT = 7200  # 2 hours

TP_PERCENT = 0.006
SL_PERCENT = 0.002
ENTRY_OFFSET = 0.001

last_time = 0
signals_today = 0
bonus_sent = 0
last_reset_day = None

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
    r.raise_for_status()
    data = r.json()["data"]

    df = pd.DataFrame(data, columns=[
        "time","open","close","high","low","volume","turnover"
    ])
    df = df.iloc[::-1]

    for col in ["open","high","low","close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df

def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()

def rsi(series, n=12):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/n, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100/(1+rs))

def reset_day(now):
    global last_reset_day, signals_today, bonus_sent
    if last_reset_day != now.date():
        last_reset_day = now.date()
        signals_today = 0
        bonus_sent = 0

def win_rate():
    total = wins + losses
    return round((wins/total)*100,1) if total else 0

def stats():
    return f"WR: {win_rate()}% | W: {wins} L: {losses}"

send("🚀 VIP BOT ACTIVE (KUCOIN MODE)")
last_time = time.time() - MIN_WAIT

while True:
    try:
        now = datetime.now(LOCAL_TZ)
        reset_day(now)

        if not (START_HOUR <= now.hour < END_HOUR):
            time.sleep(60)
            continue

        df = get_data()
        df["ema9"] = ema(df["close"], 9)
        df["ema21"] = ema(df["close"], 21)
        df["ema50"] = ema(df["close"], 50)
        df["rsi"] = rsi(df["close"], 12)

        row = df.iloc[-1]
        price = float(row["close"])
        rsi_val = float(row["rsi"])

        trend_up = row["ema9"] > row["ema21"] > row["ema50"]
        trend_down = row["ema9"] < row["ema21"] < row["ema50"]

        # ===== MAIN SIGNAL =====
        if open_trade is None and signals_today < MAX_SIGNALS:
            if (time.time() - last_time) > MIN_WAIT:

                if trend_up and rsi_val > 54:
                    side = "BUY LONG"
                    tp = price * (1 + TP_PERCENT)
                    sl = price * (1 - SL_PERCENT)

                elif trend_down and rsi_val < 46:
                    side = "SELL SHORT"
                    tp = price * (1 - TP_PERCENT)
                    sl = price * (1 + SL_PERCENT)

                else:
                    time.sleep(60)
                    continue

                msg = (
                    f"🚨 VIP SIGNAL\n\n"
                    f"BTCUSDT.P | {side}\n\n"
                    f"Entry: USE CURRENT BTCC PRICE\n"
                    f"TP: {TP_PERCENT*100:.2f}%\n"
                    f"SL: {SL_PERCENT*100:.2f}%\n\n"
                    f"{stats()}\n"
                    f"{signals_today+1}/{MAX_SIGNALS} today"
                )

                send(msg)

                open_trade = {
                    "side": side,
                    "entry": price,
                    "tp": tp,
                    "sl": sl
                }

                last_time = time.time()
                signals_today += 1

        # ===== TRACK TRADE =====
        if open_trade:
            high = float(row["high"])
            low = float(row["low"])

            if open_trade["side"] == "BUY LONG":
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

        # ===== BONUS LIMIT SIGNAL =====
        if bonus_sent < BONUS_SIGNAL_LIMIT and signals_today >= 2:
            if trend_up:
                entry = price * (1 - ENTRY_OFFSET)
                tp = entry * (1 + TP_PERCENT)
                sl = entry * (1 - SL_PERCENT)
                side = "BUY LONG"

            elif trend_down:
                entry = price * (1 + ENTRY_OFFSET)
                tp = entry * (1 - TP_PERCENT)
                sl = entry * (1 + SL_PERCENT)
                side = "SELL SHORT"
            else:
                time.sleep(60)
                continue

            msg = (
                f"🎁 BONUS LIMIT SIGNAL\n\n"
                f"BTCUSDT.P | {side}\n\n"
                f"Entry: {round(entry,2)} (LIMIT)\n"
                f"TP: {round(tp,2)}\n"
                f"SL: {round(sl,2)}\n\n"
                f"Set and wait trade"
            )

            send(msg)
            bonus_sent += 1

    except Exception as e:
        try:
            send(f"ERROR: {str(e)}")
        except:
            pass

    time.sleep(60)