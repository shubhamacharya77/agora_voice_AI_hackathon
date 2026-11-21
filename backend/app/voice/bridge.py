"""Translating between Agora's custom-LLM calls (OpenAI chat-completions format) and CoO."""

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any


def last_user_text(messages: list[dict[str, Any]]) -> str:
    """The newest thing the user said. CoO keeps its own history, so that's all we need."""
    for message in reversed(messages or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):  # [{"type": "text", "text": "..."}]
            return " ".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            ).strip()
    return ""


def for_speech(reply: str) -> str:
    """The reply as it should be heard: no list numbers, bullets, markdown or links.

    The model is asked not to use them, but sometimes does; read aloud they sound like
    "one dot", "asterisk asterisk" or a long web address. The screen keeps the original.
    """
    text = re.sub(r"https?://\S+", "", reply)
    text = re.sub(r"(?m)^\s*(?:[-*•]|\d{1,2}[.)])\s+", "", text)  # list markers
    text = re.sub(r"[*_`#>]+", "", text)  # emphasis, code, headings, quotes
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # Each list line becomes its own sentence.
    joined = " ".join(line if line[-1] in ".!?:;," else line + "." for line in lines)
    return re.sub(r"\s{2,}", " ", joined).strip()


def speakable_pieces(reply: str) -> list[str]:
    """Split a reply into sentences, so the voice can start on the first one."""
    reply = for_speech(reply)
    pieces = [p for p in re.findall(r"[^.!?]+[.!?]*\s*", reply) if p.strip()]
    return pieces or ([reply] if reply else [])


class _Chunks:
    """Builds OpenAI-style chat.completion.chunk events for one streamed reply."""

    def __init__(self, model: str) -> None:
        self._id = f"chatcmpl-{uuid.uuid4().hex}"
        self._created = int(time.time())
        self._model = model
        self._first = True

    def _event(self, delta: dict[str, Any], finish: str | None) -> str:
        body = {
            "id": self._id,
            "object": "chat.completion.chunk",
            "created": self._created,
            "model": self._model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(body)}\n\n"

    def text(self, content: str) -> str:
        delta = {"role": "assistant", "content": content} if self._first else {"content": content}
        self._first = False
        return self._event(delta, None)

    def end(self) -> list[str]:
        return [self._event({}, "stop"), "data: [DONE]\n\n"]


def sse_reply(reply: str, *, model: str = "coo") -> Iterator[str]:
    """CoO's reply as an OpenAI-style chat.completion.chunk stream, ending with [DONE]."""
    chunks = _Chunks(model)
    for piece in speakable_pieces(reply):
        yield chunks.text(piece)
    yield from chunks.end()


async def sse_turn(
    turn: "asyncio.Task[str | None]", *, filler: str, filler_after: float, model: str = "coo"
) -> AsyncIterator[str]:
    """Stream a turn's reply. If it takes longer than `filler_after` seconds, say `filler`
    first, so the voice call never goes quiet while CoO works (or waits out a rate limit).

    The turn is always shielded: if Agora drops this request (the user spoke again), the
    turn still finishes and its answer is saved; it's just not spoken. A turn that returns
    None was folded into a newer one, so nothing is said for it.
    """
    chunks = _Chunks(model)
    try:
        reply = await asyncio.wait_for(asyncio.shield(turn), filler_after)
    except TimeoutError:
        yield chunks.text(filler + " ")
        try:
            reply = await asyncio.shield(turn)
        except Exception:
            reply = "Sorry, something went wrong. Please try again."
    except Exception:
        reply = "Sorry, something went wrong. Please try again."
    for piece in speakable_pieces(reply or ""):
        yield chunks.text(piece)
    for event in chunks.end():
        yield event
