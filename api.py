"""
Lightweight JSON API for widget consumption (iPad Scriptable, Android KWGT/Tasker).

Run locally:
    uvicorn api:app --host 0.0.0.0 --port 8502 --reload

Endpoints:
    GET /                        health check
    GET /api/watchlist           spot + IV rank for every ticker
    GET /api/ticker/{ticker}     full detail + ATM call/put
    GET /api/news/{ticker}       latest headlines (limit=N, default 10)
"""

import time
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from cachetools import TTLCache

from fetcher import (
    WATCHLIST,
    get_spot_price, get_iv_rank, get_price_history,
    get_options_chain, get_expirations, get_news,
)

app = FastAPI(title="Options Evaluator API", version="1.0", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Simple TTL caches — keeps widget requests snappy without hammering yfinance
_watchlist_cache: TTLCache = TTLCache(maxsize=1,   ttl=60)
_ticker_cache:    TTLCache = TTLCache(maxsize=50,  ttl=60)
_news_cache:      TTLCache = TTLCache(maxsize=50,  ttl=300)


def _spot_and_change(ticker: str) -> tuple[float, float, float]:
    hist = get_price_history(ticker, '1D')
    if not hist.empty:
        spot    = float(hist['Close'].iloc[-1])
        open_px = float(hist['Open'].iloc[0])
    else:
        spot = open_px = get_spot_price(ticker)
    chg     = spot - open_px
    chg_pct = chg / open_px * 100 if open_px else 0.0
    return round(spot, 2), round(chg, 2), round(chg_pct, 2)


def _atm_option(chain, spot: float, opt_type: str, expiry: str) -> dict | None:
    side = chain[chain['type'] == opt_type].copy()
    if side.empty:
        return None
    strikes  = sorted(side['strike'].unique())
    atm      = min(strikes, key=lambda s: abs(s - spot))
    row      = side[side['strike'] == atm].iloc[0]
    bid      = float(row.get('bid') or 0)
    ask      = float(row.get('ask') or 0)
    mark     = round((bid + ask) / 2, 2)
    iv_raw   = row.get('iv')
    delta    = row.get('delta')
    theta    = row.get('theta')
    return {
        'strike':  atm,
        'expiry':  expiry,
        'mark':    mark,
        'bid':     round(bid, 2),
        'ask':     round(ask, 2),
        'iv_pct':  round(float(iv_raw) * 100, 1) if iv_raw else None,
        'delta':   round(float(delta), 3)         if delta  else None,
        'theta':   round(float(theta), 4)         if theta  else None,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get('/')
def health():
    return {'status': 'ok', 'ts': datetime.now(timezone.utc).isoformat()}


@app.get('/api/watchlist')
def watchlist_summary():
    """Spot price, daily change %, and IV rank for every ticker in the watchlist."""
    if 'data' in _watchlist_cache:
        return _watchlist_cache['data']

    results = []
    for ticker in WATCHLIST:
        spot, chg, chg_pct = _spot_and_change(ticker)
        results.append({
            'ticker':     ticker,
            'spot':       spot,
            'change':     chg,
            'change_pct': chg_pct,
            'iv_rank':    round(get_iv_rank(ticker), 1),
        })

    _watchlist_cache['data'] = results
    return results


@app.get('/api/ticker/{ticker}')
def ticker_detail(ticker: str):
    """Full snapshot: price, IV rank, and ATM call + put for the nearest expiry."""
    ticker = ticker.upper()
    if ticker not in WATCHLIST:
        raise HTTPException(status_code=404, detail=f'{ticker} not in watchlist')

    if ticker in _ticker_cache:
        return _ticker_cache[ticker]

    spot, chg, chg_pct = _spot_and_change(ticker)
    iv_rank = round(get_iv_rank(ticker), 1)

    exps     = get_expirations(ticker)
    expiry   = exps[0] if exps else None
    atm_call = atm_put = None

    if expiry:
        chain = get_options_chain(ticker, expiry)
        if not chain.empty:
            atm_call = _atm_option(chain, spot, 'call', expiry)
            atm_put  = _atm_option(chain, spot, 'put',  expiry)

    result = {
        'ticker':     ticker,
        'spot':       spot,
        'change':     chg,
        'change_pct': chg_pct,
        'iv_rank':    iv_rank,
        'expiry':     expiry,
        'atm_call':   atm_call,
        'atm_put':    atm_put,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }
    _ticker_cache[ticker] = result
    return result


@app.get('/api/news/{ticker}')
def ticker_news(ticker: str, limit: int = Query(default=10, ge=1, le=30)):
    """Latest news headlines with sentiment for a ticker."""
    ticker = ticker.upper()
    if ticker not in WATCHLIST:
        raise HTTPException(status_code=404, detail=f'{ticker} not in watchlist')

    cache_key = f'{ticker}:{limit}'
    if cache_key in _news_cache:
        return _news_cache[cache_key]

    items = get_news(ticker, max_per_source=5)[:limit]
    result = [
        {
            'title':     i['title'],
            'url':       i['url'],
            'source':    i['source'],
            'publisher': i['publisher'],
            'sentiment': i['sentiment'],
            'when':      i['when'],
        }
        for i in items
    ]
    _news_cache[cache_key] = result
    return result
