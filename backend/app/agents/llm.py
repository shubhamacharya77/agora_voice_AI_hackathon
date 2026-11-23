"""The chat model behind the central agent.

Agents talk to a small `ChatModel` interface, so the provider or model can change
(LLM_PROVIDER / LLM_MODEL) without touching agent code, and tests can script replies.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from app.config import Settings

REFUSAL_REPLY = "Sorry, I can't help with that request."


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ModelTurn:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str | None
    # The raw content blocks (thinking included), sent back unchanged in the tool loop.
    content: list[Any]
    usage: dict[str, int] = field(default_factory=dict)


class ModelError(RuntimeError):
    """The model couldn't be reached or rejected the request."""


class ChatModel(Protocol):
    name: str

    async def respond(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn: ...


ClientFactory = Callable[[], anthropic.AsyncAnthropic | anthropic.AsyncAnthropicBedrockMantle]


class ClaudeChatModel:
    """Claude through the official Anthropic SDK (Bedrock Mantle or the first-party API)."""

    def __init__(
        self, client_factory: ClientFactory, *, model: str, max_tokens: int, effort: str
    ) -> None:
        self.name = model
        self._client_factory = client_factory
        self._client: anthropic.AsyncAnthropic | anthropic.AsyncAnthropicBedrockMantle | None = None
        self._max_tokens = max_tokens
        self._effort = effort

    async def respond(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn:
        request: dict[str, Any] = {
            "model": self.name,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._effort},
        }
        if tools:
            request["tools"] = tools
        try:
            # Built on first use, so a missing credential becomes an honest error reply.
            if self._client is None:
                self._client = self._client_factory()
            response = await self._client.messages.create(**request)
        except (
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.NotFoundError,
        ) as exc:
            # Not retryable: bad credentials, no access to the model, or a wrong model id.
            raise ModelError(f"model setup problem ({type(exc).__name__}): {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise ModelError("the model is rate-limited right now") from exc
        except anthropic.APIConnectionError as exc:  # includes timeouts
            raise ModelError(f"couldn't reach the model ({type(exc).__name__})") from exc
        except anthropic.APIStatusError as exc:
            raise ModelError(f"model error {exc.status_code}: {exc}") from exc
        except Exception as exc:  # e.g. an expired AWS login, raised while signing
            raise ModelError(f"model call failed ({type(exc).__name__}): {exc}") from exc

        if response.stop_reason == "refusal":
            return ModelTurn(
                text=REFUSAL_REPLY,
                tool_calls=[],
                stop_reason="refusal",
                content=[{"type": "text", "text": REFUSAL_REPLY}],
            )
        text = "".join(block.text for block in response.content if block.type == "text")
        tool_calls = [
            ToolCall(id=block.id, name=block.name, input=dict(block.input))
            for block in response.content
            if block.type == "tool_use"
        ]
        return ModelTurn(
            text=text.strip(),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
            # Plain dicts of exactly what the API returned: the SDK accepts them back in the
            # tool loop, and LangGraph's checkpointer can store them as-is.
            content=[block.to_dict(mode="json") for block in response.content],
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )


def _secret(value: Any) -> str | None:
    return (value.get_secret_value() or None) if value is not None else None


def build_chat_model(settings: Settings) -> ChatModel:
    if settings.llm_provider == "groq":
        from app.agents.llm_openai import OpenAICompatibleChatModel

        return OpenAICompatibleChatModel(
            provider="Groq",
            base_url=settings.groq_base_url,
            api_key=_secret(settings.groq_api_key) or "",
            model=settings.llm_model,
            # Voice replies are short, and a small cap keeps each request well inside
            # Groq's per-minute token limits.
            max_tokens=min(settings.llm_max_tokens, 2048),
            effort=settings.llm_effort,
            timeout=settings.llm_timeout_seconds,
            max_rate_limit_wait=settings.llm_rate_limit_max_wait_seconds,
        )
    if settings.llm_provider == "gemini":
        from app.agents.llm_openai import OpenAICompatibleChatModel

        return OpenAICompatibleChatModel(
            provider="Gemini",
            base_url=settings.gemini_base_url,
            api_key=_secret(settings.gemini_api_key) or "",
            model=settings.llm_model,
            # Gemini's thinking counts against this cap, so leave it room.
            max_tokens=settings.llm_max_tokens,
            effort=settings.llm_effort,
            timeout=settings.llm_timeout_seconds,
            max_rate_limit_wait=settings.llm_rate_limit_max_wait_seconds,
        )
    if settings.llm_provider == "bedrock":

        def factory() -> anthropic.AsyncAnthropicBedrockMantle:
            return anthropic.AsyncAnthropicBedrockMantle(
                aws_region=settings.aws_region or None,
                aws_profile=settings.aws_profile or None,
                api_key=_secret(settings.aws_bearer_token_bedrock),
                timeout=settings.llm_timeout_seconds,
            )

    else:

        def factory() -> anthropic.AsyncAnthropic:
            return anthropic.AsyncAnthropic(
                api_key=_secret(settings.anthropic_api_key),
                timeout=settings.llm_timeout_seconds,
            )

    return ClaudeChatModel(
        factory,
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        effort=settings.llm_effort,
    )
