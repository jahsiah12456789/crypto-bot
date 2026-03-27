import os
import time
import requests
import pandas as pd
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# More aggressive coin list
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "AVAXUSDT"]

LOW_TF = "15m"
MID_TF = "30m"
HIGH_TF = "1h"
CHECK_EVERY_SECONDS = 30
MAX_SIGNALS_PER_DAY = 6
MAX_BONUS_SIGNALS_PER_DAY = 3

ATR_SL_MULTIPLIER = 1.0
ATR_TP_MULTIPLIER = 1.8
MIN_ADX = 6
BONUS_MIN_ADX = 4

TRADES_FILE = "trades.csv"

LOCAL_TZ = ZoneInfo("America/Toronto")
SCHEDULED_TIMES = [(10, 30), (15, 30), (21, 0)]

last_signal_by_symbol = {}
signals_today = 0
bonus_signals_today = 0
last_reset_day = None
open_trades = {}
last_update_id = 0
last_no_signal_day = None
last_scheduled_key = None
last_5h_update = None


def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg},
        timeout=20,
    )


def get_updates():
    global last_update_id
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    r = requests.get(
        url,
        params={"offset": last_update_id + 1, "timeout": 10},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json().get("result", [])
    if data:
        last_update_id = data[-1]["update_id"]
    return data


def get_data(symbol, interval, limit=300):
    r = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=20,
    )
    r.raise_for_status()
    df = pd.DataFrame(
        r.json(),
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "tbav", "tqav", "ignore"
        ],
    )
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
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


def adx(df, n=14):
    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift()).abs()
    tr3 = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr_smoothed = tr.ewm(alpha=1 / n, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_smoothed)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_smoothed)

    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).abs()) * 100
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def add_indicators(df):
    df = df.copy()
    df["ema9"] = ema(df["close"], 9)
    df["ema21"] = ema(df["close"], 21)
    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    df["rsi14"] = rsi(df["close"], 14)
    df["atr14"] = atr(df, 14)
    df["adx14"] = adx(df, 14)
    return df


def ensure_trades_file():
    if not os.path.exists(TRADES_FILE):
        pd.DataFrame(
            columns=[
                "symbol", "side", "entry_time", "entry_price", "sl", "tp",
                "exit_time", "exit_price", "status", "pnl_pct", "r_multiple",
                "signal_type"
            ]
        ).to_csv(TRADES_FILE, index=False)


def log_new_trade(symbol, side, entry_time, entry_price, sl, tp, signal_type):
    df = pd.read_csv(TRADES_FILE)
    df.loc[len(df)] = [
        symbol, side, entry_time, entry_price, sl, tp, "", "", "OPEN", "", "", signal_type
    ]
    df.to_csv(TRADES_FILE, index=False)


def close_trade(symbol, exit_time, exit_price, status):
    df = pd.read_csv(TRADES_FILE)
    open_rows = df[(df["symbol"] == symbol) & (df["status"] == "OPEN")]
    if open_rows.empty:
        return

    idx = open_rows.index[-1]
    side = df.loc[idx, "side"]
    entry = float(df.loc[idx, "entry_price"])
    sl = float(df.loc[idx, "sl"])

    if side == "LONG":
        pnl_pct = ((exit_price - entry) / entry) * 100
        r_multiple = (exit_price - entry) / (entry - sl) if entry != sl else 0
    else:
        pnl_pct = ((entry - exit_price) / entry) * 100
        r_multiple = (entry - exit_price) / (sl - entry) if entry != sl else 0

    df.loc[idx, "exit_time"] = exit_time
    df.loc[idx, "exit_price"] = round(exit_price, 6)
    df.loc[idx, "status"] = status
    df.loc[idx, "pnl_pct"] = round(pnl_pct, 3)
    df.loc[idx, "r_multiple"] = round(r_multiple, 3)
    df.to_csv(TRADES_FILE, index=False)


