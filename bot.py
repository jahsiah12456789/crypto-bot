import os
import time
import requests
import pandas as pd
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TOKEN = os.environ["TOKEN"]
CHAT_ID = str(os.environ["CHAT_ID"])

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "AVAXUSDT"]

LOW_TF = "15m"
CHECK_SIGNALS_EVERY_SECONDS = 5
COMMAND_POLL_TIMEOUT = 1

MAX_SIGNALS_PER_DAY = 6
MAX_BONUS_SIGNALS_PER_DAY = 3

TRADES_FILE = "trades.csv"

LOCAL_TZ = ZoneInfo("America/Toronto")
SCHEDULED_TIMES = [(10, 30), (15, 30), (21, 0)]
FORCED_SIGNAL_SYMBOL = "BTCUSDT"

START_HOUR = 9
END_HOUR = 22  # 10 PM Toronto time

last_signal_by_symbol = {}
signals_today = 0
bonus_signals_today = 0
last_reset_day = None
open_trades = {}
last_update_id = 0
last_no_signal_day = None
last_scheduled_key = None
last_5h_update = None
last_signal_scan_ts = 0.0


def send(msg, chat_id=None):
    target_chat = str(chat_id) if chat_id is not None else CHAT_ID
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": target_chat, "text": msg},
            timeout=20,
        )
    except Exception as e:
        print("send error:", e)


