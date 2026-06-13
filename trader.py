"""
Paper trading engine — Alpaca paper API + Gemini trade decisions.

The agent scans every ticker in WATCHLIST every 5 minutes during market hours.
For each ticker it:
  1. Collects signals (IV rank, opportunity score, news, greeks)
  2. Asks Gemini to decide: buy_call | buy_put | sell_call | sell_put |
                             buy_equity | sell_equity | close | hold
  3. Executes via Alpaca paper trading API
  4. Logs every decision (including holds) to trades.json

Position sizing: $1,000 per trade.
Options: ATM contract on nearest expiry with adequate open interest.
Equities: watched as context; traded only when options signal is strong.

Required env vars:
    ALPACA_API_KEY    — paper trading key
    ALPACA_SECRET_KEY — paper trading secret
    GEMINI_API_KEY    — for trade decisions

Set ALPACA_PAPER=true (default) to use paper endpoint.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from fetcher import (
    WATCHLIST, get_spot_price, get_iv_rank,
    get_expirations, get_options_chain, get_news, get_price_history,
)
from greeks_calc import enrich_with_greeks, VOLLIB_OK
from signals import opportunity_score, iv_vs_rv, put_call_ratios, momentum

try:
    from google import genai
    from google.genai import types as gtypes
    _GEMINI_OK = True
except ImportError:
    _GEMINI_OK = False

log = logging.getLogger(__name__)

TRADE_LOG   = Path('trades.json')
BUDGET      = 1_000.0          # dollars per trade
MIN_OI      = 50               # min open interest to consider a contract
_POLL_SEC   = 300              # 5 minutes
_RATE_LIMIT = 4.0              # seconds between Gemini calls
_last_gemini: float = 0.0

# ── Alpaca helpers ─────────────────────────────────────────────────────────────

def _alpaca_base() -> str:
    paper = os.getenv('ALPACA_PAPER', 'true').lower() != 'false'
    return 'https://paper-api.alpaca.markets' if paper else 'https://api.alpaca.markets'

def _alpaca_headers() -> dict:
    return {
        'APCA-API-KEY-ID':     os.getenv('ALPACA_API_KEY', ''),
        'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET_KEY', ''),
        'Content-Type':        'application/json',
    }

def alpaca_configured() -> bool:
    return bool(os.getenv('ALPACA_API_KEY') and os.getenv('ALPACA_SECRET_KEY'))

def _alpaca_get(path: str, params: dict | None = None) -> dict:
    resp = requests.get(
        _alpaca_base() + path, headers=_alpaca_headers(), params=params, timeout=10,
    )
    resp.raise_for_status()
    return resp.json()

def _alpaca_post(path: str, body: dict) -> dict:
    resp = requests.post(
        _alpaca_base() + path, headers=_alpaca_headers(), json=body, timeout=10,
    )
    resp.raise_for_status()
    return resp.json()

def _alpaca_delete(path: str) -> dict:
    resp = requests.delete(
        _alpaca_base() + path, headers=_alpaca_headers(), timeout=10,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {}


# ── Account + positions ────────────────────────────────────────────────────────

def get_account() -> dict:
    try:
        return _alpaca_get('/v2/account')
    except Exception as e:
        log.warning('Alpaca account error: %s', e)
        return {}

def get_positions() -> list[dict]:
    try:
        return _alpaca_get('/v2/positions')
    except Exception as e:
        log.warning('Alpaca positions error: %s', e)
        return []

def get_orders(status: str = 'open') -> list[dict]:
    try:
        return _alpaca_get('/v2/orders', {'status': status, 'limit': 50})
    except Exception as e:
        log.warning('Alpaca orders error: %s', e)
        return []


# ── Trade log ──────────────────────────────────────────────────────────────────

def _load_trades() -> list:
    try:
        return json.loads(TRADE_LOG.read_text()) if TRADE_LOG.exists() else []
    except Exception:
        return []

def _save_trade(entry: dict):
    trades = _load_trades()
    trades.append(entry)
    try:
        TRADE_LOG.write_text(json.dumps(trades[-500:], indent=2))
    except Exception as e:
        log.warning('Could not save trade log: %s', e)

def get_trade_log() -> list:
    return _load_trades()


# ── Options contract lookup ────────────────────────────────────────────────────

def _find_atm_contract(ticker: str, expiry: str, opt_type: str, spot: float) -> dict | None:
    """Find nearest ATM contract on Alpaca with adequate open interest."""
    try:
        params = {
            'underlying_symbols': ticker,
            'expiration_date':    expiry,
            'type':               opt_type,
            'limit':              100,
        }
        data = _alpaca_get('/v2/options/contracts', params)
        contracts = data if isinstance(data, list) else data.get('option_contracts', [])
        if not contracts:
            return None

        # Filter by open interest and find ATM
        eligible = [c for c in contracts if (c.get('open_interest') or 0) >= MIN_OI]
        if not eligible:
            eligible = contracts

        atm = min(eligible, key=lambda c: abs(float(c.get('strike_price', spot)) - spot))
        return atm
    except Exception as e:
        log.warning('Contract lookup failed for %s %s %s: %s', ticker, expiry, opt_type, e)
        return None


# ── Order execution ────────────────────────────────────────────────────────────

def _place_option_order(symbol: str, qty: int, side: str, reason: str) -> dict | None:
    """Place a market order for an options contract."""
    try:
        body = {
            'symbol':        symbol,
            'qty':           str(qty),
            'side':          side,
            'type':          'market',
            'time_in_force': 'day',
        }
        result = _alpaca_post('/v2/orders', body)
        log.info('Option order placed: %s %s x%d — %s', side, symbol, qty, reason)
        return result
    except Exception as e:
        log.warning('Option order failed (%s %s): %s', side, symbol, e)
        return None

def _place_equity_order(ticker: str, side: str, dollars: float, reason: str) -> dict | None:
    """Place a notional (dollar-value) equity market order."""
    try:
        body = {
            'symbol':           ticker,
            'notional':         str(round(dollars, 2)),
            'side':             side,
            'type':             'market',
            'time_in_force':    'day',
        }
        result = _alpaca_post('/v2/orders', body)
        log.info('Equity order placed: %s %s $%.0f — %s', side, ticker, dollars, reason)
        return result
    except Exception as e:
        log.warning('Equity order failed (%s %s): %s', side, ticker, e)
        return None

def _close_position(symbol: str) -> dict | None:
    try:
        result = _alpaca_delete(f'/v2/positions/{symbol}')
        log.info('Position closed: %s', symbol)
        return result
    except Exception as e:
        log.warning('Close position failed (%s): %s', symbol, e)
        return None


# ── Gemini trade decision ──────────────────────────────────────────────────────

_VALID_ACTIONS = {'buy_call', 'buy_put', 'sell_call', 'sell_put',
                  'buy_equity', 'sell_equity', 'close', 'hold'}

def _gemini_decide(ticker: str, ctx: dict) -> dict:
    """Ask Gemini to make a trade decision. Returns {action, confidence, reasoning}."""
    global _last_gemini

    if not _GEMINI_OK or not os.getenv('GEMINI_API_KEY'):
        return {'action': 'hold', 'confidence': 0, 'reasoning': 'Gemini not configured.'}

    elapsed = time.time() - _last_gemini
    if elapsed < _RATE_LIMIT:
        time.sleep(_RATE_LIMIT - elapsed)

    score      = ctx.get('score', 0)
    direction  = ctx.get('direction', 'neutral')
    iv_rank    = ctx.get('iv_rank', 0)
    spot       = ctx.get('spot', 0)
    expiry     = ctx.get('expiry', '')
    atm_strike = ctx.get('atm_strike', 0)
    iv_pct     = ctx.get('iv_pct', None)
    delta      = ctx.get('delta', None)
    theta      = ctx.get('theta', None)
    news       = ctx.get('news', [])
    notes      = ctx.get('notes', [])
    positions  = ctx.get('open_positions', [])

    news_block = '\n'.join(f'- {h}' for h in news[:5]) if news else 'No recent news.'
    notes_block = '\n'.join(f'- {n}' for n in notes) if notes else ''
    pos_syms = [p.get('symbol', '') for p in positions if ticker.upper() in p.get('symbol', '')]
    pos_block = f"Open positions in {ticker}: {', '.join(pos_syms)}" if pos_syms else f"No open positions in {ticker}."

    prompt = f"""You are an active options paper trader. Analyze the data below and decide on ONE action.

