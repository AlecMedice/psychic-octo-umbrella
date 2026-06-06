"""
Signal computation from options chain + price history.
All calculations use data already available from fetcher.py — no extra API calls.
"""

import pandas as pd
import numpy as np


# ── Chain-derived signals ─────────────────────────────────────────────────

def implied_move(chain: pd.DataFrame, spot: float) -> float | None:
    """
    ATM straddle price / spot — the market's priced-in ±move for this expiry.
    Returns as a percentage (e.g. 3.2 means ±3.2%).
    """
    if chain.empty or spot <= 0:
        return None
    try:
        calls = chain[chain['type'] == 'call']
        puts  = chain[chain['type'] == 'put']
        if calls.empty or puts.empty:
            return None
        atm_strike = min(calls['strike'].unique(), key=lambda s: abs(s - spot))
        c_row = calls[calls['strike'] == atm_strike]
        p_row = puts[puts['strike'] == atm_strike]
        if c_row.empty or p_row.empty:
            return None
        c_mark = (float(c_row.iloc[0].get('bid', 0) or 0) +
                  float(c_row.iloc[0].get('ask', 0) or 0)) / 2
        p_mark = (float(p_row.iloc[0].get('bid', 0) or 0) +
                  float(p_row.iloc[0].get('ask', 0) or 0)) / 2
        return round((c_mark + p_mark) / spot * 100, 2)
    except Exception:
        return None


def put_call_ratios(chain: pd.DataFrame) -> dict:
    """Put/call ratios by volume and open interest."""
    try:
        calls = chain[chain['type'] == 'call']
        puts  = chain[chain['type'] == 'put']
        cv = float(calls['volume'].sum()) if 'volume' in calls.columns else 0
        pv = float(puts['volume'].sum())  if 'volume' in puts.columns  else 0
        co = float(calls['open_interest'].sum()) if 'open_interest' in calls.columns else 0
        po = float(puts['open_interest'].sum())  if 'open_interest' in puts.columns  else 0
        return {
            'vol': round(pv / cv, 2) if cv > 0 else None,
            'oi':  round(po / co, 2) if co > 0 else None,
            'call_vol': int(cv), 'put_vol': int(pv),
            'call_oi':  int(co), 'put_oi':  int(po),
        }
    except Exception:
        return {}


def iv_skew(chain: pd.DataFrame, spot: float) -> float | None:
    """
    25-delta skew proxy: average IV of 25% OTM puts minus 25% OTM calls.
    Positive = put skew (bearish hedging demand), negative = call skew.
    """
    if chain.empty or spot <= 0:
        return None
    try:
        otm_put_strike  = spot * 0.975
        otm_call_strike = spot * 1.025

        puts  = chain[chain['type'] == 'put'].copy()
        calls = chain[chain['type'] == 'call'].copy()

        if puts.empty or calls.empty or 'iv' not in chain.columns:
            return None

        p_row = puts.iloc[(puts['strike'] - otm_put_strike).abs().argsort()[:1]]
        c_row = calls.iloc[(calls['strike'] - otm_call_strike).abs().argsort()[:1]]

        p_iv = float(p_row.iloc[0]['iv']) if not p_row.empty and p_row.iloc[0]['iv'] else None
        c_iv = float(c_row.iloc[0]['iv']) if not c_row.empty and c_row.iloc[0]['iv'] else None

        if p_iv and c_iv:
            return round((p_iv - c_iv) * 100, 2)
        return None
    except Exception:
        return None


def unusual_volume(chain: pd.DataFrame) -> list:
    """
    Contracts where volume > 3× open interest — signals unusual activity.
    Returns top 5 sorted by volume, each as a dict.
    """
    try:
        df = chain.copy()
        if 'volume' not in df.columns or 'open_interest' not in df.columns:
            return []
        df = df[(df['open_interest'] > 50) & (df['volume'] > 100)].copy()
        df['vol_oi_ratio'] = df['volume'] / df['open_interest'].replace(0, np.nan)
        unusual = df[df['vol_oi_ratio'] > 3].sort_values('volume', ascending=False).head(5)
        return [
            {
                'type':          r['type'],
                'strike':        r['strike'],
                'volume':        int(r['volume']),
                'open_interest': int(r['open_interest']),
                'vol_oi_ratio':  round(r['vol_oi_ratio'], 1),
                'iv':            round(float(r['iv']) * 100, 1) if r.get('iv') else None,
            }
            for _, r in unusual.iterrows()
        ]
    except Exception:
        return []


# ── Price-history signals ─────────────────────────────────────────────────

