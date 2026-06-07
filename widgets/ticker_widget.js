// Options Evaluator — Single Ticker Widget
// App: Scriptable (https://scriptable.app)
//
// Setup:
//   1. Deploy api.py to Railway/Render and paste the URL below
//   2. In Scriptable, long-press the widget → Edit Widget → Parameter → enter ticker (e.g. SPY)
//   3. Supports small and medium home screen sizes

const API_BASE = "https://your-api-url.railway.app"   // ← paste your deployed URL here
const ticker   = args.widgetParameter?.toUpperCase() || "SPY"

// ── Palette (Robinhood-inspired dark theme) ────────────────────────────────
const C = {
  bg:     new Color("#0d0d0d"),
  green:  new Color("#00C805"),
  red:    new Color("#FF5000"),
  orange: new Color("#FF9500"),
  blue:   new Color("#0EA5E9"),
  dim:    new Color("#888888"),
  rule:   new Color("#2a2a2a"),
  white:  new Color("#ffffff"),
}

// ── Helpers ────────────────────────────────────────────────────────────────
const fmt   = (n, d=2) => n != null ? n.toFixed(d) : "—"
const arrow = pct     => pct >= 0 ? "▲" : "▼"
const cc    = pct     => pct >= 0 ? C.green : C.red
const ivColor = rank  => rank > 60 ? C.red : rank > 35 ? C.orange : C.green

async function fetchTicker(t) {
  const req = new Request(`${API_BASE}/api/ticker/${t}`)
  req.timeoutInterval = 12
  return req.loadJSON()
}

// ── Small widget (2×2) ─────────────────────────────────────────────────────
function buildSmall(w, d) {
  w.setPadding(12, 14, 12, 14)

  const tkr = w.addText(d.ticker)
  tkr.font = Font.boldSystemFont(18)
  tkr.textColor = C.white

  w.addSpacer(2)

  const price = w.addText(`$${fmt(d.spot)}`)
  price.font = Font.boldSystemFont(22)
  price.textColor = cc(d.change_pct)

  const chgLine = w.addText(`${arrow(d.change_pct)} ${fmt(Math.abs(d.change_pct))}%`)
  chgLine.font = Font.systemFont(12)
  chgLine.textColor = cc(d.change_pct)

  w.addSpacer(6)

  const ivLine = w.addText(`IV Rank  ${fmt(d.iv_rank, 1)}`)
  ivLine.font = Font.systemFont(11)
  ivLine.textColor = ivColor(d.iv_rank)

  w.addSpacer(4)

  if (d.atm_call) {
    const cl = w.addText(`C $${fmt(d.atm_call.mark)}  Δ${fmt(d.atm_call.delta, 3)}`)
    cl.font = Font.systemFont(11)
    cl.textColor = C.green
  }
  if (d.atm_put) {
    const pl = w.addText(`P $${fmt(d.atm_put.mark)}  Δ${fmt(d.atm_put.delta, 3)}`)
    pl.font = Font.systemFont(11)
    pl.textColor = C.red
  }
}

