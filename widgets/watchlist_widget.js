// Options Evaluator — Watchlist Summary Widget
// App: Scriptable (https://scriptable.app)
//
// Shows all watchlist tickers with spot price, daily change, and IV rank.
// Best displayed as a large (4×4) home screen widget.

const API_BASE = "https://your-api-url.railway.app"   // ← paste your deployed URL here

// ── Palette ────────────────────────────────────────────────────────────────
const C = {
  bg:     new Color("#0d0d0d"),
  green:  new Color("#00C805"),
  red:    new Color("#FF5000"),
  orange: new Color("#FF9500"),
  dim:    new Color("#666666"),
  rule:   new Color("#1e1e1e"),
  white:  new Color("#ffffff"),
}

const fmt     = (n, d=2) => n != null ? n.toFixed(d) : "—"
const arrow   = pct     => pct >= 0 ? "▲" : "▼"
const cc      = pct     => pct >= 0 ? C.green : C.red
const ivColor = rank    => rank > 60 ? C.red : rank > 35 ? C.orange : C.green

async function fetchWatchlist() {
  const req = new Request(`${API_BASE}/api/watchlist`)
  req.timeoutInterval = 15
  return req.loadJSON()
}

function addRow(container, item) {
  const row = container.addStack()
  row.layoutHorizontally()
  row.centerAlignContent()
  row.setPadding(3, 0, 3, 0)

  // Ticker
  const tkr = row.addText(item.ticker.padEnd(5))
  tkr.font = Font.boldSystemFont(13)
  tkr.textColor = C.white
  tkr.lineLimit = 1

  row.addSpacer(4)

  // Spot price
  const price = row.addText(`$${fmt(item.spot)}`)
  price.font = Font.systemFont(13)
  price.textColor = cc(item.change_pct)
  price.lineLimit = 1

  row.addSpacer()

  // Change %
  const chg = row.addText(`${arrow(item.change_pct)}${fmt(Math.abs(item.change_pct), 2)}%`)
  chg.font = Font.systemFont(12)
  chg.textColor = cc(item.change_pct)
  chg.lineLimit = 1
  chg.minimumScaleFactor = 0.8

  row.addSpacer(8)

  // IV Rank bar (visual)
  const barBg = row.addStack()
  barBg.size = new Size(36, 6)
  barBg.backgroundColor = C.rule
  barBg.cornerRadius = 3

  const fillW = Math.round((item.iv_rank / 100) * 36)
  const fill  = barBg.addStack()
  fill.size   = new Size(fillW, 6)
  fill.backgroundColor = ivColor(item.iv_rank)
  fill.cornerRadius = 3

  row.addSpacer(4)

  // IV rank number
  const ivNum = row.addText(fmt(item.iv_rank, 0))
  ivNum.font = Font.systemFont(11)
  ivNum.textColor = ivColor(item.iv_rank)
  ivNum.lineLimit = 1
}

// ── Main ───────────────────────────────────────────────────────────────────
let items
try {
  items = await fetchWatchlist()
} catch(e) {
  const w = new ListWidget()
  w.backgroundColor = C.bg
  const t = w.addText(`⚠ Could not load watchlist\n${e.message}`)
  t.font = Font.systemFont(12)
  t.textColor = C.red
  Script.setWidget(w)
  if (config.runsInApp) await w.presentLarge()
  Script.complete()
  return
}

const w = new ListWidget()
w.backgroundColor = C.bg
w.setPadding(14, 14, 14, 14)

// Header
const hdr = w.addStack()
hdr.layoutHorizontally()
hdr.centerAlignContent()

const title = hdr.addText("Watchlist")
title.font = Font.boldSystemFont(15)
title.textColor = C.white

hdr.addSpacer()

// Column labels
const colHdr = hdr.addStack()
colHdr.layoutHorizontally()
const priceHdr = colHdr.addText("Price        Chg     IVR")
priceHdr.font = Font.systemFont(9)
priceHdr.textColor = C.dim

w.addSpacer(4)

const hr = w.addStack()
hr.backgroundColor = C.rule
hr.size = new Size(0, 1)

w.addSpacer(4)

// Ticker rows
for (const item of items) {
  addRow(w, item)
}

w.addSpacer()

// Footer timestamp
const now = new Date()
const ts  = now.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})
const foot = w.addText(`Updated ${ts}`)
foot.font = Font.systemFont(9)
foot.textColor = C.dim
foot.rightAlignText()

Script.setWidget(w)
if (config.runsInApp) await w.presentLarge()
Script.complete()