def get_updates():
    global last_update_id
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
        r = requests.get(
            url,
            params={"offset": last_update_id + 1, "timeout": COMMAND_POLL_TIMEOUT},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json().get("result", [])
        if data:
            last_update_id = data[-1]["update_id"]
        return data
    except Exception as e:
        print("get_updates error:", e)
        return []


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


def add_indicators(df):
    df = df.copy()
    df["ema9"] = ema(df["close"], 9)
    df["ema21"] = ema(df["close"], 21)
    df["ema50"] = ema(df["close"], 50)
    df["rsi14"] = rsi(df["close"], 14)
    df["atr14"] = atr(df, 14)
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
        symbol, side, entry_time, entry_price, sl, tp,
        "", "", "OPEN", "", "", signal_type
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
        risk = entry - sl
        r_multiple = (exit_price - entry) / risk if risk != 0 else 0
    else:
        pnl_pct = ((entry - exit_price) / entry) * 100
        risk = sl - entry
        r_multiple = (entry - exit_price) / risk if risk != 0 else 0

    df.loc[idx, "exit_time"] = exit_time
    df.loc[idx, "exit_price"] = round(exit_price, 6)
    df.loc[idx, "status"] = status
    df.loc[idx, "pnl_pct"] = round(pnl_pct, 3)
    df.loc[idx, "r_multiple"] = round(r_multiple, 3)
    df.to_csv(TRADES_FILE, index=False)


def summarize_performance():
    try:
        if not os.path.exists(TRADES_FILE):
            return (
                "📊 VIP STATS\n"
                "━━━━━━━━━━━━━━\n"
                "Closed Trades: 0\n"
                "Open Trades: 0\n"
                "Win Rate: 0%\n"
                "Total PnL %: 0%\n"
                "Total R: 0"
            )

        df = pd.read_csv(TRADES_FILE)

        if df.empty:
            return (
                "📊 VIP STATS\n"
                "━━━━━━━━━━━━━━\n"
                "Closed Trades: 0\n"
                "Open Trades: 0\n"
                "Win Rate: 0%\n"
                "Total PnL %: 0%\n"
                "Total R: 0"
            )

        if "status" not in df.columns:
            return "📊 VIP STATS\n━━━━━━━━━━━━━━\nStats file format error."

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

        wins = int((closed["status"] == "TP").sum())
        losses = int((closed["status"] == "SL").sum())
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
    except Exception as e:
        return f"📊 VIP STATS ERROR\n{str(e)}"


def open_trades_text():
    if not open_trades:
        return "No open trades."

    lines = ["📂 OPEN TRADES"]
    for symbol, t in open_trades.items():
        lines.append(
            f"{symbol} {t['side']} | {t['signal_type']} | "
            f"Entry: {round(t['entry'], 4)} | "
            f"SL: {round(t['sl'], 4)} | TP: {round(t['tp'], 4)}"
        )
    return "\n".join(lines)


def scheduled_update_text():
    return f"📡 VIP MARKET UPDATE\n\n{summarize_performance()}\n\n{open_trades_text()}"


def reset_daily_counter():
    global signals_today, bonus_signals_today, last_reset_day
    today = datetime.now(timezone.utc).date()
    if last_reset_day != today:
        signals_today = 0
        bonus_signals_today = 0
        last_reset_day = today


def format_signal_message(symbol, side, price, sl, tp, rr, rsi_value, candle_time, signal_type):
    emoji = "🟢" if side == "LONG" else "🔴"
    action = "BUY" if side == "LONG" else "SELL"

    if signal_type == "BONUS":
        header = "🎁 BONUS SIGNAL"
        risk_note = "⚠️ Slightly riskier setup. Manage risk tightly."
    elif signal_type == "FORCED":
        header = "⚠️ FORCED SIGNAL"
        risk_note = "⚠️ Lower confidence. Use smaller size."
    else:
        header = "🚨 VIP SIGNAL ALERT 🚨"
        risk_note = "⚠️ Manage risk properly."

    return (
        f"{header}\n\n"
        f"{emoji} {symbol} — {action} {side}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📍 Entry: {round(price, 4)}\n"
        f"🛑 Stop Loss: {round(sl, 4)}\n"
        f"🎯 Take Profit: {round(tp, 4)}\n"
        f"📊 Risk/Reward: {rr}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📈 RSI: {round(float(rsi_value), 2)}\n"
        f"🕒 Time: {candle_time}\n"
        f"📦 Daily Signals: {signals_today + 1}/{MAX_SIGNALS_PER_DAY}\n\n"
        f"{risk_note}"
    )


def build_signal(symbol, bonus=False):
    df = add_indicators(get_data(symbol, LOW_TF))
    row = df.iloc[-2]

    price = float(row["close"])
    candle_time = str(row["close_time"])
    atr_val = float(row["atr14"])

    if row["ema9"] >= row["ema21"]:
        side = "LONG"
        if bonus:
            sl = price - atr_val * 0.7
            tp = price + atr_val * 1.0
            signal_type = "BONUS"
        else:
            sl = price - atr_val * 0.8
            tp = price + atr_val * 1.2
            signal_type = "MAIN"
    else:
        side = "SHORT"
        if bonus:
            sl = price + atr_val * 0.7
            tp = price - atr_val * 1.0
            signal_type = "BONUS"
        else:
            sl = price + atr_val * 0.8
            tp = price - atr_val * 1.2
            signal_type = "MAIN"

    rr = round(abs(tp - price) / abs(price - sl), 2) if price != sl else 0
    signal_key = f"{side}-{candle_time}-{signal_type}"

    msg = format_signal_message(
        symbol=symbol,
        side=side,
        price=price,
        sl=sl,
        tp=tp,
        rr=rr,
        rsi_value=row["rsi14"],
        candle_time=candle_time,
        signal_type=signal_type,
    )

    return signal_key, msg, side, candle_time, price, sl, tp, signal_type


def build_forced_signal(symbol):
    df = add_indicators(get_data(symbol, LOW_TF))
    row = df.iloc[-2]

    price = float(row["close"])
    atr_val = float(row["atr14"])
    candle_time = str(row["close_time"])

    if row["ema9"] >= row["ema21"]:
        side = "LONG"
        sl = price - atr_val * 0.9
        tp = price + atr_val * 1.4
    else:
        side = "SHORT"
        sl = price + atr_val * 0.9
        tp = price - atr_val * 1.4

    rr = round(abs(tp - price) / abs(price - sl), 2) if price != sl else 0

    msg = format_signal_message(
        symbol=symbol,
        side=side,
        price=price,
        sl=sl,
        tp=tp,
        rr=rr,
        rsi_value=row["rsi14"],
        candle_time=candle_time,
        signal_type="FORCED",
    )

    return f"{side}-{candle_time}-FORCED", msg, side, candle_time, price, sl, tp, "FORCED"


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
                        f"🔴 {symbol} LONG stopped out\n"
                        f"🛑 Exit: {round(trade['sl'], 4)}"
                    )
                    to_remove.append(symbol)
                elif high >= trade["tp"]:
                    close_trade(symbol, str(row["close_time"]), trade["tp"], "TP")
                    send(
                        f"✅ VIP TRADE CLOSED\n\n"
                        f"🟢 {symbol} LONG take profit hit\n"
                        f"🎯 Exit: {round(trade['tp'], 4)}"
                    )
                    to_remove.append(symbol)
            else:
                if high >= trade["sl"]:
                    close_trade(symbol, str(row["close_time"]), trade["sl"], "SL")
                    send(
                        f"❌ VIP TRADE CLOSED\n\n"
                        f"🔴 {symbol} SHORT stopped out\n"
                        f"🛑 Exit: {round(trade['sl'], 4)}"
                    )
                    to_remove.append(symbol)
                elif low <= trade["tp"]:
                    close_trade(symbol, str(row["close_time"]), trade["tp"], "TP")
                    send(
                        f"✅ VIP TRADE CLOSED\n\n"
                        f"🟢 {symbol} SHORT take profit hit\n"
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

        if not chat_id:
            continue

        if text == "/start":
            send("🚀 VIP signal bot is now live.\nCommands:\n/stats\n/opentrades\n/help", chat_id)
        elif text == "/help":
            send("Commands:\n/stats - performance summary\n/opentrades - show open trades", chat_id)
        elif text == "/stats":
            send(summarize_performance(), chat_id)
        elif text == "/opentrades":
            send(open_trades_text(), chat_id)


def run_signal_engine(now, local_now):
    global signals_today, bonus_signals_today, last_no_signal_day, last_scheduled_key, last_5h_update, last_signal_scan_ts

    reset_daily_counter()
    update_open_trades()

    current_ts = time.time()
    if current_ts - last_signal_scan_ts >= CHECK_SIGNALS_EVERY_SECONDS:
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
                except Exception as e:
                    print(symbol, "main signal error:", e)

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
                except Exception as e:
                    print(symbol, "bonus signal error:", e)

        last_signal_scan_ts = current_ts

    scheduled_key = local_now.strftime("%Y-%m-%d-%H-%M")
    if (local_now.hour, local_now.minute) in SCHEDULED_TIMES and last_scheduled_key != scheduled_key:
        send(scheduled_update_text())

        if signals_today == 0 and FORCED_SIGNAL_SYMBOL not in open_trades:
            try:
                result = build_forced_signal(FORCED_SIGNAL_SYMBOL)
                signal_key, msg, side, candle_time, price, sl, tp, signal_type = result
                forced_key = f"{FORCED_SIGNAL_SYMBOL}-FORCED-{candle_time}"
                if last_signal_by_symbol.get(FORCED_SIGNAL_SYMBOL + "_forced") != forced_key:
                    send(msg)
                    last_signal_by_symbol[FORCED_SIGNAL_SYMBOL + "_forced"] = forced_key
                    open_trades[FORCED_SIGNAL_SYMBOL] = {
                        "side": side,
                        "entry": price,
                        "sl": sl,
                        "tp": tp,
                        "entry_time": candle_time,
                        "signal_type": signal_type,
                    }
                    log_new_trade(FORCED_SIGNAL_SYMBOL, side, candle_time, price, sl, tp, signal_type)
                    signals_today += 1
            except Exception as e:
                print("forced signal error:", e)

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


def main():
    ensure_trades_file()
    send("🚀 VIP signal bot is now live.")

    while True:
        try:
            handle_commands()

            now = datetime.now(timezone.utc)
            local_now = now.astimezone(LOCAL_TZ)

            if START_HOUR <= local_now.hour < END_HOUR:
                run_signal_engine(now, local_now)

        except Exception as e:
            print("Main loop error:", e)

        time.sleep(1)


if __name__ == "__main__":
    main()