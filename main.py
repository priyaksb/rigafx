import os
import requests
import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI, Query

load_dotenv()

app = FastAPI(title="RIGA FX v7 FINAL SNIPER", version="7.0")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
DEFAULT_INTERVAL = os.getenv("DEFAULT_INTERVAL", "5min")
MIN_CONFIDENCE = int(os.getenv("MIN_CONFIDENCE", "70"))

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
    if "/" in s:
        return s
    if len(s) == 6:
        return f"{s[:3]}/{s[3:]}"
    if s == "XAUUSD":
        return "XAU/USD"
    return s


def safe_float(v):
    try:
        if hasattr(v, "iloc"):
            v = v.iloc[0]
        return float(v)
    except Exception:
        return None


def fetch_twelvedata(symbol: str, outputsize: int = 120):
    if not TWELVEDATA_API_KEY:
        raise Exception("Missing TWELVEDATA_API_KEY")

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": normalize_symbol(symbol),
        "interval": DEFAULT_INTERVAL,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY,
    }

    res = requests.get(url, params=params, timeout=20)
    data = res.json()

    if "values" not in data:
        raise Exception(f"TwelveData failed: {data}")

    candles = []
    for row in reversed(data["values"]):
        o = safe_float(row.get("open"))
        h = safe_float(row.get("high"))
        l = safe_float(row.get("low"))
        c = safe_float(row.get("close"))

        if None not in [o, h, l, c]:
            candles.append({
                "open": o,
                "high": h,
                "low": l,
                "close": c
            })

    if len(candles) < 30:
        raise Exception("Not enough TwelveData candles")

    return "TwelveData", candles


def fetch_yahoo(symbol: str):
    symbol = normalize_symbol(symbol)
    yahoo_list = YAHOO_SYMBOLS.get(symbol, [])

    for ys in yahoo_list:
        try:
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
                    candles.append({
                        "open": o,
                        "high": h,
                        "low": l,
                        "close": c
                    })

            if len(candles) >= 30:
                return f"Yahoo({ys})", candles

        except Exception:
            continue

    raise Exception("Yahoo failed")


def fetch_data(symbol: str):
    symbol = normalize_symbol(symbol)

    # Gold: Yahoo first, then TwelveData
    if symbol == "XAU/USD":
        try:
            return fetch_yahoo(symbol)
        except Exception:
            pass

        return fetch_twelvedata(symbol)

    # Forex: TwelveData first, Yahoo backup
    try:
        return fetch_twelvedata(symbol)
    except Exception:
        return fetch_yahoo(symbol)


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

    if pos > 0.88 and mom > 0.04 and strength >= 0.60:
        return "BULLISH_BREAKOUT"

    if pos < 0.12 and mom < -0.04 and strength >= 0.60:
        return "BEARISH_BREAKDOWN"

    if pos > 0.70 and mom > 0.02 and strength >= 0.45:
        return "BULLISH_CONTINUATION"

    if pos < 0.30 and mom < -0.02 and strength >= 0.45:
        return "BEARISH_CONTINUATION"

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

    if upper_wick > body * 1.7 and pos < 0.75:
        return True, "upper wick rejection trap"

    if lower_wick > body * 1.7 and pos > 0.25:
        return True, "lower wick rejection trap"

    return False, "no liquidity trap"


def retest_filter(c, pattern):
    st = candle_stats(c)
    if not st:
        return False, "no retest data"

    pos = st["position"]

    if pattern in ["BULLISH_BREAKOUT", "BULLISH_CONTINUATION"] and pos >= 0.72:
        return True, "bullish acceptance/retest approximation"

    if pattern in ["BEARISH_BREAKDOWN", "BEARISH_CONTINUATION"] and pos <= 0.28:
        return True, "bearish acceptance/retest approximation"

    return False, "retest not confirmed"


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


def round_price(symbol, price):
    symbol = normalize_symbol(symbol)

    if symbol == "XAU/USD":
        return round(price, 2)
    if "JPY" in symbol:
        return round(price, 3)

    return round(price, 5)


