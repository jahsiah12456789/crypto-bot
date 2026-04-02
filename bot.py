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

CURRENT_MODE = "SAFE"   # SAFE / BALANCED / AGGRESSIVE
BOT_PAUSED = False

SAFE_MAX_VIP = 2
SAFE_MAX_SCALP = 0
SAFE_SCALP_PER_HOUR = 0

BAL_MAX_VIP = 3
BAL_MAX_SCALP = 1
BAL_SCALP_PER_HOUR = 1

AGG_MAX_VIP = 4
AGG_MAX_SCALP = 2
AGG_SCALP_PER_HOUR = 1

MIN_WAIT_SAFE = 90 * 60
MIN_WAIT_BAL = 60 * 60
MIN_WAIT_AGG = 45 * 60

LOSS_STREAK_PAUSE_CANDLES = 8
SINGLE_LOSS_PAUSE_CANDLES = 3

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

last_signal_ts = 0
cooldown_candles = 0
loss_streak = 0
last_signal_side = None


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


def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()


def rsi(series, n=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def atr(df, n=14):
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def wr(w, l):
    total = w + l
    return round((w / total) * 100, 1) if total else 0.0


def all_stats():
    total_w = vip_wins + scalp_wins
    total_l = vip_losses + scalp_losses
    return f"Overall WR: {wr(total_w, total_l)}% | W:{total_w} L:{total_l}"


def get_limits():
    if CURRENT_MODE == "SAFE":
        return {
            "max_vip": SAFE_MAX_VIP,
            "max_scalp": SAFE_MAX_SCALP,
            "scalp_per_hour": SAFE_SCALP_PER_HOUR,
            "min_wait": MIN_WAIT_SAFE,
        }
    if CURRENT_MODE == "AGGRESSIVE":
        return {
            "max_vip": AGG_MAX_VIP,
            "max_scalp": AGG_MAX_SCALP,
            "scalp_per_hour": AGG_SCALP_PER_HOUR,
            "min_wait": MIN_WAIT_AGG,
        }
    return {
        "max_vip": BAL_MAX_VIP,
        "max_scalp": BAL_MAX_SCALP,
        "scalp_per_hour": BAL_SCALP_PER_HOUR,
        "min_wait": MIN_WAIT_BAL,
    }


def get_stats_message():
    total_w = vip_wins + scalp_wins
    total_l = vip_losses + scalp_losses
    return (
        f"📊 DXM ELITE STATS\n\n"
        f"VIP -> WR: {wr(vip_wins, vip_losses)}% | W:{vip_wins} L:{vip_losses}\n"
        f"SCALP -> WR: {wr(scalp_wins, scalp_losses)}% | W:{scalp_wins} L:{scalp_losses}\n"
        f"OVERALL -> WR: {wr(total_w, total_l)}% | W:{total_w} L:{total_l}\n\n"
        f"VIP Today: {signals_today}\n"
        f"SCALP Today: {scalps_today}\n"
        f"Loss Streak: {loss_streak}\n"
        f"Cooldown Candles: {cooldown_candles}\n"
        f"Mode: {CURRENT_MODE}\n"
        f"Paused: {BOT_PAUSED}"
    )


def get_status_message():
    limits = get_limits()
    trade_text = "No open trade"
    if open_trade:
        trade_text = (
            f"Open Trade: {open_trade['signal_type']} {open_trade['side']}\n"
            f"Entry: {open_trade['entry']}\n"
            f"TP: {open_trade['tp']}\n"
            f"SL: {open_trade['sl']}"
        )
    return (
        f"🤖 DXM ELITE STATUS\n\n"
        f"Paused: {BOT_PAUSED}\n"
        f"Mode: {CURRENT_MODE}\n"
        f"VIP Used: {signals_today}/{limits['max_vip']}\n"
        f"SCALP Used: {scalps_today}/{limits['max_scalp']}\n"
        f"Scalp Hour Count: {scalp_count_this_hour}/{limits['scalp_per_hour']}\n"
        f"Cooldown Candles: {cooldown_candles}\n"
        f"Loss Streak: {loss_streak}\n\n"
        f"{trade_text}\n\n"
        f"{all_stats()}"
    )


def process_commands():
    global last_update_id, BOT_PAUSED, CURRENT_MODE
    global vip_wins, vip_losses, scalp_wins, scalp_losses
    global signals_today, scalps_today, open_trade
    global cooldown_candles, loss_streak

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
                    f"Scalp Max/Hour: {limits['scalp_per_hour']}\n"
                    f"Min Wait: {int(limits['min_wait']/60)} min"
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
                cooldown_candles = 0
                loss_streak = 0
                send("🔄 Stats reset. Trade cleared. Cooldown reset.")

    except Exception as e:
        safe_send_error(f"ERROR COMMANDS: {str(e)}")


def get_data(tf):
    r = requests.get(
        "https://api.kucoin.com/api/v1/market/candles",
        params={"type": tf, "symbol": SYMBOL},
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


def in_session(now):
    return START_HOUR <= now.hour < END_HOUR


def get_trend_df():
    df = get_data("1hour")
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["ema100"] = ema(df["close"], 100)
    df["atr"] = atr(df, 14)
    return df


def get_entry_df():
    df = get_data("15min")
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["rsi"] = rsi(df["close"], 14)
    df["atr"] = atr(df, 14)
    return df


def trend_state(trend_df):
    row = trend_df.iloc[-1]
    price = float(row["close"])
    ema20_val = float(row["ema20"])
    ema50_val = float(row["ema50"])
    ema100_val = float(row["ema100"])
    atr_val = float(row["atr"]) if pd.notna(row["atr"]) else price * 0.003

    bullish = price > ema20_val > ema50_val > ema100_val
    bearish = price < ema20_val < ema50_val < ema100_val

    spread1 = abs(ema20_val - ema50_val) / price
    spread2 = abs(ema50_val - ema100_val) / price
    atr_ratio = atr_val / price

    strong_trend = spread1 > 0.0015 and spread2 > 0.0015 and atr_ratio > 0.002

    if bullish and strong_trend:
        return "BULL_STRONG"
    if bearish and strong_trend:
        return "BEAR_STRONG"
    if bullish:
        return "BULL_WEAK"
    if bearish:
        return "BEAR_WEAK"
    return "SIDEWAYS"


def is_sideways(entry_df):
    recent = entry_df.iloc[-8:]
    if len(recent) < 8:
        return True

    price = float(recent.iloc[-1]["close"])
    recent_high = float(recent["high"].max())
    recent_low = float(recent["low"].min())
    rng = (recent_high - recent_low) / price

    ema20_val = float(recent.iloc[-1]["ema20"])
    ema50_val = float(recent.iloc[-1]["ema50"])
    ema_gap = abs(ema20_val - ema50_val) / price

    atr_mean = recent["atr"].mean()
    atr_ratio = float(atr_mean / price) if pd.notna(atr_mean) else 0.0

    return rng < 0.0045 or ema_gap < 0.0012 or atr_ratio < 0.0018


def strong_bull_breakout(entry_df):
    row = entry_df.iloc[-1]
    prev = entry_df.iloc[-2]
    recent_high = entry_df["high"].iloc[-10:-2].max()
    candle_range = row["high"] - row["low"]
    body = abs(row["close"] - row["open"])
    body_ratio = body / candle_range if candle_range > 0 else 0

    return (
        row["close"] > recent_high
        and row["close"] > row["open"]
        and row["rsi"] > 56
        and row["close"] > row["ema20"] > row["ema50"]
        and row["close"] > prev["high"]
        and body_ratio > 0.6
    )


def strong_bear_breakdown(entry_df):
    row = entry_df.iloc[-1]
    prev = entry_df.iloc[-2]
    recent_low = entry_df["low"].iloc[-10:-2].min()
    candle_range = row["high"] - row["low"]
    body = abs(row["close"] - row["open"])
    body_ratio = body / candle_range if candle_range > 0 else 0

    return (
        row["close"] < recent_low
        and row["close"] < row["open"]
        and row["rsi"] < 44
        and row["close"] < row["ema20"] < row["ema50"]
        and row["close"] < prev["low"]
        and body_ratio > 0.6
    )


def elite_vip_signal(entry_df, trend_label):
    row = entry_df.iloc[-1]
    price = float(row["close"])
    atr_val = float(row["atr"]) if pd.notna(row["atr"]) else price * 0.003

    if trend_label == "BULL_STRONG" and strong_bull_breakout(entry_df):
        sl = price - (atr_val * 1.2)
        tp = price + ((price - sl) * 1.8)
        return "BUY LONG", price, tp, sl, "Elite Bull Breakout"

    if trend_label == "BEAR_STRONG" and strong_bear_breakdown(entry_df):
        sl = price + (atr_val * 1.2)
        tp = price - ((sl - price) * 1.8)
        return "SELL SHORT", price, tp, sl, "Elite Bear Breakdown"

    return None


def scalp_signal(entry_df, trend_label):
    row = entry_df.iloc[-1]
    prev = entry_df.iloc[-2]
    price = float(row["close"])
    ema20_val = float(row["ema20"])
    r = float(row["rsi"])

    close_to_ema = abs(price - ema20_val) / price < 0.0009

    if CURRENT_MODE == "SAFE":
        return None

    if not close_to_ema:
        return None

    if trend_label in ["SIDEWAYS", "BULL_WEAK", "BEAR_WEAK"]:
        if CURRENT_MODE == "BALANCED":
            if 47 <= r <= 53:
                if row["close"] > prev["high"]:
                    return "BUY LONG", price, price * 1.0025, price * 0.9987, "Scalp Bounce"
                if row["close"] < prev["low"]:
                    return "SELL SHORT", price, price * 0.9975, price * 1.0013, "Scalp Reject"

        if CURRENT_MODE == "AGGRESSIVE":
            if 45 <= r <= 55:
                if row["close"] > prev["close"]:
                    return "BUY LONG", price, price * 1.003, price * 0.9985, "Scalp Bounce"
                if row["close"] < prev["close"]:
                    return "SELL SHORT", price, price * 0.997, price * 1.0015, "Scalp Reject"

    return None


def can_send_signal():
    limits = get_limits()
    enough_wait = (time.time() - last_signal_ts) >= limits["min_wait"]
    return enough_wait and cooldown_candles <= 0


def track(entry_df):
    global open_trade
    global vip_wins, vip_losses, scalp_wins, scalp_losses
    global cooldown_candles, loss_streak

    if not open_trade:
        return

    row = entry_df.iloc[-1]
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

        loss_streak = 0
        send(
            f"✅ TP HIT\n\n"
            f"Type: {open_trade['signal_type']}\n"
            f"Side: {open_trade['side']}\n"
            f"Setup: {open_trade['setup']}\n"
            f"{all_stats()}"
        )
        open_trade = None

    elif hit_sl:
        if open_trade["signal_type"] == "VIP":
            vip_losses += 1
        else:
            scalp_losses += 1

        loss_streak += 1
        cooldown_candles = LOSS_STREAK_PAUSE_CANDLES if loss_streak >= 2 else SINGLE_LOSS_PAUSE_CANDLES

        send(
            f"❌ SL HIT\n\n"
            f"Type: {open_trade['signal_type']}\n"
            f"Side: {open_trade['side']}\n"
            f"Setup: {open_trade['setup']}\n"
            f"Loss Streak: {loss_streak}\n"
            f"Cooldown Candles: {cooldown_candles}\n"
            f"{all_stats()}"
        )
        open_trade = None


send("🚀 DXM ELITE BOT ACTIVE")

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

        trend_df = get_trend_df()
        entry_df = get_entry_df()

        track(entry_df)

        if cooldown_candles > 0 and open_trade is None:
            cooldown_candles -= 1
            time.sleep(LOOP_SLEEP)
            continue

        trend_label = trend_state(trend_df)
        sideways = is_sideways(entry_df)
        limits = get_limits()

        if not open_trade and can_send_signal():
            # VIP
            if signals_today < limits["max_vip"] and not sideways:
                sig = elite_vip_signal(entry_df, trend_label)
                if sig:
                    side, entry, tp, sl, setup = sig

                    # block same-side instant re-entry after weak market
                    if last_signal_side == side:
                        pass

                    send(
                        f"🚨 ELITE VIP SIGNAL\n\n"
                        f"BTCUSDT.P | {side}\n\n"
                        f"Entry: {round(entry, 2)}\n"
                        f"TP: {round(tp, 2)}\n"
                        f"SL: {round(sl, 2)}\n\n"
                        f"Setup: {setup}\n"
                        f"Mode: {CURRENT_MODE}\n"
                        f"VIP WR: {wr(vip_wins, vip_losses)}% | W:{vip_wins} L:{vip_losses}\n"
                        f"{all_stats()}"
                    )

                    open_trade = {
                        "signal_type": "VIP",
                        "side": "LONG" if "BUY" in side else "SHORT",
                        "entry": round(entry, 2),
                        "tp": tp,
                        "sl": sl,
                        "setup": setup,
                    }
                    signals_today += 1
                    last_signal_ts = time.time()
                    last_signal_side = side

            # SCALP
            if (
                not open_trade
                and scalps_today < limits["max_scalp"]
                and scalp_count_this_hour < limits["scalp_per_hour"]
            ):
                scalp = scalp_signal(entry_df, trend_label)
                if scalp:
                    side, entry, tp, sl, setup = scalp

                    send(
                        f"⚡ ELITE SCALP SIGNAL\n\n"
                        f"BTCUSDT.P | {side}\n\n"
                        f"Entry: {round(entry, 2)}\n"
                        f"TP: {round(tp, 2)}\n"
                        f"SL: {round(sl, 2)}\n\n"
                        f"Setup: {setup}\n"
                        f"Mode: {CURRENT_MODE}\n"
                        f"SCALP WR: {wr(scalp_wins, scalp_losses)}% | W:{scalp_wins} L:{scalp_losses}\n"
                        f"{all_stats()}"
                    )

                    open_trade = {
                        "signal_type": "SCALP",
                        "side": "LONG" if "BUY" in side else "SHORT",
                        "entry": round(entry, 2),
                        "tp": tp,
                        "sl": sl,
                        "setup": setup,
                    }
                    scalps_today += 1
                    scalp_count_this_hour += 1
                    last_signal_ts = time.time()
                    last_signal_side = side

    except Exception as e:
        safe_send_error(f"ERROR: {str(e)}")

    time.sleep(LOOP_SLEEP)