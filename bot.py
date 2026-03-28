import os
import time
import requests
import pandas as pd

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

last_signal_time = 0
last_side = None
MIN_SECONDS_BETWEEN_SIGNALS = 300  # 5 min

def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg},
        timeout=20,
    )

def get_data():
    r = requests.get(
        "https://api.exchange.coinbase.com/products/BTC-USD/candles",
        params={"granularity": 60},
        timeout=20,
    )
    r.raise_for_status()

    df = pd.DataFrame(r.json(), columns=["time","low","high","open","close","volume"])
    df = df.sort_values("time")

    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col])

    return df

def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()

def rsi(series, n=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/n, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100/(1+rs))

def atr(df, n=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl,hc,lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

send("🚀 BTC VIP BOT LIVE (BTCUSDT STYLE)")

while True:
    try:
        df = get_data()

        df["ema9"] = ema(df["close"], 9)
        df["ema21"] = ema(df["close"], 21)
        df["ema50"] = ema(df["close"], 50)
        df["rsi"] = rsi(df["close"], 14)
        df["atr"] = atr(df)

        row = df.iloc[-1]
        price = float(row["close"])
        atr_val = float(row["atr"])
        rsi_val = float(row["rsi"])

        trend_up = row["ema9"] > row["ema21"] > row["ema50"]
        trend_down = row["ema9"] < row["ema21"] < row["ema50"]

        if trend_up and rsi_val > 50:
            side = "LONG"
            signal = "BUY LONG"
            tp = price + atr_val * 2
            sl = price - atr_val * 1
        elif trend_down and rsi_val < 50:
            side = "SHORT"
            signal = "SELL SHORT"
            tp = price - atr_val * 2
            sl = price + atr_val * 1
        else:
            time.sleep(60)
            continue

        flipped = side != last_side
        time_ok = (time.time() - last_signal_time) > MIN_SECONDS_BETWEEN_SIGNALS

        strength = "STRONG" if abs(rsi_val - 50) > 5 else "NORMAL"

        if flipped and time_ok:
            msg = (
                f"🚨 VIP SIGNAL 🚨\n\n"
                f"💰 BTCUSDT\n"
                f"📊 {signal}\n"
                f"━━━━━━━━━━━━━━\n"
                f"📍 Entry: {round(price,2)}\n"
                f"🎯 TP: {round(tp,2)}\n"
                f"🛑 SL: {round(sl,2)}\n"
                f"━━━━━━━━━━━━━━\n"
                f"📈 RSI: {round(rsi_val,1)}\n"
                f"🔥 Strength: {strength}"
            )

            send(msg)
            last_signal_time = time.time()
            last_side = side

    except Exception as e:
        try:
            send(f"❌ ERROR\n{str(e)}")
        except:
            pass

    time.sleep(60)