def summarize_performance():
    df = pd.read_csv(TRADES_FILE)
    closed = df[df["status"].isin(["TP", "SL"])]
    open_count = len(df[df["status"] == "OPEN"])

    if closed.empty:
        return (
            "📊 VIP STATS\n"
            "━━━━━━━━━━━━━━\n"
            f"Closed Trades: 0\n"
            f"Open Trades: {open_count}\n"
            "Win Rate: 0%\n"
            "Total PnL %: 0%\n"
            "Total R: 0"
        )

    wins = (closed["status"] == "TP").sum()
    losses = (closed["status"] == "SL").sum()
    total = len(closed)
    win_rate = round((wins / total) * 100, 2) if total else 0
    total_r = round(pd.to_numeric(closed["r_multiple"], errors="coerce").fillna(0).sum(), 2)
    total_pnl = round(pd.to_numeric(closed["pnl_pct"], errors="coerce").fillna(0).sum(), 2)

    return (
        "📊 VIP STATS\n"
        "━━━━━━━━━━━━━━\n"
        f"Closed Trades: {total}\n"
        f"Open Trades: {open_count}\n"
        f"Wins: {wins}\n"
        f"Losses: {losses}\n"
        f"Win Rate: {win_rate}%\n"
        f"Total PnL %: {total_pnl}%\n"
        f"Total R: {total_r}"
    )


def open_trades_text():
    if not open_trades:
        return "No open trades."

    lines = ["📂 OPEN TRADES"]
    for symbol, t in open_trades.items():
        lines.append(
            f"{symbol} {t['side']} | {t['signal_type']} | Entry: {round(t['entry'], 4)} | "
            f"SL: {round(t['sl'], 4)} | TP: {round(t['tp'], 4)}"
        )
    return "\n".join(lines)


def scheduled_update_text():
    stats = summarize_performance()
    opens = open_trades_text()
    return f"📡 VIP MARKET UPDATE\n\n{stats}\n\n{opens}"


def reset_daily_counter():
    global signals_today, bonus_signals_today, last_reset_day
    today = datetime.now(timezone.utc).date()
    if last_reset_day != today:
        signals_today = 0
        bonus_signals_today = 0
        last_reset_day = today


