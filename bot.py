import os
import time
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

TOKEN = os.environ["TOKEN"]
CHAT_ID = str(os.environ["CHAT_ID"])

LOCAL_TZ = ZoneInfo("America/Toronto")

KUCOIN_SYMBOL = "BTC-USDT"

START_HOUR = 9
END_HOUR = 22

MAX_SIGNALS_WEEKDAY = 4
MAX_SIGNALS_WEEKEND = 2

BONUS_LIMIT_WEEKDAY = 1
BONUS_LIMIT_WEEKEND = 0

MIN_WAIT_WEEKDAY = 60 * 60
MIN_WAIT_WEEKEND = 2 * 60 * 60

COOLDOWN_AFTER_LOSS = 3
LOOP_SLEEP = 60

signals_today = 0
bonus_sent = 0
last_reset = None
last_signal_time = 0
last_summary_date = None
last_error_text = ""
last_error_time = 0

wins = 0
losses = 0
open_trade = None
loss_cooldown = 0

last_update_id = None


def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg},
        timeout=20,
    )


def safe_send_error(msg):
    global last_error_text, last_error_time
    now_ts = time.time()

    if msg == last_error_text and (now_ts - last_error_time) < 600:
        return

    last_error_text = msg
    last_error_time = now_ts
    try:
        send(msg)
    except Exception:
        pass


def get_updates(offset=None):
    params = {"timeout": 1}
    if offset is not None:
        params["offset"] = offset

    r = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/getUpdates",
        params=params,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def process_commands():
    global last_update_id, wins, losses, signals_today, bonus_sent, open_trade, loss_cooldown

    try:
        data = get_updates(last_update_id + 1 if last_update_id is not None else None)
        if not data.get("ok"):
            return

        for item in data.get("result", []):
            last_update_id = item["update_id"]

            message = item.get("message")
            if not message:
                continue

            chat_id = str(message["chat"]["id"])
            if chat_id != CHAT_ID:
                continue

            text = message.get("text", "").strip().lower()

            if text == "/stats":
                send(get_stats_message())

            elif text == "/status":
                send(get_status_message())

            elif text == "/reset":
                wins = 0
                losses = 0
                signals_today = 0
                bonus_sent = 0
                open_trade = None
                loss_cooldown = 0
                send("🔄 Bot stats reset.\nOpen trade cleared.\nSignals reset for today.")

            elif text == "/mode":
                send(get_mode_message())

    except Exception as e:
        safe_send_error(f"ERROR COMMANDS: {str(e)}")


