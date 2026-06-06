import os
import re
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

WATCHLIST = ['DIS', 'JPM', 'HTZ', 'TMO', 'CAG', 'SPY', 'HOOD', 'BA', 'ORCL']

_ALPACA_KEY = os.getenv('ALPACA_API_KEY', '')
_ALPACA_SECRET = os.getenv('ALPACA_SECRET_KEY', '')
_ALPACA_BASE = 'https://data.alpaca.markets/v1beta1'


def parse_occ_symbol(symbol: str) -> dict:
    """Parse OCC option symbol format: {TICKER}{YYMMDD}{C/P}{8-digit-strike}"""
    m = re.match(r'([A-Z1-9]+)(\d{6})([CP])(\d{8})', symbol)
    if not m:
        return {}
    ticker, date_str, cp, strike_str = m.groups()
    return {
        'ticker': ticker,
        'expiry': datetime.strptime(date_str, '%y%m%d').strftime('%Y-%m-%d'),
        'strike': int(strike_str) / 1000.0,
        'type': 'call' if cp == 'C' else 'put',
    }


def get_spot_price(ticker: str) -> float:
    try:
        hist = yf.Ticker(ticker).history(period='1d')
        return float(hist['Close'].iloc[-1]) if not hist.empty else 0.0
    except Exception:
        return 0.0


def get_expirations(ticker: str) -> list:
    try:
        return list(yf.Ticker(ticker).options)
    except Exception:
        return []


def _chain_from_alpaca(ticker: str, expiry: str | None) -> pd.DataFrame:
    headers = {
        'APCA-API-KEY-ID': _ALPACA_KEY,
        'APCA-API-SECRET-KEY': _ALPACA_SECRET,
    }
    params = {}
    if expiry:
        params['expiration_date'] = expiry

    resp = requests.get(
        f'{_ALPACA_BASE}/options/chain/{ticker}',
        headers=headers, params=params, timeout=10,
    )
    resp.raise_for_status()
    snapshots = resp.json().get('snapshots', {})

    rows = []
    for symbol, snap in snapshots.items():
        parsed = parse_occ_symbol(symbol)
        if not parsed:
            continue
        greeks = snap.get('greeks') or {}
        quote = snap.get('latestQuote') or {}
        rows.append({
            'symbol': symbol,
            **parsed,
            'bid': quote.get('bp') or 0.0,
            'ask': quote.get('ap') or 0.0,
            'iv': snap.get('impliedVolatility') or 0.0,
            'delta': greeks.get('delta') or 0.0,
            'gamma': greeks.get('gamma') or 0.0,
            'theta': greeks.get('theta') or 0.0,
            'vega': greeks.get('vega') or 0.0,
            'open_interest': snap.get('openInterest') or 0,
        })

    return pd.DataFrame(rows)


def _chain_from_yfinance(ticker: str) -> pd.DataFrame:
    tk = yf.Ticker(ticker)
    expirations = tk.options
    if not expirations:
        return pd.DataFrame()

    expiry = expirations[0]
    chain = tk.option_chain(expiry)
    calls = chain.calls.assign(type='call')
    puts = chain.puts.assign(type='put')
    df = pd.concat([calls, puts], ignore_index=True)
    df['ticker'] = ticker
    df['expiry'] = expiry

    df = df.rename(columns={'impliedVolatility': 'iv', 'openInterest': 'open_interest'})
    for col in ['delta', 'gamma', 'theta', 'vega']:
        df[col] = None

    keep = ['ticker', 'strike', 'expiry', 'type', 'bid', 'ask', 'iv',
            'delta', 'gamma', 'theta', 'vega', 'open_interest']
    return df[[c for c in keep if c in df.columns]]


def get_options_chain(ticker: str, expiry: str | None = None) -> pd.DataFrame:
    if _ALPACA_KEY and _ALPACA_SECRET:
        try:
            df = _chain_from_alpaca(ticker, expiry)
            if not df.empty:
                return df
        except Exception:
            pass
    return _chain_from_yfinance(ticker)


def get_iv_rank(ticker: str) -> float:
    """IV rank approximated from 52-week rolling 30-day realized volatility."""
    try:
        hist = yf.Ticker(ticker).history(period='1y')
        if hist.empty or len(hist) < 31:
            return 0.0
        rv = hist['Close'].pct_change().dropna().rolling(30).std() * (252 ** 0.5)
        rv = rv.dropna()
        lo, hi, cur = rv.min(), rv.max(), rv.iloc[-1]
        if hi == lo:
            return 50.0
        return round(float((cur - lo) / (hi - lo) * 100), 1)
    except Exception:
        return 0.0
