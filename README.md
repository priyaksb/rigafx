# RIGA FX Twelve Data Backend

Signal-only forex backend for a Personal Custom GPT Action.

## What this does
- Uses Twelve Data API key only
- Runs on Render
- Provides live forex candles
- Generates RIGA FX BUY / SELL / NO TRADE signals
- No order placement

## Files
- `main.py` - FastAPI backend
- `openapi_schema_for_custom_gpt.json` - paste into Custom GPT Actions
- `GPT_INSTRUCTIONS_RIGA_FX.txt` - paste into Custom GPT instructions
- `requirements.txt` - Python dependencies
- `render.yaml` - Render deploy config
- `.env.example` - environment variable template

## Render Environment Variables
Add these in Render:

```env
RIGA_ACTION_TOKEN=Krushna123
TWELVEDATA_API_KEY=YOUR_TWELVEDATA_API_KEY_HERE
DEFAULT_INTERVAL=5min
MIN_CONFIDENCE=70
MIN_RR=1.5
```

## Render commands
Build command:
```bash
pip install -r requirements.txt
```

Start command:
```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

## Test URLs
After deploy, replace `YOUR-RENDER-APP`:

```text
https://YOUR-RENDER-APP.onrender.com/health
https://YOUR-RENDER-APP.onrender.com/fx-signal?symbol=EUR/USD&interval=5min
https://YOUR-RENDER-APP.onrender.com/fx-scan?interval=5min
```

## Custom GPT Action
1. Open `openapi_schema_for_custom_gpt.json`
2. Replace `https://YOUR-RENDER-APP.onrender.com` with your real Render URL
3. Paste into GPT Builder → Actions → Schema
4. Authentication: API Key / Bearer
5. Bearer token value: your `RIGA_ACTION_TOKEN`, e.g. `Krushna123`

## Important
This backend is for educational signal generation only. It does not place trades.