def get_data(symbol="BTC-USDT", timeframe="15min"):
    r = requests.get(
        "https://api.kucoin.com/api/v1/market/candles",
        params={"type": timeframe, "symbol": symbol},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()["data"]

    df = pd.DataFrame(
        data,
        columns=["time", "open", "close", "high", "low", "volume", "turnover"]
    )

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


def is_weekend(now):
    return now.weekday() >= 5


def get_limits(now):
    if is_weekend(now):
        return {
            "max_signals": MAX_SIGNALS_WEEKEND,
            "bonus_limit": BONUS_LIMIT_WEEKEND,
            "min_wait": MIN_WAIT_WEEKEND,
            "mode_name": "Weekend Mode",
        }
    return {
        "max_signals": MAX_SIGNALS_WEEKDAY,
        "bonus_limit": BONUS_LIMIT_WEEKDAY,
        "min_wait": MIN_WAIT_WEEKDAY,
        "mode_name": "Weekday Mode",
    }


def stats():
    total = wins + losses
    wr = round((wins / total) * 100, 1) if total else 0.0
    return f"WR: {wr}% | W:{wins} L:{losses}"


def get_stats_message():
    total = wins + losses
    wr = round((wins / total) * 100, 1) if total else 0.0
    return (
        f"📊 DXM VIP STATS\n\n"
        f"Wins: {wins}\n"
        f"Losses: {losses}\n"
        f"Total Closed Trades: {total}\n"
        f"Win Rate: {wr}%\n"
        f"Signals Today: {signals_today}\n"
        f"Bonus Sent Today: {bonus_sent}"
    )


def get_status_message():
    now = datetime.now(LOCAL_TZ)
    limits = get_limits(now)

    trade_status = "No open trade"
    if open_trade:
        trade_status = (
            f"Open Trade: {open_trade['side']}\n"
            f"TP1: {open_trade['tp1']}\n"
            f"SL: {open_trade['sl']}"
        )

    return (
        f"🤖 DXM VIP BOT STATUS\n\n"
        f"Time: {now.strftime('%Y-%m-%d %I:%M %p')}\n"
        f"Session: {'ACTIVE' if in_session(now) else 'OFF'}\n"
        f"Mode: {limits['mode_name']}\n"
        f"Signals Used: {signals_today}/{limits['max_signals']}\n"
        f"Bonus Used: {bonus_sent}/{limits['bonus_limit']}\n"
        f"Cooldown: {loss_cooldown}\n\n"
        f"{trade_status}\n\n"
        f"{stats()}"
    )


def get_mode_message():
    now = datetime.now(LOCAL_TZ)
    limits = get_limits(now)
    return (
        f"⚙️ CURRENT MODE\n\n"
        f"Mode: {limits['mode_name']}\n"
        f"Max Signals: {limits['max_signals']}\n"
        f"Bonus Limit: {limits['bonus_limit']}\n"
        f"Min Wait: {int(limits['min_wait'] / 60)} min\n"
        f"Trading Hours: {START_HOUR}:00 - {END_HOUR}:00 Toronto time"
    )


def in_session(now):
    return START_HOUR <= now.hour < END_HOUR


def reset_day(now):
    global last_reset, signals_today, bonus_sent

    if last_reset != now.date():
        last_reset = now.date()
        signals_today = 0
        bonus_sent = 0
        send(
            f"📅 New trading day started\n\n"
            f"Signals reset: 0\n"
            f"Bonus reset: 0\n"
            f"Mode: {get_limits(now)['mode_name']}"
        )


def maybe_send_summary(now):
    global last_summary_date

    if now.hour >= END_HOUR and last_summary_date != now.date():
        last_summary_date = now.date()
        send(
            f"📊 Daily Summary\n\n"
            f"Signals Today: {signals_today}\n"
            f"Bonus Sent: {bonus_sent}\n"
            f"{stats()}"
        )


def get_trend():
    df = get_data(KUCOIN_SYMBOL, "1hour")
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
    df = get_data(KUCOIN_SYMBOL, "15min")
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
    price = recent.iloc[-1]["close"]

    if pd.isna(avg_range) or pd.isna(avg_atr):
        return True

    small_candles = avg_range < (avg_atr * 0.8 if avg_atr > 0 else avg_range + 1)
    tangled_emas = ema_gap < (price * 0.0015)

    return small_candles and tangled_emas


def breakout_long(df):
    row = df.iloc[-1]
    prev = df.iloc[-2]
    recent_high = df["high"].iloc[-8:-2].max()

    return (
        row["close"] > recent_high
        and row["close"] > row["open"]
        and row["rsi"] > 52
        and row["close"] > row["ema20"]
        and row["close"] > prev["close"]
    )


def breakout_short(df):
    row = df.iloc[-1]
    prev = df.iloc[-2]
    recent_low = df["low"].iloc[-8:-2].min()

    return (
        row["close"] < recent_low
        and row["close"] < row["open"]
        and row["rsi"] < 48
        and row["close"] < row["ema20"]
        and row["close"] < prev["close"]
    )


def pullback_long(df):
    row = df.iloc[-1]
    prev = df.iloc[-2]

    return (
        row["low"] <= row["ema20"]
        and row["close"] > row["ema20"]
        and row["close"] > row["open"]
        and row["close"] > prev["close"]
        and row["rsi"] > 50
    )


def pullback_short(df):
    row = df.iloc[-1]
    prev = df.iloc[-2]

    return (
        row["high"] >= row["ema20"]
        and row["close"] < row["ema20"]
        and row["close"] < row["open"]
        and row["close"] < prev["close"]
        and row["rsi"] < 50
    )


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


def format_signal(trade, mode, signal_number, max_signals):
    trend_text = "Bullish" if trade["side"] == "BUY LONG" else "Bearish"
    return (
        f"🚨 DXM VIP SIGNAL\n\n"
        f"Pair: BTCUSDT.P\n"
        f"Side: {trade['side']}\n"
        f"Entry Zone: {trade['entry_low']} - {trade['entry_high']}\n"
        f"TP1: {trade['tp1']}\n"
        f"TP2: {trade['tp2']}\n"
        f"TP3: {trade['tp3']}\n"
        f"SL: {trade['sl']}\n\n"
        f"Trend: {trend_text}\n"
        f"Mode: {mode}\n"
        f"Stats: {stats()}\n"
        f"Signal: {signal_number}/{max_signals}"
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
            send(f"❌ SL HIT\n\n{stats()}")
            open_trade = None
            return

        if high >= open_trade["tp1"]:
            wins += 1
            send(f"✅ TP HIT\n\n{stats()}")
            open_trade = None
            return

    if open_trade and open_trade["side"] == "SHORT":
        if high >= open_trade["sl"]:
            losses += 1
            loss_cooldown = COOLDOWN_AFTER_LOSS
            send(f"❌ SL HIT\n\n{stats()}")
            open_trade = None
            return

        if low <= open_trade["tp1"]:
            wins += 1
            send(f"✅ TP HIT\n\n{stats()}")
            open_trade = None
            return


send("🚀 DXM VIP BOT ACTIVE (POLISHED FINAL VERSION)")
last_signal_time = time.time() - MIN_WAIT_WEEKDAY

while True:
    try:
        process_commands()

        now = datetime.now(LOCAL_TZ)
        limits = get_limits(now)

        reset_day(now)
        maybe_send_summary(now)

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

        if open_trade is None and signals_today < limits["max_signals"]:
            enough_wait = (time.time() - last_signal_time) > limits["min_wait"]
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
                    send(
                        format_signal(
                            trade,
                            mode,
                            signals_today + 1,
                            limits["max_signals"]
                        )
                    )

                    open_trade = {
                        "side": "LONG" if trade["side"] == "BUY LONG" else "SHORT",
                        "tp1": trade["tp1"],
                        "sl": trade["sl"],
                    }

                    last_signal_time = time.time()
                    signals_today += 1

        if (
            open_trade is None
            and bonus_sent < limits["bonus_limit"]
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

            send(
                f"🎁 BONUS LIMIT\n\n"
                f"Pair: BTCUSDT.P\n"
                f"Side: {side}\n"
                f"Entry: {round(entry, 2)} (LIMIT)\n"
                f"TP: {round(tp, 2)}\n"
                f"SL: {round(sl, 2)}"
            )

            bonus_sent += 1

    except Exception as e:
        safe_send_error(f"ERROR: {str(e)}")

    time.sleep(LOOP_SLEEP)