def build_signal(symbol, bonus=False):
    low_df = add_indicators(get_data(symbol, LOW_TF))
    mid_df = add_indicators(get_data(symbol, MID_TF))
    high_df = add_indicators(get_data(symbol, HIGH_TF))

    row = low_df.iloc[-2]
    mid = mid_df.iloc[-2]
    high = high_df.iloc[-2]

    price = float(row["close"])
    candle_time = str(row["close_time"])
    atr_val = float(row["atr14"])

    bullish_cross = row["ema9"] > row["ema21"]
    bearish_cross = row["ema9"] < row["ema21"]

    mid_bull = mid["ema9"] > mid["ema21"]
    mid_bear = mid["ema9"] < mid["ema21"]

    high_bull = high["ema9"] > high["ema21"]
    high_bear = high["ema9"] < high["ema21"]

    if bonus:
        strong_trend = row["adx14"] >= BONUS_MIN_ADX
        not_overextended_long = 30 <= row["rsi14"] <= 90
        not_overextended_short = 10 <= row["rsi14"] <= 70
        volatility_ok = (atr_val / price) >= 0.0002
    else:
        strong_trend = row["adx14"] >= MIN_ADX
        not_overextended_long = 35 <= row["rsi14"] <= 85
        not_overextended_short = 15 <= row["rsi14"] <= 65
        volatility_ok = (atr_val / price) >= 0.0003

    long_cond = (
        bullish_cross
        and mid_bull
        and high_bull
        and strong_trend
        and not_overextended_long
        and volatility_ok
    )

    short_cond = (
        bearish_cross
        and mid_bear
        and high_bear
        and strong_trend
        and not_overextended_short
        and volatility_ok
    )

    if long_cond:
        side = "LONG"
        sl = price - atr_val * ATR_SL_MULTIPLIER
        tp = price + atr_val * ATR_TP_MULTIPLIER
    elif short_cond:
        side = "SHORT"
        sl = price + atr_val * ATR_SL_MULTIPLIER
        tp = price - atr_val * ATR_TP_MULTIPLIER
    else:
        return None

    rr = round(abs(tp - price) / abs(price - sl), 2) if price != sl else 0
    signal_key = f"{side}-{candle_time}-{'BONUS' if bonus else 'MAIN'}"

    emoji = "🟢" if side == "LONG" else "🔴"
    action = "BUY" if side == "LONG" else "SELL"
    header = "🎁 BONUS SIGNAL" if bonus else "🚨 VIP SIGNAL ALERT 🚨"
    risk_note = "⚠️ Slightly riskier setup. Manage risk tightly." if bonus else "⚠️ Manage risk properly."
    signal_type = "BONUS" if bonus else "MAIN"

    msg = (
        f"{header}\n\n"
        f"{emoji} {symbol} — {action} {side}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📍 Entry: {round(price, 4)}\n"
        f"🛑 Stop Loss: {round(sl, 4)}\n"
        f"🎯 Take Profit: {round(tp, 4)}\n"
        f"📊 Risk/Reward: {rr}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📈 RSI: {round(float(row['rsi14']), 2)}\n"
        f"🔥 ADX: {round(float(row['adx14']), 2)}\n"
        f"🕒 Time: {candle_time}\n"
        f"📦 Daily Signals: {signals_today + 1}/{MAX_SIGNALS_PER_DAY}\n\n"
        f"{risk_note}"
    )
    return signal_key, msg, side, candle_time, price, sl, tp, signal_type


def update_open_trades():
    to_remove = []
    for symbol, trade in list(open_trades.items()):
        try:
            df = get_data(symbol, LOW_TF, 5)
            row = df.iloc[-2]
            high = float(row["high"])
            low = float(row["low"])

            if trade["side"] == "LONG":
                if low <= trade["sl"]:
                    close_trade(symbol, str(row["close_time"]), trade["sl"], "SL")
                    send(
                        f"❌ VIP TRADE CLOSED\n\n"
                        f"🔴 {symbol} {trade['side']} stopped out\n"
                        f"🛑 Exit: {round(trade['sl'], 4)}"
                    )
                    to_remove.append(symbol)
                elif high >= trade["tp"]:
                    close_trade(symbol, str(row["close_time"]), trade["tp"], "TP")
                    send(
                        f"✅ VIP TRADE CLOSED\n\n"
                        f"🟢 {symbol} {trade['side']} take profit hit\n"
                        f"🎯 Exit: {round(trade['tp'], 4)}"
                    )
                    to_remove.append(symbol)
            else:
                if high >= trade["sl"]:
                    close_trade(symbol, str(row["close_time"]), trade["sl"], "SL")
                    send(
                        f"❌ VIP TRADE CLOSED\n\n"
                        f"🔴 {symbol} {trade['side']} stopped out\n"
                        f"🛑 Exit: {round(trade['sl'], 4)}"
                    )
                    to_remove.append(symbol)
                elif low <= trade["tp"]:
                    close_trade(symbol, str(row["close_time"]), trade["tp"], "TP")
                    send(
                        f"✅ VIP TRADE CLOSED\n\n"
                        f"🟢 {symbol} {trade['side']} take profit hit\n"
                        f"🎯 Exit: {round(trade['tp'], 4)}"
                    )
                    to_remove.append(symbol)
        except Exception as e:
            print("Trade update error:", symbol, e)

    for symbol in to_remove:
        open_trades.pop(symbol, None)


