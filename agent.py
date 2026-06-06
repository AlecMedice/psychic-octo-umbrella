import os
from typing import Generator

try:
    from google import genai
    from google.genai import types
    _GEMINI_OK = True
except ImportError:
    _GEMINI_OK = False


def agent_configured() -> bool:
    return _GEMINI_OK and bool(os.getenv('GEMINI_API_KEY', ''))


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

    parts = []
    if iv_val:
        parts.append(f'IV {float(iv_val)*100:.1f}%')
    if delta is not None:
        parts.append(f'delta {float(delta):.3f}')
    if theta is not None:
        parts.append(f'theta ${float(theta):.4f}/day')
    if mark:
        parts.append(f'mark ${float(mark):.2f}')
    greeks_block = (
        f'\nATM {opt_type} ({expiry}, strike ${atm:.2f}): {", ".join(parts)}'
        if parts else ''
    )

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
    if not agent_configured():
        yield "Agent unavailable — add GEMINI_API_KEY to .env to enable."
        return

    client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))

    # Convert to Gemini format: roles are "user" / "model"
    contents = [
        types.Content(
            role='model' if m['role'] == 'assistant' else 'user',
            parts=[types.Part(text=m['content'])],
        )
        for m in messages
    ]

    config = types.GenerateContentConfig(
        system_instruction=build_system_prompt(ctx),
        max_output_tokens=400,
    )

    try:
        for chunk in client.models.generate_content_stream(
            model='gemini-2.0-flash',
            contents=contents,
            config=config,
        ):
            if chunk.text:
                yield chunk.text
    except Exception as e:
        yield f"Error: {e}"
