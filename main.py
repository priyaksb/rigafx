import os
import requests
import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from typing import Optional

load_dotenv()

app = FastAPI(title="RIGA FX PRO", version="1.0")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
MIN_CONFIDENCE = int(os.getenv("MIN_CONFIDENCE", "60"))
INTERVAL = "5min"

PAIRS = ["XAU/USD", "EUR/USD", "GBP/USD", "USD/JPY"]

YAHOO = {
    "XAU/USD": ["GC=F", "MGC=F"],
    "EUR/USD": ["EURUSD=X"],
    "GBP/USD": ["GBPUSD=X"],
    "USD/JPY": ["JPY=X"]
}


def norm(symbol: str):
    s = symbol.upper().replace(" ", "")
    if s == "XAUUSD":
        return "XAU/USD"
    if "/" in s:
        return s
    if len(s) == 6:
        return s[:3] + "/" + s[3:]
    return s


def fnum(v):
    try:
        if hasattr(v, "iloc"):
            v = v.iloc[0]
        return float(v)
    except:
        return None


def fetch_twelve(symbol):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": norm(symbol),
        "interval": INTERVAL,
        "outputsize": 120,
        "apikey": TWELVEDATA_API_KEY
    }

    data = requests.get(url, params=params, timeout=20).json()

    if "values" not in data:
        raise Exception(str(data))

    candles = []
    for r in reversed(data["values"]):
        o, h, l, c = fnum(r["open"]), fnum(r["high"]), fnum(r["low"]), fnum(r["close"])
        if None not in [o, h, l, c]:
            candles.append({"open": o, "high": h, "low": l, "close": c})

    if len(candles) < 40:
        raise Exception("Not enough TwelveData candles")

    return "TwelveData", candles


def fetch_yahoo(symbol):
    symbol = norm(symbol)

    for ys in YAHOO.get(symbol, []):
        df = yf.download(ys, period="5d", interval="5m", progress=False, auto_adjust=False)

        if df.empty:
            continue

        candles = []
        for _, r in df.tail(120).iterrows():
            o, h, l, c = fnum(r["Open"]), fnum(r["High"]), fnum(r["Low"]), fnum(r["Close"])
            if None not in [o, h, l, c]:
                candles.append({"open": o, "high": h, "low": l, "close": c})

        if len(candles) >= 40:
            return f"Yahoo({ys})", candles

    raise Exception("Yahoo failed")


def fetch_data(symbol):
    symbol = norm(symbol)

    if symbol == "XAU/USD":
        try:
            return fetch_yahoo(symbol)
        except:
            return fetch_twelve(symbol)

    try:
        return fetch_twelve(symbol)
    except:
        return fetch_yahoo(symbol)


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
        d = values[i] - values[i - 1]
        gains.append(max(d, 0))
        losses.append(abs(min(d, 0)))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def candle_info(c):
    rng = c["high"] - c["low"]
    if rng <= 0:
        return "BAD", 0, "neutral"

    body = abs(c["close"] - c["open"])
    strength = body / rng

    direction = "bullish" if c["close"] > c["open"] else "bearish"

    if strength >= 0.70:
        grade = "A_PLUS"
    elif strength >= 0.55:
        grade = "A_GRADE"
    elif strength >= 0.40:
        grade = "B_GRADE"
    else:
        grade = "LOW_QUALITY"

    return grade, strength, direction


def trap_check(c):
    rng = c["high"] - c["low"]
    if rng <= 0:
        return True, "bad candle"

    body = abs(c["close"] - c["open"])
    upper = c["high"] - max(c["open"], c["close"])
    lower = min(c["open"], c["close"]) - c["low"]

    if body <= 0:
        return True, "doji candle"

    if upper > body * 2:
        return True, "upper wick trap"

    if lower > body * 2:
        return True, "lower wick trap"

    return False, "no trap"


def round_price(symbol, value):
    symbol = norm(symbol)
    if symbol == "XAU/USD":
        return round(value, 2)
    if "JPY" in symbol:
        return round(value, 3)
    return round(value, 5)


