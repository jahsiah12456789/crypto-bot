import os
import time
import requests
import pandas as pd

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

PRODUCT_ID = "BTC-USD"

last_side = None
last_signal_time = 0
MIN_SECONDS_BETWEEN_SIGNALS = 180  # 3 min

def send(msg):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg},
        timeout=20,
    )
    r.raise_for_status()

def get_data():
    r = requests.get(
        f"https://api.exchange.coinbase.com/products/{PRODUCT_ID}/candles",
        params={"granularity": 60},
        timeout=20,
        headers={"Accept": "application/json"},
    )
    r.raise_for_status()

    data = r.json()
    # Coinbase candles: [time, low, high, open, close, volume]
    df = pd.DataFrame(data, columns=["time", "low", "high", "open", "close", "volume"])

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values("time").reset_index(drop=True)
    return df

def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()

def rsi(series, n=10):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(df, n=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()

send("🚀 BTC VIP BOT LIVE (COINBASE DATA)")

while True:
    try:
        df = get_data()
        df["ema9"] = ema(df["close"], 9)
        df["ema21"] = ema(df["close"], 21)
        df["rsi10"] = rsi(df["close"], 10)
        df["atr14"] = atr(df)

        row = df.iloc[-1]
        price = float(row["close"])
        atr_val = float(row["atr14"])
        rsi_val = float(row["rsi10"])

        if row["ema9"] > row["ema21"]:
            side = "LONG"
            tp = price + atr_val * 1.2
            sl = price - atr_val * 0.8
            signal_text = "BUY LONG"
        else:
            side = "SHORT"
            tp = price - atr_val * 1.2
            sl = price + atr_val * 0.8
            signal_text = "SELL SHORT"

        flipped = side != last_side
        time_ok = (time.time() - last_signal_time) >= MIN_SECONDS_BETWEEN_SIGNALS

        strength = "STRONG" if ((side == "LONG" and rsi_val > 55) or (side == "SHORT" and rsi_val < 45)) else "ACTIVE"

        if flipped or time_ok:
            msg = (
                f"🚨 BTC VIP SIGNAL\n\n"
                f"Source: Coinbase\n"
                f"Symbol: BTCUSD\n"
                f"Signal: {signal_text}\n"
                f"Strength: {strength}\n"
                f"Entry: {round(price, 2)}\n"
                f"TP: {round(tp, 2)}\n"
                f"SL: {round(sl, 2)}\n"
                f"RSI: {round(rsi_val, 2)}"
            )
            send(msg)
            last_side = side
            last_signal_time = time.time()

    except Exception as e:
        try:
            send(f"❌ BOT ERROR\n{type(e).__name__}: {str(e)}")
        except Exception:
            pass

    time.sleep(60)