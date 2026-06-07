import os
import re
import calendar
import requests
import pandas as pd
import yfinance as yf
import feedparser
from datetime import datetime, timedelta, timezone, date
from dotenv import load_dotenv

load_dotenv()

WATCHLIST = ['DIS', 'JPM', 'HTZ', 'TMO', 'CAG', 'SPY', 'HOOD', 'BA', 'ORCL']

# VIX term-structure symbols (yfinance)
VIX_SYMBOLS = {
    '9D':  '^VIX9D',
    '1M':  '^VIX',
    '3M':  '^VIX3M',
    '6M':  '^VIX6M',
}


def get_vix_term_structure() -> dict:
    """Spot VIX levels across 4 tenors — free via yfinance."""
    result = {}
    for label, sym in VIX_SYMBOLS.items():
        try:
            h = yf.Ticker(sym).history(period='1d')
            result[label] = round(float(h['Close'].iloc[-1]), 2) if not h.empty else None
        except Exception:
            result[label] = None
    return result


def get_fear_greed() -> dict:
    """Fear & Greed Index via feargreedchart.com — free, no key required."""
    try:
        resp = requests.get(
            'https://feargreedchart.com/api/?action=all',
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=8,
        )
        data = resp.json()
        score = float(data.get('score') or data.get('fgi', {}).get('now', {}).get('value', 0))
        rating = (data.get('rating') or data.get('fgi', {}).get('now', {}).get('valueText', '')).title()
        return {'score': round(score, 1), 'rating': rating}
    except Exception:
        return {'score': None, 'rating': ''}


def get_earnings_date(ticker: str) -> str | None:
    """Next earnings date from yfinance calendar."""
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return None
        dates = cal.get('Earnings Date', [])
        if dates:
            d = dates[0]
            return str(d.date()) if hasattr(d, 'date') else str(d)
        return None
    except Exception:
        return None


def get_short_interest(ticker: str) -> dict:
    """FINRA short interest — biweekly, free, no key required."""
    try:
        resp = requests.get(
            'https://api.finra.org/data/group/otcmarket/name/equityShortInterest',
            params={'limit': 1, 'fields': 'issueName,symbolCode,shortInterestQty,daysToCover,settlementDate',
                    'compareFilters': f'symbolCode:eq:{ticker}'},
            headers={'Accept': 'application/json'},
            timeout=10,
        )
        rows = resp.json()
        if rows:
            r = rows[0]
            return {
                'short_interest': int(r.get('shortInterestQty', 0)),
                'days_to_cover':  round(float(r.get('daysToCover', 0)), 1),
                'settlement_date': r.get('settlementDate', ''),
            }
        return {}
    except Exception:
        return {}

_ALPACA_KEY    = os.getenv('ALPACA_API_KEY', '')
_ALPACA_SECRET = os.getenv('ALPACA_SECRET_KEY', '')
_ALPACA_BASE   = 'https://data.alpaca.markets/v1beta1'


def alpaca_configured() -> bool:
    return bool(_ALPACA_KEY and _ALPACA_SECRET)


_TRADIER_TOKEN = os.getenv('TRADIER_TOKEN', '')
_TRADIER_BASE  = (
    'https://sandbox.tradier.com/v1' if os.getenv('TRADIER_SANDBOX', 'true').lower() == 'true'
    else 'https://api.tradier.com/v1'
)


def tradier_configured() -> bool:
    return bool(_TRADIER_TOKEN)


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


