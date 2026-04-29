import os
import requests
import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from typing import Optional

load_dotenv()

app = FastAPI(title="RIGA FX v7 Balanced", version="7.1")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
DEFAULT_INTERVAL = os.getenv("DEFAULT_INTERVAL", "5min")
MIN_CONFIDENCE = int(os.getenv("MIN_CONFIDENCE", "60"))

DEFAULT_PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF",
    "AUD/USD", "NZD/USD", "USD/CAD",
    "EUR/JPY", "GBP/JPY", "EUR/GBP",
    "XAU/USD"
]

YAHOO_SYMBOLS = {
    "XAU/USD": ["GC=F", "MGC=F"],
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


def normalize_symbol(symbol: str) -> str:
    s = symbol.strip().upper().replace(" ", "")
    if s == "XAUUSD":
        return "XAU/USD"
    if "/" in s:
        return s
    if len(s) == 6:
        return f"{s[:3]}/{s[3:]}"
    return s


def safe_float(v):
    try:
        if hasattr(v, "iloc"):
            v = v.iloc[0]
        return float(v)
    except Exception:
        return None


def fetch_twelvedata(symbol: str):
    if not TWELVEDATA_API_KEY:
        raise Exception("Missing TWELVEDATA_API_KEY")

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": normalize_symbol(symbol),
        "interval": DEFAULT_INTERVAL,
        "outputsize": 120,
        "apikey": TWELVEDATA_API_KEY,
    }

    data = requests.get(url, params=params, timeout=20).json()

    if "values" not in data:
        raise Exception(f"TwelveData failed: {data}")

    candles = []
    for row in reversed(data["values"]):
        o = safe_float(row.get("open"))
        h = safe_float(row.get("high"))
        l = safe_float(row.get("low"))
        c = safe_float(row.get("close"))

        if None not in [o, h, l, c]:
            candles.append({"open": o, "high": h, "low": l, "close": c})

    if len(candles) < 40:
        raise Exception("Not enough TwelveData candles")

    return "TwelveData", candles


def fetch_yahoo(symbol: str):
    symbol = normalize_symbol(symbol)

    for ys in YAHOO_SYMBOLS.get(symbol, []):
        df = yf.download(
            ys,
            period="5d",
            interval="5m",
            progress=False,
            auto_adjust=False
        )

        if df.empty:
            continue

        candles = []
        for _, r in df.tail(120).iterrows():
            o = safe_float(r["Open"])
            h = safe_float(r["High"])
            l = safe_float(r["Low"])
            c = safe_float(r["Close"])

            if None not in [o, h, l, c]:
                candles.append({"open": o, "high": h, "low": l, "close": c})

        if len(candles) >= 40:
            return f"Yahoo({ys})", candles

    raise Exception("Yahoo failed")


def fetch_data(symbol: str):
    symbol = normalize_symbol(symbol)

    if symbol == "XAU/USD":
        try:
            return fetch_yahoo(symbol)
        except Exception:
            return fetch_twelvedata(symbol)

    try:
        return fetch_twelvedata(symbol)
    except Exception:
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

    gains = []
    losses = []

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


def candle_stats(c):
    high = c["high"]
    low = c["low"]
    close = c["close"]
    open_p = c["open"]

    rng = high - low
    if rng <= 0:
        return None

    body = abs(close - open_p)
    upper_wick = high - max(open_p, close)
    lower_wick = min(open_p, close) - low
    position = (close - low) / rng
    momentum = ((close - open_p) / open_p) * 100 if open_p else 0
    strength = body / rng

    return {
        "range": rng,
        "body": body,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "position": position,
        "momentum": momentum,
        "strength": strength
    }


def candle_quality(c):
    st = candle_stats(c)
    if not st:
        return "BAD", 0

    strength = st["strength"]

    if strength >= 0.70:
        return "A_PLUS", strength
    if strength >= 0.60:
        return "A_GRADE", strength
    if strength >= 0.45:
        return "B_GRADE", strength

    return "LOW_QUALITY", strength


