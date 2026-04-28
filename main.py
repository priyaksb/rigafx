import os
import math
from typing import Optional, Dict, Any, List

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query

load_dotenv()

app = FastAPI(title="RIGA FX Twelve Data Backend", version="1.0.0")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
RIGA_ACTION_TOKEN = os.getenv("RIGA_ACTION_TOKEN", "")
DEFAULT_INTERVAL = os.getenv("DEFAULT_INTERVAL", "5min")
MIN_CONFIDENCE = int(os.getenv("MIN_CONFIDENCE", "70"))
MIN_RR = float(os.getenv("MIN_RR", "1.5"))

DEFAULT_PAIRS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "USD/CHF",
    "AUD/USD",
    "NZD/USD",
    "USD/CAD",
    "EUR/JPY",
    "GBP/JPY",
    "EUR/GBP",
]


def check_token(authorization: Optional[str]) -> None:
    if RIGA_ACTION_TOKEN and authorization != f"Bearer {RIGA_ACTION_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def require_api_key() -> None:
    if not TWELVEDATA_API_KEY:
        raise HTTPException(status_code=500, detail="Missing TWELVEDATA_API_KEY env variable")


def normalize_symbol(symbol: str) -> str:
    s = symbol.strip().upper().replace(" ", "")
    if "/" in s:
        base, quote = s.split("/", 1)
        return f"{base}/{quote}"
    if len(s) == 6:
        return f"{s[:3]}/{s[3:]}"
    return symbol.strip().upper()


def fetch_time_series(symbol: str, interval: str = DEFAULT_INTERVAL, outputsize: int = 120) -> Dict[str, Any]:
    require_api_key()
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": normalize_symbol(symbol),
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY,
        "format": "JSON",
    }
    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Twelve Data request failed: {exc}")

    if isinstance(data, dict) and data.get("status") == "error":
        raise HTTPException(status_code=502, detail=data)
    if "values" not in data:
        raise HTTPException(status_code=502, detail={"message": "Unexpected Twelve Data response", "response": data})
    return data


def to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        x = float(value)
        if math.isnan(x):
            return None
        return x
    except Exception:
        return None


