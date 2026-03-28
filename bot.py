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
MIN_WAIT = 10800  # 3 hours between new signals

TP_PERCENT = 0.006   # 0.6%
SL_PERCENT = 0.002   # 0.2%

last_side = None
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

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df

def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()

def rsi(series, n=12):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def win_rate():
    total = wins + losses
    if total == 0:
        return 0.0
    return round((wins / total) * 100, 1)

def stats_text():
    total = wins + losses
    return (
        f"📊 STATS\n\n"
        f"Trades Closed: {total}\n"
        f"Wins: {wins}\n"
        f"Losses: {losses}\n"
        f"Win Rate: {win_rate()}%\n"
        f"Signals Today: {signals_today}/{MAX_SIGNALS_PER_DAY}"
    )

def reset_day_if_needed(now_local):
    global last_reset_day, signals_today
    today = now_local.date()
    if last_reset_day != today:
        last_reset_day = today
        signals_today = 0

send("🚀 VIP BOT ACTIVE (PERCENT TP/SL MODE)")
last_time = time.time() - MIN_WAIT

while True:
    try:
        now_local = datetime.now(LOCAL_TZ)
        reset_day_if_needed(now_local)

        df = get_data()

        row = df.iloc[-1]
        high_price = float(row["high"])
        low_price = float(row["low"])

        # ===== Check open trade first =====
        if open_trade is not None:
            if open_trade["side"] == "LONG":
                if low_price <= open_trade["sl"]:
                    losses += 1
                    send(
                        f"❌ TRADE CLOSED\n\n"
                        f"💰 BTCUSDT.P\n"
                        f"📊 BUY LONG\n"
                        f"Result: SL HIT\n"
                        f"Exit: {round(open_trade['sl'], 2)}\n\n"
                        f"{stats_text()}"
                    )
                    open_trade = None

                elif high_price >= open_trade["tp"]:
                    wins += 1
                    send(
                        f"✅ TRADE CLOSED\n\n"
                        f"💰 BTCUSDT.P\n"
                        f"📊 BUY LONG\n"
                        f"Result: FULL TP HIT\n"
                        f"Exit: {round(open_trade['tp'], 2)}\n\n"
                        f"{stats_text()}"
                    )
                    open_trade = None

            elif open_trade["side"] == "SHORT":
                if high_price >= open_trade["sl"]:
                    losses += 1
                    send(
                        f"❌ TRADE CLOSED\n\n"
                        f"💰 BTCUSDT.P\n"
                        f"📊 SELL SHORT\n"
                        f"Result: SL HIT\n"
                        f"Exit: {round(open_trade['sl'], 2)}\n\n"
                        f"{stats_text()}"
                    )
                    open_trade = None

                elif low_price <= open_trade["tp"]:
                    wins += 1
                    send(
                        f"✅ TRADE CLOSED\n\n"
                        f"💰 BTCUSDT.P\n"
                        f"📊 SELL SHORT\n"
                        f"Result: FULL TP HIT\n"
                        f"Exit: {round(open_trade['tp'], 2)}\n\n"
                        f"{stats_text()}"
                    )
                    open_trade = None

        # ===== Only generate signals during session =====
        if not (START_HOUR <= now_local.hour < END_HOUR):
            time.sleep(60)
            continue

        # ===== Indicators =====
        df["ema9"] = ema(df["close"], 9)
        df["ema21"] = ema(df["close"], 21)
        df["ema50"] = ema(df["close"], 50)
        df["rsi"] = rsi(df["close"], 12)

        row = df.iloc[-1]
        price = float(row["close"])
        rsi_val = float(row["rsi"])

        trend_up = row["ema9"] > row["ema21"] and row["ema21"] > row["ema50"]
        trend_down = row["ema9"] < row["ema21"] and row["ema21"] < row["ema50"]

        if trend_up and rsi_val > 54:
            side = "LONG"
            signal = "BUY LONG"
            entry_low = price * 0.999
            entry_high = price * 1.001
            tp = price * (1 + TP_PERCENT)
            sl = price * (1 - SL_PERCENT)
            strength = "STRONG" if rsi_val > 57 else "NORMAL"

        elif trend_down and rsi_val < 46:
            side = "SHORT"
            signal = "SELL SHORT"
            entry_low = price * 0.999
            entry_high = price * 1.001
            tp = price * (1 - TP_PERCENT)
            sl = price * (1 + SL_PERCENT)
            strength = "STRONG" if rsi_val < 43 else "NORMAL"

        else:
            time.sleep(60)
            continue

        enough_time_passed = (time.time() - last_time) > MIN_WAIT
        can_send = (
            open_trade is None and
            signals_today < MAX_SIGNALS_PER_DAY and
            enough_time_passed
        )

        if can_send:
            msg = (
                f"🚨 VIP SIGNAL 🚨\n\n"
                f"💰 BTCUSDT.P\n"
                f"📊 {signal}\n"
                f"━━━━━━━━━━━━━━\n"
                f"📍 Entry Zone: {round(entry_low, 2)} - {round(entry_high, 2)}\n"
                f"🎯 TP: {round(tp, 2)} ({round(TP_PERCENT * 100, 2)}%)\n"
                f"🛑 SL: {round(sl, 2)} ({round(SL_PERCENT * 100, 2)}%)\n"
                f"━━━━━━━━━━━━━━\n"
                f"📈 RSI: {round(rsi_val, 1)}\n"
                f"🔥 Strength: {strength}\n\n"
                f"{stats_text()}"
            )

            send(msg)

            open_trade = {
                "side": side,
                "entry": price,
                "tp": tp,
                "sl": sl
            }

            last_side = side
            last_time = time.time()
            signals_today += 1

    except Exception as e:
        try:
            send(f"❌ ERROR\n{str(e)}")
        except:
            pass

    time.sleep(60)