def detect_pattern(candles):
    last = candles[-1]
    st = candle_stats(last)

    if not st:
        return "NO_PATTERN"

    pos = st["position"]
    mom = st["momentum"]
    strength = st["strength"]

    if pos > 0.85 and mom > 0.02 and strength >= 0.45:
        return "BULLISH_CONTINUATION"

    if pos < 0.15 and mom < -0.02 and strength >= 0.45:
        return "BEARISH_CONTINUATION"

    if pos > 0.75 and strength >= 0.50:
        return "BULLISH_PRESSURE"

    if pos < 0.25 and strength >= 0.50:
        return "BEARISH_PRESSURE"

    return "NO_PATTERN"


def liquidity_trap_filter(c):
    st = candle_stats(c)
    if not st:
        return True, "bad candle data"

    body = st["body"]
    upper_wick = st["upper_wick"]
    lower_wick = st["lower_wick"]
    pos = st["position"]

    if body <= 0:
        return True, "doji/no body trap"

    if upper_wick > body * 2.0 and pos < 0.70:
        return True, "upper wick rejection trap"

    if lower_wick > body * 2.0 and pos > 0.30:
        return True, "lower wick rejection trap"

    return False, "no liquidity trap"


def retest_filter(c, pattern):
    st = candle_stats(c)
    if not st:
        return False, "no retest data"

    pos = st["position"]

    if pattern in ["BULLISH_CONTINUATION", "BULLISH_PRESSURE"] and pos >= 0.65:
        return True, "bullish acceptance"

    if pattern in ["BEARISH_CONTINUATION", "BEARISH_PRESSURE"] and pos <= 0.35:
        return True, "bearish acceptance"

    return False, "retest optional / not confirmed"


def round_price(symbol, price):
    symbol = normalize_symbol(symbol)

    if symbol == "XAU/USD":
        return round(price, 2)
    if "JPY" in symbol:
        return round(price, 3)

    return round(price, 5)