def candles_from_response(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    values = data.get("values", [])
    candles = []
    for row in reversed(values):  # oldest -> latest
        o, h, l, c = [to_float(row.get(k)) for k in ["open", "high", "low", "close"]]
        if None in [o, h, l, c]:
            continue
        candles.append({
            "datetime": row.get("datetime"),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
        })
    return candles


def ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for price in values[period:]:
        e = price * k + e * (1 - k)
    return e


def rsi(values: List[float], period: int = 14) -> Optional[float]:
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
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def candle_quality(candle: Dict[str, float]) -> Dict[str, Any]:
    o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
    rng = max(h - l, 1e-10)
    body = abs(c - o)
    body_pct = body / rng
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    direction = "Bullish" if c > o else "Bearish" if c < o else "Neutral"

    if body_pct >= 0.65:
        grade = "A+"
    elif body_pct >= 0.50:
        grade = "A"
    elif body_pct >= 0.35:
        grade = "B"
    else:
        grade = "Weak"

    return {
        "direction": direction,
        "grade": grade,
        "body_pct": round(body_pct, 3),
        "upper_wick_pct": round(upper_wick / rng, 3),
        "lower_wick_pct": round(lower_wick / rng, 3),
    }


def analyze_riga_fx(symbol: str, interval: str, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(candles) < 40:
        return {"market": symbol, "signal": "NO TRADE", "confidence": 0, "reason": "Not enough candles"}

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    last = candles[-1]
    last_close = closes[-1]
    prev_close = closes[-2]
    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    rsi14 = rsi(closes, 14)
    quality = candle_quality(last)

    lookback_high = max(highs[-21:-1])
    lookback_low = min(lows[-21:-1])
    atr_proxy = sum((highs[i] - lows[i]) for i in range(-14, 0)) / 14
    atr_proxy = max(atr_proxy, last_close * 0.0005)

    bullish_breakout = last_close > lookback_high and prev_close <= lookback_high
    bearish_breakdown = last_close < lookback_low and prev_close >= lookback_low
    bullish_trend = e9 is not None and e21 is not None and e9 > e21 and last_close > e9
    bearish_trend = e9 is not None and e21 is not None and e9 < e21 and last_close < e9
    momentum_bull = rsi14 is not None and 55 <= rsi14 <= 75
    momentum_bear = rsi14 is not None and 25 <= rsi14 <= 45

    score = 0
    reasons = []
    signal = "NO TRADE"
    bias = "Neutral"

    # Bullish setup
    bull_score = 0
    if bullish_trend:
        bull_score += 25; reasons.append("EMA 9 above EMA 21 with price above EMA 9")
    if bullish_breakout:
        bull_score += 25; reasons.append("fresh 20-candle breakout")
    if momentum_bull:
        bull_score += 20; reasons.append("RSI momentum supports bullish continuation")
    if quality["direction"] == "Bullish" and quality["grade"] in ["A+", "A", "B"]:
        bull_score += 20; reasons.append(f"{quality['grade']} bullish candle")
    if last_close > prev_close:
        bull_score += 10

    # Bearish setup
    bear_reasons = []
    bear_score = 0
    if bearish_trend:
        bear_score += 25; bear_reasons.append("EMA 9 below EMA 21 with price below EMA 9")
    if bearish_breakdown:
        bear_score += 25; bear_reasons.append("fresh 20-candle breakdown")
    if momentum_bear:
        bear_score += 20; bear_reasons.append("RSI momentum supports bearish continuation")
    if quality["direction"] == "Bearish" and quality["grade"] in ["A+", "A", "B"]:
        bear_score += 20; bear_reasons.append(f"{quality['grade']} bearish candle")
    if last_close < prev_close:
        bear_score += 10

    if bull_score >= bear_score:
        score = bull_score
        selected_reasons = reasons
        if score >= MIN_CONFIDENCE:
            signal = "BUY"
            bias = "Bullish"
            entry = last_close
            stop_loss = entry - atr_proxy
            target = entry + (entry - stop_loss) * MIN_RR
        else:
            entry = stop_loss = target = None
    else:
        score = bear_score
        selected_reasons = bear_reasons
        if score >= MIN_CONFIDENCE:
            signal = "SELL"
            bias = "Bearish"
            entry = last_close
            stop_loss = entry + atr_proxy
            target = entry - (stop_loss - entry) * MIN_RR
        else:
            entry = stop_loss = target = None

    if signal == "NO TRADE":
        return {
            "market": normalize_symbol(symbol),
            "interval": interval,
            "signal": "NO TRADE",
            "bias": bias,
            "confidence": min(score, 69),
            "entry": None,
            "stop_loss": None,
            "target": None,
            "risk_reward": None,
            "last_price": round(last_close, 5),
            "ema9": round(e9, 5) if e9 else None,
            "ema21": round(e21, 5) if e21 else None,
            "rsi14": round(rsi14, 2) if rsi14 else None,
            "candle_quality": quality,
            "reason": "NO TRADE: confidence below threshold or setup incomplete",
        }

    return {
        "market": normalize_symbol(symbol),
        "interval": interval,
        "signal": signal,
        "bias": bias,
        "confidence": min(score, 95),
        "entry": round(entry, 5),
        "stop_loss": round(stop_loss, 5),
        "target": round(target, 5),
        "risk_reward": f"1:{MIN_RR}",
        "last_price": round(last_close, 5),
        "ema9": round(e9, 5) if e9 else None,
        "ema21": round(e21, 5) if e21 else None,
        "rsi14": round(rsi14, 2) if rsi14 else None,
        "candle_quality": quality,
        "reason": "; ".join(selected_reasons[:4]) or "RIGA FX confirmation met",
    }


@app.get("/")
def root():
    return {"status": "running", "name": "RIGA FX Twelve Data Backend", "version": "1.0.0"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/fx-candles")
def fx_candles(
    symbol: str = Query("EUR/USD", description="Forex pair like EUR/USD or EURUSD"),
    interval: str = Query(DEFAULT_INTERVAL, description="1min, 5min, 15min, 1h, etc."),
    outputsize: int = Query(50, ge=10, le=500),
    authorization: Optional[str] = Header(default=None),
):
    check_token(authorization)
    data = fetch_time_series(symbol, interval, outputsize)
    return {
        "symbol": normalize_symbol(symbol),
        "interval": interval,
        "meta": data.get("meta"),
        "values": data.get("values", []),
    }


@app.get("/fx-signal")
def fx_signal(
    symbol: str = Query("EUR/USD", description="Forex pair like EUR/USD or EURUSD"),
    interval: str = Query(DEFAULT_INTERVAL),
    authorization: Optional[str] = Header(default=None),
):
    check_token(authorization)
    data = fetch_time_series(symbol, interval, 120)
    candles = candles_from_response(data)
    return analyze_riga_fx(symbol, interval, candles)


@app.get("/fx-scan")
def fx_scan(
    pairs: Optional[str] = Query(None, description="Comma-separated pairs. Example: EUR/USD,GBP/USD,USD/JPY"),
    interval: str = Query(DEFAULT_INTERVAL),
    authorization: Optional[str] = Header(default=None),
):
    check_token(authorization)
    symbols = [normalize_symbol(x) for x in pairs.split(",")] if pairs else DEFAULT_PAIRS
    results = []
    for symbol in symbols:
        try:
            data = fetch_time_series(symbol, interval, 120)
            candles = candles_from_response(data)
            results.append(analyze_riga_fx(symbol, interval, candles))
        except Exception as exc:
            results.append({"market": normalize_symbol(symbol), "signal": "ERROR", "confidence": 0, "reason": str(exc)})
    trades = [r for r in results if r.get("signal") in ["BUY", "SELL"] and r.get("confidence", 0) >= MIN_CONFIDENCE]
    trades.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    return {
        "scan_type": "riga_fx_scan",
        "interval": interval,
        "total_scanned": len(results),
        "trade_count": len(trades),
        "best_trade": trades[0] if trades else None,
        "trades": trades,
        "all_results": results,
    }
