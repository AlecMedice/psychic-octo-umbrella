import os
from typing import Generator

try:
    import anthropic
    _ANTHROPIC_OK = True
except ImportError:
    _ANTHROPIC_OK = False

_client = None


def anthropic_configured() -> bool:
    return _ANTHROPIC_OK and bool(os.getenv('ANTHROPIC_API_KEY', ''))


def _get_client():
    global _client
    if _client is None and anthropic_configured():
        _client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    return _client


def build_system_prompt(ctx: dict) -> str:
    ticker   = ctx.get('ticker', 'unknown')
    spot     = ctx.get('spot', 0.0)
    iv_rank  = ctx.get('iv_rank', 0.0)
    expiry   = ctx.get('expiry', '')
    atm      = ctx.get('atm_strike', 0.0)
    opt_type = ctx.get('opt_type', '')
    mark     = ctx.get('mark', 0.0)
    iv_val   = ctx.get('iv', None)
    delta    = ctx.get('delta', None)
    theta    = ctx.get('theta', None)
    news     = ctx.get('news_headlines', [])

    news_block = ''
    if news:
        headlines = '\n'.join(f'- {h}' for h in news[:5])
        news_block = f'\n\nRecent headlines for {ticker}:\n{headlines}'

    greeks_block = ''
    parts = []
    if iv_val:
        parts.append(f'IV {float(iv_val)*100:.1f}%')
    if delta is not None:
        parts.append(f'delta {float(delta):.3f}')
    if theta is not None:
        parts.append(f'theta ${float(theta):.4f}/day')
    if mark:
        parts.append(f'mark ${float(mark):.2f}')
    if parts:
        greeks_block = f'\nATM {opt_type} ({expiry}, strike ${atm:.2f}): {", ".join(parts)}'

    return (
        f"You are a concise options-trading assistant. The user is currently viewing {ticker} "
        f"(spot ${spot:.2f}, IV Rank {iv_rank:.1f})."
        f"{greeks_block}"
        f"{news_block}\n\n"
        "Answer questions about this ticker, its options chain, Greeks, implied volatility, "
        "and relevant news. Be concise — 2-4 sentences unless asked for more detail. "
        "Do not provide personalized financial advice or recommend specific trades."
    )


def stream_response(messages: list, ctx: dict) -> Generator[str, None, None]:
    client = _get_client()
    if client is None:
        yield "Agent unavailable — add ANTHROPIC_API_KEY to .env to enable."
        return

    api_messages = [{'role': m['role'], 'content': m['content']} for m in messages]

    try:
        with client.messages.stream(
            model="claude-opus-4-8",
            max_tokens=400,
            system=build_system_prompt(ctx),
            messages=api_messages,
        ) as stream:
            for text in stream.text_stream:
                yield text
    except Exception as e:
        yield f"Error: {e}"