Ticker: {ticker}
Spot: ${spot:.2f}
IV Rank: {iv_rank:.1f}
Opportunity Score: {score}/10 ({direction})
Signal notes:
{notes_block}

Nearest expiry: {expiry}
ATM strike: ${atm_strike:.2f}
{"IV: " + str(iv_pct) + "%" if iv_pct else ""}
{"Delta: " + str(delta) if delta else ""}
{"Theta: $" + str(theta) + "/day" if theta else ""}

Recent news:
{news_block}

{pos_block}

Budget per trade: $1,000. You are paper trading (no real money at risk).

Respond with JSON only:
{{
  "action": one of [buy_call, buy_put, sell_call, sell_put, buy_equity, sell_equity, close, hold],
  "confidence": integer 1-10,
  "reasoning": "one or two sentences"
}}

Rules:
- buy_call/buy_put: buy a long option contract
- sell_call/sell_put: sell (write) an option contract
- buy_equity/sell_equity: trade the underlying stock
- close: close an existing position in this ticker
- hold: do nothing
- Prefer options over equities. Use equity only as a hedge or when no options signal is clear.
- Only suggest close if there is an open position.
- Be active — lean toward trading when score >= 3."""

    try:
        client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
        config = gtypes.GenerateContentConfig(
            response_mime_type='application/json',
            max_output_tokens=300,
        )
        _last_gemini = time.time()
        resp = client.models.generate_content(
            model='gemini-2.0-flash', contents=prompt, config=config,
        )
        data = json.loads(resp.text)
        action = data.get('action', 'hold')
        if action not in _VALID_ACTIONS:
            action = 'hold'
        return {
            'action':     action,
            'confidence': int(data.get('confidence', 5)),
            'reasoning':  data.get('reasoning', ''),
        }
    except Exception as e:
        log.warning('Gemini decision error for %s: %s', ticker, e)
        return {'action': 'hold', 'confidence': 0, 'reasoning': f'Error: {e}'}


# ── Per-ticker trade cycle ─────────────────────────────────────────────────────

def _trade_ticker(ticker: str, open_positions: list):
    try:
        spot    = get_spot_price(ticker)
        iv_rank = get_iv_rank(ticker)
        exps    = get_expirations(ticker)
        expiry  = exps[0] if exps else None
        news    = [n['title'] for n in get_news(ticker, max_per_source=3)[:5]]
        hist    = get_price_history(ticker, '1Y')

        chain_df  = get_options_chain(ticker, expiry) if expiry else None
        atm_strike = None
        iv_pct     = None
        delta      = None
        theta      = None

        if chain_df is not None and not chain_df.empty:
            if VOLLIB_OK:
                chain_df = enrich_with_greeks(chain_df, spot)
            calls = chain_df[chain_df['type'] == 'call']
            if not calls.empty:
                atm_strike = min(calls['strike'].unique(), key=lambda s: abs(s - spot))
                atm_row = calls[calls['strike'] == atm_strike].iloc[0]
                iv_raw  = atm_row.get('iv')
                iv_pct  = round(float(iv_raw) * 100, 1) if iv_raw else None
                delta   = round(float(atm_row['delta']), 3) if atm_row.get('delta') is not None else None
                theta   = round(float(atm_row['theta']), 4) if atm_row.get('theta') is not None else None

            opp  = opportunity_score(
                iv_rank,
                iv_vs_rv(chain_df, hist),
                put_call_ratios(chain_df),
                momentum(hist),
            )
        else:
            opp = {'score': 0, 'direction': 'neutral', 'notes': []}

        ctx = {
            'score':          opp['score'],
            'direction':      opp['direction'],
            'notes':          opp['notes'],
            'iv_rank':        iv_rank,
            'spot':           spot,
            'expiry':         expiry or '',
            'atm_strike':     atm_strike or spot,
            'iv_pct':         iv_pct,
            'delta':          delta,
            'theta':          theta,
            'news':           news,
            'open_positions': open_positions,
        }

        decision = _gemini_decide(ticker, ctx)
        action     = decision['action']
        confidence = decision['confidence']
        reasoning  = decision['reasoning']

        order_result = None
        executed     = False

        if action == 'hold':
            pass

        elif action in ('buy_call', 'buy_put', 'sell_call', 'sell_put'):
            opt_type = 'call' if 'call' in action else 'put'
            side     = 'buy'  if action.startswith('buy') else 'sell'
            if expiry and atm_strike:
                contract = _find_atm_contract(ticker, expiry, opt_type, spot)
                if contract:
                    symbol   = contract.get('symbol') or contract.get('id', '')
                    mark     = float(contract.get('close_price') or contract.get('last_price') or 1.0)
                    qty      = max(1, int(BUDGET / (mark * 100)))
                    order_result = _place_option_order(symbol, qty, side, reasoning)
                    executed = order_result is not None
                else:
                    reasoning += ' (no contract found)'

        elif action in ('buy_equity', 'sell_equity'):
            side = 'buy' if action == 'buy_equity' else 'sell'
            order_result = _place_equity_order(ticker, side, BUDGET, reasoning)
            executed = order_result is not None

        elif action == 'close':
            pos_syms = [p['symbol'] for p in open_positions
                        if ticker.upper() in p.get('symbol', '').upper()]
            for sym in pos_syms:
                _close_position(sym)
            if pos_syms:
                executed = True

        _save_trade({
            'ts':         datetime.now(timezone.utc).isoformat(),
            'ticker':     ticker,
            'action':     action,
            'confidence': confidence,
            'reasoning':  reasoning,
            'spot':       spot,
            'iv_rank':    iv_rank,
            'score':      opp['score'],
            'expiry':     expiry,
            'atm_strike': atm_strike,
            'executed':   executed,
            'order':      order_result,
        })

        log.info('[%s] %s (conf %d) — %s', ticker, action.upper(), confidence, reasoning)

    except Exception as e:
        log.error('Trade cycle error for %s: %s', ticker, e)


# ── Main trading loop ──────────────────────────────────────────────────────────

def run_trading_cycle():
    """Scan all watchlist tickers and make trade decisions. Call from scheduler."""
    if not alpaca_configured():
        log.warning('Alpaca not configured — skipping trade cycle.')
        return
    if not _GEMINI_OK or not os.getenv('GEMINI_API_KEY'):
        log.warning('Gemini not configured — skipping trade cycle.')
        return

    log.info('--- Trade cycle start ---')
    positions = get_positions()

    for ticker in WATCHLIST:
        _trade_ticker(ticker, positions)
        time.sleep(1)   # small delay between tickers to avoid rate limits

    log.info('--- Trade cycle complete ---')
