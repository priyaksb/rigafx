import os
import requests
import yfinance as yf
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

app = FastAPI(title="RIGA FX Backend (Twelve + Yahoo Gold Backup)", version="2.3.0")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
DEFAULT_INTERVAL = "5min"

DEFAULT_PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD",
    "NZD/USD", "USD/CAD", "EUR/JPY", "GBP/JPY", "EUR/GBP",
    "XAU/USD", "BTC/USD", "ETH/USD"
]

YAHOO_SYMBOLS = {
    "XAU/USD": ["GC=F", "XAUUSD=X"],
    "BTC/USD": ["BTC-USD"],
    "ETH/USD": ["ETH-USD"],
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
    return s


def safe_float(value):
    try:
        if hasattr(value, "iloc"):
            value = value.iloc[0]
        return float(value)
    except Exception:
        return None


def fetch_yahoo(symbol: str):
    yahoo_symbols = YAHOO_SYMBOLS.get(normalize_symbol(symbol))

    if not yahoo_symbols:
        raise Exception(f"No Yahoo symbol for {symbol}")

    last_error = None

    for yahoo_symbol in yahoo_symbols:
        try:
            df = yf.download(
                yahoo_symbol,
                period="5d",
                interval="5m",
                progress=False,
                auto_adjust=False
            )

            if df.empty:
                raise Exception(f"Yahoo empty data for {yahoo_symbol}")

            candles = []

            for _, row in df.tail(120).iterrows():
                candle = {
                    "open": safe_float(row["Open"]),
                    "high": safe_float(row["High"]),
                    "low": safe_float(row["Low"]),
                    "close": safe_float(row["Close"]),
                }

                if None not in candle.values():
                    candles.append(candle)

            if len(candles) >= 40:
                return yahoo_symbol, candles

            raise Exception(f"Not enough candles from {yahoo_symbol}")

        except Exception as e:
            last_error = e
            print(f"Yahoo failed for {yahoo_symbol}:", e)

    raise Exception(f"All Yahoo symbols failed for {symbol}: {last_error}")


def fetch_data(symbol: str):
    try:
        if not TWELVEDATA_API_KEY:
            raise Exception("No TwelveData API key")

        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": normalize_symbol(symbol),
            "interval": DEFAULT_INTERVAL,
            "outputsize": 120,
            "apikey": TWELVEDATA_API_KEY,
        }

        r = requests.get(url, params=params, timeout=15)
        data = r.json()

        if r.status_code == 200 and "values" in data:
            candles = []

            for row in reversed(data["values"]):
                candle = {
                    "open": safe_float(row["open"]),
                    "high": safe_float(row["high"]),
                    "low": safe_float(row["low"]),
                    "close": safe_float(row["close"]),
                }

                if None not in candle.values():
                    candles.append(candle)

            if len(candles) >= 40:
                return "TwelveData", candles

        print("TwelveData failed:", data)

    except Exception as e:
        print("TwelveData error:", e)

    try:
        yahoo_symbol, candles = fetch_yahoo(symbol)
        return f"Yahoo Finance ({yahoo_symbol})", candles

    except Exception as e:
        print("Yahoo error:", e)

    raise Exception("All data sources failed")


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


def get_sl_tp(symbol, last, trade_type):
    symbol = normalize_symbol(symbol)

    if symbol == "XAU/USD":
        sl_gap = 3.0
        tp_gap = 6.0
        digits = 2
    elif symbol in ["BTC/USD", "ETH/USD"]:
        sl_gap = last * 0.005
        tp_gap = last * 0.010
        digits = 2
    elif "JPY" in symbol:
        sl_gap = 0.20
        tp_gap = 0.40
        digits = 3
    else:
        sl_gap = 0.0020
        tp_gap = 0.0040
        digits = 5

    if trade_type == "BUY":
        sl = last - sl_gap
        tp = last + tp_gap
    else:
        sl = last + sl_gap
        tp = last - tp_gap

    return round(sl, digits), round(tp, digits), round(last, digits)


def analyze(symbol, candles):
    if len(candles) < 40:
        return {"market": symbol, "signal": "NO TRADE"}

    closes = [c["close"] for c in candles]
    last = closes[-1]

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    r = rsi(closes)

    if e9 is None or e21 is None or r is None:
        return {"market": symbol, "signal": "NO TRADE"}

    if e9 > e21 and r > 55:
        sl, tp, entry = get_sl_tp(symbol, last, "BUY")
        return {
            "market": symbol,
            "type": "BUY",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "confidence": 80
        }

    if e9 < e21 and r < 45:
        sl, tp, entry = get_sl_tp(symbol, last, "SELL")
        return {
            "market": symbol,
            "type": "SELL",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "confidence": 80
        }

    return {"market": symbol, "signal": "NO TRADE"}


@app.get("/")
def root():
    return {"status": "RIGA FX running", "version": "2.3.0"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/fx-signal")
def fx_signal(symbol: str = "EUR/USD"):
    try:
        source, candles = fetch_data(symbol)
        result = analyze(symbol, candles)
        result["data_source"] = source
        return result
    except Exception as e:
        return {
            "market": symbol,
            "signal": "NO TRADE",
            "error": str(e)
        }


@app.get("/fx-scan")
def scan():
    results = []

    for pair in DEFAULT_PAIRS:
        try:
            source, candles = fetch_data(pair)
            result = analyze(pair, candles)
            result["data_source"] = source
            results.append(result)

        except Exception as e:
            results.append({
                "market": pair,
                "signal": "NO TRADE",
                "error": str(e)
            })

    return results
