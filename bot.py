import os
import time
import requests
import pandas as pd
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]

LOCAL_TZ = ZoneInfo("America/Toronto")

START_HOUR = 9
END_HOUR = 22

CHECK_EVERY_SECONDS = 60

MAX_SIGNALS = 6
MAX_BONUS = 3

signals_today = 0
bonus_today = 0
last_reset = None

last_signal = {}
last_send_time = 0

def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg},
        timeout=20,
    )

def get_data(symbol, tf):
    r = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": symbol, "interval": tf, "limit": 100},
        timeout=10,
    )
    df = pd.DataFrame(r.json())
    df = df.iloc[:, :6]
    df.columns = ["time","open","high","low","close","volume"]

    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    return df

def ema(series, n):
    return series.ewm(span=n).mean()

def rsi(series, n=10):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/n).mean()
    avg_loss = loss.ewm(alpha=1/n).mean()
    rs = avg_gain / avg_loss
    return 100 - (100/(1+rs))

def atr(df, n=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n).mean()

def reset_day(now):
    global signals_today, bonus_today, last_reset
    if last_reset != now.date():
        signals_today = 0
        bonus_today = 0
        last_reset = now.date()

def build_signal(symbol, bonus=False):
    df1 = get_data(symbol, "1m")
    df15 = get_data(symbol, "15m")
    df1h = get_data(symbol, "1h")

    df1["ema9"] = ema(df1["close"], 9)
    df1["ema21"] = ema(df1["close"], 21)
    df1["rsi"] = rsi(df1["close"], 10)
    df1["atr"] = atr(df1)

    df15["ema"] = ema(df15["close"], 21)
    df1h["ema"] = ema(df1h["close"], 21)

    row = df1.iloc[-1]
    mid = df15.iloc[-1]
    high = df1h.iloc[-1]

    price = row["close"]
    atr_val = row["atr"]

    # TREND
    up_trend = mid["close"] > mid["ema"] or high["close"] > high["ema"]
    down_trend = mid["close"] < mid["ema"] or high["close"] < high["ema"]

    # MAIN SIGNAL
    if row["ema9"] > row["ema21"] and row["rsi"] > 52 and up_trend:
        side = "LONG"
        tp = price + atr_val * 1.5
        sl = price - atr_val * 1.0
        signal_type = "VIP"

    elif row["ema9"] < row["ema21"] and row["rsi"] < 48 and down_trend:
        side = "SHORT"
        tp = price - atr_val * 1.5
        sl = price + atr_val * 1.0
        signal_type = "VIP"

    else:
        # FALLBACK
        if bonus:
            if row["ema9"] >= row["ema21"]:
                side = "LONG"
            else:
                side = "SHORT"

            tp = price + atr_val if side == "LONG" else price - atr_val
            sl = price - atr_val if side == "LONG" else price + atr_val
            signal_type = "BONUS"
        else:
            return None

    return side, price, tp, sl, signal_type

def format_msg(symbol, side, price, tp, sl, signal_type):
    emoji = "🟢" if side == "LONG" else "🔴"

    if signal_type == "VIP":
        header = "🚨 VIP SIGNAL"
    else:
        header = "🎁 BONUS SIGNAL"

    return f"""{header}

{emoji} {symbol} {side}
━━━━━━━━━━━━━━
📍 Entry: {round(price,2)}
🎯 TP: {round(tp,2)}
🛑 SL: {round(sl,2)}
━━━━━━━━━━━━━━"""

def main():
    global last_send_time, signals_today, bonus_today

    send("🚀 FULL VIP BOT LIVE")

    while True:
        try:
            now = datetime.now(timezone.utc).astimezone(LOCAL_TZ)

            reset_day(now)

            if not (START_HOUR <= now.hour < END_HOUR):
                time.sleep(60)
                continue

            # spacing between signals
            if time.time() - last_send_time < 300:
                time.sleep(10)
                continue

            # MAIN SIGNALS
            if signals_today < MAX_SIGNALS:
                for symbol in SYMBOLS:
                    result = build_signal(symbol, bonus=False)
                    if result:
                        side, price, tp, sl, typ = result
                        key = f"{symbol}-{side}"

                        if last_signal.get(symbol) != key:
                            send(format_msg(symbol, side, price, tp, sl, typ))
                            last_signal[symbol] = key
                            last_send_time = time.time()
                            signals_today += 1
                            break

            # BONUS SIGNALS
            elif bonus_today < MAX_BONUS:
                for symbol in SYMBOLS:
                    result = build_signal(symbol, bonus=True)
                    if result:
                        side, price, tp, sl, typ = result
                        send(format_msg(symbol, side, price, tp, sl, typ))
                        last_send_time = time.time()
                        bonus_today += 1
                        break

        except Exception as e:
            print("ERROR:", e)

        time.sleep(CHECK_EVERY_SECONDS)

if __name__ == "__main__":
    main()