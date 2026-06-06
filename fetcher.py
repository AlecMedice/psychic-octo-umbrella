import os
import re
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

WATCHLIST = ['DIS', 'JPM', 'HTZ', 'TMO', 'CAG', 'SPY', 'HOOD', 'BA', 'ORCL']

_ALPACA_KEY    = os.getenv('ALPACA_API_KEY', '')
_ALPACA_SECRET = os.getenv('ALPACA_SECRET_KEY', '')
_ALPACA_BASE   = 'https://data.alpaca.markets/v1beta1'


def alpaca_configured() -> bool:
    return bool(_ALPACA_KEY and _ALPACA_SECRET)


def parse_occ_symbol(symbol: str) -> dict:
    """Parse OCC option symbol: {TICKER}{YYMMDD}{C/P}{8-digit-strike}"""
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
        greeks    = snap.get('greeks') or {}
        quote     = snap.get('latestQuote') or {}
        trade     = snap.get('latestTrade') or {}
        daily_bar = snap.get('dailyBar') or {}
        rows.append({
            'symbol':       symbol,
            **parsed,
            'bid':          quote.get('bp') or 0.0,
            'ask':          quote.get('ap') or 0.0,
            'last':         trade.get('p') or 0.0,
            'volume':       daily_bar.get('v') or 0,
            'iv':           snap.get('impliedVolatility') or 0.0,
            'delta':        greeks.get('delta') or 0.0,
            'gamma':        greeks.get('gamma') or 0.0,
            'theta':        greeks.get('theta') or 0.0,
            'vega':         greeks.get('vega') or 0.0,
            'open_interest': snap.get('openInterest') or 0,
        })

    return pd.DataFrame(rows)


def _chain_from_yfinance(ticker: str, expiry: str | None = None) -> pd.DataFrame:
    try:
        tk = yf.Ticker(ticker)
        expirations = tk.options
        if not expirations:
            return pd.DataFrame()

        exp = expiry if expiry in expirations else expirations[0]
        chain = tk.option_chain(exp)
        calls = chain.calls.assign(type='call')
        puts  = chain.puts.assign(type='put')
        df = pd.concat([calls, puts], ignore_index=True)
        df['ticker'] = ticker
        df['expiry'] = exp

        df = df.rename(columns={
            'impliedVolatility': 'iv',
            'openInterest':      'open_interest',
            'lastPrice':         'last',
        })
        for col in ['delta', 'gamma', 'theta', 'vega']:
            df[col] = None

        keep = ['ticker', 'strike', 'expiry', 'type',
                'bid', 'ask', 'last', 'volume',
                'open_interest', 'iv',
                'delta', 'gamma', 'theta', 'vega']
        return df[[c for c in keep if c in df.columns]]
    except Exception:
        return pd.DataFrame()


def get_options_chain(ticker: str, expiry: str | None = None) -> pd.DataFrame:
    if alpaca_configured():
        try:
            df = _chain_from_alpaca(ticker, expiry)
            if not df.empty:
                return df
        except Exception:
            pass
    return _chain_from_yfinance(ticker, expiry)


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


# Chart period → (yfinance kwargs) mapping
CHART_PERIODS = {
    '1D':  dict(period='1d',  interval='5m'),
    '3D':  dict(start=(datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d'), interval='15m'),
    '1W':  dict(start=(datetime.now() - timedelta(weeks=1)).strftime('%Y-%m-%d'), interval='30m'),
    '1M':  dict(period='1mo', interval='1h'),
    '3M':  dict(period='3mo', interval='1d'),
    '6M':  dict(period='6mo', interval='1d'),
}


def get_price_history(ticker: str, period_label: str) -> pd.DataFrame:
    try:
        kwargs = CHART_PERIODS.get(period_label, CHART_PERIODS['1D'])
        return yf.Ticker(ticker).history(**kwargs)
    except Exception:
        return pd.DataFrame()


def _time_ago(pub_ts: float = 0, pub_date: str = '') -> str:
    try:
        now = datetime.now(timezone.utc).timestamp()
        if pub_ts:
            diff = now - pub_ts
        elif pub_date:
            dt = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
            diff = now - dt.timestamp()
        else:
            return ''
        if diff < 3600:
            return f"{int(diff / 60)}m ago"
        if diff < 86400:
            return f"{int(diff / 3600)}h ago"
        return f"{int(diff / 86400)}d ago"
    except Exception:
        return ''


def get_news(ticker: str, max_items: int = 10) -> list:
    """Return recent news articles for ticker (yfinance, no API key needed)."""
    try:
        raw = yf.Ticker(ticker).news or []
        items = []
        for article in raw[:max_items]:
            # yfinance ≥0.2.50 wraps fields under 'content'; older versions are flat
            c = article.get('content', article)
            title = c.get('title', '')
            url = (
                (c.get('clickThroughUrl') or {}).get('url')
                or (c.get('canonicalUrl') or {}).get('url')
                or c.get('link', '')
            )
            provider = c.get('provider', {})
            publisher = (
                provider.get('displayName', '') if isinstance(provider, dict)
                else c.get('publisher', '')
            )
            pub_ts   = c.get('providerPublishTime', 0)
            pub_date = c.get('pubDate', '')
            summary  = c.get('summary', c.get('description', ''))

            if title and url:
                items.append({
                    'title':     title,
                    'url':       url,
                    'publisher': publisher,
                    'when':      _time_ago(pub_ts, pub_date),
                    'summary':   summary[:140] if summary else '',
                })
        return items
    except Exception:
        return []
