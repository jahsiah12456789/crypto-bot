import os
import time
import requests
import pandas as pd

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SYMBOL = "BTCUSDT"

last_signal_time = 0
last_side = None
MIN_SECONDS_BETWEEN_SIGNALS = 300  # 5 min

def send(msg):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg},
        timeout=20,
    )
    r.raise_for_status()

def get_data():
    r = requests.get(
        "https://api.bybit.com/v5/market/kline",
        params={
            "category": "linear",
            "symbol": SYMBOL,
            "interval": "1",
            "limit": 200
        },
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()

    if data.get("retCode") != 0:
        raise Exception(f"Bybit API error: {data}")

    rows = data["result"]["list"]

    # Bybit returns newest first
    df = pd.DataFrame(rows, columns=[
        "startTime", "open", "high", "low", "close", "volume", "turnover"
    ])

    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["startTime"] = pd.to_numeric(df["startTime"], errors="coerce")
    df = df.sort_values("startTime").reset_index(drop=True)
    return df

def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()

def rsi(series, n=14):
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

send("🚀 BTC VIP BOT LIVE (BYBIT BTCUSDT FEED)")

while True:
    try:
        df = get_data()

        df["ema9"] = ema(df["close"], 9)
        df["ema21"] = ema(df["close"], 21)
        df["ema50"] = ema(df["close"], 50)
        df["rsi14"] = rsi(df["close"], 14)
        df["atr14"] = atr(df)

        row = df.iloc[-1]
        price = float(row["close"])
        atr_val = float(row["atr14"])
        rsi_val = float(row["rsi14"])

        trend_up = row["ema9"] > row["ema21"] > row["ema50"]
        trend_down = row["ema9"] < row["ema21"] < row["ema50"]

        if trend_up and rsi_val > 52:
            side = "LONG"
            signal_text = "BUY LONG"
            tp = price + atr_val * 2.0
            sl = price - atr_val * 1.0
        elif trend_down and rsi_val < 48:
            side = "SHORT"
            signal_text = "SELL SHORT"
            tp = price - atr_val * 2.0
            sl = price + atr_val * 1.0
        else:
            # fallback so bot stays active
            if row["ema9"] >= row["ema21"]:
                side = "LONG"
                signal_text = "BUY LONG"
                tp = price + atr_val * 1.2
                sl = price - atr_val * 0.8
            else:
                side = "SHORT"
                signal_text = "SELL SHORT"
                tp = price - atr_val * 1.2
                sl = price + atr_val * 0.8

        flipped = side != last_side
        time_ok = (time.time() - last_signal_time) >= MIN_SECONDS_BETWEEN_SIGNALS

        strength = "STRONG" if (
            (side == "LONG" and rsi_val > 55) or
            (side == "SHORT" and rsi_val < 45)
        ) else "ACTIVE"

        if flipped or time_ok:
            msg = (
                f"🚨 VIP SIGNAL 🚨\n\n"
                f"💰 BTCUSDT\n"
                f"📊 {signal_text}\n"
                f"━━━━━━━━━━━━━━\n"
                f"📍 Entry: {round(price, 2)}\n"
                f"🎯 TP: {round(tp, 2)}\n"
                f"🛑 SL: {round(sl, 2)}\n"
                f"━━━━━━━━━━━━━━\n"
                f"📈 RSI: {round(rsi_val, 1)}\n"
                f"🔥 Trend: {strength}\n"
                f"📡 Feed: Bybit BTCUSDT"
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