def riga_fx_logic(symbol, candles):
    symbol = normalize_symbol(symbol)

    if len(candles) < 40:
        return {
            "market": symbol,
            "signal": "NO TRADE",
            "confidence": 0,
            "reason": "Not enough candles"
        }

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    last_candle = candles[-1]
    last = closes[-1]

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    rsi14 = rsi(closes, 14)

    if e9 is None or e21 is None or rsi14 is None:
        return {
            "market": symbol,
            "signal": "NO TRADE",
            "confidence": 0,
            "reason": "Indicator incomplete"
        }

    recent_high = max(highs[-30:])
    recent_low = min(lows[-30:])
    range_size = recent_high - recent_low

    if range_size <= 0:
        return {
            "market": symbol,
            "signal": "NO TRADE",
            "confidence": 0,
            "reason": "Invalid range"
        }

    zone = (last - recent_low) / range_size

    trend = "BULLISH" if e9 > e21 else "BEARISH"
    phase = "RANGE" if 0.35 < zone < 0.65 else "EDGE"

    pattern = detect_pattern(candles)
    candle, strength = candle_quality(last_candle)
    trap, trap_reason = liquidity_trap_filter(last_candle)
    retest, retest_reason = retest_filter(last_candle, pattern)

    buy_score = 0
    sell_score = 0
    buy_reasons = []
    sell_reasons = []

    if trend == "BULLISH":
        buy_score += 20
        buy_reasons.append("EMA bullish")
    else:
        sell_score += 20
        sell_reasons.append("EMA bearish")

    if rsi14 > 50:
        buy_score += 15
        buy_reasons.append("RSI bullish")
    if rsi14 < 50:
        sell_score += 15
        sell_reasons.append("RSI bearish")

    if candle in ["A_PLUS", "A_GRADE"]:
        buy_score += 15
        sell_score += 15
        buy_reasons.append(candle)
        sell_reasons.append(candle)
    elif candle == "B_GRADE":
        buy_score += 8
        sell_score += 8
        buy_reasons.append("B candle")
        sell_reasons.append("B candle")

    if pattern in ["BULLISH_CONTINUATION", "BULLISH_PRESSURE"]:
        buy_score += 20
        buy_reasons.append(pattern)

    if pattern in ["BEARISH_CONTINUATION", "BEARISH_PRESSURE"]:
        sell_score += 20
        sell_reasons.append(pattern)

    if zone <= 0.35:
        buy_score += 12
        buy_reasons.append("near support")

    if zone >= 0.65:
        sell_score += 12
        sell_reasons.append("near resistance")

    if retest:
        if trend == "BULLISH":
            buy_score += 8
            buy_reasons.append(retest_reason)
        if trend == "BEARISH":
            sell_score += 8
            sell_reasons.append(retest_reason)

    if trap:
        buy_score -= 20
        sell_score -= 20
        buy_reasons.append(trap_reason)
        sell_reasons.append(trap_reason)

    if 0.47 < zone < 0.53:
        buy_score -= 12
        sell_score -= 12
        buy_reasons.append("mid zone")
        sell_reasons.append("mid zone")

    atr_proxy = sum([highs[i] - lows[i] for i in range(-14, 0)]) / 14
    atr_proxy = max(atr_proxy, range_size * 0.08)

    if buy_score >= MIN_CONFIDENCE and buy_score >= sell_score:
        entry = last
        sl = entry - atr_proxy
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
            "pattern": pattern,
            "pattern_valid": pattern != "NO_PATTERN",
            "candle": candle,
            "candle_quality": round(strength, 2),
            "retest": retest,
            "liquidity_trap": trap,
            "rr": "1:2",
            "reason": ", ".join(buy_reasons)
        }

    if sell_score >= MIN_CONFIDENCE and sell_score > buy_score:
        entry = last
        sl = entry + atr_proxy
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
            "pattern": pattern,
            "pattern_valid": pattern != "NO_PATTERN",
            "candle": candle,
            "candle_quality": round(strength, 2),
            "retest": retest,
            "liquidity_trap": trap,
            "rr": "1:2",
            "reason": ", ".join(sell_reasons)
        }

    return {
        "market": symbol,
        "signal": "NO TRADE",
        "confidence": max(buy_score, sell_score),
        "trend": trend,
        "phase": phase,
        "pattern": pattern,
        "candle": candle,
        "candle_quality": round(strength, 2),
        "retest": retest,
        "liquidity_trap": trap,
        "reason": "Confirmations below threshold"
    }


def select_best_trade(results):
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
        "status": "RIGA FX v7 BALANCED LIVE",
        "version": "7.1",
        "min_confidence": MIN_CONFIDENCE
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/fx-signal")
def fx_signal(symbol: str = Query("EUR/USD")):
    try:
        source, candles = fetch_data(symbol)
        result = riga_fx_logic(symbol, candles)
        result["data_source"] = source
        return result
    except Exception as e:
        return {
            "market": normalize_symbol(symbol),
            "signal": "NO TRADE",
            "confidence": 0,
            "reason": str(e)
        }


@app.get("/fx-scan")
def fx_scan(pairs: Optional[str] = Query(None)):
    symbols = [normalize_symbol(x) for x in pairs.split(",")] if pairs else DEFAULT_PAIRS

    results = []

    for symbol in symbols:
        try:
            source, candles = fetch_data(symbol)
            result = riga_fx_logic(symbol, candles)
            result["data_source"] = source
            results.append(result)
        except Exception as e:
            results.append({
                "market": normalize_symbol(symbol),
                "signal": "NO TRADE",
                "confidence": 0,
                "reason": str(e)
            })

    return {
        "scan_type": "RIGA_FX_V7_BALANCED",
        "total_scanned": len(results),
        "best_trade": select_best_trade(results),
        "all_results": results
    }
