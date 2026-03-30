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
MIN_WAIT = 60 * 60          # 1 hour between new signals
COOLDOWN_AFTER_LOSS = 3     # wait 3 loops after SL
LOOP_SLEEP = 60

TREND_SYMBOL = "BTC-USDT"
ENTRY_SYMBOL = "BTC-USDT"

signals_today = 0
bonus_sent = 0
last_reset = None
last_signal_time = 0

open_trade = None
wins = 0
losses = 0
loss_cooldown = 0


def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg},
        timeout=20,
    )


def get_data(symbol="BTC-USDT", timeframe="1min"):
    r = requests.get(
        "https://api.kucoin.com/api/v1/market/candles",
        params={"type": timeframe, "symbol": symbol},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()["data"]

    df = pd.DataFrame(data, columns=[
        "time", "open", "close", "high", "low", "volume", "turnover"
    ])
    df = df.iloc[::-1].reset_index(drop=True)

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()


def rsi(series, n=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1/n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/n, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def atr(df, n=14):
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()

    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def reset_day(now):
    global last_reset, signals_today, bonus_sent
    if last_reset != now.date():
        last_reset = now.date()
        signals_today = 0
        bonus_sent = 0


def stats():
    total = wins + losses
    wr = round((wins / total) * 100, 1) if total else 0.0
    return f"WR: {wr}% | W:{wins} L:{losses}"


def in_session(now):
    return START_HOUR <= now.hour < END_HOUR


def get_trend():
    df = get_data(TREND_SYMBOL, "1hour")
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["ema100"] = ema(df["close"], 100)

    row = df.iloc[-1]
    price = float(row["close"])
    ema20 = float(row["ema20"])
    ema50 = float(row["ema50"])
    ema100 = float(row["ema100"])

    bullish = price > ema20 > ema50 > ema100
    bearish = price < ema20 < ema50 < ema100

    if bullish:
        return "BULLISH", df
    if bearish:
        return "BEARISH", df
    return "SIDEWAYS", df


def get_entry_df():
    df = get_data(ENTRY_SYMBOL, "15min")
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["rsi"] = rsi(df["close"], 14)
    df["atr"] = atr(df, 14)
    return df


def is_choppy(df):
    recent = df.iloc[-6:]
    if len(recent) < 6:
        return True

    avg_range = (recent["high"] - recent["low"]).mean()
    avg_atr = recent["atr"].mean()
    ema_gap = abs(recent.iloc[-1]["ema20"] - recent.iloc[-1]["ema50"])

    if pd.isna(avg_range) or pd.isna(avg_atr):
        return True

    small_candles = avg_range < (avg_atr * 0.8 if avg_atr > 0 else avg_range + 1)
    tangled_emas = ema_gap < (recent.iloc[-1]["close"] * 0.0015)

    return small_candles and tangled_emas


def breakout_long(df):
    row = df.iloc[-1]
    prev = df.iloc[-2]
    recent_high = df["high"].iloc[-8:-2].max()

    strong_close = row["close"] > recent_high
    bullish_body = row["close"] > row["open"]
    rsi_ok = row["rsi"] > 52
    hold_above_ema = row["close"] > row["ema20"]

    return strong_close and bullish_body and rsi_ok and hold_above_ema and row["close"] > prev["close"]


def breakout_short(df):
    row = df.iloc[-1]
    prev = df.iloc[-2]
    recent_low = df["low"].iloc[-8:-2].min()

    strong_close = row["close"] < recent_low
    bearish_body = row["close"] < row["open"]
    rsi_ok = row["rsi"] < 48
    hold_below_ema = row["close"] < row["ema20"]

    return strong_close and bearish_body and rsi_ok and hold_below_ema and row["close"] < prev["close"]


def pullback_long(df):
    row = df.iloc[-1]
    prev = df.iloc[-2]

    wick_reject = row["low"] <= row["ema20"] and row["close"] > row["ema20"]
    bullish_close = row["close"] > row["open"]
    higher_than_prev = row["close"] > prev["close"]
    rsi_ok = row["rsi"] > 50

    return wick_reject and bullish_close and higher_than_prev and rsi_ok


def pullback_short(df):
    row = df.iloc[-1]
    prev = df.iloc[-2]

    wick_reject = row["high"] >= row["ema20"] and row["close"] < row["ema20"]
    bearish_close = row["close"] < row["open"]
    lower_than_prev = row["close"] < prev["close"]
    rsi_ok = row["rsi"] < 50

    return wick_reject and bearish_close and lower_than_prev and rsi_ok


def build_trade(side, df):
    row = df.iloc[-1]
    price = float(row["close"])
    current_atr = float(row["atr"]) if pd.notna(row["atr"]) else price * 0.003

    if side == "BUY LONG":
        recent_swing_low = float(df["low"].iloc[-6:].min())
        entry_low = min(price, float(row["ema20"]))
        entry_high = price
        sl = recent_swing_low - (current_atr * 0.2)
        risk = entry_high - sl
        tp1 = entry_high + (risk * 1.2)
        tp2 = entry_high + (risk * 2.0)
        tp3 = entry_high + (risk * 3.0)

    else:
        recent_swing_high = float(df["high"].iloc[-6:].max())
        entry_low = price
        entry_high = max(price, float(row["ema20"]))
        sl = recent_swing_high + (current_atr * 0.2)
        risk = sl - entry_low
        tp1 = entry_low - (risk * 1.2)
        tp2 = entry_low - (risk * 2.0)
        tp3 = entry_low - (risk * 3.0)

    return {
        "side": side,
        "entry_low": round(entry_low, 2),
        "entry_high": round(entry_high, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "tp3": round(tp3, 2),
        "sl": round(sl, 2),
    }


def format_signal(trade, mode):
    trend_text = "Bullish" if trade["side"] == "BUY LONG" else "Bearish"
    return (
        f"🚨 VIP SIGNAL\n\n"
        f"BTCUSDT.P | {trade['side']}\n\n"
        f"Entry Zone: {trade['entry_low']} - {trade['entry_high']}\n"
        f"TP1: {trade['tp1']}\n"
        f"TP2: {trade['tp2']}\n"
        f"TP3: {trade['tp3']}\n"
        f"SL: {trade['sl']}\n\n"
        f"Trend: {trend_text}\n"
        f"Mode: {mode}\n"
        f"{stats()}\n"
        f"{signals_today + 1}/{MAX_SIGNALS}"
    )


def track_trade(df):
    global open_trade, wins, losses, loss_cooldown

    if open_trade is None:
        return

    row = df.iloc[-1]
    high = float(row["high"])
    low = float(row["low"])

    if open_trade["side"] == "LONG":
        if low <= open_trade["sl"]:
            losses += 1
            loss_cooldown = COOLDOWN_AFTER_LOSS
            send(f"❌ SL HIT\n{stats()}")
            open_trade = None
            return

        if high >= open_trade["tp1"]:
            wins += 1
            send(f"✅ TP HIT\n{stats()}")
            open_trade = None
            return

    if open_trade and open_trade["side"] == "SHORT":
        if high >= open_trade["sl"]:
            losses += 1
            loss_cooldown = COOLDOWN_AFTER_LOSS
            send(f"❌ SL HIT\n{stats()}")
            open_trade = None
            return

        if low <= open_trade["tp1"]:
            wins += 1
            send(f"✅ TP HIT\n{stats()}")
            open_trade = None
            return


send("🚀 DXM VIP BOT ACTIVE (UPGRADED)")

last_signal_time = time.time() - MIN_WAIT

while True:
    try:
        now = datetime.now(LOCAL_TZ)
        reset_day(now)

        if not in_session(now):
            time.sleep(LOOP_SLEEP)
            continue

        trend, trend_df = get_trend()
        entry_df = get_entry_df()

        track_trade(entry_df)

        if loss_cooldown > 0:
            loss_cooldown -= 1
            time.sleep(LOOP_SLEEP)
            continue

        if open_trade is None and signals_today < MAX_SIGNALS:
            enough_wait = (time.time() - last_signal_time) > MIN_WAIT
            choppy = is_choppy(entry_df)

            if enough_wait and not choppy:
                trade = None
                mode = None

                if trend == "BULLISH":
                    if breakout_long(entry_df):
                        trade = build_trade("BUY LONG", entry_df)
                        mode = "Breakout Confirmed"
                    elif pullback_long(entry_df):
                        trade = build_trade("BUY LONG", entry_df)
                        mode = "Pullback Rejection"

                elif trend == "BEARISH":
                    if breakout_short(entry_df):
                        trade = build_trade("SELL SHORT", entry_df)
                        mode = "Breakdown Confirmed"
                    elif pullback_short(entry_df):
                        trade = build_trade("SELL SHORT", entry_df)
                        mode = "Pullback Rejection"

                if trade:
                    send(format_signal(trade, mode))

                    open_trade = {
                        "side": "LONG" if trade["side"] == "BUY LONG" else "SHORT",
                        "tp1": trade["tp1"],
                        "sl": trade["sl"],
                    }

                    last_signal_time = time.time()
                    signals_today += 1

        if (
            open_trade is None
            and bonus_sent < BONUS_LIMIT
            and signals_today >= 2
            and trend in ["BULLISH", "BEARISH"]
            and not is_choppy(entry_df)
        ):
            row = entry_df.iloc[-1]
            atr_now = float(row["atr"]) if pd.notna(row["atr"]) else float(row["close"]) * 0.003

            if trend == "BULLISH":
                entry = float(row["ema20"])
                sl = entry - (atr_now * 0.8)
                risk = entry - sl
                tp = entry + (risk * 2.0)
                side = "BUY LONG"

            else:
                entry = float(row["ema20"])
                sl = entry + (atr_now * 0.8)
                risk = sl - entry
                tp = entry - (risk * 2.0)
                side = "SELL SHORT"

            msg = (
                f"🎁 BONUS LIMIT\n\n"
                f"BTCUSDT.P | {side}\n\n"
                f"Entry: {round(entry, 2)} (LIMIT)\n"
                f"TP: {round(tp, 2)}\n"
                f"SL: {round(sl, 2)}"
            )

            send(msg)
            bonus_sent += 1

    except Exception as e:
        try:
            send(f"ERROR: {str(e)}")
        except Exception:
            pass

    time.sleep(LOOP_SLEEP)