def analyze(symbol, candles):
    symbol = norm(symbol)

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    last_candle = candles[-1]
    last = closes[-1]

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    r = rsi(closes)

    if e9 is None or e21 is None or r is None:
        return {"market": symbol, "signal": "NO TRADE", "confidence": 0, "reason": "indicator incomplete"}

    trend = "BULLISH" if e9 > e21 else "BEARISH"

    recent_high = max(highs[-30:])
    recent_low = min(lows[-30:])
    range_size = recent_high - recent_low

    if range_size <= 0:
        return {"market": symbol, "signal": "NO TRADE", "confidence": 0, "reason": "invalid range"}

    zone = (last - recent_low) / range_size

    phase = "RANGE" if 0.35 < zone < 0.65 else "EDGE"

    candle, strength, candle_dir = candle_info(last_candle)
    trap, trap_reason = trap_check(last_candle)

    buy_score = 0
    sell_score = 0
    buy_reason = []
    sell_reason = []

    if trend == "BULLISH":
        buy_score += 20
        buy_reason.append("EMA bullish trend")
    else:
        sell_score += 20
        sell_reason.append("EMA bearish trend")

    if r > 50:
        buy_score += 15
        buy_reason.append("RSI bullish momentum")

    if r < 50:
        sell_score += 15
        sell_reason.append("RSI bearish momentum")

    if candle in ["A_PLUS", "A_GRADE"]:
        buy_score += 15
        sell_score += 15
        buy_reason.append(f"{candle} candle")
        sell_reason.append(f"{candle} candle")
    elif candle == "B_GRADE":
        buy_score += 8
        sell_score += 8
        buy_reason.append("B grade candle")
        sell_reason.append("B grade candle")

    if candle_dir == "bullish":
        buy_score += 12
        buy_reason.append("bullish candle direction")

    if candle_dir == "bearish":
        sell_score += 12
        sell_reason.append("bearish candle direction")

    if zone <= 0.35:
        buy_score += 15
        buy_reason.append("near support zone")

    if zone >= 0.65:
        sell_score += 15
        sell_reason.append("near resistance zone")

    if trap:
        buy_score -= 20
        sell_score -= 20
        buy_reason.append(trap_reason)
        sell_reason.append(trap_reason)

    if 0.47 < zone < 0.53:
        buy_score -= 10
        sell_score -= 10
        buy_reason.append("mid-range zone")
        sell_reason.append("mid-range zone")

    atr = sum([highs[i] - lows[i] for i in range(-14, 0)]) / 14
    atr = max(atr, range_size * 0.08)

    if buy_score >= MIN_CONFIDENCE and buy_score >= sell_score:
        entry = last
        sl = entry - atr
        tp = entry + (entry - sl) * 2

        return {
            "market": symbol,
            "type": "BUY",
            "entry": round_price(symbol, entry),
            "sl": round_price(symbol, sl),
            "tp": round_price(symbol, tp),
            "confidence": min(buy_score, 95),
            "trend": trend,
            "phase": phase,
            "level": "support" if zone <= 0.35 else "continuation",
            "momentum": "bullish",
            "pattern": "BULLISH_PRESSURE",
            "candle": candle,
            "candle_quality": round(strength, 2),
            "liquidity_trap": trap,
            "rr": "1:2",
            "reason": ", ".join(buy_reason)
        }

    if sell_score >= MIN_CONFIDENCE and sell_score > buy_score:
        entry = last
        sl = entry + atr
        tp = entry - (sl - entry) * 2

        return {
            "market": symbol,
            "type": "SELL",
            "entry": round_price(symbol, entry),
            "sl": round_price(symbol, sl),
            "tp": round_price(symbol, tp),
            "confidence": min(sell_score, 95),
            "trend": trend,
            "phase": phase,
            "level": "resistance" if zone >= 0.65 else "continuation",
            "momentum": "bearish",
            "pattern": "BEARISH_PRESSURE",
            "candle": candle,
            "candle_quality": round(strength, 2),
            "liquidity_trap": trap,
            "rr": "1:2",
            "reason": ", ".join(sell_reason)
        }

    return {
        "market": symbol,
        "signal": "NO TRADE",
        "confidence": max(buy_score, sell_score),
        "trend": trend,
        "phase": phase,
        "pattern": "NO_VALID_PATTERN",
        "candle": candle,
        "candle_quality": round(strength, 2),
        "liquidity_trap": trap,
        "reason": "confirmations below threshold"
    }


def best_trade(results):
    trades = [
        r for r in results
        if r.get("type") in ["BUY", "SELL"] and r.get("confidence", 0) >= MIN_CONFIDENCE
    ]

    if not trades:
        return "NO TRADE"

    return sorted(trades, key=lambda x: x.get("confidence", 0), reverse=True)[0]


@app.get("/")
def root():
    return {
        "status": "RIGA FX PRO LIVE",
        "version": "1.0",
        "pairs": PAIRS,
        "min_confidence": MIN_CONFIDENCE
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/fx-signal")
def fx_signal(symbol: str = Query("XAU/USD")):
    try:
        source, candles = fetch_data(symbol)
        result = analyze(symbol, candles)
        result["data_source"] = source
        return result
    except Exception as e:
        return {
            "market": norm(symbol),
            "signal": "NO TRADE",
            "confidence": 0,
            "reason": str(e)
        }


@app.get("/fx-scan")
def fx_scan(pairs: Optional[str] = Query(None)):
    symbols = [norm(x) for x in pairs.split(",")] if pairs else PAIRS

    results = []

    for s in symbols:
        try:
            source, candles = fetch_data(s)
            result = analyze(s, candles)
            result["data_source"] = source
            results.append(result)
        except Exception as e:
            results.append({
                "market": norm(s),
                "signal": "NO TRADE",
                "confidence": 0,
                "reason": str(e)
            })

    return {
        "scan_type": "RIGA_FX_PRO",
        "total_scanned": len(results),
        "best_trade": best_trade(results),
        "all_results": results
    }
