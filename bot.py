import os
import time
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

TOKEN = os.environ["TOKEN"]
CHAT_ID = str(os.environ["CHAT_ID"])

LOCAL_TZ = ZoneInfo("America/Toronto")
SYMBOL = "BTC-USDT"

START_HOUR = 9
END_HOUR = 22
LOOP_SLEEP = 60

# ---------- MODES ----------
CURRENT_MODE = "BALANCED"   # SAFE / BALANCED / AGGRESSIVE
BOT_PAUSED = False

# ---------- LIMITS ----------
SAFE_MAX_VIP = 2
SAFE_MAX_SCALP = 1
SAFE_SCALP_PER_HOUR = 1

BAL_MAX_VIP = 4
BAL_MAX_SCALP = 3
BAL_SCALP_PER_HOUR = 1

AGG_MAX_VIP = 5
AGG_MAX_SCALP = 5
AGG_SCALP_PER_HOUR = 2

# ---------- STATE ----------
last_reset = None
signals_today = 0
scalps_today = 0
open_trade = None

vip_wins = 0
vip_losses = 0
scalp_wins = 0
scalp_losses = 0

last_update_id = None
last_error_text = ""
last_error_time = 0

scalp_hour_key = None
scalp_count_this_hour = 0


# ---------- TELEGRAM ----------
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


# ---------- MODE HELPERS ----------
def get_limits():
    if CURRENT_MODE == "SAFE":
        return {
            "max_vip": SAFE_MAX_VIP,
            "max_scalp": SAFE_MAX_SCALP,
            "scalp_per_hour": SAFE_SCALP_PER_HOUR,
        }
    if CURRENT_MODE == "AGGRESSIVE":
        return {
            "max_vip": AGG_MAX_VIP,
            "max_scalp": AGG_MAX_SCALP,
            "scalp_per_hour": AGG_SCALP_PER_HOUR,
        }
    return {
        "max_vip": BAL_MAX_VIP,
        "max_scalp": BAL_MAX_SCALP,
        "scalp_per_hour": BAL_SCALP_PER_HOUR,
    }


# ---------- COMMANDS ----------
def process_commands():
    global last_update_id, BOT_PAUSED, CURRENT_MODE
    global vip_wins, vip_losses, scalp_wins, scalp_losses
    global signals_today, scalps_today, open_trade

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

            if text == "/pause":
                BOT_PAUSED = True
                send("⏸️ Bot paused.")

            elif text == "/resume":
                BOT_PAUSED = False
                send("▶️ Bot resumed.")

            elif text == "/safe":
                CURRENT_MODE = "SAFE"
                send("🛡️ Mode changed to SAFE.")

            elif text == "/balanced":
                CURRENT_MODE = "BALANCED"
                send("⚖️ Mode changed to BALANCED.")

            elif text == "/aggressive":
                CURRENT_MODE = "AGGRESSIVE"
                send("🔥 Mode changed to AGGRESSIVE.")

            elif text == "/mode":
                limits = get_limits()
                send(
                    f"⚙️ CURRENT MODE\n\n"
                    f"Mode: {CURRENT_MODE}\n"
                    f"VIP Max/Day: {limits['max_vip']}\n"
                    f"Scalp Max/Day: {limits['max_scalp']}\n"
                    f"Scalp Max/Hour: {limits['scalp_per_hour']}"
                )

            elif text == "/stats":
                send(get_stats_message())

            elif text == "/status":
                send(get_status_message())

            elif text == "/reset":
                vip_wins = 0
                vip_losses = 0
                scalp_wins = 0
                scalp_losses = 0
                signals_today = 0
                scalps_today = 0
                open_trade = None
                send("🔄 Stats reset. Open trade cleared.")

    except Exception as e:
        safe_send_error(f"ERROR COMMANDS: {str(e)}")


