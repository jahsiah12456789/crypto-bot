from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

def send(msg):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": msg},
        timeout=20,
    )
    r.raise_for_status()

@app.route("/")
def home():
    return "BTCC TradingView Telegram bot is live"

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)

        pair = data.get("pair", "BTCUSDT.P")
        signal = data.get("signal", "NO SIGNAL")
        entry = data.get("entry", "N/A")
        tp = data.get("tp", "N/A")
        sl = data.get("sl", "N/A")
        tf = data.get("timeframe", "1m")
        strength = data.get("strength", "ACTIVE")

        msg = (
            f"🚨 VIP SIGNAL 🚨\n\n"
            f"💰 {pair}\n"
            f"📊 {signal}\n"
            f"━━━━━━━━━━━━━━\n"
            f"📍 Entry: {entry}\n"
            f"🎯 TP: {tp}\n"
            f"🛑 SL: {sl}\n"
            f"━━━━━━━━━━━━━━\n"
            f"⏱ Timeframe: {tf}\n"
            f"🔥 Strength: {strength}"
        )

        send(msg)
        return jsonify({"ok": True})

    except Exception as e:
        try:
            send(f"❌ WEBHOOK ERROR\n{type(e).__name__}: {str(e)}")
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)