// ── Medium widget (4×2) ────────────────────────────────────────────────────
function buildMedium(w, d) {
  w.setPadding(14, 16, 14, 16)

  // Row 1: ticker name + IV rank pill
  const row1 = w.addStack()
  row1.layoutHorizontally()
  row1.centerAlignContent()

  const tkr = row1.addText(d.ticker)
  tkr.font = Font.boldSystemFont(20)
  tkr.textColor = C.white

  row1.addSpacer()

  const ivStack = row1.addStack()
  ivStack.layoutVertically()
  const ivVal = ivStack.addText(fmt(d.iv_rank, 1))
  ivVal.font = Font.boldSystemFont(17)
  ivVal.textColor = ivColor(d.iv_rank)
  ivVal.rightAlignText()
  const ivLbl = ivStack.addText("IV RANK")
  ivLbl.font = Font.systemFont(8)
  ivLbl.textColor = C.dim
  ivLbl.rightAlignText()

  w.addSpacer(5)

  // Row 2: price + change
  const row2 = w.addStack()
  row2.layoutHorizontally()
  row2.centerAlignContent()

  const price = row2.addText(`$${fmt(d.spot)}`)
  price.font = Font.boldSystemFont(28)
  price.textColor = cc(d.change_pct)

  row2.addSpacer(10)

  const chg = row2.addText(`${arrow(d.change_pct)} ${fmt(Math.abs(d.change_pct))}%`)
  chg.font = Font.semiboldSystemFont(14)
  chg.textColor = cc(d.change_pct)

  w.addSpacer(8)

  // Divider
  const hr = w.addStack()
  hr.backgroundColor = C.rule
  hr.size = new Size(0, 1)

  w.addSpacer(8)

  // Row 3: ATM call | put
  const row3 = w.addStack()
  row3.layoutHorizontally()

  // Call column
  const callCol = row3.addStack()
  callCol.layoutVertically()

  const callHdr = callCol.addText("CALL")
  callHdr.font = Font.boldSystemFont(9)
  callHdr.textColor = C.green

  callCol.addSpacer(2)

  const callMark = callCol.addText(d.atm_call ? `$${fmt(d.atm_call.mark)}` : "—")
  callMark.font = Font.boldSystemFont(17)
  callMark.textColor = C.white

  const callDelta = callCol.addText(d.atm_call ? `Δ ${fmt(d.atm_call.delta, 3)}` : "")
  callDelta.font = Font.systemFont(11)
  callDelta.textColor = C.dim

  row3.addSpacer()

  // Vertical rule
  const vr = row3.addStack()
  vr.backgroundColor = C.rule
  vr.size = new Size(1, 44)

  row3.addSpacer()

  // Put column
  const putCol = row3.addStack()
  putCol.layoutVertically()

  const putHdr = putCol.addText("PUT")
  putHdr.font = Font.boldSystemFont(9)
  putHdr.textColor = C.red

  putCol.addSpacer(2)

  const putMark = putCol.addText(d.atm_put ? `$${fmt(d.atm_put.mark)}` : "—")
  putMark.font = Font.boldSystemFont(17)
  putMark.textColor = C.white

  const putDelta = putCol.addText(d.atm_put ? `Δ ${fmt(d.atm_put.delta, 3)}` : "")
  putDelta.font = Font.systemFont(11)
  putDelta.textColor = C.dim

  // Footer: strike + expiry
  w.addSpacer(6)
  const strike = d.atm_call?.strike ?? d.atm_put?.strike
  if (strike && d.expiry) {
    const footer = w.addText(`Strike $${fmt(strike, 0)}  ·  exp ${d.expiry}`)
    footer.font = Font.systemFont(9)
    footer.textColor = C.dim
  }
}

// ── Error widget ───────────────────────────────────────────────────────────
function buildError(msg) {
  const w = new ListWidget()
  w.backgroundColor = C.bg
  w.setPadding(12, 14, 12, 14)
  const t = w.addText(`⚠ ${ticker}`)
  t.font = Font.boldSystemFont(14)
  t.textColor = C.red
  w.addSpacer(4)
  const e = w.addText(msg)
  e.font = Font.systemFont(11)
  e.textColor = C.dim
  e.minimumScaleFactor = 0.7
  return w
}

// ── Main ───────────────────────────────────────────────────────────────────
let data
try {
  data = await fetchTicker(ticker)
} catch(e) {
  const w = buildError(e.message)
  Script.setWidget(w)
  if (config.runsInApp) await w.presentMedium()
  Script.complete()
  return
}

const w  = new ListWidget()
w.backgroundColor = C.bg

const size = config.widgetFamily
if (size === "small") {
  buildSmall(w, data)
} else {
  buildMedium(w, data)   // medium or large both use medium layout
}

Script.setWidget(w)
if (config.runsInApp) await w.presentMedium()
Script.complete()
