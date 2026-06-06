import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import yfinance as yf

from fetcher import (
    WATCHLIST, get_options_chain, get_spot_price,
    get_iv_rank, get_expirations, alpaca_configured,
)
from greeks_calc import enrich_with_greeks, VOLLIB_OK

st.set_page_config(page_title="Options Evaluator", page_icon="📈", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 1.2rem; }
    div[data-testid="metric-container"] { background: #111; border-radius: 8px; padding: 8px 12px; }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    selected = st.selectbox("Watchlist", WATCHLIST)
    st.divider()
    min_oi      = st.number_input("Min Open Interest", 0, value=0, step=100)
    num_strikes = st.slider("Strikes shown", 6, 40, 16)
    st.divider()
    st.caption("Alpaca + yfinance" if alpaca_configured() else "yfinance (no exchange Greeks)")


# ── Cached data loaders ────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def load_intraday(ticker):
    tk = yf.Ticker(ticker)
    hist = tk.history(period='1d', interval='5m')
    prev = tk.history(period='5d')
    prev_close = float(prev['Close'].iloc[-2]) if len(prev) >= 2 else None
    return hist, prev_close


@st.cache_data(ttl=60, show_spinner=False)
def load_chain(ticker, exp):
    spot = get_spot_price(ticker)
    df = get_options_chain(ticker, exp)
    if not df.empty and ('delta' not in df.columns or df['delta'].isna().all()) and VOLLIB_OK:
        df = enrich_with_greeks(df, spot)
    return df, spot


@st.cache_data(ttl=300, show_spinner=False)
def load_watchlist_summary():
    return pd.DataFrame([
        {'Ticker': t, 'Spot': round(get_spot_price(t), 2), 'IV Rank': get_iv_rank(t)}
        for t in WATCHLIST
    ])


# ── Stock header + price chart ─────────────────────────────────────────────────
with st.spinner(""):
    intraday, prev_close = load_intraday(selected)
    iv_rank = get_iv_rank(selected)

spot    = float(intraday['Close'].iloc[-1]) if not intraday.empty else get_spot_price(selected)
open_px = float(intraday['Open'].iloc[0])   if not intraday.empty else spot
chg     = spot - open_px
chg_pct = (chg / open_px * 100) if open_px else 0.0
is_up   = chg >= 0
RH_GREEN = "#00C805"
RH_RED   = "#FF5000"
color    = RH_GREEN if is_up else RH_RED
sign     = "+" if is_up else ""

# Ticker name + price row
name_col, price_col, stat_col = st.columns([1, 2, 1])
with name_col:
    st.markdown(f"## {selected}")
    st.caption(f"IV Rank: **{iv_rank:.1f}**")

with price_col:
    st.markdown(
        f"<div style='font-size:2.2rem;font-weight:700;color:{color}'>${spot:.2f}</div>"
        f"<div style='color:{color}'>{sign}{chg:.2f} ({sign}{chg_pct:.2f}%) today</div>",
        unsafe_allow_html=True,
    )

with stat_col:
    if prev_close:
        st.metric("Prev Close", f"${prev_close:.2f}")

# Price sparkline
if not intraday.empty:
    fig_spark = go.Figure()
    fig_spark.add_trace(go.Scatter(
        x=intraday.index, y=intraday['Close'],
        mode='lines', line=dict(color=color, width=2),
        fill='tozeroy',
        fillcolor=f"{'rgba(0,200,5,0.08)' if is_up else 'rgba(255,80,0,0.08)'}",
    ))
    if prev_close:
        fig_spark.add_hline(y=prev_close, line_dash='dot',
                            line_color='gray', opacity=0.5,
                            annotation_text="prev close", annotation_position="right")
    fig_spark.update_layout(
        height=160, margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
        yaxis=dict(showgrid=False, zeroline=False),
        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
        showlegend=False,
    )
    st.plotly_chart(fig_spark, use_container_width=True)

st.divider()

# ── Expiration + calls/puts controls ──────────────────────────────────────────
expirations = get_expirations(selected)
if not expirations:
    st.warning(f"No options data available for {selected}.")
    st.stop()

ctrl_exp, ctrl_type = st.columns([4, 1])
with ctrl_exp:
    expiry = st.radio("Expiration", expirations[:8], horizontal=True,
                      label_visibility="collapsed")
with ctrl_type:
    opt_label = st.radio("Type", ["Calls", "Puts"], horizontal=True,
                         label_visibility="collapsed")

opt_type = "call" if opt_label == "Calls" else "put"

# ── Load + filter chain ────────────────────────────────────────────────────────
with st.spinner(""):
    chain, spot = load_chain(selected, expiry)

if chain.empty:
    st.warning(f"No options data for {selected} — {expiry}.")
    st.stop()

side = chain[chain['type'] == opt_type].drop_duplicates('strike').copy()

if min_oi > 0 and 'open_interest' in side.columns:
    side = side[side['open_interest'] >= min_oi]

all_strikes = sorted(side['strike'].unique())
if not all_strikes:
    st.warning("No contracts match current filters.")
    st.stop()

atm_idx    = min(range(len(all_strikes)), key=lambda i: abs(all_strikes[i] - spot))
half       = num_strikes // 2
shown      = all_strikes[max(0, atm_idx - half): atm_idx + half + 1]
atm_strike = min(shown, key=lambda s: abs(s - spot))

side = side[side['strike'].isin(shown)].copy()

# Derived columns
side['mark']       = ((side['bid'] + side['ask']) / 2).round(2)
side['break_even'] = (
    (side['strike'] + side['mark']) if opt_type == 'call'
    else (side['strike'] - side['mark'])
).round(2)
side['itm'] = side['strike'] < spot if opt_type == 'call' else side['strike'] > spot

# Sort: calls ascending (OTM → ITM top to bottom), puts descending
ascending = (opt_type == 'call')
side = side.sort_values('strike', ascending=ascending).reset_index(drop=True)

# ── Options chain table ────────────────────────────────────────────────────────
COLS = {
    'strike':        'Strike',
    'mark':          'Mark',
    'bid':           'Bid',
    'ask':           'Ask',
    'break_even':    'Break Even',
    'delta':         'Delta',
    'iv':            'IV',
    'volume':        'Volume',
    'open_interest': 'OI',
}
COLS = {k: v for k, v in COLS.items() if k in side.columns}

disp = side[list(COLS.keys())].copy()
disp.columns = list(COLS.values())

FMT = {
    'Strike':    '${:.2f}',
    'Mark':      '${:.2f}',
    'Bid':       '${:.2f}',
    'Ask':       '${:.2f}',
    'Break Even':'${:.2f}',
    'Delta':     '{:.3f}',
    'IV':        '{:.1%}',
    'Volume':    '{:,.0f}',
    'OI':        '{:,.0f}',
}
FMT = {k: v for k, v in FMT.items() if k in disp.columns}

ITM_BG  = 'background-color: #0a1a0a' if opt_type == 'call' else 'background-color: #1a0a0a'
ATM_STY = f'background-color: #0d1f3c; font-weight: bold; border-left: 3px solid {RH_GREEN}'


def _style(df):
    s = pd.DataFrame('', index=df.index, columns=df.columns)
    for i in df.index:
        strike = float(df.loc[i, 'Strike'])
        if abs(strike - atm_strike) < 0.01:
            s.loc[i, :] = ATM_STY
        elif side.loc[i, 'itm']:
            s.loc[i, :] = ITM_BG
    return s


st.dataframe(
    disp.style.apply(_style, axis=None).format(FMT, na_rep='—'),
    use_container_width=True,
    height=480,
    hide_index=True,
)

# ── Contract detail ────────────────────────────────────────────────────────────
st.divider()

sel_strike = st.select_slider(
    "Select a strike for details",
    options=sorted(side['strike'].unique()),
    value=atm_strike,
    format_func=lambda x: f"${x:.2f}",
)

hit = side[side['strike'] == sel_strike]
if not hit.empty:
    r       = hit.iloc[0]
    bid     = float(r.get('bid')  or 0)
    ask     = float(r.get('ask')  or 0)
    mark    = float(r.get('mark') or (bid + ask) / 2)
    be      = float(r.get('break_even') or 0)
    spread  = ask - bid
    sp_pct  = (spread / mark * 100) if mark else 0.0
    iv_v    = r.get('iv')
    delta_v = r.get('delta')
    gamma_v = r.get('gamma')
    theta_v = r.get('theta')
    vega_v  = r.get('vega')
    vol     = r.get('volume')
    oi      = r.get('open_interest')

    itm_tag  = "ITM" if r['itm'] else "OTM"
    lbl      = "Call" if opt_type == 'call' else "Put"
    dot_color = RH_GREEN if opt_type == 'call' else RH_RED

    st.markdown(
        f"<span style='color:{dot_color};font-weight:bold'>{lbl} ${sel_strike:.2f}</span>"
        f" &nbsp;·&nbsp; {expiry} &nbsp;·&nbsp; <span style='color:gray'>{itm_tag}</span>",
        unsafe_allow_html=True,
    )

    def _f(v, spec='.4f', prefix=''):
        return f"{prefix}{float(v):{spec}}" if v is not None and not pd.isna(v) else "—"

    # Row 1 — price detail
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Mark",       f"${mark:.2f}")
    c2.metric("Bid",        f"${bid:.2f}")
    c3.metric("Ask",        f"${ask:.2f}")
    c4.metric("Spread",     f"${spread:.2f} ({sp_pct:.1f}%)")
    c5.metric("Break Even", f"${be:.2f}")

    # Row 2 — greeks + market data
    c6, c7, c8, c9, c10 = st.columns(5)
    c6.metric("IV",            f"{float(iv_v)*100:.1f}%" if iv_v else "—")
    c7.metric("Delta (Δ)",     _f(delta_v))
    c8.metric("Gamma (Γ)",     _f(gamma_v))
    c9.metric("Theta (Θ)/day", _f(theta_v, prefix='$'))
    c10.metric("Vega (V)",     _f(vega_v))

    c11, c12 = st.columns(2)
    c11.metric("Volume",        f"{int(vol):,}" if vol and not pd.isna(vol) else "—")
    c12.metric("Open Interest", f"{int(oi):,}"  if oi  and not pd.isna(oi)  else "—")

# ── Watchlist comparison (collapsed) ──────────────────────────────────────────
with st.expander("Watchlist — IV Rank", expanded=False):
    summary = load_watchlist_summary()
    fig_iv = px.bar(
        summary.sort_values('IV Rank'), x='IV Rank', y='Ticker',
        orientation='h', color='IV Rank',
        color_continuous_scale='RdYlGn_r', range_color=[0, 100],
    )
    fig_iv.add_vline(x=50, line_dash='dash', line_color='white', opacity=0.4)
    fig_iv.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0),
                         coloraxis_showscale=False)
    st.plotly_chart(fig_iv, use_container_width=True)
    st.dataframe(summary.style.format({'Spot': '${:.2f}', 'IV Rank': '{:.1f}'}),
                 use_container_width=True, hide_index=True)

st.caption(
    "IV Rank from 52-week realized vol · Greeks via vollib (Black-Scholes) when not from exchange"
)
