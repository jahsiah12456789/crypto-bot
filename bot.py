import os
import time
import requests
import pandas as pd

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SYMBOL = "BTC-USD"

last_side = None
last_time = 0
MIN_WAIT = 540  # 9 minutes

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

def atr(df, n=12):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()

send("🚀 VIP BOT ACTIVE (2-5 TARGET)")

while True:
    try:
        df = get_data()

        df["ema9"] = ema(df["close"], 9)
        df["ema21"] = ema(df["close"], 21)
        df["ema50"] = ema(df["close"], 50)
        df["rsi"] = rsi(df["close"], 12)
        df["atr"] = atr(df, 12)

        row = df.iloc[-1]
        price = float(row["close"])
        rsi_val = float(row["rsi"])
        atr_val = float(row["atr"])

        trend_up = row["ema9"] > row["ema21"] and row["ema21"] > row["ema50"]
        trend_down = row["ema9"] < row["ema21"] and row["ema21"] < row["ema50"]

        if trend_up and rsi_val > 54:
            side = "LONG"
            signal = "BUY LONG"
            entry_low = price - atr_val * 0.25
            entry_high = price + atr_val * 0.25
            tp1 = price + atr_val * 1.4
            tp2 = price + atr_val * 2.2
            sl = price - atr_val * 1.0
            strength = "STRONG" if rsi_val > 57 else "NORMAL"

        elif trend_down and rsi_val < 46:
            side = "SHORT"
            signal = "SELL SHORT"
            entry_low = price - atr_val * 0.25
            entry_high = price + atr_val * 0.25
            tp1 = price - atr_val * 1.4
            tp2 = price - atr_val * 2.2
            sl = price + atr_val * 1.0
            strength = "STRONG" if rsi_val < 43 else "NORMAL"

        else:
            time.sleep(60)
            continue

        if side != last_side or (time.time() - last_time) > MIN_WAIT:
            msg = (
                f"🚨 VIP SIGNAL 🚨\n\n"
                f"💰 BTCUSDT.P\n"
                f"📊 {signal}\n"
                f"━━━━━━━━━━━━━━\n"
                f"📍 Entry Zone: {round(entry_low, 2)} - {round(entry_high, 2)}\n"
                f"🎯 TP1: {round(tp1, 2)}\n"
                f"🎯 TP2: {round(tp2, 2)}\n"
                f"🛑 SL: {round(sl, 2)}\n"
                f"━━━━━━━━━━━━━━\n"
                f"📈 RSI: {round(rsi_val, 1)}\n"
                f"🔥 Strength: {strength}"
            )

            send(msg)
            last_side = side
            last_time = time.time()

    except Exception as e:
        try:
            send(f"❌ ERROR\n{str(e)}")
        except:
            pass

    time.sleep(60)