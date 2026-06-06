import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from fetcher import (
    WATCHLIST, get_options_chain, get_spot_price,
    get_iv_rank, get_expirations, alpaca_configured,
)
from greeks_calc import enrich_with_greeks, VOLLIB_OK

st.set_page_config(page_title="Options Evaluator", page_icon="📈", layout="wide")

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Controls")
    selected = st.selectbox("Ticker", WATCHLIST)
    expirations = get_expirations(selected)
    expiry = st.selectbox("Expiration", expirations) if expirations else None

    st.divider()
    st.subheader("Chain Filters")
    show_itm    = st.checkbox("Show ITM", value=True)
    show_otm    = st.checkbox("Show OTM", value=True)
    min_oi      = st.number_input("Min Open Interest", min_value=0, value=0, step=100)
    num_strikes = st.slider("Strikes around ATM", 6, 40, 20)

    st.divider()
    data_note = "Alpaca + yfinance fallback" if alpaca_configured() else "yfinance (add Alpaca keys for exchange Greeks)"
    st.caption(f"Data: {data_note}")


# ── Data loading ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def load_summary():
    rows = []
    for t in WATCHLIST:
        rows.append({'Ticker': t, 'Spot': round(get_spot_price(t), 2), 'IV Rank': get_iv_rank(t)})
    return pd.DataFrame(rows)


@st.cache_data(ttl=60, show_spinner=False)
def load_chain(ticker, exp):
    spot = get_spot_price(ticker)
    df = get_options_chain(ticker, exp)
    if not df.empty:
        needs_greeks = 'delta' not in df.columns or df['delta'].isna().all()
        if needs_greeks and VOLLIB_OK:
            df = enrich_with_greeks(df, spot)
    return df, spot


with st.spinner("Loading…"):
    summary = load_summary()
    chain, spot = load_chain(selected, expiry)

has_exchange_greeks = (
    not chain.empty
    and 'delta' in chain.columns
    and chain['delta'].notna().any()
    and alpaca_configured()
)

# ── Header metrics ─────────────────────────────────────────────────────────────
st.title(f"📈 {selected}")

iv_rank_row = summary[summary['Ticker'] == selected]
iv_rank = float(iv_rank_row['IV Rank'].iloc[0]) if not iv_rank_row.empty else 0.0

m1, m2, m3, m4 = st.columns(4)
m1.metric("Spot", f"${spot:.2f}")
m2.metric("IV Rank", f"{iv_rank:.1f}",
          delta="Elevated" if iv_rank > 50 else "Low",
          delta_color="inverse" if iv_rank > 50 else "off")
m3.metric("Expiration", expiry or "—")
m4.metric("Greeks", "Exchange" if has_exchange_greeks else "Black-Scholes (local)")

if not VOLLIB_OK and not has_exchange_greeks:
    st.warning("vollib not installed — Greeks unavailable. Run: `pip install vollib`")

# ── Watchlist IV rank (collapsible) ───────────────────────────────────────────
with st.expander("Watchlist IV Rank", expanded=False):
    fig_bar = px.bar(
        summary.sort_values('IV Rank'),
        x='IV Rank', y='Ticker', orientation='h',
        color='IV Rank', color_continuous_scale='RdYlGn_r',
        range_color=[0, 100],
    )
    fig_bar.add_vline(x=50, line_dash='dash', line_color='white',
                      opacity=0.4, annotation_text="50")
    fig_bar.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0),
                          coloraxis_showscale=False)
    st.plotly_chart(fig_bar, use_container_width=True)

st.divider()

# ── Options chain ──────────────────────────────────────────────────────────────
st.subheader(f"Options Chain — {expiry or 'no expiry selected'}")

if chain.empty:
    st.warning(f"No options data available for {selected}.")
    st.stop()

calls = chain[chain['type'] == 'call'].drop_duplicates('strike').copy()
puts  = chain[chain['type'] == 'put'].drop_duplicates('strike').copy()

# ITM / OTM filter
if not show_itm:
    calls = calls[calls['strike'] >= spot]
    puts  = puts[puts['strike'] <= spot]
if not show_otm:
    calls = calls[calls['strike'] <= spot]
    puts  = puts[puts['strike'] >= spot]

