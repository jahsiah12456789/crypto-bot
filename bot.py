import os
import time
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SYMBOL = "BTC-USD"
LOCAL_TZ = ZoneInfo("America/Toronto")

START_HOUR = 9
END_HOUR = 22

MAX_SIGNALS_PER_DAY = 4
MIN_WAIT = 10800  # 3 hours

TP_PERCENT = 0.006   # 0.6%
SL_PERCENT = 0.002   # 0.2%

last_time = 0
last_reset_day = None
signals_today = 0

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
        f"https://api.exchange.coinbase.com/products/{SYMBOL}/candles",
        params={"granularity": 60},
        timeout=20,
    )
    r.raise_for_status()

    df = pd.DataFrame(
        r.json(),
        columns=["time", "low", "high", "open", "close", "volume"]
    )
    df = df.sort_values("time").reset_index(drop=True)

    for col in ["open", "high", "low", "close"]:
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

def win_rate():
    total = wins + losses
    return round((wins / total) * 100, 1) if total else 0

def stats_text():
    total = wins + losses
    return (
        f"📊 Trades: {total} | Wins: {wins} | Losses: {losses} | WR: {win_rate()}%\n"
        f"Signals Today: {signals_today}/{MAX_SIGNALS_PER_DAY}"
    )

def reset_day(now):
    global last_reset_day, signals_today
    if last_reset_day != now.date():
        last_reset_day = now.date()
        signals_today = 0

send("🚀 VIP SCALP BOT ACTIVE")
last_time = time.time() - MIN_WAIT

while True:
    try:
        now = datetime.now(LOCAL_TZ)
        reset_day(now)

        df = get_data()
        row = df.iloc[-1]

        high_price = float(row["high"])
        low_price = float(row["low"])

        # ===== CHECK TRADE =====
        if open_trade:
            if open_trade["side"] == "LONG":
                if low_price <= open_trade["sl"]:
                    losses += 1
                    send(
                        f"❌ TRADE CLOSED\n\nBUY LONG\n"
                        f"Entry: {round(open_trade['entry'],2)}\n"
                        f"Exit: {round(open_trade['sl'],2)}\n"
                        f"Result: SL HIT\n\n{stats_text()}"
                    )
                    open_trade = None

                elif high_price >= open_trade["tp"]:
                    wins += 1
                    send(
                        f"✅ TRADE CLOSED\n\nBUY LONG\n"
                        f"Entry: {round(open_trade['entry'],2)}\n"
                        f"Exit: {round(open_trade['tp'],2)}\n"
                        f"Result: TP HIT\n\n{stats_text()}"
                    )
                    open_trade = None

            else:
                if high_price >= open_trade["sl"]:
                    losses += 1
                    send(
                        f"❌ TRADE CLOSED\n\nSELL SHORT\n"
                        f"Entry: {round(open_trade['entry'],2)}\n"
                        f"Exit: {round(open_trade['sl'],2)}\n"
                        f"Result: SL HIT\n\n{stats_text()}"
                    )
                    open_trade = None

                elif low_price <= open_trade["tp"]:
                    wins += 1
                    send(
                        f"✅ TRADE CLOSED\n\nSELL SHORT\n"
                        f"Entry: {round(open_trade['entry'],2)}\n"
                        f"Exit: {round(open_trade['tp'],2)}\n"
                        f"Result: TP HIT\n\n{stats_text()}"
                    )
                    open_trade = None

        if not (START_HOUR <= now.hour < END_HOUR):
            time.sleep(60)
            continue

        df["ema9"] = ema(df["close"], 9)
        df["ema21"] = ema(df["close"], 21)
        df["ema50"] = ema(df["close"], 50)
        df["rsi"] = rsi(df["close"], 12)

        row = df.iloc[-1]
        price = float(row["close"])
        rsi_val = float(row["rsi"])

        trend_up = row["ema9"] > row["ema21"] > row["ema50"]
        trend_down = row["ema9"] < row["ema21"] < row["ema50"]

        if trend_up and rsi_val > 54:
            side = "LONG"
            signal = "BUY LONG"
            tp = price * (1 + TP_PERCENT)
            sl = price * (1 - SL_PERCENT)
            strength = "STRONG" if rsi_val > 57 else "NORMAL"

        elif trend_down and rsi_val < 46:
            side = "SHORT"
            signal = "SELL SHORT"
            tp = price * (1 - TP_PERCENT)
            sl = price * (1 + SL_PERCENT)
            strength = "STRONG" if rsi_val < 43 else "NORMAL"

        else:
            time.sleep(60)
            continue

        if open_trade is None and signals_today < MAX_SIGNALS_PER_DAY:
            if (time.time() - last_time) > MIN_WAIT:

                rr = round(TP_PERCENT / SL_PERCENT, 1)

                msg = (
                    f"🚨 VIP SCALP SIGNAL\n\n"
                    f"BTCUSDT.P | {signal}\n\n"
                    f"Entry: {round(price, 2)}\n"
                    f"TP: {round(tp, 2)} (+{TP_PERCENT*100:.2f}%)\n"
                    f"SL: {round(sl, 2)} (-{SL_PERCENT*100:.2f}%)\n"
                    f"RR: 1:{rr}\n\n"
                    f"Strength: {strength}\n"
                    f"RSI: {round(rsi_val,1)}\n\n"
                    f"{stats_text()}"
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

    except Exception as e:
        try:
            send(f"❌ ERROR: {str(e)}")
        except:
            pass

    time.sleep(60)