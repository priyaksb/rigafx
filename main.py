import os
import math
from typing import Optional, Dict, Any, List

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query

load_dotenv()

app = FastAPI(title="RIGA FX Twelve Data Backend", version="1.0.0")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
DEFAULT_INTERVAL = os.getenv("DEFAULT_INTERVAL", "5min")
MIN_CONFIDENCE = int(os.getenv("MIN_CONFIDENCE", "70"))
MIN_RR = float(os.getenv("MIN_RR", "1.5"))

DEFAULT_PAIRS = [
    "EUR/USD","GBP/USD","USD/JPY","USD/CHF","AUD/USD",
    "NZD/USD","USD/CAD","EUR/JPY","GBP/JPY","EUR/GBP",
    "XAU/USD","BTC/USD","ETH/USD"
]

# ✅ AUTH DISABLED (IMPORTANT)
def check_token(authorization: Optional[str]) -> None:
    return


def require_api_key() -> None:
    if not TWELVEDATA_API_KEY:
        raise HTTPException(status_code=500, detail="Missing TWELVEDATA_API_KEY")


def normalize_symbol(symbol: str) -> str:
    s = symbol.strip().upper().replace(" ", "")
    if "/" in s:
        return s
    if len(s) == 6:
        return f"{s[:3]}/{s[3:]}"
    return s


def fetch_time_series(symbol: str, interval: str = DEFAULT_INTERVAL, outputsize: int = 120):
    require_api_key()
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": normalize_symbol(symbol),
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY,
    }
    r = requests.get(url, params=params, timeout=20)
    data = r.json()

    if "values" not in data:
        raise HTTPException(status_code=502, detail=data)

    return data


def candles_from_response(data):
    candles = []
    for row in reversed(data["values"]):
        candles.append({
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        })
    return candles


def ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for price in values[period:]:
        e = price * k + e * (1 - k)
    return e


def rsi(values, period=14):
    if len(values) <= period:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def analyze(symbol, candles):
    if len(candles) < 40:
        return {"market": symbol, "signal": "NO TRADE"}

    closes = [c["close"] for c in candles]
    last = closes[-1]

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    r = rsi(closes)

    if not e9 or not e21 or not r:
        return {"market": symbol, "signal": "NO TRADE"}

    if e9 > e21 and r > 55:
        return {
            "market": symbol,
            "type": "BUY",
            "entry": round(last, 5),
            "sl": round(last - 0.0020, 5),
            "tp": round(last + 0.0040, 5),
            "confidence": 80
        }

    if e9 < e21 and r < 45:
        return {
            "market": symbol,
            "type": "SELL",
            "entry": round(last, 5),
            "sl": round(last + 0.0020, 5),
            "tp": round(last - 0.0040, 5),
            "confidence": 80
        }

    return {"market": symbol, "signal": "NO TRADE"}


@app.get("/")
def root():
    return {"status": "running"}


@app.get("/fx-scan")
def scan():
    results = []

    for pair in DEFAULT_PAIRS:
        try:
            data = fetch_time_series(pair)
            candles = candles_from_response(data)
            results.append(analyze(pair, candles))
        except Exception as e:
            results.append({"market": pair, "error": str(e)})

    return results
