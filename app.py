import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from fetcher import WATCHLIST, get_options_chain, get_spot_price, get_iv_rank, get_expirations
from greeks_calc import enrich_with_greeks, get_atm_contract, VOLLIB_OK

st.set_page_config(page_title="Options Evaluator", page_icon="📈", layout="wide")
st.title("Robinhood Watchlist — Options Evaluator")

if not VOLLIB_OK:
    st.warning("vollib not installed — Greeks will not be computed locally. Run: `pip install vollib`")

# ── Sidebar ────────────────────────────────────────────────────────────────────
st.sidebar.header("Controls")
selected = st.sidebar.selectbox("Ticker", WATCHLIST)
opt_type = st.sidebar.radio("Option Type", ["call", "put"], horizontal=True)

expirations = get_expirations(selected)
expiry = st.sidebar.selectbox("Expiration", expirations) if expirations else None

min_oi = st.sidebar.number_input("Min Open Interest", min_value=0, value=0, step=100)
max_spread_pct = st.sidebar.slider("Max Bid/Ask Spread %", 0, 100, 100)

# ── Watchlist Summary ──────────────────────────────────────────────────────────
st.header("Watchlist — IV Rank")


@st.cache_data(ttl=300, show_spinner=False)
def load_summary():
    rows = []
    for t in WATCHLIST:
        spot = get_spot_price(t)
        iv_rank = get_iv_rank(t)
        rows.append({'Ticker': t, 'Spot ($)': round(spot, 2), 'IV Rank': iv_rank})
    return pd.DataFrame(rows)


with st.spinner("Loading watchlist…"):
    summary = load_summary()

fig_bar = px.bar(
    summary.sort_values('IV Rank'),
    x='IV Rank', y='Ticker', orientation='h',
    color='IV Rank', color_continuous_scale='RdYlGn_r',
    range_color=[0, 100],
    title='IV Rank (approx. from 52-week realized volatility)',
)
fig_bar.add_vline(x=50, line_dash='dash', line_color='white', opacity=0.4)
fig_bar.update_layout(height=320, margin=dict(l=0, r=0, t=40, b=0), coloraxis_showscale=False)
st.plotly_chart(fig_bar, use_container_width=True)

st.dataframe(
    summary.style.format({'Spot ($)': '${:.2f}', 'IV Rank': '{:.1f}'}),
    use_container_width=True,
    hide_index=True,
)

st.divider()

# ── Options Chain Detail ───────────────────────────────────────────────────────
st.header(f"{selected} — {opt_type.capitalize()} Options Chain")
if expiry:
    st.caption(f"Expiration: {expiry}")


@st.cache_data(ttl=60, show_spinner=False)
def load_chain(ticker, exp):
    spot = get_spot_price(ticker)
    df = get_options_chain(ticker, exp)
    if not df.empty:
        needs_greeks = 'delta' not in df.columns or df['delta'].isna().all()
        if needs_greeks:
            df = enrich_with_greeks(df, spot)
    return df, spot


with st.spinner(f"Loading {selected} chain…"):
    chain, spot = load_chain(selected, expiry)

if chain.empty:
    st.warning(f"No options data returned for {selected}.")
    st.stop()

# Filter to selected type
filtered = chain[chain['type'] == opt_type].copy()

# Apply sidebar filters
if 'open_interest' in filtered.columns:
    filtered = filtered[filtered['open_interest'] >= min_oi]

if 'bid' in filtered.columns and 'ask' in filtered.columns:
    mid = (filtered['bid'] + filtered['ask']) / 2
    spread_pct = ((filtered['ask'] - filtered['bid']) / mid.replace(0, float('nan'))) * 100
    filtered = filtered[spread_pct.fillna(100) <= max_spread_pct]

# ── ATM summary metrics ────────────────────────────────────────────────────────
atm = get_atm_contract(filtered, spot, opt_type)

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Spot", f"${spot:.2f}")
if atm is not None:
    atm_iv = float(atm.get('iv') or 0)
    atm_delta = atm.get('delta')
    atm_theta = atm.get('theta')
    atm_bid = float(atm.get('bid') or 0)
    atm_ask = float(atm.get('ask') or 0)
    col2.metric("ATM Strike", f"${float(atm['strike']):.2f}")
    col3.metric("ATM IV", f"{atm_iv*100:.1f}%" if atm_iv else "—")
    col4.metric("ATM Delta", f"{float(atm_delta):.3f}" if atm_delta is not None and not pd.isna(atm_delta) else "—")
    col5.metric("ATM Theta/day", f"${float(atm_theta):.4f}" if atm_theta is not None and not pd.isna(atm_theta) else "—")

# ── Chain table ────────────────────────────────────────────────────────────────
display_cols = ['strike', 'bid', 'ask', 'iv', 'delta', 'gamma', 'theta', 'vega', 'open_interest']
display_cols = [c for c in display_cols if c in filtered.columns]

fmt = {
    'strike': '${:.2f}', 'bid': '${:.2f}', 'ask': '${:.2f}',
    'iv': '{:.1%}', 'delta': '{:.3f}', 'gamma': '{:.4f}',
    'theta': '{:.4f}', 'vega': '{:.4f}', 'open_interest': '{:,.0f}',
}
fmt = {k: v for k, v in fmt.items() if k in display_cols}


def _highlight_atm(row):
    if atm is not None and abs(row['strike'] - float(atm['strike'])) < 0.01:
        return ['background-color: #1a3a5c'] * len(row)
    return [''] * len(row)


display_df = filtered[display_cols].sort_values('strike').reset_index(drop=True)

st.dataframe(
    display_df.style.apply(_highlight_atm, axis=1).format(fmt, na_rep='—'),
    use_container_width=True,
    height=480,
)

# ── Volatility smile ───────────────────────────────────────────────────────────
if 'iv' in filtered.columns and filtered['iv'].notna().any() and (filtered['iv'] > 0).any():
    smile_data = filtered[filtered['iv'] > 0].sort_values('strike')
    fig_smile = go.Figure()
    fig_smile.add_trace(go.Scatter(
        x=smile_data['strike'], y=smile_data['iv'],
        mode='lines+markers', name='IV',
        line=dict(color='#3498db', width=2),
        marker=dict(size=6),
    ))
    if atm is not None:
        fig_smile.add_vline(
            x=float(atm['strike']), line_dash='dash',
            line_color='orange', annotation_text='ATM',
        )
    fig_smile.update_layout(
        title=f'{selected} Volatility Smile — {opt_type.capitalize()}s ({expiry})',
        xaxis_title='Strike', yaxis_title='Implied Volatility',
        yaxis_tickformat='.0%', height=360,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_smile, use_container_width=True)

st.caption(
    "Data: Alpaca Markets (if API keys set in .env) with yfinance fallback. "
    "IV Rank approximated from 52-week realized volatility. Greeks via vollib (Black-Scholes)."
)
