"""
Background alert scheduler for political / Trump feed.

Polls get_political_news() every 90 seconds. New posts that Gemini tags as
a high-signal category (Tariffs/Trade, Geopolitics, Fed/Monetary) with a
non-neutral direction (risk-on, risk-off) are fired to Telegram.

Deduplication is persisted in .seen_posts.json so restarts don't re-alert.
On the very first run the file doesn't exist, so we seed it with the current
posts without sending — avoiding a spam burst on first deploy.

Required env vars (see .env.example):
    TELEGRAM_BOT_TOKEN   — from @BotFather
    TELEGRAM_CHAT_ID     — your personal or group chat ID

Optional:
    TRUTH_SOCIAL_RSS_URL — override the default RSS archive
    GEMINI_API_KEY       — without this, posts pass through unclassified
                           and NO alerts fire (can't filter without tags)
"""
import json
import logging
import os
import threading
from datetime import time
from datetime import datetime
from pathlib import Path

import requests as _requests
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

from fetcher import WATCHLIST, get_political_news
from agent import agent_configured, classify_political_posts

load_dotenv()
log = logging.getLogger(__name__)

_SEEN_FILE      = Path('.seen_posts.json')
_POLL_SECONDS   = 90
_MAX_SEEN       = 500   # cap file size

ALERT_CATEGORIES = {'Tariffs/Trade', 'Geopolitics', 'Fed/Monetary'}
ALERT_DIRECTIONS = {'risk-on', 'risk-off'}

_CAT_EMOJI = {
    'Tariffs/Trade': '💰',
    'Geopolitics':   '⚔️',
    'Fed/Monetary':  '🏦',
    'Corporate':     '🏢',
    'Other':         '📢',
}
_DIR_LABEL = {
    'risk-on':  '🟢 RISK-ON',
    'risk-off': '🔴 RISK-OFF',
    'neutral':  '⚪ NEUTRAL',
}


# ── Config checks ──────────────────────────────────────────────────────────────

def telegram_configured() -> bool:
    return bool(os.getenv('TELEGRAM_BOT_TOKEN') and os.getenv('TELEGRAM_CHAT_ID'))


# ── Persistence ────────────────────────────────────────────────────────────────

def _load_seen() -> set:
    try:
        return set(json.loads(_SEEN_FILE.read_text()))
    except Exception:
        return set()


def _save_seen(seen: set):
    try:
        recent = list(seen)[-_MAX_SEEN:]
        _SEEN_FILE.write_text(json.dumps(recent))
    except Exception as e:
        log.warning('Could not save seen_posts: %s', e)


def _post_key(post: dict) -> str:
    return post['title'].strip()[:80]


# ── Telegram sender ────────────────────────────────────────────────────────────

def _send_telegram(text: str):
    token   = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    try:
        resp = _requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
            timeout=10,
        )
        if not resp.ok:
            log.warning('Telegram error %s: %s', resp.status_code, resp.text[:200])
    except Exception as e:
        log.warning('Telegram send failed: %s', e)


def _build_message(post: dict, tag: dict) -> str:
    cat   = tag.get('category', 'Other')
    dire  = tag.get('direction', 'neutral')
    emoji = _CAT_EMOJI.get(cat, '📢')
    dlab  = _DIR_LABEL.get(dire, '')
    when  = post.get('when', '')

    tickers = tag.get('tickers') or []
    tk_line = ''
    if tickers:
        parts = []
        for t in tickers:
            arrow = '▲' if t.get('bias') == 'up' else '▼'
            parts.append(f"{arrow} {t.get('ticker', '')}")
        tk_line = f"\n<b>Watchlist:</b> {', '.join(parts)}"

    rationale = tag.get('rationale', '')
    rat_line  = f"\n<i>{rationale}</i>" if rationale else ''
    when_line = f"  <i>{when}</i>" if when else ''

    return (
        f"{emoji} <b>[{cat.upper()}]</b>  {dlab}{when_line}\n\n"
        f"{post['title']}"
        f"{tk_line}"
        f"{rat_line}"
    )


# ── Core check ─────────────────────────────────────────────────────────────────

def check_and_alert(seed_only: bool = False):
    """Fetch new posts, classify, and fire Telegram alerts.

    seed_only=True: populate seen set without sending (used on first run).
    """
    if not telegram_configured() and not seed_only:
        return

    seen  = _load_seen()
    posts = get_political_news(limit=15)
    new   = [p for p in posts if _post_key(p) not in seen]

    if not new:
        return

    if seed_only:
        for p in new:
            seen.add(_post_key(p))
        _save_seen(seen)
        log.info('Alerts seeded: %d posts marked as seen (no alerts sent)', len(new))
        return

    tags = (
        classify_political_posts([p['title'] for p in new], WATCHLIST)
        if agent_configured()
        else [{} for _ in new]
    )

    sent = 0
    for post, tag in zip(new, tags):
        key  = _post_key(post)
        seen.add(key)

        cat  = tag.get('category', '')
        dire = tag.get('direction', '')

        if cat in ALERT_CATEGORIES and dire in ALERT_DIRECTIONS:
            _send_telegram(_build_message(post, tag))
            sent += 1

    _save_seen(seen)
    if sent:
        log.info('Sent %d political alert(s)', sent)


# ── Scheduler ──────────────────────────────────────────────────────────────────

def _market_hours() -> bool:
    """True if current ET time is within regular market hours Mon–Fri."""
    from datetime import timezone, timedelta
    et_now = datetime.now(timezone(timedelta(hours=-4)))  # ET (approx; ignores DST edge)
    if et_now.weekday() >= 5:
        return False
    return time(9, 30) <= et_now.time() <= time(16, 0)


def _maybe_trade():
    """Run trade cycle only during market hours."""
    if not _market_hours():
        return
    try:
        from trader import run_trading_cycle
        run_trading_cycle()
    except Exception as e:
        log.warning('Trading cycle error: %s', e)


def start_scheduler() -> BackgroundScheduler:
    """Start the background polling job. Call once via @st.cache_resource."""
    # On first run: seed without alerting, then schedule normal polls.
    threading.Thread(
        target=lambda: check_and_alert(seed_only=not _SEEN_FILE.exists()),
        daemon=True,
    ).start()

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(check_and_alert, 'interval', seconds=_POLL_SECONDS,
                      id='political_alert', max_instances=1)
    scheduler.add_job(_maybe_trade, 'interval', seconds=300,
                      id='ai_trader', max_instances=1)
    scheduler.start()
    log.info('Schedulers started: political alerts every %ds, trade cycle every 300s', _POLL_SECONDS)
    return scheduler
