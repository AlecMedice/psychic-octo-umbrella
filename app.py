import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

from fetcher import (
    WATCHLIST, CHART_PERIODS,
    get_options_chain, get_spot_price, get_iv_rank,
    get_expirations, get_price_history, get_news,
    alpaca_configured,
)
from greeks_calc import enrich_with_greeks, VOLLIB_OK
from agent import agent_configured, stream_response

st.set_page_config(page_title="Options Evaluator", page_icon="📈", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 1.2rem; }
    div[data-testid="metric-container"] { background:#111; border-radius:8px; padding:8px 12px; }
    .news-card { border-left: 3px solid #333; padding: 6px 10px; margin-bottom: 8px; }
    .news-title { font-size: 0.82rem; font-weight: 600; line-height: 1.3; }
    .news-meta  { font-size: 0.72rem; color: #888; margin-top: 2px; }
</style>
""", unsafe_allow_html=True)


# ── Cached loaders ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=60,  show_spinner=False)
def load_chart(ticker, period):
    return get_price_history(ticker, period)

@st.cache_data(ttl=60,  show_spinner=False)
def load_chain(ticker, exp):
    spot = get_spot_price(ticker)
    df   = get_options_chain(ticker, exp)
    if not df.empty and ('delta' not in df.columns or df['delta'].isna().all()) and VOLLIB_OK:
        df = enrich_with_greeks(df, spot)
    return df, spot

@st.cache_data(ttl=300, show_spinner=False)
def load_summary():
    return pd.DataFrame([
        {'Ticker': t, 'Spot': round(get_spot_price(t), 2), 'IV Rank': get_iv_rank(t)}
        for t in WATCHLIST
    ])

@st.cache_data(ttl=300, show_spinner=False)
def load_news(ticker):
    return get_news(ticker)


# ── Sidebar: ticker + news ─────────────────────────────────────────────────────
with st.sidebar:
    selected = st.selectbox("Watchlist", WATCHLIST)

    with st.expander("Chain Filters", expanded=False):
        min_oi      = st.number_input("Min Open Interest", 0, value=0, step=100)
        num_strikes = st.slider("Strikes shown", 6, 40, 16)

    st.divider()
    st.markdown("#### 📰 Latest News")

    SOURCE_COLORS = {
        'Yahoo Finance':  '#6001D2',
        'Google News':    '#4285F4',
        'Alpha Vantage':  '#00875A',
        'Finnhub':        '#FF6B35',
    }
    SENTIMENT_ICONS = {
        'Bullish': '🟢',
        'Bearish': '🔴',
        'Neutral': '⚪',
    }

    news_items = load_news(selected)
    if news_items:
        for item in news_items:
            src    = item.get('source', '')
            badge_color = SOURCE_COLORS.get(src, '#555')
            badge  = f"<span style='background:{badge_color};color:#fff;font-size:0.65rem;padding:1px 5px;border-radius:3px;margin-right:4px'>{src}</span>" if src else ''
            sent   = item.get('sentiment', '')
            sent_icon = SENTIMENT_ICONS.get(sent, '')
            pub    = item.get('publisher', '')
            when   = item.get('when', '')
            meta_parts = list(filter(None, [pub, when]))
            meta   = ' · '.join(meta_parts)
            st.markdown(
                f"<div class='news-card'>"
                f"<div class='news-title'>"
                f"<a href='{item['url']}' target='_blank'>{item['title']}</a>"
                f" {sent_icon}</div>"
                f"<div class='news-meta'>{badge}{meta}</div>"
                + (f"<div class='news-meta' style='margin-top:3px'>{item['summary']}</div>"
                   if item.get('summary') else "")
                + "</div>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("No recent news found.")

    st.divider()
    st.caption("Alpaca + yfinance" if alpaca_configured() else "yfinance (add Alpaca keys for exchange Greeks)")


# ── Load stock data ────────────────────────────────────────────────────────────
period_label = st.radio(
    "Period", list(CHART_PERIODS.keys()), horizontal=True,
    label_visibility="collapsed",
)
hist = load_chart(selected, period_label)

spot    = float(hist['Close'].iloc[-1]) if not hist.empty else get_spot_price(selected)
open_px = float(hist['Open'].iloc[0])   if not hist.empty else spot
chg     = spot - open_px
chg_pct = (chg / open_px * 100) if open_px else 0.0
is_up   = chg >= 0
COLOR   = "#00C805" if is_up else "#FF5000"
sign    = "+" if is_up else ""
iv_rank = get_iv_rank(selected)

# ── Header row ─────────────────────────────────────────────────────────────────
h1, h2, h3 = st.columns([1, 2, 1])
with h1:
    st.markdown(f"## {selected}")
    st.caption(f"IV Rank: **{iv_rank:.1f}**")
with h2:
    st.markdown(
        f"<div style='font-size:2.1rem;font-weight:700;color:{COLOR}'>${spot:.2f}</div>"
        f"<div style='color:{COLOR}'>{sign}{chg:.2f} ({sign}{chg_pct:.2f}%)"
        f" &nbsp;<span style='color:#888;font-size:0.85rem'>{period_label}</span></div>",
        unsafe_allow_html=True,
    )
with h3:
    hi52 = float(hist['High'].max()) if not hist.empty else 0
    lo52 = float(hist['Low'].min())  if not hist.empty else 0
    if hi52:
        st.metric("Period High", f"${hi52:.2f}")
        st.metric("Period Low",  f"${lo52:.2f}")

# ── Price chart ────────────────────────────────────────────────────────────────
if not hist.empty:
    short_period = period_label in ('1D', '3D', '1W')
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=hist.index, y=hist['Close'],
        mode='lines',
        line=dict(color=COLOR, width=2),
        fill='tozeroy' if short_period else 'none',
        fillcolor=f"{'rgba(0,200,5,0.07)' if is_up else 'rgba(255,80,0,0.07)'}",
        hovertemplate='%{x|%b %d %H:%M}<br>$%{y:.2f}<extra></extra>',
    ))
    # Reference line: opening price for short periods, first close for longer ones
    ref = open_px if short_period else float(hist['Close'].iloc[0])
    fig.add_hline(
        y=ref, line_dash='dot', line_color='#555',
        annotation_text=f"{'Open' if short_period else 'Start'} ${ref:.2f}",
        annotation_position="right",
        annotation_font_color='#666',
    )
    fig.update_layout(
        height=210,
        margin=dict(l=0, r=60, t=0, b=0),
        xaxis=dict(
            showgrid=False, zeroline=False,
            tickformat='%H:%M' if period_label == '1D' else '%b %d',
            tickcolor='#444', tickfont=dict(color='#888', size=10),
        ),
        yaxis=dict(
            showgrid=True, gridcolor='#1a1a1a', zeroline=False,
            tickprefix='$', tickfont=dict(color='#888', size=10),
            side='right',
        ),
        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# ── Options: expiration + calls/puts ──────────────────────────────────────────
expirations = get_expirations(selected)
if not expirations:
    st.warning(f"No options available for {selected}.")
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
side['mark']       = ((side['bid'] + side['ask']) / 2).round(2)
side['break_even'] = (
    (side['strike'] + side['mark']) if opt_type == 'call'
    else (side['strike'] - side['mark'])
).round(2)
side['itm'] = side['strike'] < spot if opt_type == 'call' else side['strike'] > spot
side = side.sort_values('strike', ascending=(opt_type == 'call')).reset_index(drop=True)

# ── Chain table ────────────────────────────────────────────────────────────────
COLS = {k: v for k, v in {
    'strike': 'Strike', 'mark': 'Mark', 'bid': 'Bid', 'ask': 'Ask',
    'break_even': 'Break Even', 'delta': 'Delta', 'iv': 'IV',
    'volume': 'Volume', 'open_interest': 'OI',
}.items() if k in side.columns}

disp = side[list(COLS.keys())].copy()
disp.columns = list(COLS.values())

FMT = {k: v for k, v in {
    'Strike': '${:.2f}', 'Mark': '${:.2f}', 'Bid': '${:.2f}', 'Ask': '${:.2f}',
    'Break Even': '${:.2f}', 'Delta': '{:.3f}', 'IV': '{:.1%}',
    'Volume': '{:,.0f}', 'OI': '{:,.0f}',
}.items() if k in disp.columns}

RH_GREEN = "#00C805"
ITM_BG   = 'background-color: #0a1a0a' if opt_type == 'call' else 'background-color: #1a0a0a'
ATM_STY  = f'background-color: #0d1f3c; font-weight: bold; border-left: 3px solid {RH_GREEN}'


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
    use_container_width=True, height=480, hide_index=True,
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
    r      = hit.iloc[0]
    bid    = float(r.get('bid')  or 0)
    ask    = float(r.get('ask')  or 0)
    mark   = float(r.get('mark') or (bid + ask) / 2)
    be     = float(r.get('break_even') or 0)
    spread = ask - bid
    sp_pct = (spread / mark * 100) if mark else 0.0
    iv_v   = r.get('iv')
    vol    = r.get('volume')
    oi     = r.get('open_interest')

    dot  = RH_GREEN if opt_type == 'call' else "#FF5000"
    lbl  = "Call" if opt_type == 'call' else "Put"
    tag  = "ITM" if r['itm'] else "OTM"
    st.markdown(
        f"<span style='color:{dot};font-weight:bold'>{lbl} ${sel_strike:.2f}</span>"
        f" &nbsp;·&nbsp; {expiry} &nbsp;·&nbsp; <span style='color:#888'>{tag}</span>",
        unsafe_allow_html=True,
    )

    def _f(v, spec='.4f', prefix=''):
        return f"{prefix}{float(v):{spec}}" if v is not None and not pd.isna(v) else "—"

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Mark",       f"${mark:.2f}")
    c2.metric("Bid",        f"${bid:.2f}")
    c3.metric("Ask",        f"${ask:.2f}")
    c4.metric("Spread",     f"${spread:.2f} ({sp_pct:.1f}%)")
    c5.metric("Break Even", f"${be:.2f}")

    c6, c7, c8, c9, c10 = st.columns(5)
    c6.metric( "IV",             f"{float(iv_v)*100:.1f}%" if iv_v else "—")
    c7.metric( "Delta (Δ)",      _f(r.get('delta')))
    c8.metric( "Gamma (Γ)",      _f(r.get('gamma')))
    c9.metric( "Theta (Θ)/day",  _f(r.get('theta'), prefix='$'))
    c10.metric("Vega (V)",       _f(r.get('vega')))

    c11, c12 = st.columns(2)
    c11.metric("Volume",        f"{int(vol):,}" if vol and not pd.isna(vol) else "—")
    c12.metric("Open Interest", f"{int(oi):,}"  if oi  and not pd.isna(oi)  else "—")

# ── Watchlist IV rank ──────────────────────────────────────────────────────────
with st.expander("Watchlist — IV Rank", expanded=False):
    summary = load_summary()
    fig_iv = px.bar(
        summary.sort_values('IV Rank'), x='IV Rank', y='Ticker',
        orientation='h', color='IV Rank',
        color_continuous_scale='RdYlGn_r', range_color=[0, 100],
    )
    fig_iv.add_vline(x=50, line_dash='dash', line_color='white', opacity=0.4)
    fig_iv.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0),
                         coloraxis_showscale=False)
    st.plotly_chart(fig_iv, use_container_width=True)

st.caption(
    "IV Rank from 52-week realized vol · Greeks via vollib (Black-Scholes) when not from exchange"
)

# ── AI Agent sidebar ───────────────────────────────────────────────────────────
with st.sidebar:
    st.divider()
    st.markdown("#### 🤖 Ask the Agent")

    if not agent_configured():
        st.caption("Add `GEMINI_API_KEY` to `.env` to enable the AI assistant.")
    else:
        if 'agent_messages' not in st.session_state:
            st.session_state.agent_messages = []

        # Build context from computed variables
        atm_row = side[side['strike'] == atm_strike]
        _r = atm_row.iloc[0] if not atm_row.empty else None
        agent_ctx = {
            'ticker':        selected,
            'spot':          spot,
            'iv_rank':       iv_rank,
            'expiry':        expiry,
            'atm_strike':    atm_strike,
            'opt_type':      opt_type,
            'mark':          float(_r['mark']) if _r is not None else 0.0,
            'iv':            _r.get('iv')    if _r is not None else None,
            'delta':         _r.get('delta') if _r is not None else None,
            'theta':         _r.get('theta') if _r is not None else None,
            'news_headlines': [n['title'] for n in news_items[:5]],
        }

        # Display recent conversation (last 6 messages)
        for msg in st.session_state.agent_messages[-6:]:
            role_label = "**You:** " if msg['role'] == 'user' else "**Agent:** "
            st.markdown(
                f"<div style='font-size:0.78rem;margin-bottom:4px'>{role_label}"
                f"{msg['content']}</div>",
                unsafe_allow_html=True,
            )

        with st.form("agent_form", clear_on_submit=True):
            user_input = st.text_input(
                "Ask about this ticker or its options…",
                label_visibility="collapsed",
                placeholder="e.g. Is IV rank high? What does delta mean here?",
            )
            submitted = st.form_submit_button("Send", use_container_width=True)

        if submitted and user_input.strip():
            st.session_state.agent_messages.append(
                {'role': 'user', 'content': user_input.strip()}
            )
            with st.spinner(""):
                reply = ''.join(stream_response(st.session_state.agent_messages, agent_ctx))
            st.session_state.agent_messages.append(
                {'role': 'assistant', 'content': reply}
            )
            st.rerun()

        if st.session_state.get('agent_messages'):
            if st.button("Clear chat", use_container_width=True):
                st.session_state.agent_messages = []
                st.rerun()
