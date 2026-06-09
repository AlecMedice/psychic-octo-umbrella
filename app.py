import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

from fetcher import (
    WATCHLIST, CHART_PERIODS,
    get_options_chain, get_spot_price, get_iv_rank,
    get_expirations, get_price_history, get_previous_close, get_news,
    get_fear_greed, get_vix_term_structure, get_earnings_date, get_short_interest,
    alpaca_configured, tradier_configured,
)
from greeks_calc import enrich_with_greeks, VOLLIB_OK
from agent import agent_configured, stream_response
from signals import (
    implied_move, put_call_ratios, iv_skew, unusual_volume,
    iv_vs_rv, relative_volume, momentum, opportunity_score,
)

st.set_page_config(page_title="Options Evaluator", page_icon="📈", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 1rem; }
    div[data-testid="metric-container"] {
        background: #0e0e0e;
        border-radius: 8px;
        padding: 10px 14px;
        border: 1px solid #1e1e1e;
    }
    .news-card {
        border-left: 3px solid #222;
        padding: 6px 10px;
        margin-bottom: 8px;
        border-radius: 0 4px 4px 0;
        background: #080808;
    }
    .news-title { font-size: 0.82rem; font-weight: 600; line-height: 1.3; }
    .news-meta  { font-size: 0.72rem; color: #888; margin-top: 2px; }
    .section-label {
        font-size: 0.68rem;
        font-weight: 700;
        color: #444;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        margin-bottom: 8px;
    }
    .contract-title {
        font-size: 1.05rem;
        font-weight: 700;
        padding-bottom: 10px;
        margin-bottom: 12px;
        border-bottom: 1px solid #1e1e1e;
    }
    .sig-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 0.8rem;
        padding: 5px 0;
        border-bottom: 1px solid #111;
    }
    .sig-row:last-child { border-bottom: none; }
    .sig-label { color: #666; }
    .ua-card {
        background: #0d0d0d;
        border: 1px solid #1e1e1e;
        border-radius: 8px;
        padding: 10px 12px;
    }
</style>
""", unsafe_allow_html=True)


# ── Cached loaders ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=60,  show_spinner=False)
def load_chart(ticker, period):
    return get_price_history(ticker, period)

@st.cache_data(ttl=300, show_spinner=False)
def load_previous_close(ticker):
    return get_previous_close(ticker)

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

@st.cache_data(ttl=300, show_spinner=False)
def load_fear_greed():
    return get_fear_greed()

@st.cache_data(ttl=300, show_spinner=False)
def load_vix():
    return get_vix_term_structure()

@st.cache_data(ttl=3600, show_spinner=False)
def load_earnings(ticker):
    return get_earnings_date(ticker)

@st.cache_data(ttl=86400, show_spinner=False)
def load_short_interest(ticker):
    return get_short_interest(ticker)


# ── Sidebar: ticker, filters, news ────────────────────────────────────────────
SOURCE_COLORS = {
    'Yahoo Finance':  '#6001D2', 'Google News': '#4285F4',
    'Alpha Vantage':  '#00875A', 'Finnhub':     '#FF6B35',
    'Reddit':         '#FF4500', 'Reddit Buzz': '#FF4500',
    'Marketaux':      '#0EA5E9',
}
SENTIMENT_ICONS = {'Bullish': '🟢', 'Bearish': '🔴', 'Neutral': '⚪'}

with st.sidebar:
    selected = st.selectbox("Watchlist", WATCHLIST)

    with st.expander("⚙️ Chain Filters", expanded=False):
        min_oi      = st.number_input("Min Open Interest", 0, value=0, step=100)
        num_strikes = st.slider("Strikes shown", 6, 40, 16)

    st.divider()
    st.markdown("#### 📰 News")

    news_items = load_news(selected)
    if news_items:
        for item in news_items:
            src   = item.get('source', '')
            badge_color = SOURCE_COLORS.get(src, '#555')
            badge = (f"<span style='background:{badge_color};color:#fff;font-size:0.65rem;"
                     f"padding:1px 5px;border-radius:3px;margin-right:4px'>{src}</span>"
                     if src else '')
            sent_icon = SENTIMENT_ICONS.get(item.get('sentiment', ''), '')
            meta  = ' · '.join(filter(None, [item.get('publisher', ''), item.get('when', '')]))
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
    _sources = []
    if alpaca_configured():
        _sources.append("Alpaca")
    if tradier_configured():
        _sources.append("Tradier")
    _sources.append("yfinance")
    if len(_sources) == 1:
        st.caption("yfinance only — add Alpaca or Tradier keys for exchange Greeks")
    else:
        st.caption(" → ".join(_sources) + " (fallback order)")


# ── Stock data ─────────────────────────────────────────────────────────────────
iv_rank = get_iv_rank(selected)

# ── Header ─────────────────────────────────────────────────────────────────────
h1, h2, h3 = st.columns([2, 3, 3])
with h1:
    st.markdown(f"## {selected}")
    ivr_color = '#FF5000' if iv_rank > 70 else ('#FF9500' if iv_rank > 50 else ('#00C805' if iv_rank < 30 else '#888'))
    st.markdown(
        f"<span style='font-size:0.78rem;color:{ivr_color};font-weight:600'>"
        f"IV Rank {iv_rank:.1f}</span>",
        unsafe_allow_html=True,
    )
with h3:
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

with h2:
    st.markdown(
        f"<div style='font-size:2.1rem;font-weight:700;color:{COLOR}'>${spot:.2f}</div>"
        f"<div style='color:{COLOR}'>{sign}{chg:.2f} ({sign}{chg_pct:.2f}%)</div>",
        unsafe_allow_html=True,
    )


# ── Check options availability before tabs ────────────────────────────────────
expirations = get_expirations(selected)
if not expirations:
    st.warning(f"No options available for {selected}.")
    st.stop()


# ── Main tabs ──────────────────────────────────────────────────────────────────
tab_chart, tab_chain, tab_signals = st.tabs(["📈  Chart", "🎯  Options Chain", "📊  Signals"])


# ─────────────────────────────── TAB 1: CHART ─────────────────────────────────
with tab_chart:
    _fg  = load_fear_greed()
    _vix = load_vix()

    if not hist.empty:
        short_period = period_label in ('1D', '3D', '1W')
        prev_close   = load_previous_close(selected)

        lo = float(hist['Low'].min())
        hi = float(hist['High'].max())
        if prev_close:
            lo = min(lo, prev_close)
            hi = max(hi, prev_close)
        pad = (hi - lo) * 0.08 or hi * 0.01
        y_range = [lo - pad, hi + pad]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=hist.index, y=hist['Close'],
            mode='lines',
            line=dict(color=COLOR, width=2),
            fill='tozeroy' if short_period else 'none',
            fillcolor='rgba(0,200,5,0.07)' if is_up else 'rgba(255,80,0,0.07)',
            hovertemplate='%{x|%b %d %H:%M}<br>$%{y:.2f}<extra></extra>',
        ))
        ref = prev_close if (short_period and prev_close) else float(hist['Close'].iloc[0])
        ref_label = 'Prev Close' if (short_period and prev_close) else 'Start'
        fig.add_hline(
            y=ref, line_dash='dot', line_color='#555',
            annotation_text=f"{ref_label} ${ref:.2f}",
            annotation_position="right",
            annotation_font_color='#666',
        )
        fig.update_layout(
            height=240,
            margin=dict(l=0, r=60, t=4, b=0),
            xaxis=dict(
                showgrid=False, zeroline=False,
                tickformat='%H:%M' if period_label == '1D' else '%b %d',
                tickcolor='#444', tickfont=dict(color='#888', size=10),
            ),
            yaxis=dict(
                showgrid=True, gridcolor='#1a1a1a', zeroline=False,
                tickprefix='$', tickfont=dict(color='#888', size=10),
                side='right', range=y_range,
            ),
            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

    # Quick stats row
    hi52 = float(hist['High'].max()) if not hist.empty else 0
    lo52 = float(hist['Low'].min())  if not hist.empty else 0
    qs1, qs2, qs3, qs4 = st.columns(4)
    if hi52:
        qs1.metric("Period High", f"${hi52:.2f}")
        qs2.metric("Period Low",  f"${lo52:.2f}")
    if _fg.get('score') is not None:
        fg_s     = _fg['score']
        fg_delta = _fg.get('rating', '')
        qs3.metric("Fear & Greed", f"{fg_s:.0f}", fg_delta)
    vix_vals = {k: v for k, v in _vix.items() if v is not None}
    if vix_vals:
        vix_str = '  ·  '.join(f"**{k}** {v}" for k, v in vix_vals.items())
        qs4.markdown(
            f"<div style='font-size:0.72rem;color:#555;margin-top:6px'>VIX term</div>"
            f"<div style='font-size:0.8rem;color:#888;margin-top:2px'>"
            + '  ·  '.join(f"<b style='color:#ccc'>{k}</b> {v}" for k, v in vix_vals.items())
            + "</div>",
            unsafe_allow_html=True,
        )


# ──────────────────────────── TAB 2: OPTIONS CHAIN ────────────────────────────
with tab_chain:
    ctrl_exp, ctrl_type = st.columns([5, 1])
    with ctrl_exp:
        expiry = st.radio("Expiration", expirations[:8], horizontal=True,
                          label_visibility="collapsed")
    with ctrl_type:
        opt_label = st.radio("Type", ["Calls", "Puts"], horizontal=True,
                             label_visibility="collapsed")

    opt_type = "call" if opt_label == "Calls" else "put"

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
        use_container_width=True, height=400, hide_index=True,
    )

    # ── Contract detail ────────────────────────────────────────────────────────
    st.markdown("---")

    sel_strike = st.select_slider(
        "Select strike",
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

        dot = RH_GREEN if opt_type == 'call' else "#FF5000"
        lbl = "Call" if opt_type == 'call' else "Put"
        tag = "ITM" if r['itm'] else "OTM"

        st.markdown(
            f"<div class='contract-title'>"
            f"<span style='color:{dot}'>{lbl} ${sel_strike:.2f}</span>"
            f" &nbsp;·&nbsp; {expiry}"
            f" &nbsp;·&nbsp; <span style='color:#555;font-size:0.85rem;font-weight:400'>{tag}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

        def _f(v, spec='.4f', prefix=''):
            return f"{prefix}{float(v):{spec}}" if v is not None and not pd.isna(v) else "—"

        g1, g2, g3 = st.columns(3)

        with g1:
            st.markdown("<div class='section-label'>Pricing</div>", unsafe_allow_html=True)
            p1, p2 = st.columns(2)
            p1.metric("Mark",  f"${mark:.2f}")
            p2.metric("Bid",   f"${bid:.2f}")
            p3, p4 = st.columns(2)
            p3.metric("Ask",   f"${ask:.2f}")
            p4.metric("Spread", f"${spread:.2f} ({sp_pct:.1f}%)")
            st.metric("Break Even", f"${be:.2f}")

        with g2:
            st.markdown("<div class='section-label'>Greeks</div>", unsafe_allow_html=True)
            g_a, g_b = st.columns(2)
            g_a.metric("IV",      f"{float(iv_v)*100:.1f}%" if iv_v else "—")
            g_b.metric("Delta Δ", _f(r.get('delta')))
            g_c, g_d = st.columns(2)
            g_c.metric("Gamma Γ", _f(r.get('gamma')))
            g_d.metric("Theta Θ", _f(r.get('theta'), prefix='$'))
            st.metric("Vega V",   _f(r.get('vega')))

        with g3:
            st.markdown("<div class='section-label'>Activity</div>", unsafe_allow_html=True)
            st.metric("Volume",        f"{int(vol):,}" if vol and not pd.isna(vol) else "—")
            st.metric("Open Interest", f"{int(oi):,}"  if oi  and not pd.isna(oi)  else "—")
            st.metric("Bid/Ask Width", f"{sp_pct:.1f}%")

    st.caption("IV Rank from 52-week realized vol · Greeks via vollib (Black-Scholes) when not from exchange")


# ──────────────────────────── TAB 3: SIGNALS ──────────────────────────────────
with tab_signals:
    _earn    = load_earnings(selected)
    _si      = load_short_interest(selected)
    _mom     = momentum(hist)
    _rv_sig  = iv_vs_rv(chain, hist)
    _pc      = put_call_ratios(chain)
    _im      = implied_move(chain, spot)
    _skew    = iv_skew(chain, spot)
    _rvol    = relative_volume(hist)
    _unusual = unusual_volume(chain)
    _score   = opportunity_score(iv_rank, _rv_sig, _pc, _mom, _fg.get('score'))

    # ── Opportunity score ──────────────────────────────────────────────────────
    s         = _score['score']
    direction = _score['direction']
    dir_color = '#00C805' if direction == 'sell' else ('#0EA5E9' if direction == 'buy' else '#888')
    dir_label = {'sell': 'Sell Premium', 'buy': 'Buy Premium', 'neutral': 'No Clear Edge'}[direction]
    bar_pct   = int(s / 10 * 100)

    opp_score_col, opp_notes_col = st.columns([1, 2])
    with opp_score_col:
        st.markdown(
            f"<div style='text-align:center;padding:20px 16px;background:#0d0d0d;"
            f"border:1px solid #1e1e1e;border-radius:10px'>"
            f"<div style='font-size:0.65rem;font-weight:700;color:#444;"
            f"letter-spacing:0.1em;text-transform:uppercase;margin-bottom:8px'>Opportunity Score</div>"
            f"<div style='font-size:3rem;font-weight:800;color:{dir_color};line-height:1'>{s:.1f}</div>"
            f"<div style='font-size:0.7rem;color:#444;margin-bottom:10px'>/ 10</div>"
            f"<div style='background:#1a1a1a;border-radius:4px;height:6px;margin-bottom:10px'>"
            f"<div style='width:{bar_pct}%;background:{dir_color};border-radius:4px;height:6px'></div></div>"
            f"<div style='font-size:0.82rem;font-weight:700;color:{dir_color}'>{dir_label}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with opp_notes_col:
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        for note in _score['notes']:
            st.markdown(
                f"<div style='font-size:0.8rem;color:#888;padding:5px 0 5px 10px;"
                f"border-left:2px solid #222;margin-bottom:6px'>· {note}</div>",
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Signal grid ────────────────────────────────────────────────────────────
    def _sig_row(label, value, color='#ccc', sub=''):
        sub_html = f"<span style='color:#444;font-size:0.68rem'> {sub}</span>" if sub else ''
        st.markdown(
            f"<div class='sig-row'>"
            f"<span class='sig-label'>{label}</span>"
            f"<span style='color:{color};font-weight:600'>{value}{sub_html}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    sig_left, sig_right = st.columns(2)

    with sig_left:
        st.markdown("<div class='section-label'>Market Context</div>", unsafe_allow_html=True)
        if _im is not None:
            _sig_row("Implied Move", f"±{_im:.1f}%", '#ccc', f"by {expiry}")
        if _pc.get('vol') is not None:
            pc_v = _pc['vol']
            pc_color = '#FF5000' if pc_v > 1.3 else ('#00C805' if pc_v < 0.7 else '#888')
            _sig_row("P/C Ratio (vol)", f"{pc_v:.2f}", pc_color)
        if _rv_sig.get('premium') is not None:
            prem = _rv_sig['premium']
            p_color = '#FF5000' if prem > 4 else ('#00C805' if prem < -2 else '#888')
            _sig_row("IV vs RV (30d)", f"{prem:+.1f}%", p_color,
                     f"IV {_rv_sig['avg_iv']}% / RV {_rv_sig['rv30']}%")
        if _skew is not None:
            sk_color = '#FF5000' if _skew > 3 else ('#00C805' if _skew < -1 else '#888')
            _sig_row("25Δ Skew", f"{_skew:+.1f}%", sk_color,
                     "put rich" if _skew > 0 else "call rich")
        if _fg.get('score') is not None:
            fg_s = _fg['score']
            fg_color = '#00C805' if fg_s < 30 else ('#FF5000' if fg_s > 70 else '#888')
            _sig_row("Fear & Greed", f"{fg_s:.0f} — {_fg['rating']}", fg_color)
        vix_vals = {k: v for k, v in _vix.items() if v is not None}
        if vix_vals:
            vix_str = '  '.join(f"{k} {v}" for k, v in vix_vals.items())
            st.markdown(
                f"<div style='font-size:0.72rem;color:#444;margin-top:6px'>VIX term: "
                f"<span style='color:#666'>{vix_str}</span></div>",
                unsafe_allow_html=True,
            )

    with sig_right:
        st.markdown("<div class='section-label'>Technicals</div>", unsafe_allow_html=True)
        if _mom.get('rsi') is not None:
            rsi = _mom['rsi']
            rsi_color = '#FF5000' if rsi > 70 else ('#00C805' if rsi < 35 else '#888')
            _sig_row("RSI-14", f"{rsi:.0f}", rsi_color)
        if _rvol is not None:
            rv_color = '#FF9500' if _rvol > 1.5 else '#888'
            _sig_row("Rel Volume", f"{_rvol:.1f}×", rv_color)
        if _earn:
            days_to = (pd.Timestamp(_earn) - pd.Timestamp.now()).days
            earn_color = '#FF9500' if days_to <= 14 else '#666'
            _sig_row("Next Earnings", _earn, earn_color,
                     f"{days_to}d" if days_to >= 0 else "passed")
        if _si.get('short_interest'):
            _sig_row("Short Interest", f"{_si['short_interest']:,}", '#888',
                     f"{_si['days_to_cover']:.1f}d cover")

    # ── Unusual activity ──────────────────────────────────────────────────────
    if _unusual:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            "<div class='section-label' style='color:#FF9500'>⚡ Unusual Activity</div>",
            unsafe_allow_html=True,
        )
        ua_cols = st.columns(min(len(_unusual[:4]), 4))
        for i, u in enumerate(_unusual[:4]):
            arrow   = '▲' if u['type'] == 'call' else '▼'
            u_color = '#00C805' if u['type'] == 'call' else '#FF5000'
            iv_str  = f"IV {u['iv']}%" if u['iv'] else ''
            with ua_cols[i]:
                st.markdown(
                    f"<div class='ua-card'>"
                    f"<div style='color:{u_color};font-weight:700;font-size:0.85rem'>"
                    f"{arrow} {u['type'].upper()} ${u['strike']:.0f}</div>"
                    f"<div style='color:#888;font-size:0.75rem;margin-top:4px'>"
                    f"vol {u['volume']:,} ({u['vol_oi_ratio']:.1f}× OI)"
                    f"{'  ' + iv_str if iv_str else ''}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

    # ── Watchlist IV rank ──────────────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    with st.expander("📊 Watchlist — IV Rank", expanded=True):
        summary = load_summary()
        fig_iv = px.bar(
            summary.sort_values('IV Rank'), x='IV Rank', y='Ticker',
            orientation='h', color='IV Rank',
            color_continuous_scale='RdYlGn_r', range_color=[0, 100],
        )
        fig_iv.add_vline(x=50, line_dash='dash', line_color='white', opacity=0.4)
        fig_iv.update_layout(
            height=280, margin=dict(l=0, r=0, t=10, b=0),
            coloraxis_showscale=False,
            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
        )
        st.plotly_chart(fig_iv, use_container_width=True)


# ── AI Agent sidebar (placed last: needs chain variables) ──────────────────────
with st.sidebar:
    st.divider()
    st.markdown("#### 🤖 Ask the Agent")

    if not agent_configured():
        st.caption("Add `GEMINI_API_KEY` to `.env` to enable the AI assistant.")
    else:
        if 'agent_messages' not in st.session_state:
            st.session_state.agent_messages = []

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
            atm_row = side[side['strike'] == atm_strike]
            _r_atm  = atm_row.iloc[0] if not atm_row.empty else None
            agent_ctx = {
                'ticker':         selected,
                'spot':           spot,
                'iv_rank':        iv_rank,
                'expiry':         expiry,
                'atm_strike':     atm_strike,
                'opt_type':       opt_type,
                'mark':           float(_r_atm['mark']) if _r_atm is not None else 0.0,
                'iv':             _r_atm.get('iv')    if _r_atm is not None else None,
                'delta':          _r_atm.get('delta') if _r_atm is not None else None,
                'theta':          _r_atm.get('theta') if _r_atm is not None else None,
                'news_headlines': [n['title'] for n in news_items[:5]],
            }
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
