# Robinhood Watchlist — Options Evaluator

A Streamlit app for evaluating options across a personal watchlist of equities.

![Watchlist](https://img.shields.io/badge/tickers-DIS%20JPM%20HTZ%20TMO%20CAG%20SPY%20HOOD%20BA%20ORCL-blue)

## Features

- **IV Rank bar chart** — ranks all 9 tickers by implied volatility rank (approximated from 52-week realized volatility)
- **Options chain table** — filtered by expiration, option type, min open interest, and max bid/ask spread; ATM row highlighted
- **Local Greeks** — delta, gamma, theta, vega computed via Black-Scholes (vollib) when not provided by the data source
- **Volatility smile chart** — IV plotted against strike with ATM marker
- **ATM summary metrics** — spot price, ATM strike, IV, delta, and daily theta at a glance

## Stack

| Layer | Tool |
|---|---|
| UI | [Streamlit](https://streamlit.io) + [Plotly](https://plotly.com/python/) |
| Options data | [Alpaca Markets API](https://alpaca.markets) (free tier) with [yfinance](https://pypi.org/project/yfinance/) fallback |
| Greeks / IV | [vollib](https://vollib.org) (Black-Scholes) |
| Historical vol | yfinance 1-year price history |

## Quickstart

```bash
git clone https://github.com/alecmedice/psychic-octo-umbrella.git
cd psychic-octo-umbrella
pip install -r requirements.txt
cp .env.example .env        # optionally add Alpaca API keys
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501).

## API Keys (optional)

Copy `.env.example` to `.env` and fill in your [Alpaca Markets](https://app.alpaca.markets) keys:

```
ALPACA_API_KEY=your_api_key_here
ALPACA_SECRET_KEY=your_secret_key_here
```

Without keys the app falls back to yfinance, which provides options chains with IV but no exchange-sourced Greeks. Greeks are then computed locally via Black-Scholes.

## Data Notes

- **IV Rank** is approximated from rolling 30-day realized volatility over the past 52 weeks — not true options-derived IV rank. For production use, consider [Tradier](https://tradier.com) (ORATS-sourced Greeks) or [Market Data App](https://www.marketdata.app).
- **yfinance** does not return Greeks natively; they are filled in by vollib using the mid-price as the market price input.
- **Alpaca free tier** provides real-time options snapshots including exchange-computed Greeks and implied volatility.

## Watchlist

Hardcoded from a Robinhood watchlist snapshot:

`DIS` `JPM` `HTZ` `TMO` `CAG` `SPY` `HOOD` `BA` `ORCL`

To change the watchlist, edit the `WATCHLIST` list at the top of `fetcher.py`.