if min_oi > 0 and 'open_interest' in calls.columns:
    calls = calls[calls['open_interest'] >= min_oi]
    puts  = puts[puts['open_interest'] >= min_oi]

# Limit to N strikes around ATM
all_strikes = sorted(set(calls['strike'].tolist() + puts['strike'].tolist()))
if not all_strikes:
    st.warning("No contracts match the current filters.")
    st.stop()

atm_idx  = min(range(len(all_strikes)), key=lambda i: abs(all_strikes[i] - spot))
half     = num_strikes // 2
shown    = all_strikes[max(0, atm_idx - half): atm_idx + half + 1]
atm_strike = min(shown, key=lambda s: abs(s - spot))

calls = calls[calls['strike'].isin(shown)]
puts  = puts[puts['strike'].isin(shown)]

c_idx = calls.set_index('strike')
p_idx = puts.set_index('strike')


def _get(df, strike, col):
    if df.empty or strike not in df.index or col not in df.columns:
        return None
    v = df.loc[strike, col]
    return None if pd.isna(v) else v


# Calls on left (wide to narrow), strike center, puts on right (narrow to wide)
CALL_COLS = [
    ('bid',          'C Bid'),
    ('ask',          'C Ask'),
    ('last',         'C Last'),
    ('volume',       'C Vol'),
    ('open_interest','C OI'),
    ('iv',           'C IV'),
    ('delta',        'C Δ'),
    ('gamma',        'C Γ'),
    ('theta',        'C Θ'),
    ('vega',         'C V'),
]
PUT_COLS = [
    ('delta',        'P Δ'),
    ('gamma',        'P Γ'),
    ('theta',        'P Θ'),
    ('vega',         'P V'),
    ('iv',           'P IV'),
    ('open_interest','P OI'),
    ('volume',       'P Vol'),
    ('last',         'P Last'),
    ('bid',          'P Bid'),
    ('ask',          'P Ask'),
]

rows = []
for strike in sorted(shown, reverse=True):
    row = {}
    for col, label in CALL_COLS:
        row[label] = _get(c_idx, strike, col)
    row['Strike'] = strike
    for col, label in PUT_COLS:
        row[label] = _get(p_idx, strike, col)
    rows.append(row)

tbl = pd.DataFrame(rows)
call_labels = [lbl for _, lbl in CALL_COLS if lbl in tbl.columns]
put_labels  = [lbl for _, lbl in PUT_COLS  if lbl in tbl.columns]


def _style_chain(df):
    s = pd.DataFrame('', index=df.index, columns=df.columns)
    for i, row in df.iterrows():
        strike = row['Strike']
        if strike == atm_strike:
            s.loc[i, :] = 'background-color: #1a3a5c; font-weight: bold'
        else:
            if strike < spot:           # ITM calls
                for c in call_labels:
                    s.loc[i, c] = 'background-color: #0d2a0d'
            if strike > spot:           # ITM puts
                for c in put_labels:
                    s.loc[i, c] = 'background-color: #2a0d0d'
    return s


CHAIN_FMT = {
    'Strike':  '${:.2f}',
    'C Bid':   '${:.2f}', 'C Ask':  '${:.2f}', 'C Last': '${:.2f}',
    'C Vol':   '{:,.0f}', 'C OI':   '{:,.0f}',
    'C IV':    '{:.1%}',  'C Δ':    '{:.3f}',
    'C Γ':     '{:.4f}',  'C Θ':    '{:.4f}',  'C V':    '{:.4f}',
    'P Δ':     '{:.3f}',  'P Γ':    '{:.4f}',
    'P Θ':     '{:.4f}',  'P V':    '{:.4f}',
    'P IV':    '{:.1%}',  'P OI':   '{:,.0f}', 'P Vol':  '{:,.0f}',
    'P Last':  '${:.2f}', 'P Bid':  '${:.2f}', 'P Ask':  '${:.2f}',
}
CHAIN_FMT = {k: v for k, v in CHAIN_FMT.items() if k in tbl.columns}

st.caption("ITM calls shaded green · ATM row blue · ITM puts shaded red")
st.dataframe(
    tbl.style.apply(_style_chain, axis=None).format(CHAIN_FMT, na_rep='—'),
    use_container_width=True,
    height=520,
    hide_index=True,
)

# ── Contract detail ────────────────────────────────────────────────────────────
st.divider()
st.subheader("Contract Detail")