def handle_commands():
    updates = get_updates()
    for update in updates:
        message = update.get("message", {})
        text = message.get("text", "").strip().lower()
        chat_id = str(message.get("chat", {}).get("id", ""))

        if chat_id != str(CHAT_ID):
            continue

        if text == "/start":
            send("🚀 VIP signal bot is now live.\nCommands:\n/stats\n/opentrades\n/help")
        elif text == "/help":
            send("Commands:\n/stats - performance summary\n/opentrades - show open trades")
        elif text == "/stats":
            send(summarize_performance())
        elif text == "/opentrades":
            send(open_trades_text())


def main():
    global signals_today, bonus_signals_today, last_no_signal_day, last_scheduled_key, last_5h_update

    ensure_trades_file()
    send("🚀 VIP signal bot is now live.")

    while True:
        try:
            reset_daily_counter()
            handle_commands()
            update_open_trades()

            if signals_today < MAX_SIGNALS_PER_DAY:
                for symbol in SYMBOLS:
                    if signals_today >= MAX_SIGNALS_PER_DAY:
                        break

                    if symbol in open_trades:
                        continue

                    try:
                        result = build_signal(symbol, bonus=False)
                        if result:
                            signal_key, msg, side, candle_time, price, sl, tp, signal_type = result
                            if last_signal_by_symbol.get(symbol) != signal_key:
                                send(msg)
                                last_signal_by_symbol[symbol] = signal_key
                                open_trades[symbol] = {
                                    "side": side,
                                    "entry": price,
                                    "sl": sl,
                                    "tp": tp,
                                    "entry_time": candle_time,
                                    "signal_type": signal_type,
                                }
                                log_new_trade(symbol, side, candle_time, price, sl, tp, signal_type)
                                signals_today += 1
                    except Exception as symbol_error:
                        print(symbol, "main signal error:", symbol_error)

            if bonus_signals_today < MAX_BONUS_SIGNALS_PER_DAY:
                for symbol in SYMBOLS:
                    if bonus_signals_today >= MAX_BONUS_SIGNALS_PER_DAY:
                        break

                    if symbol in open_trades:
                        continue

                    try:
                        result = build_signal(symbol, bonus=True)
                        if result:
                            signal_key, msg, side, candle_time, price, sl, tp, signal_type = result
                            bonus_key = f"{symbol}-BONUS-{candle_time}"
                            if last_signal_by_symbol.get(symbol + "_bonus") != bonus_key:
                                send(msg)
                                last_signal_by_symbol[symbol + "_bonus"] = bonus_key
                                open_trades[symbol] = {
                                    "side": side,
                                    "entry": price,
                                    "sl": sl,
                                    "tp": tp,
                                    "entry_time": candle_time,
                                    "signal_type": signal_type,
                                }
                                log_new_trade(symbol, side, candle_time, price, sl, tp, signal_type)
                                bonus_signals_today += 1
                    except Exception as symbol_error:
                        print(symbol, "bonus signal error:", symbol_error)

            now = datetime.now(timezone.utc)

            local_now = now.astimezone(LOCAL_TZ)
            scheduled_key = local_now.strftime("%Y-%m-%d-%H-%M")
            if (local_now.hour, local_now.minute) in SCHEDULED_TIMES:
                if last_scheduled_key != scheduled_key:
                    send(scheduled_update_text())
                    last_scheduled_key = scheduled_key

            five_hour_bucket = now.strftime("%Y-%m-%d-") + str(now.hour // 5)
            if now.minute == 0 and last_5h_update != five_hour_bucket:
                send(summarize_performance())
                last_5h_update = five_hour_bucket

            today = now.date()
            if now.hour == 23 and now.minute >= 55:
                if signals_today == 0 and bonus_signals_today == 0 and last_no_signal_day != today:
                    send("📭 No signals today — market not clean")
                    last_no_signal_day = today

        except Exception as e:
            print("Main loop error:", e)

        time.sleep(CHECK_EVERY_SECONDS)


if __name__ == "__main__":
    main()