import os
import time
import requests
import pandas as pd
from datetime import datetime, timezone

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
LOW_TF = "15m"
MID_TF = "1h"
HIGH_TF = "4h"
CHECK_EVERY_SECONDS = 60
MAX_SIGNALS_PER_DAY = 3

ATR_SL_MULTIPLIER = 1.3
ATR_TP_MULTIPLIER = 2.6

TRADES_FILE = "trades.csv"

last_signal_by_symbol = {}
signals_today = 0
last_reset_day = None
open_trades = {}

def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg},
        timeout=20,
    )

def get_data(symbol, interval, limit=300):
    r = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=20,
    )
    r.raise_for_status()
    df = pd.DataFrame(r.json(), columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "trades", "tbav", "tqav", "ignore"
    ])
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
    avg_gain = gain.ewm(alpha=1/n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/n, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(df, n=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def adx(df, n=14):
    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift()).abs()
    tr3 = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr_smoothed = tr.ewm(alpha=1/n, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/n, adjust=False).mean() / atr_smoothed)
    minus_di = 100 * (minus_dm.ewm(alpha=1/n, adjust=False).mean() / atr_smoothed)

    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).abs()) * 100
    return dx.ewm(alpha=1/n, adjust=False).mean()

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
        pd.DataFrame(columns=[
            "symbol", "side", "entry_time", "entry_price",
            "sl", "tp", "exit_time", "exit_price", "status",
            "pnl_pct", "r_multiple"
        ]).to_csv(TRADES_FILE, index=False)