atm_default_idx = lambda strikes: (
    min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    if strikes else 0
)

call_strikes = sorted(calls['strike'].unique())
put_strikes  = sorted(puts['strike'].unique())

det_col1, det_col2 = st.columns(2)
with det_col1:
    call_sel = st.selectbox("Call strike", call_strikes,
                            index=atm_default_idx(call_strikes),
                            key="cs") if call_strikes else None
with det_col2:
    put_sel = st.selectbox("Put strike", put_strikes,
                           index=atm_default_idx(put_strikes),
                           key="ps") if put_strikes else None


def _contract_detail(df, strike, label, color):
    if strike is None:
        return
    subset = df[df['strike'] == strike]
    if subset.empty:
        st.info(f"No data for {label} ${strike:.2f}")
        return
    r = subset.iloc[0]

    bid  = float(r.get('bid')  or 0)
    ask  = float(r.get('ask')  or 0)
    last = float(r.get('last') or 0)
    mid  = (bid + ask) / 2
    spread     = ask - bid
    spread_pct = (spread / mid * 100) if mid > 0 else 0.0

    st.markdown(f"**{color} {label} — Strike ${strike:.2f}** · exp {r.get('expiry', expiry)}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bid",  f"${bid:.2f}")
    c2.metric("Ask",  f"${ask:.2f}")
    c3.metric("Last", f"${last:.2f}" if last else "—")
    c4.metric("Mid",  f"${mid:.2f}")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Spread", f"${spread:.2f} ({spread_pct:.1f}%)")
    iv_v = r.get('iv') or 0
    c6.metric("IV", f"{float(iv_v)*100:.1f}%" if iv_v else "—")
    vol = r.get('volume')
    c7.metric("Volume", f"{int(vol):,}" if vol and not pd.isna(vol) else "—")
    oi = r.get('open_interest')
    c8.metric("Open Interest", f"{int(oi):,}" if oi and not pd.isna(oi) else "—")

    delta_v = r.get('delta')
    gamma_v = r.get('gamma')
    theta_v = r.get('theta')
    vega_v  = r.get('vega')

    def _fmt(v, spec=',.4f', prefix=''):
        if v is None or pd.isna(v):
            return "—"
        return f"{prefix}{float(v):{spec}}"

    c9, c10, c11, c12 = st.columns(4)
    c9.metric( "Delta (Δ)",      _fmt(delta_v))
    c10.metric("Gamma (Γ)",      _fmt(gamma_v))
    c11.metric("Theta (Θ) /day", _fmt(theta_v, prefix='$'))
    c12.metric("Vega (V)",       _fmt(vega_v))


with det_col1:
    _contract_detail(calls, call_sel, "CALL", "🟢")
with det_col2:
    _contract_detail(puts,  put_sel,  "PUT",  "🔴")

# ── Volatility smile ───────────────────────────────────────────────────────────
st.divider()
smile_calls = calls[calls['iv'] > 0].sort_values('strike') if not calls.empty else pd.DataFrame()
smile_puts  = puts[puts['iv']  > 0].sort_values('strike') if not puts.empty  else pd.DataFrame()

if not smile_calls.empty or not smile_puts.empty:
    st.subheader("Volatility Smile")
    fig = go.Figure()
    if not smile_calls.empty:
        fig.add_trace(go.Scatter(
            x=smile_calls['strike'], y=smile_calls['iv'],
            mode='lines+markers', name='Calls',
            line=dict(color='#2ecc71', width=2), marker=dict(size=6),
        ))
    if not smile_puts.empty:
        fig.add_trace(go.Scatter(
            x=smile_puts['strike'], y=smile_puts['iv'],
            mode='lines+markers', name='Puts',
            line=dict(color='#e74c3c', width=2), marker=dict(size=6),
        ))
    fig.add_vline(x=spot, line_dash='dash', line_color='orange',
                  annotation_text=f"Spot ${spot:.2f}")
    fig.update_layout(
        title=f'{selected} Volatility Smile — {expiry}',
        xaxis_title='Strike', yaxis_title='Implied Volatility',
        yaxis_tickformat='.0%', height=360,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)

st.caption(
    "IV Rank from 52-week realized vol. Greeks via vollib (Black-Scholes) when not sourced from exchange."
)
