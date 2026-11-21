"""Check every external piece CoO needs, live, without printing any secret.

Usage (from backend/):  uv run python -m app.check_setup
"""

import asyncio
import sys
import time
import uuid

import httpx2

from app.agents.llm import ModelError, build_chat_model
from app.config import Settings, get_settings, secret
from app.db import Database
from app.mail import GmailMailbox, MailError
from app.websearch import TavilyClient, WebSearchError
from app.voice.agora import VOICE_KEY_HEADER, agent_token

OK, MISSING, FAIL = "[ok]", "[--]", "[!!]"
MURF_VOICES_URL = "https://api.murf.ai/v1/speech/voices"


def show(mark: str, name: str, detail: str) -> bool:
    print(f"{mark} {name:<22} {detail}")
    return mark == OK


async def check_database(settings: Settings) -> bool:
    db = Database(settings.database_url, settings.db_connect_timeout_seconds)
    try:
        if await db.ping():
            return show(OK, "Database", "PostgreSQL answers")
        return show(FAIL, "Database", "not reachable. Start it: docker compose up -d db")
    finally:
        await db.dispose()


async def check_model(settings: Settings) -> bool:
    started = time.perf_counter()
    try:
        await build_chat_model(settings).respond(
            system="Reply with one word.", messages=[{"role": "user", "content": "Ping?"}], tools=[]
        )
    except ModelError as exc:
        hints = {
            "groq": " Key: https://console.groq.com/keys -> GROQ_API_KEY",
            "anthropic": " Key: https://console.anthropic.com -> ANTHROPIC_API_KEY",
            "bedrock": " Run: aws login",
        }
        return show(FAIL, "AI model", f"{settings.llm_provider} {settings.llm_model}: {exc}.{hints[settings.llm_provider]}")
    return show(OK, "AI model", f"{settings.llm_model} answered in {time.perf_counter() - started:.1f}s")


def check_agora(settings: Settings) -> bool:
    if not settings.agora_app_id or not secret(settings.agora_app_certificate):
        return show(MISSING, "Agora", "AGORA_APP_ID / AGORA_APP_CERTIFICATE missing (console.agora.io)")
    try:
        agent_token(settings, "coo-setup-check", 999_001)
    except Exception as exc:
        return show(FAIL, "Agora", f"credentials rejected locally: {exc}")
    auth = "Customer ID/Secret" if settings.agora_customer_id and secret(settings.agora_customer_secret) else "token"
    return show(OK, "Agora", f"App ID + Certificate look valid (REST auth: {auth})")


async def check_murf(settings: Settings) -> bool:
    key = secret(settings.murf_api_key)
    if not key:
        return show(MISSING, "Murf", "MURF_API_KEY missing (https://murf.ai/api/dashboard)")
    async with httpx2.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(MURF_VOICES_URL, params={"model": "falcon-2.1"}, headers={"api-key": key})
        except httpx2.HTTPError as exc:
            return show(FAIL, "Murf", f"couldn't reach Murf ({type(exc).__name__})")
    if response.status_code == 403:
        return show(FAIL, "Murf", "the API key was rejected (invalid or expired)")
    if response.status_code != 200:
        return show(FAIL, "Murf", f"Murf answered {response.status_code}")
    voices = response.json()
    ids = {v.get("voiceId") for v in voices}
    indian = sorted(v.get("voiceId") for v in voices if v.get("locale") == "en-IN")[:8]
    if settings.murf_voice_id not in ids:
        return show(FAIL, "Murf", f"key works, but voice '{settings.murf_voice_id}' isn't a Falcon voice. "
                    f"Try MURF_VOICE_ID= one of: {', '.join(indian) or 'see the Murf voice library'}")  # fmt: skip
    note = f" (Indian English voices: {', '.join(indian)})" if indian else ""
    return show(OK, "Murf", f"key works; voice '{settings.murf_voice_id}' is available{note}")


async def check_gmail(settings: Settings) -> bool:
    if settings.gmail_missing:
        step = "uv run python -m app.mail.connect" if settings.gmail_missing == ["GMAIL_REFRESH_TOKEN"] else \
            "Google Cloud console -> Clients -> Desktop app"  # fmt: skip
        return show(MISSING, "Gmail", f"{', '.join(settings.gmail_missing)} missing ({step})")
    box = GmailMailbox.from_settings(settings)
    try:
        profile = await box.client.profile()
    except MailError as exc:
        return show(FAIL, "Gmail", str(exc))
    contacts = f"; {len(box.contacts)} contact(s) in MAIL_CONTACTS" if box.contacts else ""
    return show(OK, "Gmail", f"connected as {profile.get('emailAddress')}{contacts}")


async def check_web(settings: Settings) -> bool:
    key = secret(settings.tavily_api_key)
    if not key:
        return show(MISSING, "Web search", "TAVILY_API_KEY missing (https://app.tavily.com)")
    started = time.perf_counter()
    try:
        results = await TavilyClient(key).search("top news headlines today", news=True)
    except WebSearchError as exc:
        return show(FAIL, "Web search", str(exc))
    return show(OK, "Web search", f"Tavily answered in {time.perf_counter() - started:.1f}s with {len(results)} headline(s)")


def check_shared_key(settings: Settings) -> bool:
    if not secret(settings.custom_llm_api_key):
        return show(MISSING, "Voice shared key", "CUSTOM_LLM_API_KEY missing")
    return show(OK, "Voice shared key", "set")


async def check_tunnel(settings: Settings) -> bool:
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        return show(MISSING, "Public URL", "PUBLIC_BASE_URL missing (run: ngrok http 8000)")
    headers = {"ngrok-skip-browser-warning": "1"}
    async with httpx2.AsyncClient(timeout=15.0) as client:
        try:
            health = await client.get(f"{base}/health", headers=headers)
            # What Agora will do: call the voice endpoint with the shared key. With no such
            # session, CoO answers 404, which proves the route and the key both work.
            probe = await client.post(
                f"{base}/voice/llm/{uuid.uuid4()}/chat/completions",
                headers={**headers, VOICE_KEY_HEADER: secret(settings.custom_llm_api_key)},
                json={"messages": []},
            )
        except httpx2.HTTPError as exc:
            return show(FAIL, "Public URL", f"{base} unreachable ({type(exc).__name__}). Is ngrok running?")
    if health.status_code != 200:
        return show(FAIL, "Public URL", f"{base}/health answered {health.status_code}. Is the backend running?")
    if probe.status_code != 404:
        return show(FAIL, "Public URL", f"voice endpoint answered {probe.status_code} (expected 404 for a test call)")
    return show(OK, "Public URL", f"{base} reaches this backend; the voice endpoint accepts the shared key")


async def main() -> int:
    settings = get_settings()
    print("CoO setup check (no secrets are printed)\n")
    results = [
        await check_database(settings),
        await check_model(settings),
        check_agora(settings),
        await check_murf(settings),
        check_shared_key(settings),
        await check_tunnel(settings),
        await check_gmail(settings),
        await check_web(settings),
    ]
    ready = sum(results)
    print(f"\n{ready}/{len(results)} ready.", "Voice mode: agora" if not settings.voice_missing else
          f"Voice mode: browser stand-in (missing: {', '.join(settings.voice_missing)})")  # fmt: skip
    print("Mail: real Gmail" if settings.mail_connected else "Mail: not connected (mail tools will say so)")
    return 0 if all(results) else 1


if __name__ == "__main__":
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    raise SystemExit(asyncio.run(main(), loop_factory=loop_factory))