# ---------- DATA ----------
def get_data(tf):
    r = requests.get(
        "https://api.kucoin.com/api/v1/market/candles",
        params={"type": tf, "symbol": SYMBOL},
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


# ---------- STATS ----------
def wr(w, l):
    total = w + l
    return round((w / total) * 100, 1) if total else 0.0


def all_stats():
    total_w = vip_wins + scalp_wins
    total_l = vip_losses + scalp_losses
    return f"Overall WR: {wr(total_w, total_l)}% | W:{total_w} L:{total_l}"


def get_stats_message():
    total_w = vip_wins + scalp_wins
    total_l = vip_losses + scalp_losses
    return (
        f"📊 DXM STATS\n\n"
        f"VIP -> WR: {wr(vip_wins, vip_losses)}% | W:{vip_wins} L:{vip_losses}\n"
        f"SCALP -> WR: {wr(scalp_wins, scalp_losses)}% | W:{scalp_wins} L:{scalp_losses}\n"
        f"OVERALL -> WR: {wr(total_w, total_l)}% | W:{total_w} L:{total_l}\n\n"
        f"VIP Today: {signals_today}\n"
        f"SCALP Today: {scalps_today}\n"
        f"Mode: {CURRENT_MODE}\n"
        f"Paused: {BOT_PAUSED}"
    )


def get_status_message():
    limits = get_limits()
    trade_text = "No open trade"

    if open_trade:
        trade_text = (
            f"Open Trade: {open_trade['signal_type']} {open_trade['side']}\n"
            f"TP: {open_trade['tp']}\n"
            f"SL: {open_trade['sl']}"
        )

    return (
        f"🤖 DXM STATUS\n\n"
        f"Paused: {BOT_PAUSED}\n"
        f"Mode: {CURRENT_MODE}\n"
        f"VIP Used: {signals_today}/{limits['max_vip']}\n"
        f"SCALP Used: {scalps_today}/{limits['max_scalp']}\n"
        f"Scalp Hour Count: {scalp_count_this_hour}/{limits['scalp_per_hour']}\n\n"
        f"{trade_text}\n\n"
        f"{all_stats()}"
    )


# ---------- RESET ----------
def reset_day(now):
    global last_reset, signals_today, scalps_today
    if last_reset != now.date():
        last_reset = now.date()
        signals_today = 0
        scalps_today = 0
        send(
            f"📅 New Day Started\n\n"
            f"VIP reset: 0\n"
            f"Scalp reset: 0\n"
            f"Mode: {CURRENT_MODE}"
        )


def reset_scalp_hour(now):
    global scalp_hour_key, scalp_count_this_hour
    hour_key = (now.date(), now.hour)

    if scalp_hour_key != hour_key:
        scalp_hour_key = hour_key
        scalp_count_this_hour = 0


# ---------- SESSION ----------
def in_session(now):
    return START_HOUR <= now.hour < END_HOUR


# ---------- TREND ----------
def get_trend():
    df = get_data("1hour")
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["ema100"] = ema(df["close"], 100)

    row = df.iloc[-1]
    price = float(row["close"])

    if price > row["ema20"] > row["ema50"] > row["ema100"]:
        return "BULL"
    if price < row["ema20"] < row["ema50"] < row["ema100"]:
        return "BEAR"
    return "SIDE"


# ---------- ENTRY ----------
def get_entry():
    df = get_data("15min")
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["rsi"] = rsi(df["close"], 14)
    return df


# ---------- VIP SIGNAL ----------
def vip_signal(df, trend):
    row = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(row["close"])

    if trend == "BULL":
        if CURRENT_MODE == "SAFE":
            if row["close"] > prev["high"] and row["rsi"] > 55:
                return "BUY LONG", price, price * 1.010, price * 0.995
        elif CURRENT_MODE == "AGGRESSIVE":
            if row["close"] > prev["close"] and row["rsi"] > 51:
                return "BUY LONG", price, price * 1.008, price * 0.996
        else:
            if row["close"] > prev["high"] and row["rsi"] > 53:
                return "BUY LONG", price, price * 1.009, price * 0.995

    if trend == "BEAR":
        if CURRENT_MODE == "SAFE":
            if row["close"] < prev["low"] and row["rsi"] < 45:
                return "SELL SHORT", price, price * 0.990, price * 1.005
        elif CURRENT_MODE == "AGGRESSIVE":
            if row["close"] < prev["close"] and row["rsi"] < 49:
                return "SELL SHORT", price, price * 0.992, price * 1.004
        else:
            if row["close"] < prev["low"] and row["rsi"] < 47:
                return "SELL SHORT", price, price * 0.991, price * 1.005

    return None


# ---------- SCALP SIGNAL ----------
def scalp_signal(df):
    row = df.iloc[-1]
    prev = df.iloc[-2]

    price = float(row["close"])
    ema20_val = float(row["ema20"])
    r = float(row["rsi"])

    close_to_ema = abs(price - ema20_val) / price < 0.0012

    if not close_to_ema:
        return None

    if CURRENT_MODE == "SAFE":
        if 47 <= r <= 53:
            if row["close"] > prev["high"]:
                return "BUY LONG", price, price * 1.0025, price * 0.9985
            if row["close"] < prev["low"]:
                return "SELL SHORT", price, price * 0.9975, price * 1.0015

    elif CURRENT_MODE == "AGGRESSIVE":
        if 44 <= r <= 56:
            if row["close"] > prev["close"]:
                return "BUY LONG", price, price * 1.0035, price * 0.998
            if row["close"] < prev["close"]:
                return "SELL SHORT", price, price * 0.9965, price * 1.002

    else:
        if 45 <= r <= 55:
            if row["close"] > prev["close"]:
                return "BUY LONG", price, price * 1.003, price * 0.998
            if row["close"] < prev["close"]:
                return "SELL SHORT", price, price * 0.997, price * 1.002

    return None


# ---------- TRACK ----------
def track(df):
    global open_trade
    global vip_wins, vip_losses, scalp_wins, scalp_losses

    if not open_trade:
        return

    row = df.iloc[-1]
    high = float(row["high"])
    low = float(row["low"])

    hit_tp = False
    hit_sl = False

    if open_trade["side"] == "LONG":
        if low <= open_trade["sl"]:
            hit_sl = True
        elif high >= open_trade["tp"]:
            hit_tp = True
    else:
        if high >= open_trade["sl"]:
            hit_sl = True
        elif low <= open_trade["tp"]:
            hit_tp = True

    if hit_tp:
        if open_trade["signal_type"] == "VIP":
            vip_wins += 1
        else:
            scalp_wins += 1

        send(
            f"✅ TP HIT\n\n"
            f"Type: {open_trade['signal_type']}\n"
            f"Side: {open_trade['side']}\n"
            f"{all_stats()}"
        )
        open_trade = None

    elif hit_sl:
        if open_trade["signal_type"] == "VIP":
            vip_losses += 1
        else:
            scalp_losses += 1

        send(
            f"❌ SL HIT\n\n"
            f"Type: {open_trade['signal_type']}\n"
            f"Side: {open_trade['side']}\n"
            f"{all_stats()}"
        )
        open_trade = None


# ---------- START ----------
send("🚀 DXM BOT UPGRADED\nVIP + SCALP + COMMANDS + MODES ACTIVE")

while True:
    try:
        process_commands()

        now = datetime.now(LOCAL_TZ)
        reset_day(now)
        reset_scalp_hour(now)

        if BOT_PAUSED:
            time.sleep(LOOP_SLEEP)
            continue

        if not in_session(now):
            time.sleep(LOOP_SLEEP)
            continue

        limits = get_limits()
        df = get_entry()
        trend = get_trend()

        track(df)

        # VIP SIGNAL
        if not open_trade and signals_today < limits["max_vip"]:
            sig = vip_signal(df, trend)
            if sig:
                side, entry, tp, sl = sig

                send(
                    f"🚨 VIP SIGNAL\n\n"
                    f"BTCUSDT.P | {side}\n\n"
                    f"Entry: {round(entry, 2)}\n"
                    f"TP: {round(tp, 2)}\n"
                    f"SL: {round(sl, 2)}\n\n"
                    f"VIP WR: {wr(vip_wins, vip_losses)}% | W:{vip_wins} L:{vip_losses}\n"
                    f"Overall: {all_stats()}"
                )

                open_trade = {
                    "signal_type": "VIP",
                    "side": "LONG" if "BUY" in side else "SHORT",
                    "tp": tp,
                    "sl": sl,
                }

                signals_today += 1

        # SCALP SIGNAL
        if (
            not open_trade
            and scalps_today < limits["max_scalp"]
            and scalp_count_this_hour < limits["scalp_per_hour"]
        ):
            scalp = scalp_signal(df)
            if scalp:
                side, entry, tp, sl = scalp

                send(
                    f"⚡ SCALP SIGNAL\n\n"
                    f"BTCUSDT.P | {side}\n\n"
                    f"Entry: {round(entry, 2)}\n"
                    f"TP: {round(tp, 2)}\n"
                    f"SL: {round(sl, 2)}\n\n"
                    f"SCALP WR: {wr(scalp_wins, scalp_losses)}% | W:{scalp_wins} L:{scalp_losses}\n"
                    f"Overall: {all_stats()}"
                )

                open_trade = {
                    "signal_type": "SCALP",
                    "side": "LONG" if "BUY" in side else "SHORT",
                    "tp": tp,
                    "sl": sl,
                }

                scalps_today += 1
                scalp_count_this_hour += 1

    except Exception as e:
        safe_send_error(f"ERROR: {str(e)}")

    time.sleep(LOOP_SLEEP)