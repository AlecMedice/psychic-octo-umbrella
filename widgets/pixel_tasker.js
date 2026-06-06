// Options Evaluator — Pixel / Android Widget
// App: Tasker (https://tasker.joaoapps.com, ~$3.49 on Play Store)
//
// ── Setup (one time) ──────────────────────────────────────────────────────
// 1. Install Tasker
// 2. New Task → "Fetch Options" → Add Action → Script → JavaScript
// 3. Paste this file, set TICKERS and API_BASE below
// 4. New Profile → Time → Every 5 minutes → link "Fetch Options" task
// 5. Add widget: long-press home screen → Widgets → Tasker → Widget 4×2
//    Display text: see WIDGET TEXT TEMPLATE at bottom of this file
//
// For a richer widget UI, paste the KWGT formula (at the bottom) into
// a KWGT text element instead of a plain Tasker widget.
// ──────────────────────────────────────────────────────────────────────────

const API_BASE = "https://your-api-url.railway.app"  // ← your Railway URL
const TICKERS  = ["SPY", "HOOD", "BA"]               // tickers to cycle / show

async function fetchTicker(ticker) {
  const resp = await fetch(`${API_BASE}/api/ticker/${ticker}`)
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}

function fmt(n, d = 2) {
  return n != null ? Number(n).toFixed(d) : "—"
}

function arrow(pct) { return pct >= 0 ? "▲" : "▼" }
function sign(pct)  { return pct >= 0 ? "+" : ""  }

async function main() {
  const errors = []

  for (const ticker of TICKERS) {
    try {
      const d = await fetchTicker(ticker)

      const prefix    = ticker.toLowerCase()   // e.g. "spy"
      const changeTxt = `${arrow(d.change_pct)} ${sign(d.change_pct)}${fmt(Math.abs(d.change_pct))}%`
      const callTxt   = d.atm_call
        ? `C $${fmt(d.atm_call.mark)}  Δ${fmt(d.atm_call.delta, 3)}`
        : "—"
      const putTxt    = d.atm_put
        ? `P $${fmt(d.atm_put.mark)}  Δ${fmt(d.atm_put.delta, 3)}`
        : "—"

      // Tasker globals — reference as %OPT_SPY_SPOT, %OPT_SPY_CHANGE, etc.
      setGlobal(`OPT_${ticker}_SPOT`,    `$${fmt(d.spot)}`)
      setGlobal(`OPT_${ticker}_CHANGE`,  changeTxt)
      setGlobal(`OPT_${ticker}_IVRANK`,  fmt(d.iv_rank, 1))
      setGlobal(`OPT_${ticker}_CALL`,    callTxt)
      setGlobal(`OPT_${ticker}_PUT`,     putTxt)
      setGlobal(`OPT_${ticker}_EXPIRY`,  d.expiry || "—")
      setGlobal(`OPT_${ticker}_STRIKE`,  d.atm_call ? `$${fmt(d.atm_call.strike, 0)}` : "—")
    } catch (e) {
      errors.push(`${ticker}: ${e.message}`)
    }
  }

  const ts = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
  setGlobal("OPT_UPDATED", ts)

  if (errors.length) {
    flash("Options widget error: " + errors.join(", "))
  }
}

main()


/* ── WIDGET TEXT TEMPLATE ─────────────────────────────────────────────────────
   In a Tasker 4×2 widget, set the text to:

   SPY  %OPT_SPY_SPOT  %OPT_SPY_CHANGE  IVR %OPT_SPY_IVRANK
   %OPT_SPY_CALL    %OPT_SPY_PUT
   Exp %OPT_SPY_EXPIRY  ·  %OPT_UPDATED

   Repeat rows for HOOD, BA, etc. as needed.
   ─────────────────────────────────────────────────────────────────────────── */


/* ── KWGT FORMULA (paste into a KWGT text element) ────────────────────────────
   KWGT (https://play.google.com/store/apps/details?id=org.kustom.widget)
   lets you build a more visual widget. Use the HTTP data source:

   Data Source URL:
     https://your-api-url.railway.app/api/watchlist

   Then reference fields with $json(...) formula, e.g.:
     $json(0, spot)$           → spot price of first ticker
     $json(0, change_pct)$     → daily change %
     $json(0, iv_rank)$        → IV rank

   Full text formula for one row:
     $si(json, 0, ticker)$ · $$si(json, 0, spot)$ · $si(json, 0, change_pct)$% · IVR $si(json, 0, iv_rank)$
   ─────────────────────────────────────────────────────────────────────────── */