def iv_vs_rv(chain: pd.DataFrame, hist: pd.DataFrame) -> dict:
    """
    Compare the chain's average ATM IV against 30-day realized volatility.
    Positive premium = options are expensive (favor selling).
    """
    try:
        rv30 = hist['Close'].pct_change().dropna().rolling(30).std().iloc[-1] * (252 ** 0.5)
        rv30 = round(float(rv30) * 100, 1)

        atm_iv_rows = chain[chain['type'] == 'call']
        avg_iv = atm_iv_rows['iv'].dropna().mean() if 'iv' in atm_iv_rows.columns else None
        avg_iv_pct = round(float(avg_iv) * 100, 1) if avg_iv else None

        premium = round(avg_iv_pct - rv30, 1) if avg_iv_pct else None
        return {'rv30': rv30, 'avg_iv': avg_iv_pct, 'premium': premium}
    except Exception:
        return {}


def relative_volume(hist: pd.DataFrame) -> float | None:
    """Today's volume relative to 20-day average. >1.5 = elevated activity."""
    try:
        if 'Volume' not in hist.columns or len(hist) < 5:
            return None
        avg = float(hist['Volume'].rolling(20).mean().iloc[-1])
        today = float(hist['Volume'].iloc[-1])
        return round(today / avg, 2) if avg > 0 else None
    except Exception:
        return None


def momentum(hist: pd.DataFrame) -> dict:
    """RSI-14 and distance from 20-day Bollinger Band midline."""
    try:
        close = hist['Close'].dropna()
        if len(close) < 20:
            return {}

        # RSI
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = round(float(100 - 100 / (1 + rs.iloc[-1])), 1)

        # Bollinger position: 0 = lower band, 1 = upper band
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = sma20 + 2 * std20
        lower = sma20 - 2 * std20
        band_range = float(upper.iloc[-1] - lower.iloc[-1])
        bb_pos = round((float(close.iloc[-1]) - float(lower.iloc[-1])) / band_range, 2) if band_range else 0.5

        return {'rsi': rsi, 'bb_position': bb_pos}
    except Exception:
        return {}


# ── Composite opportunity score ────────────────────────────────────────────

def opportunity_score(
    iv_rank: float,
    iv_rv:   dict,
    pc:      dict,
    mom:     dict,
    fg_score: float | None = None,
) -> dict:
    """
    0–10 score + direction label for the most probable short-term edge.
    'sell' = IV rich, collect premium (iron condor / strangle / covered call)
    'buy'  = IV cheap, directional bet (long call/put / debit spread)
    'neutral' = no clear edge
    """
    pts   = 0.0
    notes = []
    direction = 'neutral'

    # IV rank
    if iv_rank >= 70:
        pts += 2.5; notes.append(f"IV Rank {iv_rank:.0f} — elevated (sell)")
    elif iv_rank >= 50:
        pts += 1.5; notes.append(f"IV Rank {iv_rank:.0f} — moderate (mild sell edge)")
    elif iv_rank <= 25:
        pts -= 1.5; notes.append(f"IV Rank {iv_rank:.0f} — depressed (buy edge)")

    # IV premium over RV
    prem = iv_rv.get('premium')
    if prem is not None:
        if prem >= 5:
            pts += 2.0; notes.append(f"IV {iv_rv.get('avg_iv')}% vs RV {iv_rv.get('rv30')}% (+{prem:.1f}% premium)")
        elif prem >= 2:
            pts += 1.0; notes.append(f"Slight IV premium +{prem:.1f}%")
        elif prem <= -3:
            pts -= 1.5; notes.append(f"IV discount {prem:.1f}% vs RV (buy edge)")

    # Put/call ratio (contrarian)
    pc_vol = pc.get('vol')
    if pc_vol is not None:
        if pc_vol >= 1.4:
            pts += 1.5; notes.append(f"P/C ratio {pc_vol:.2f} — bearish crowding (contrarian buy)")
        elif pc_vol <= 0.6:
            pts += 1.0; notes.append(f"P/C ratio {pc_vol:.2f} — bullish crowding (contrarian put)")

    # RSI extremes
    rsi = mom.get('rsi')
    bb  = mom.get('bb_position')
    if rsi is not None:
        if rsi >= 75:
            pts += 1.0; notes.append(f"RSI {rsi:.0f} — overbought")
        elif rsi <= 30:
            pts += 1.0; notes.append(f"RSI {rsi:.0f} — oversold")

    # Fear & Greed
    if fg_score is not None:
        if fg_score <= 25:
            pts += 1.5; notes.append(f"Fear & Greed {fg_score:.0f} — extreme fear (contrarian buy)")
        elif fg_score >= 75:
            pts += 1.0; notes.append(f"Fear & Greed {fg_score:.0f} — extreme greed (sell/hedge)")

    score = round(min(max(pts, 0), 10), 1)

    if pts > 0:
        direction = 'sell'   # overall IV-rich / premium-selling environment
    elif pts < -1:
        direction = 'buy'    # IV-cheap / directional bet environment

    return {'score': score, 'direction': direction, 'notes': notes}