def log_new_trade(symbol, side, entry_time, entry_price, sl, tp):
    df = pd.read_csv(TRADES_FILE)
    df.loc[len(df)] = [
        symbol, side, entry_time, entry_price,
        sl, tp, "", "", "OPEN", "", ""
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
    if closed.empty:
        return "No closed trades yet."

    wins = (closed["status"] == "TP").sum()
    losses = (closed["status"] == "SL").sum()
    total = len(closed)
    win_rate = round((wins / total) * 100, 2) if total else 0
    total_r = round(pd.to_numeric(closed["r_multiple"], errors="coerce").fillna(0).sum(), 2)
    total_pnl = round(pd.to_numeric(closed["pnl_pct"], errors="coerce").fillna(0).sum(), 2)

    return (
        f"📊 Performance Update\n"
        f"Closed Trades: {total}\n"
        f"Wins: {wins}\n"
        f"Losses: {losses}\n"
        f"Win Rate: {win_rate}%\n"
        f"Total PnL %: {total_pnl}%\n"
        f"Total R: {total_r}"
    )

def reset_daily_counter():
    global signals_today, last_reset_day
    today = datetime.now(timezone.utc).date()
    if last_reset_day != today:
        signals_today = 0
        last_reset_day = today

def build_signal(symbol):
    low_df = add_indicators(get_data(symbol, LOW_TF))
    mid_df = add_indicators(get_data(symbol, MID_TF))
    high_df = add_indicators(get_data(symbol, HIGH_TF))

    row = low_df.iloc[-2]
    prev = low_df.iloc[-3]
    mid = mid_df.iloc[-2]
    high = high_df.iloc[-2]

    price = float(row["close"])
    candle_time = str(row["close_time"])
    atr_val = float(row["atr14"])

    bullish_cross = prev["ema9"] <= prev["ema21"] and row["ema9"] > row["ema21"]
    bearish_cross = prev["ema9"] >= prev["ema21"] and row["ema9"] < row["ema21"]

    mid_bull = mid["close"] > mid["ema50"] > mid["ema200"]
    mid_bear = mid["close"] < mid["ema50"] < mid["ema200"]

    high_bull = high["close"] > high["ema50"] > high["ema200"]
    high_bear = high["close"] < high["ema50"] < high["ema200"]

    strong_trend = row["adx14"] >= 22
    not_overextended_long = 53 <= row["rsi14"] <= 68
    not_overextended_short = 32 <= row["rsi14"] <= 47

    volatility_ok = (atr_val / price) >= 0.002
    huge_candle = abs(row["close"] - row["open"]) > atr_val * 1.8

    long_cond = (
        bullish_cross and mid_bull and high_bull and strong_trend and
        not_overextended_long and row["close"] > row["ema21"] and
        volatility_ok and not huge_candle
    )
    short_cond = (
        bearish_cross and mid_bear and high_bear and strong_trend and
        not_overextended_short and row["close"] < row["ema21"] and
        volatility_ok and not huge_candle
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
    signal_key = f"{side}-{candle_time}"

    msg = (
        f"🎯 {symbol} {side}\n"
        f"Time: {candle_time}\n"
        f"Entry: {round(price, 4)}\n"
        f"SL: {round(sl, 4)}\n"
        f"TP: {round(tp, 4)}\n"
        f"RR: {rr}\n"
        f"RSI: {round(float(row['rsi14']), 2)}\n"
        f"ADX: {round(float(row['adx14']), 2)}"
    )
    return signal_key, msg, side, candle_time, price, sl, tp

def update_open_trades():
    to_remove = []
    for symbol, trade in list(open_trades.items()):
        try:
            df = get_data(symbol, LOW_TF, 5)
            row = df.iloc[-2]
            high = float(row["high"])
            low = float(row["low"])
            close_time = str(row["close_time"])

            if trade["side"] == "LONG":
                if low <= trade["sl"]:
                    close_trade(symbol, close_time, trade["sl"], "SL")
                    send(f"🛑 {symbol} LONG stopped out\nExit: {round(trade['sl'], 4)}")
                    to_remove.append(symbol)
                elif high >= trade["tp"]:
                    close_trade(symbol, close_time, trade["tp"], "TP")
                    send(f"💰 {symbol} LONG take profit hit\nExit: {round(trade['tp'], 4)}")
                    to_remove.append(symbol)
            else:
                if high >= trade["sl"]:
                    close_trade(symbol, close_time, trade["sl"], "SL")
                    send(f"🛑 {symbol} SHORT stopped out\nExit: {round(trade['sl'], 4)}")
                    to_remove.append(symbol)
                elif low <= trade["tp"]:
                    close_trade(symbol, close_time, trade["tp"], "TP")
                    send(f"💰 {symbol} SHORT take profit hit\nExit: {round(trade['tp'], 4)}")
                    to_remove.append(symbol)
        except Exception as e:
            print("Trade update error:", symbol, e)

    for symbol in to_remove:
        open_trades.pop(symbol, None)

def main():
    global signals_today
    ensure_trades_file()
    send("✅ Performance bot started")

    while True:
        try:
            reset_daily_counter()
            update_open_trades()

            if signals_today < MAX_SIGNALS_PER_DAY:
                for symbol in SYMBOLS:
                    if signals_today >= MAX_SIGNALS_PER_DAY:
                        break
                    if symbol in open_trades:
                        continue

                    try:
                        result = build_signal(symbol)
                        if not result:
                            print(symbol, "- no signal")
                            continue

                        signal_key, msg, side, candle_time, price, sl, tp = result

                        if last_signal_by_symbol.get(symbol) != signal_key:
                            send(msg)
                            last_signal_by_symbol[symbol] = signal_key
                            open_trades[symbol] = {
                                "side": side,
                                "entry": price,
                                "sl": sl,
                                "tp": tp,
                                "entry_time": candle_time
                            }
                            log_new_trade(symbol, side, candle_time, price, sl, tp)
                            signals_today += 1
                        else:
                            print(symbol, "- duplicate skipped")

                    except Exception as symbol_error:
                        print(symbol, "error:", symbol_error)

            now = datetime.now(timezone.utc)
            if now.minute == 0:
                try:
                    send(summarize_performance())
                    time.sleep(61)
                    continue
                except Exception as e:
                    print("Summary error:", e)

        except Exception as e:
            print("Main loop error:", e)

        time.sleep(CHECK_EVERY_SECONDS)

if __name__ == "__main__":
    main()