def riga_fx_v7_logic(symbol, candles):
    symbol = normalize_symbol(symbol)

    if not candles or len(candles) < 40:
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
            "reason": "Indicator data incomplete"
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
    phase = "RANGE" if 0.35 < zone < 0.65 else "TREND_EDGE"

    pattern = detect_pattern(candles)
    candle, strength = candle_quality(last_candle)
    trap, trap_reason = liquidity_trap_filter(last_candle)
    retest, retest_reason = retest_filter(last_candle, pattern)

    st = candle_stats(last_candle)
    momentum = st["momentum"]

    buy_score = 0
    sell_score = 0
    buy_reasons = []
    sell_reasons = []

    if trend == "BULLISH":
        buy_score += 20
        buy_reasons.append("EMA bullish trend")
    else:
        sell_score += 20
        sell_reasons.append("EMA bearish trend")

    if pattern == "BULLISH_BREAKOUT":
        buy_score += 30
        buy_reasons.append("bullish breakout")
    elif pattern == "BULLISH_CONTINUATION":
        buy_score += 22
        buy_reasons.append("bullish continuation")

    if pattern == "BEARISH_BREAKDOWN":
        sell_score += 30
        sell_reasons.append("bearish breakdown")
    elif pattern == "BEARISH_CONTINUATION":
        sell_score += 22
        sell_reasons.append("bearish continuation")

    if candle == "A_PLUS":
        buy_score += 18
        sell_score += 18
        buy_reasons.append("A+ candle")
        sell_reasons.append("A+ candle")
    elif candle == "A_GRADE":
        buy_score += 14
        sell_score += 14
        buy_reasons.append("A grade candle")
        sell_reasons.append("A grade candle")
    elif candle == "B_GRADE":
        buy_score += 8
        sell_score += 8
        buy_reasons.append("B grade candle")
        sell_reasons.append("B grade candle")

    if rsi14 > 52:
        buy_score += 15
        buy_reasons.append("RSI bullish momentum")

    if rsi14 < 48:
        sell_score += 15
        sell_reasons.append("RSI bearish momentum")

    if zone <= 0.30:
        buy_score += 12
        buy_reasons.append("price near support zone")

    if zone >= 0.70:
        sell_score += 12
        sell_reasons.append("price near resistance zone")

    if retest:
        if pattern in ["BULLISH_BREAKOUT", "BULLISH_CONTINUATION"]:
            buy_score += 10
            buy_reasons.append(retest_reason)

        if pattern in ["BEARISH_BREAKDOWN", "BEARISH_CONTINUATION"]:
            sell_score += 10
            sell_reasons.append(retest_reason)

    if trap:
        buy_score -= 25
        sell_score -= 25
        buy_reasons.append(trap_reason)
        sell_reasons.append(trap_reason)

    # avoid exact middle only
    if 0.45 < zone < 0.55:
        buy_score -= 20
        sell_score -= 20
        buy_reasons.append("mid-range zone")
        sell_reasons.append("mid-range zone")

    atr_proxy = sum(highs[-14:]) / 14 - sum(lows[-14:]) / 14
    atr_proxy = max(atr_proxy, range_size * 0.10)

    if buy_score >= MIN_CONFIDENCE and buy_score >= sell_score:
        entry = last
        sl = entry - atr_proxy
        target = entry + (entry - sl) * 2

        return {
            "market": symbol,
            "type": "BUY",
            "entry": round_price(symbol, entry),
            "sl": round_price(symbol, sl),
            "tp": round_price(symbol, target),
            "confidence": min(buy_score, 95),
            "trend": trend,
            "phase": phase,
            "level": "support" if zone <= 0.30 else "continuation",
            "momentum": "strong" if rsi14 > 55 else "medium",
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
        target = entry - (sl - entry) * 2

        return {
            "market": symbol,
            "type": "SELL",
            "entry": round_price(symbol, entry),
            "sl": round_price(symbol, sl),
            "tp": round_price(symbol, target),
            "confidence": min(sell_score, 95),
            "trend": trend,
            "phase": phase,
            "level": "resistance" if zone >= 0.70 else "continuation",
            "momentum": "strong" if rsi14 < 45 else "medium",
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
        "reason": "RIGA FX v7 confirmations below 70"
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
        "status": "RIGA FX v7 FINAL LIVE",
        "features": [
            "forex + gold scan",
            "TwelveData + Yahoo fallback",
            "candlestick grading",
            "pattern confirmation",
            "liquidity trap filter",
            "retest approximation",
            "70 confidence rule",
            "1:2 RR"
        ]
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/fx-signal")
def fx_signal(symbol: str = Query("EUR/USD")):
    try:
        source, candles = fetch_data(symbol)
        result = riga_fx_v7_logic(symbol, candles)
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
def fx_scan(pairs: str = Query(None)):
    symbols = [normalize_symbol(x) for x in pairs.split(",")] if pairs else DEFAULT_PAIRS

    results = []

    for symbol in symbols:
        try:
            source, candles = fetch_data(symbol)
            result = riga_fx_v7_logic(symbol, candles)
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
        "scan_type": "RIGA_FX_V7_FINAL",
        "total_scanned": len(results),
        "best_trade": select_best_trade(results),
        "all_results": results
    }