def _chain_from_tradier(ticker: str, expiry: str | None) -> pd.DataFrame:
    headers = {
        'Authorization': f'Bearer {_TRADIER_TOKEN}',
        'Accept': 'application/json',
    }

    if not expiry:
        resp = requests.get(
            f'{_TRADIER_BASE}/markets/options/expirations',
            headers=headers, params={'symbol': ticker}, timeout=10,
        )
        resp.raise_for_status()
        dates = (resp.json().get('expirations') or {}).get('date') or []
        if isinstance(dates, str):
            dates = [dates]
        if not dates:
            return pd.DataFrame()
        expiry = dates[0]

    resp = requests.get(
        f'{_TRADIER_BASE}/markets/options/chains',
        headers=headers, params={'symbol': ticker, 'expiration': expiry, 'greeks': 'true'},
        timeout=10,
    )
    resp.raise_for_status()
    options = (resp.json().get('options') or {}).get('option') or []
    if isinstance(options, dict):
        options = [options]

    rows = []
    for o in options:
        greeks = o.get('greeks') or {}
        rows.append({
            'symbol':        o.get('symbol', ''),
            'ticker':        ticker,
            'expiry':        o.get('expiration_date', expiry),
            'strike':        float(o.get('strike') or 0),
            'type':          o.get('option_type', ''),
            'bid':           o.get('bid') or 0.0,
            'ask':           o.get('ask') or 0.0,
            'last':          o.get('last') or 0.0,
            'volume':        o.get('volume') or 0,
            'open_interest': o.get('open_interest') or 0,
            'iv':            greeks.get('mid_iv') or greeks.get('smv_vol') or 0.0,
            'delta':         greeks.get('delta') or 0.0,
            'gamma':         greeks.get('gamma') or 0.0,
            'theta':         greeks.get('theta') or 0.0,
            'vega':          greeks.get('vega') or 0.0,
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
    if tradier_configured():
        try:
            df = _chain_from_tradier(ticker, expiry)
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
            diff = now - float(pub_ts)
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


def _make_item(title, url, publisher, ts=0, pub_date='',
               summary='', source='', sentiment='') -> dict:
    return {
        'title':     title,
        'url':       url,
        'publisher': publisher,
        'when':      _time_ago(ts, pub_date),
        '_ts':       float(ts) if ts else 0.0,
        'summary':   (summary or '')[:160],
        'source':    source,
        'sentiment': sentiment,   # 'Bullish' | 'Bearish' | 'Neutral' | ''
    }


# ── Per-source fetchers ────────────────────────────────────────────────────────

def _news_yfinance(ticker: str, n: int) -> list:
    try:
        raw = yf.Ticker(ticker).news or []
        out = []
        for a in raw[:n]:
            c = a.get('content', a)
            title = c.get('title', '')
            url   = (
                (c.get('clickThroughUrl') or {}).get('url')
                or (c.get('canonicalUrl') or {}).get('url')
                or c.get('link', '')
            )
            provider  = c.get('provider', {})
            publisher = provider.get('displayName', '') if isinstance(provider, dict) else c.get('publisher', '')
            ts        = c.get('providerPublishTime', 0)
            pub_date  = c.get('pubDate', '')
            summary   = c.get('summary', c.get('description', ''))
            if title and url:
                out.append(_make_item(title, url, publisher, ts, pub_date, summary, 'Yahoo Finance'))
        return out
    except Exception:
        return []


def _news_google_rss(ticker: str, n: int) -> list:
    try:
        url  = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
        feed = feedparser.parse(url)
        out  = []
        for e in feed.entries[:n]:
            parsed_time = e.get('published_parsed')
            ts = calendar.timegm(parsed_time) if parsed_time else 0
            # Google News embeds source in title as "Headline - Source"
            raw_title = e.get('title', '')
            if ' - ' in raw_title:
                headline, pub = raw_title.rsplit(' - ', 1)
            else:
                headline, pub = raw_title, 'Google News'
            if headline and e.get('link'):
                out.append(_make_item(headline, e['link'], pub, ts,
                                      summary=e.get('summary', ''), source='Google News'))
        return out
    except Exception:
        return []


def _news_alpha_vantage(ticker: str, n: int) -> list:
    key = os.getenv('ALPHA_VANTAGE_KEY', '')
    if not key:
        return []
    try:
        resp = requests.get(
            'https://www.alphavantage.co/query',
            params=dict(function='NEWS_SENTIMENT', tickers=ticker, apikey=key, limit=50),
            timeout=10,
        )
        out = []
        for a in resp.json().get('feed', [])[:n]:
            ts = 0
            tp = a.get('time_published', '')
            if tp:
                try:
                    dt = datetime.strptime(tp, '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc)
                    ts = dt.timestamp()
                except Exception:
                    pass
            # Ticker-specific sentiment
            tk_sent = next(
                (s for s in a.get('ticker_sentiment', []) if s.get('ticker') == ticker), {}
            )
            sentiment = tk_sent.get('ticker_sentiment_label', '')
            # Normalize to short label
            if 'Bullish' in sentiment:
                sentiment = 'Bullish'
            elif 'Bearish' in sentiment:
                sentiment = 'Bearish'
            else:
                sentiment = 'Neutral' if sentiment else ''
            if a.get('title') and a.get('url'):
                out.append(_make_item(
                    a['title'], a['url'], a.get('source', ''), ts,
                    summary=a.get('summary', ''),
                    source='Alpha Vantage', sentiment=sentiment,
                ))
        return out
    except Exception:
        return []


def _news_finnhub(ticker: str, n: int) -> list:
    key = os.getenv('FINNHUB_KEY', '')
    if not key:
        return []
    try:
        today    = date.today()
        week_ago = today - timedelta(days=7)
        resp = requests.get(
            'https://finnhub.io/api/v1/company-news',
            params=dict(symbol=ticker, **{'from': str(week_ago), 'to': str(today)}, token=key),
            timeout=10,
        )
        out = []
        for a in resp.json()[:n]:
            if a.get('headline') and a.get('url'):
                out.append(_make_item(
                    a['headline'], a['url'], a.get('source', ''),
                    ts=a.get('datetime', 0),
                    summary=a.get('summary', ''), source='Finnhub',
                ))
        return out
    except Exception:
        return []


def _news_reddit_rss(ticker: str, n: int) -> list:
    """Reddit posts via r/stocks and r/wallstreetbets RSS (no API key needed)."""
    out = []
    for subreddit in ('stocks', 'options', 'investing'):
        try:
            url  = f"https://www.reddit.com/r/{subreddit}/search.rss?q={ticker}&sort=new&restrict_sr=true"
            feed = feedparser.parse(url)
            for e in feed.entries[:n]:
                parsed_time = e.get('published_parsed')
                ts = calendar.timegm(parsed_time) if parsed_time else 0
                title = e.get('title', '')
                link  = e.get('link', '')
                if title and link:
                    out.append(_make_item(title, link, f'r/{subreddit}', ts,
                                          summary=e.get('summary', '')[:160],
                                          source='Reddit'))
        except Exception:
            continue
    return out[:n]


def _news_marketaux(ticker: str, n: int) -> list:
    """Marketaux news with per-article sentiment (free: 100 req/day, no CC)."""
    key = os.getenv('MARKETAUX_KEY', '')
    if not key:
        return []
    try:
        resp = requests.get(
            'https://api.marketaux.com/v1/news/all',
            params=dict(
                symbols=ticker,
                filter_entities='true',
                language='en',
                limit=n,
                api_token=key,
            ),
            timeout=10,
        )
        out = []
        for a in resp.json().get('data', [])[:n]:
            pub_date = a.get('published_at', '')
            # Extract sentiment for this specific ticker from entity list
            score = next(
                (e.get('sentiment_score', 0)
                 for e in a.get('entities', []) if e.get('symbol') == ticker),
                0,
            )
            if score > 0.1:
                sentiment = 'Bullish'
            elif score < -0.1:
                sentiment = 'Bearish'
            else:
                sentiment = 'Neutral'
            if a.get('title') and a.get('url'):
                out.append(_make_item(
                    a['title'], a['url'], a.get('source', ''),
                    pub_date=pub_date,
                    summary=a.get('description', ''),
                    source='Marketaux', sentiment=sentiment,
                ))
        return out
    except Exception:
        return []


def _news_apewisdom(ticker: str) -> list:
    """ApeWisdom Reddit sentiment rank — synthesised as a pinned news item."""
    try:
        resp = requests.get(
            'https://api.apewisdom.io/v1.0/trending/all-stocks',
            timeout=10,
        )
        rows = resp.json().get('results', [])
        entry = next((r for r in rows if r.get('ticker') == ticker), None)
        if not entry:
            return []
        rank     = entry.get('rank', '?')
        mentions = entry.get('mentions', 0)
        rank_24h = entry.get('rank_24h_ago')
        if rank_24h and isinstance(rank_24h, int):
            delta = rank_24h - rank
            trend = f"▲{delta}" if delta > 0 else (f"▼{abs(delta)}" if delta < 0 else "–")
        else:
            trend = ''
        title = (
            f"{ticker} is #{rank} on Reddit right now "
            f"({mentions:,} mentions{', ' + trend + ' vs 24h ago' if trend else ''})"
        )
        return [_make_item(title, f'https://apewisdom.io/', 'ApeWisdom',
                           source='Reddit Buzz')]
    except Exception:
        return []


# ── Aggregator ────────────────────────────────────────────────────────────────

def get_news(ticker: str, max_per_source: int = 8) -> list:
    """Fetch from all configured sources, deduplicate, sort by recency."""
    raw: list = []
    raw.extend(_news_yfinance(ticker, max_per_source))
    raw.extend(_news_google_rss(ticker, max_per_source))
    raw.extend(_news_alpha_vantage(ticker, max_per_source))
    raw.extend(_news_finnhub(ticker, max_per_source))
    raw.extend(_news_reddit_rss(ticker, max_per_source))
    raw.extend(_news_marketaux(ticker, max_per_source))
    raw.extend(_news_apewisdom(ticker))

    # Deduplicate on first 60 chars of lowercased title
    seen, unique = set(), []
    for item in raw:
        key = item['title'].lower().strip()[:60]
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    # Sort newest first
    unique.sort(key=lambda x: x['_ts'], reverse=True)
    return unique
