# ===============================
# RIGA FX PRO BACKEND (FINAL FIXED)
# ===============================

import os
import requests
import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

app = FastAPI(title="RIGA FX PRO", version="3.0.0")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
DEFAULT_INTERVAL = "5min"

DEFAULT_PAIRS = [
    "EUR/USD","GBP/USD","USD/JPY","USD/CHF",
    "AUD/USD","NZD/USD","USD/CAD",
    "EUR/JPY","GBP/JPY","EUR/GBP",
    "XAU/USD"
]

YAHOO_SYMBOLS = {
    "XAU/USD": ["GC=F","MGC=F"],
    "EUR/USD": ["EURUSD=X"],
    "GBP/USD": ["GBPUSD=X"],
    "USD/JPY": ["JPY=X"],
    "USD/CHF": ["CHF=X"],
    "AUD/USD": ["AUDUSD=X"],
    "NZD/USD": ["NZDUSD=X"],
    "USD/CAD": ["CAD=X"],
    "EUR/JPY": ["EURJPY=X"],
    "GBP/JPY": ["GBPJPY=X"],
    "EUR/GBP": ["EURGBP=X"],
}

# ===============================
# DATA FETCH
# ===============================

def fetch_yahoo(symbol):
    for sym in YAHOO_SYMBOLS.get(symbol, []):
        df = yf.download(sym, period="5d", interval="5m", progress=False)

        if not df.empty:
            candles = []
            for _, r in df.tail(120).iterrows():
                candles.append({
                    "open": float(r["Open"]),
                    "high": float(r["High"]),
                    "low": float(r["Low"]),
                    "close": float(r["Close"])
                })
            return f"Yahoo({sym})", candles

    raise Exception("Yahoo fail")


def fetch_data(symbol):
    try:
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol,
            "interval": DEFAULT_INTERVAL,
            "apikey": TWELVEDATA_API_KEY,
            "outputsize": 120
        }

        r = requests.get(url, params=params).json()

        if "values" in r:
            candles = []
            for row in reversed(r["values"]):
                candles.append({
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"])
                })
            return "TwelveData", candles
    except:
        pass

    return fetch_yahoo(symbol)

# ===============================
# INDICATORS
# ===============================

def ema(data, p):
    k = 2/(p+1)
    e = sum(data[:p])/p
    for price in data[p:]:
        e = price*k + e*(1-k)
    return e


def rsi(data, p=14):
    gains, losses = [], []

    for i in range(1,len(data)):
        diff = data[i]-data[i-1]
        gains.append(max(diff,0))
        losses.append(abs(min(diff,0)))

    avg_gain = sum(gains[-p:])/p
    avg_loss = sum(losses[-p:])/p

    if avg_loss == 0:
        return 100

    rs = avg_gain/avg_loss
    return 100 - (100/(1+rs))

# ===============================
# DETECTION
# ===============================

def detect_candle(c):
    body = abs(c["close"] - c["open"])
    rng = c["high"] - c["low"]

    if body > rng*0.6:
        return "marubozu","strong"

    if c["close"] > c["open"]:
        return "bullish","strong"

    if c["open"] > c["close"]:
        return "bearish","strong"

    return "weak","weak"


def detect_pattern(closes):
    high = max(closes[-20:])
    low = min(closes[-20:])
    last = closes[-1]

    if last > high:
        return "breakout", True

    if last < low:
        return "breakdown", True

    return "range", False

# ===============================
# ANALYSIS ENGINE
# ===============================

def analyze(symbol, candles):

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    last = closes[-1]

    e9 = ema(closes,9)
    e21 = ema(closes,21)
    r = rsi(closes)

    trend = "bullish" if e9 > e21 else "bearish"

    recent_high = max(highs[-30:])
    recent_low = min(lows[-30:])
    mid = (recent_high + recent_low) / 2
    range_size = recent_high - recent_low

    # MID FILTER
    if abs(last-mid) < range_size*0.2:
        return {"market":symbol,"signal":"NO TRADE"}

    pattern, valid = detect_pattern(closes)
    candle, quality = detect_candle(candles[-1])

    # SELL
    if trend=="bearish" and valid and r<45 and last>mid:
        sl = recent_high
        tp = last - (sl-last)*2

        return {
            "market":symbol,
            "type":"SELL",
            "entry":round(last,2),
            "sl":round(sl,2),
            "tp":round(tp,2),
            "confidence":80,
            "pattern":pattern,
            "candle":candle,
            "rr":"1:2"
        }

    # BUY
    if trend=="bullish" and valid and r>55 and last<mid:
        sl = recent_low
        tp = last + (last-sl)*2

        return {
            "market":symbol,
            "type":"BUY",
            "entry":round(last,2),
            "sl":round(sl,2),
            "tp":round(tp,2),
            "confidence":80,
            "pattern":pattern,
            "candle":candle,
            "rr":"1:2"
        }

    return {"market":symbol,"signal":"NO TRADE"}

# ===============================
# ROUTES
# ===============================

@app.get("/fx-signal")
def fx_signal(symbol: str = "EUR/USD"):
    source, candles = fetch_data(symbol)
    res = analyze(symbol, candles)
    res["data_source"] = source
    return res


@app.get("/fx-scan")
def fx_scan():
    results = []

    for p in DEFAULT_PAIRS:
        try:
            source, candles = fetch_data(p)
            r = analyze(p, candles)
            r["data_source"] = source
            results.append(r)
        except:
            results.append({"market":p,"signal":"NO TRADE"})

    return results
