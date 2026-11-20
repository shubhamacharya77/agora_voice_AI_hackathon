"""Check that the configured model answers, and how fast.

Usage (from backend/):  uv run python -m app.agents.check_model
"""

import asyncio
import time

from app.agents.llm import ModelError, build_chat_model
from app.config import get_settings


async def _main() -> int:
    settings = get_settings()
    model = build_chat_model(settings)
    print(
        f"provider={settings.llm_provider} model={settings.llm_model} "
        f"region={settings.aws_region or '-'} effort={settings.llm_effort}"
    )
    started = time.perf_counter()
    try:
        turn = await model.respond(
            system="Reply with one short, friendly sentence.",
            messages=[{"role": "user", "content": "Say hello to the CoO team."}],
            tools=[],
        )
    except ModelError as exc:
        print(f"FAILED after {time.perf_counter() - started:.2f}s: {exc}")
        return 1
    print(f"OK in {time.perf_counter() - started:.2f}s: {turn.text}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
