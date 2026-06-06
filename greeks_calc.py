import pandas as pd
from datetime import datetime

try:
    from vollib.black_scholes import black_scholes
    from vollib.black_scholes.implied_volatility import implied_volatility
    from vollib.black_scholes.greeks.analytical import delta, gamma, theta, vega
    VOLLIB_OK = True
except ImportError:
    VOLLIB_OK = False

RISK_FREE_RATE = 0.05


def tte(expiry_str: str) -> float:
    """Time to expiry in years (floor at 1 day)."""
    try:
        exp = datetime.strptime(str(expiry_str)[:10], '%Y-%m-%d')
        return max((exp - datetime.now()).days, 1) / 365.0
    except Exception:
        return 1 / 365.0


def calc_iv(market_price: float, spot: float, strike: float, t: float, r: float, flag: str) -> float:
    if not VOLLIB_OK or market_price <= 0 or t <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    try:
        return float(implied_volatility(market_price, spot, strike, t, r, flag))
    except Exception:
        return 0.0


def calc_greeks(spot: float, strike: float, t: float, r: float, sigma: float, flag: str) -> dict:
    empty = {'delta': 0.0, 'gamma': 0.0, 'theta': 0.0, 'vega': 0.0}
    if not VOLLIB_OK or t <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return empty
    try:
        return {
            'delta': float(delta(flag, spot, strike, t, r, sigma)),
            'gamma': float(gamma(flag, spot, strike, t, r, sigma)),
            'theta': float(theta(flag, spot, strike, t, r, sigma)),
            'vega': float(vega(flag, spot, strike, t, r, sigma)),
        }
    except Exception:
        return empty


def enrich_with_greeks(df: pd.DataFrame, spot: float, r: float = RISK_FREE_RATE) -> pd.DataFrame:
    """Compute missing Greeks via Black-Scholes for rows that have IV but no Greeks."""
    if df.empty or not VOLLIB_OK:
        return df

    rows = []
    for _, row in df.iterrows():
        row = row.copy()
        flag = 'c' if row.get('type') == 'call' else 'p'
        t = tte(row.get('expiry', ''))
        mid = ((row.get('bid') or 0.0) + (row.get('ask') or 0.0)) / 2.0

        iv = float(row.get('iv') or 0.0)
        if iv == 0.0 and mid > 0:
            iv = calc_iv(mid, spot, float(row.get('strike', 0)), t, r, flag)
            row['iv'] = iv

        if iv > 0.0 and pd.isna(row.get('delta')):
            for k, v in calc_greeks(spot, float(row.get('strike', 0)), t, r, iv, flag).items():
                row[k] = v

        rows.append(row)

    return pd.DataFrame(rows)


def get_atm_contract(df: pd.DataFrame, spot: float, option_type: str = 'call') -> pd.Series | None:
    subset = df[df['type'] == option_type] if 'type' in df.columns else df
    if subset.empty:
        return None
    return subset.loc[(subset['strike'] - spot).abs().idxmin()]
