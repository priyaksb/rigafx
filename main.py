from fastapi import FastAPI, Query
import requests
import os
from typing import Optional

app = FastAPI()

API_KEY = os.getenv("TWELVEDATA_API_KEY")

# -------------------------------------
# 📡 GET PRICE DATA
# -------------------------------------
def get_price(symbol):
    url = f"https://api.twelvedata.com/price?symbol={symbol}&apikey={API_KEY}"
    res = requests.get(url).json()
    if "price" in res:
        return float(res["price"])
    return None

# -------------------------------------
# 📡 GET CANDLE DATA
# -------------------------------------
def get_candles(symbol):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=5min&outputsize=50&apikey={API_KEY}"
    res = requests.get(url).json()

    if "values" not in res:
        return None

    closes = [float(c["close"]) for c in res["values"]]
    highs = [float(c["high"]) for c in res["values"]]
    lows = [float(c["low"]) for c in res["values"]]

    return closes, highs, lows

# -------------------------------------
# 🧠 SIMPLE RIGA FX LOGIC (BALANCED MODE)
# -------------------------------------
def generate_signal(symbol):

    data = get_candles(symbol)
    price = get_price(symbol)

    if not data or not price:
        return {
            "market": symbol,
            "signal": "NO TRADE",
            "reason": "Data fetch failed",
            "data_source": "TwelveData"
        }

    closes, highs, lows = data

    # ----------------------------
    # 📊 TREND CHECK
    # ----------------------------
    if closes[-1] > closes[-10]:
        trend = "bullish"
    elif closes[-1] < closes[-10]:
        trend = "bearish"
    else:
        trend = "sideways"

    # ----------------------------
    # 📊 SUPPORT / RESISTANCE
    # ----------------------------
    resistance = max(highs[-10:])
    support = min(lows[-10:])

    breakout = False
    breakdown = False

    if price > resistance:
        breakout = True
    elif price < support:
        breakdown = True

    # ----------------------------
    # ⚡ MOMENTUM (simple)
    # ----------------------------
    momentum = abs(closes[-1] - closes[-2]) > 0.5

    # ----------------------------
    # 🧠 CONFIDENCE SCORE
    # ----------------------------
    confidence = 0

    if trend != "sideways":
        confidence += 15

    if breakout or breakdown:
        confidence += 25

    if momentum:
        confidence += 15

    if (resistance - support) > 5:
        confidence += 10

    # RR assumed
    confidence += 10

    # ----------------------------
    # 🎯 DECISION
    # ----------------------------
    if confidence < 60:
        return {
            "market": symbol,
            "signal": "NO TRADE",
            "data_source": "TwelveData"
        }

    # ----------------------------
    # 📈 TRADE BUILD
    # ----------------------------
    if breakout:
        entry = price
        sl = price - 3
        target = price + 6
        signal = "BUY"

    elif breakdown:
        entry = price
        sl = price + 3
        target = price - 6
        signal = "SELL"

    else:
        return {
            "market": symbol,
            "signal": "NO TRADE",
            "data_source": "TwelveData"
        }

    return {
        "market": symbol,
        "type": signal,
        "entry": round(entry, 2),
        "sl": round(sl, 2),
        "tp": round(target, 2),
        "confidence": confidence,
        "data_source": "TwelveData"
    }

# -------------------------------------
# 🔎 SINGLE SIGNAL
# -------------------------------------
@app.get("/fx-signal")
def fx_signal(symbol: str = Query(...)):
    return generate_signal(symbol)

# -------------------------------------
# 🔎 MARKET SCAN
# -------------------------------------
@app.get("/fx-scan")
def fx_scan(pairs: Optional[str] = Query(None)):

    default_pairs = [
        "EUR/USD", "GBP/USD", "USD/JPY",
        "AUD/USD", "USD/CHF", "USD/CAD",
        "NZD/USD", "EUR/JPY", "GBP/JPY",
        "XAU/USD"
    ]

    if pairs:
        symbols = pairs.split(",")
    else:
        symbols = default_pairs

    results = []

    for sym in symbols:
        results.append(generate_signal(sym.strip()))

    return results

# -------------------------------------
# ❤️ HEALTH CHECK
# -------------------------------------
@app.get("/")
def root():
    return {"status": "RIGA FX running", "mode": "Balanced"}
