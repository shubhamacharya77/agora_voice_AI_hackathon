"""OpenAI-compatible chat models: Groq and Google Gemini.

CoO's agent works with Claude-shaped messages internally: content blocks with `tool_use`
and `tool_result`. This adapter translates them to the OpenAI chat-completions format on
the way out, and translates the reply back, so the agent, tools and approvals don't change.

Gemini 3 attaches a "thought signature" to each tool call (in `extra_content`) and rejects
the next request of the tool loop unless it comes back unchanged, so tool calls keep any
`extra_content` the provider sent.
"""

import asyncio
import json
import logging
import re
from typing import Any

import httpx2

from app.agents.llm import ModelError, ModelTurn, ToolCall

logger = logging.getLogger(__name__)

THINK_TAGS = re.compile(r"<think>.*?</think>", re.DOTALL)
# Models that accept `reasoning_effort` (low / medium / high).
REASONING_MODEL_PREFIXES = ("openai/gpt-oss", "qwen/", "gemini-3")


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for tool in tools
    ]


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return "" if content is None else str(content)


def to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Claude-shaped history -> OpenAI messages (assistant tool_calls, role "tool" results)."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    tool_names: dict[str, str] = {}
    for message in messages:
        role, content = message["role"], message["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        blocks = [b for b in content if isinstance(b, dict)]
        if role == "assistant":
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})},
                }
                | ({"extra_content": b["extra_content"]} if b.get("extra_content") else {})
                for b in blocks
                if b.get("type") == "tool_use"
            ]
            tool_names.update({c["id"]: c["function"]["name"] for c in calls})
            entry: dict[str, Any] = {"role": "assistant", "content": text or ("" if not calls else None)}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
            continue
        texts = []
        for block in blocks:  # a user turn: tool results and/or text
            if block.get("type") == "tool_result":
                call_id = block["tool_use_id"]
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": tool_names.get(call_id, ""),
                        "content": _text_of(block.get("content")),
                    }
                )
            elif block.get("type") == "text":
                texts.append(block.get("text", ""))
        if texts:
            out.append({"role": "user", "content": "\n\n".join(texts)})
    return out


def from_openai_response(data: dict[str, Any]) -> ModelTurn:
    """OpenAI response -> ModelTurn, with Claude-shaped content blocks for the tool loop."""
    choice = data["choices"][0]
    message = choice.get("message") or {}
    text = THINK_TAGS.sub("", message.get("content") or "").strip()
    calls: list[ToolCall] = []
    extras: dict[str, Any] = {}  # provider data to send back with each call (Gemini signatures)
    for call in message.get("tool_calls") or []:
        if call.get("extra_content"):
            extras[call["id"]] = call["extra_content"]
        function = call.get("function") or {}
        raw = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError:
            arguments = {"_unparseable_arguments": raw}  # the tool's validation reports it back
        if not isinstance(arguments, dict):
            arguments = {"_unparseable_arguments": raw}
        calls.append(ToolCall(id=call["id"], name=function.get("name", ""), input=arguments))
    content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    content += [
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
        | ({"extra_content": extras[c.id]} if c.id in extras else {})
        for c in calls
    ]
    usage = data.get("usage") or {}
    return ModelTurn(
        text=text,
        tool_calls=calls,
        stop_reason=choice.get("finish_reason"),
        content=content,
        usage={
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    )


class OpenAICompatibleChatModel:
    """Chat completions over plain HTTPS. `transport` lets tests stand in for the provider."""

    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
        model: str,
        max_tokens: int,
        effort: str,
        timeout: float,
        max_rate_limit_wait: float = 20.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = model
        # A rate limit that clears within this many seconds is waited out, not reported.
        self._max_rate_limit_wait = max_rate_limit_wait
        self._provider = provider
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._effort = effort
        self._timeout = timeout
        self._transport = transport

    async def respond(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn:
        if not self._api_key:
            raise ModelError(f"model setup problem: no {self._provider} API key in .env")
        body: dict[str, Any] = {
            "model": self.name,
            "messages": to_openai_messages(system, messages),
            "max_completion_tokens": self._max_tokens,
        }
        if tools:
            body["tools"] = to_openai_tools(tools)
            body["tool_choice"] = "auto"
        if self.name.startswith(REASONING_MODEL_PREFIXES):
            body["reasoning_effort"] = self._effort  # low / medium / high
        return from_openai_response(await self._post(body))

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        waited = 0.0  # total time spent waiting out rate limits for this call
        retried_tool_call = False
        for _ in range(8):
            async with httpx2.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                try:
                    response = await client.post(self._url, json=body, headers=headers)
                except httpx2.HTTPError as exc:
                    raise ModelError(f"couldn't reach {self._provider} ({type(exc).__name__})") from exc
            if response.status_code == 200:
                data = response.json()
                usage = data.get("usage") or {}
                logger.info(
                    "%s call model=%s prompt_tokens=%s completion_tokens=%s tpm_left=%s",
                    self._provider,
                    self.name,
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                    response.headers.get("x-ratelimit-remaining-tokens"),
                )
                return data
            detail, code = _error_details(response)
            status = response.status_code
            if status == 400 and not retried_tool_call and _is_bad_tool_call(detail, code):
                # The model produced a malformed tool call: one fresh attempt usually fixes it.
                logger.warning("%s rejected a malformed tool call; retrying once", self._provider)
                retried_tool_call = True
                continue
            if status == 429:
                wait = _retry_after(response)
                if waited + wait <= self._max_rate_limit_wait:
                    logger.warning("%s rate limit: waiting %.1fs, then retrying", self._provider, wait)
                    await asyncio.sleep(wait)
                    waited += wait
                    continue
                raise ModelError(f"{self._provider} rate limit reached; try again in about {round(wait)}s")
            if status in (401, 403):
                raise ModelError(f"model setup problem: {self._provider} rejected the API key ({detail})")
            if status == 404:
                raise ModelError(f"model setup problem: {self._provider} has no model '{self.name}' ({detail})")
            raise ModelError(f"{self._provider} error {status}: {detail}")
        raise ModelError(f"{self._provider} kept rejecting the request")


def _error_details(response: httpx2.Response) -> tuple[str, str]:
    """(message, code) from an error response."""
    try:
        error = response.json().get("error") or {}
        return str(error.get("message") or error)[:300], str(error.get("code") or "")
    except ValueError:
        return response.text[:300], ""


def _is_bad_tool_call(message: str, code: str) -> bool:
    # Groq: code "tool_use_failed", message "Failed to call a function. Please adjust your prompt."
    lowered = message.lower()
    return code == "tool_use_failed" or "failed_generation" in lowered or "function" in lowered or "tool" in lowered


def _retry_after(response: httpx2.Response) -> float:
    try:
        return float(response.headers.get("retry-after", "1"))
    except ValueError:
        return 1.0
