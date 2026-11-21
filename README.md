# CoO — Chief of Operations

A voice-first operations assistant for **Alrium**. You talk to CoO, see and hear the answer, and approve anything consequential by voice before it runs.

**Stack:**
- **Backend:** FastAPI, SQLModel (SQLAlchemy + Pydantic), PostgreSQL, Alembic, LangGraph
- **Voice:** Agora Conversational AI Engine, with Murf Falcon as CoO's voice
- **AI model:** Groq (`openai/gpt-oss-120b`); Claude through Anthropic or Bedrock is a config switch
- **Frontend:** React + TypeScript (Vite)

## Status

| Phase | State |
| --- | --- |
| 1. Backend foundation: config, PostgreSQL, SQLModel, Alembic, `/health` | Done |
| 2. Auth: argon2 passwords, JWT, one-click demo logins, role permissions | Done |
| 3. Conversation storage: conversations, messages, summaries, pending actions | Done |
| 4. Central agent: LangGraph loop, permission-filtered tools, specialists, RAG knowledge base | Done; verified with the real model |
| 5. Human approval: yes / no / change / show again, 15-minute window, runs once, honest outcome | Done; verified with the real model |
| 6. Recall and resume: earlier conversations, "new conversation", summaries, LangGraph checkpoints | Done |
| 7. Voice: Agora Conversational AI Engine + Murf Falcon | Done. Agora start/stop and streaming through ngrok verified live; first in-browser call still to do |
| 8. Voice portal: demo login, animated orb, live transcript, result and proposal cards | Done |

**Tests:** 142 backend (`pytest`) and 7 frontend (`vitest`).

**Verified live with the real model:**
- news through the Web specialist, about 2.8 s
- a leave question answered from the knowledge base, about 2.2 s
- an email drafted and read back, about 1.7 s
- "Yes, send it." approved and sent once, 0.1 s

## What you need

| Service | For | Put in `.env` |
| --- | --- | --- |
| Docker Desktop | PostgreSQL | nothing |
| [Groq](https://console.groq.com/keys) | The AI model (free tier works) | `GROQ_API_KEY` |
| [Agora](https://console.agora.io) | The voice call | `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE` |
| [Murf](https://murf.ai/api/dashboard) | CoO's voice | `MURF_API_KEY` (voice: `MURF_VOICE_ID=en-IN-isha`) |
| [ngrok](https://ngrok.com) | A public URL so Agora's cloud can reach your laptop | `PUBLIC_BASE_URL` |

Copy `.env.example` to `.env` and fill it in. Step-by-step instructions for each account are in the *CoO Setup Checklist* doc. Never paste keys into chat or commit `.env`.

`AGORA_CUSTOMER_ID` and `AGORA_CUSTOMER_SECRET` are optional. Without them, CoO signs Agora's REST calls with a token built from the App ID and Certificate.

## Run it

```bash
# 1. PostgreSQL (host port 5433; this machine already has another PostgreSQL on 5432)
docker compose up -d db

# 2. Backend: dependencies, migrations, demo users, the Alrium knowledge base
cd backend
uv sync
uv run alembic upgrade head
uv run python -m app.seed

# 3. Public tunnel (separate terminal): copy the https URL into PUBLIC_BASE_URL in .env.
#    The free plan's address stays the same across restarts.
ngrok http 8000

# 4. API (separate terminal, from backend/)
uv run uvicorn app.main:app --port 8000 --loop asyncio:SelectorEventLoop

# 5. Check every external piece, live (prints no secrets)
uv run python -m app.check_setup

# 6. Voice portal (separate terminal)
cd frontend
npm install
npm run dev        # open http://localhost:5173 in Chrome or Edge
```

`check_setup` should finish with `6/6 ready. Voice mode: agora`. In the portal:
1. Click **Continue as Admin**.
2. Tap the orb and allow the microphone. CoO greets you.
3. Try: "What's the news today?" → "Email Akshay about the deployment delay" → "Yes, send it."

**Notes:**
- **Without Agora keys,** the portal falls back to the browser's own speech, as a stand-in.
- **Testing without speaking:** add `?text` to the address for a typing box. It's for testing only; the demo is voice-only.
- **Settings are read at startup.** After changing `.env`, restart the backend.
- **Why `--loop asyncio:SelectorEventLoop`:** psycopg's async mode can't use Windows' default Proactor event loop, so without it the database looks unavailable. The flag is harmless on macOS and Linux.
- **API docs:** http://127.0.0.1:8000/docs

## AI model

Set in `.env`. No code change is needed to switch:

| `LLM_PROVIDER` | `LLM_MODEL` | Key |
| --- | --- | --- |
| `groq` (default) | `openai/gpt-oss-120b` (or `openai/gpt-oss-20b`) | `GROQ_API_KEY` |
| `anthropic` | `claude-haiku-5-5` | `ANTHROPIC_API_KEY` |
| `bedrock` | `anthropic.claude-haiku-5-5` | `aws login` or `AWS_BEARER_TOKEN_BEDROCK` |

**How Groq is wired in:**
- **Translation:** the agent speaks one internal format. `app/agents/llm_openai.py` translates it to Groq's OpenAI-style chat completions and back, so tools, approvals, recall and voice don't change.
- **Malformed tool calls:** Groq's `tool_use_failed` is retried once.
- **Rate limits are waited out, up to 20 s in total** (`LLM_RATE_LIMIT_MAX_WAIT_SECONDS`), instead of failing. On a voice call, CoO says "One moment." first.
- **Hidden reasoning:** the model's reasoning text is never spoken.

**Groq free tier:** 8,000 tokens per minute and 1,000 requests per day. A model call is about 1,600–2,200 tokens, and a tool-using turn makes two calls. That's fine at a normal speaking pace, but rapid-fire questions will wait. Groq's pay-as-you-go plan raises the limit about 30 times for a few cents.

## Voice (Agora + Murf)

```
browser mic --Agora RTC--> CoO's Agora agent (speech-to-text)
   --> POST {PUBLIC_BASE_URL}/voice/llm/{session}/chat/completions   (a normal CoO turn)
   <-- the reply, streamed as OpenAI chat-completion chunks
CoO's Agora agent --Murf Falcon (falcon-2.1, en-IN-isha)--> Agora RTC --> browser speaker
```

1. **Starting a call:** tapping the orb calls `POST /voice/sessions`.
   - The backend starts CoO's agent through Agora's REST API: Agora's speech recognition, this backend as the LLM, Murf as the voice.
   - It returns an RTC token, so the browser can join the channel and publish the microphone.
2. **Each time you speak,** Agora calls `/voice/llm/{session}/chat/completions`.
   - The session id in the URL plus a shared secret header (`X-CoO-Voice-Key`, set through Agora's `llm.headers`) tie the call to the logged-in user. Wrong key → 401; unknown or ended session → 404.
   - It runs the same turn as text: approvals, recall, "new conversation". A conversation switch moves the call too.
   - If the turn takes more than 1.2 s, "One moment." streams first.
3. **On screen:** the transcript comes from CoO's own saved conversation, refreshed every 1.5 s during the call. The orb follows Agora's volume levels: CoO speaking, you speaking, or thinking in between.
4. **Hanging up:** `DELETE /voice/sessions/{id}` stops the agent. A user has one call at a time.

`GET /voice/config` reports `agora` when everything's set, or `browser` plus a list of the missing `.env` keys.

**Verified live:**
- Agora accepted the start request (the token sign-in and the Murf voice settings), and the agent ran and stopped cleanly.
- A reply streamed through the ngrok tunnel.
- A conversation switch moved the call.

**Still to confirm on the first browser call:** which header Agora uses to send our key. CoO accepts both `X-CoO-Voice-Key` and `Authorization: Bearer`.

## Approvals

Sending email, cancelling a meeting and requesting leave never happen straight away. The tool only creates a proposal, which CoO reads back word for word, asking once: "To confirm, I'll send email to Akshay Rao (akshay@alrium.example). Subject: Deployment delay. Shall I send it? Say yes, no, or tell me what to change." That starts a 15-minute window (`APPROVAL_WINDOW_MINUTES`).

When a proposal is waiting, your next message is read as a reply to it first (`app/agents/approvals.py`):

| Reply | What happens |
| --- | --- |
| A plain yes ("yes, send it", "looks good, send it") | Recognised without the model. Approved and run once. |
| A plain no ("no, don't send it", "never mind") | Cancelled; nothing runs. |
| "Read it again" | Read back again, and the window restarts. |
| "Change the subject to..." | Rewritten as **version 2** of the same proposal, which needs a fresh yes. A yes to version 1 no longer works. |
| Anything else about it | A small classifier sees only the proposal and your words. Even if it says "approve", your words must contain a clear yes with nothing like "no", "but", "change", "later" or "maybe". Otherwise CoO asks again. |
| Something unrelated | Answered normally; the proposal keeps waiting. |
| A yes after the window | The proposal is read back again instead of running. |

**Running it once:**
- Approving is one atomic `UPDATE`, bound to the presented version and the open window.
- Running is a second atomic `UPDATE` (`approved → executing`), committed before the provider call, so retries, double yeses and restarts can't run anything twice.
- After a crash mid-run, startup marks the action `uncertain` and never re-runs it.

**Honest outcomes:** the provider's answer is checked, then reported as `succeeded`, `failed` or `uncertain`. Two demo switches show failures: email to `bounce@...` is rejected, and email to `slow@...` times out.

## Agent

`POST /conversations/{id}/turns` with `{"text": "..."}` runs one turn. Agora's voice calls run the same `run_turn`.

```
START -> central_agent --tool calls?--> specialists -> central_agent ... -> END
```

- **Saving:** your words are saved first. Each tool run is stored as a `tool` message, and the reply as an `assistant` message with its details in `meta`: spoken text, cards, specialists used, model calls, latency.
- **Permissions:** the model only sees the tools you're allowed to use, and the executor checks again before anything runs.
- **Sample data:** tools run on demo data (`app/agents/mock_data.py`) and mark results `"mock": true`. CoO says so once.
- **Failures:** if the model fails, the reply honestly says so, and anything half-done is rolled back.
- **Context:** the last 10 messages (`AGENT_HISTORY_MESSAGES`) go to the model; older ones are reachable through recall and summaries.

| Specialist | Tools |
| --- | --- |
| web | `web_get_news`, `web_search` |
| company_info | `company_search_knowledge` (RAG over the Alrium knowledge base) |
| hr | `hr_get_my_profile`, `hr_find_employee`, `hr_request_leave` (needs approval) |
| mail | `mail_search_inbox`, `mail_read_email`, `mail_send_email` (needs approval) |
| coo (meetings) | `meetings_list`, `meetings_cancel` (needs approval) |
| management | `facilities_report_issue`, `inventory_check_stock` |
| recall | `conversations_find`, `conversations_read`, `conversations_resume`, `conversations_start_new` |

## Recall and resume

Every new conversation starts clean. Earlier ones come back only when you ask.

- **Opening the portal** (`POST /conversations/start`) continues your latest conversation if you were active in the last 2 hours (`CONVERSATION_CONTINUE_HOURS`); otherwise a fresh one starts.
- **"New conversation"** creates a separate conversation without calling the model. Anything still waiting stays in the old one.
- **"Where did we stop?" / "the one about the deployment delay"** finds earlier conversations, by recency or full-text search, with each one's summary, last exchange and anything still waiting.
- **"Repeat your previous answer"** reads the exact original messages, never a summary.
- **"Let's continue"** switches to that conversation, and any waiting proposal is read back with a fresh approval window.
- **Summaries:** two-sentence recaps are refreshed in the background every 6 messages (`SUMMARY_EVERY_MESSAGES`). They never replace the messages.
- **LangGraph checkpoints:** every agent step is checkpointed in Postgres (`AsyncPostgresSaver`), keyed by conversation id. Its tables are created by LangGraph and ignored by Alembic.

## Knowledge base (RAG)

`backend/knowledge/alrium/` holds 12 markdown documents of **demo content** for Alrium: invented people, vendors and figures, not real policy. Two of them are admin-only.

1. **Load:** `python -m app.seed` splits each document into one passage per `## ` section (61 passages).
2. **Retrieve:** PostgreSQL full-text search ranks titles above headings, and headings above text. Passages with every keyword come first. Admin-only passages are filtered out in the SQL, so they never reach an employee or the model working for them.
3. **Generate:** CoO answers only from the passages and names the document ("According to the Leave and holidays policy…").

To change content, edit the markdown (front matter: `title`, `category`, `audience: everyone|admins`) and re-run the seed. Write the words people actually say ("work from home", not only "remote work"), because keyword search doesn't match synonyms.

## Auth

| Endpoint | What it does |
| --- | --- |
| `POST /auth/demo-login` `{"role": "admin" \| "employee"}` | One-click login (only when `DEMO_LOGIN_ENABLED=true`). |
| `POST /auth/login` `{"email", "password"}` | Normal login. Unknown email and wrong password get the same error. |
| `GET /auth/me` | The current user and their permissions. |

- **Demo users:** `admin@coo.example` and `employee@coo.example`.
- **Passwords** are stored as argon2 hashes.
- **Tokens** carry only the user id. Role and permissions are re-read on every request.

| Permission | Employee | Admin |
| --- | --- | --- |
| `chat`, `web:search`, `knowledge:read`, `hr:read_own`, `leave:request`, `tickets:create` | Yes | Yes |
| `knowledge:read_restricted`, `email:read`, `email:send`, `hr:read_all`, `meetings:manage`, `inventory:manage`, `tickets:manage` | No | Yes |

## Conversations API

| Endpoint | What it does |
| --- | --- |
| `POST /conversations/start` | What the portal calls on open: continue or start fresh. |
| `POST /conversations` | Start a new conversation. |
| `GET /conversations` | Your conversations, most recent first. |
| `GET /conversations/{id}` | One conversation, including its summary. |
| `GET /conversations/{id}/messages` | The original messages in order. |
| `GET /conversations/{id}/pending-actions` | Proposals and their approval state. |
| `POST /conversations/{id}/turns` | Talk to CoO. |

- **Private:** someone else's conversation answers exactly like a missing one (404), admins included.
- **No forged replies:** clients can't post assistant messages or approvals; only the agent writes them.

## Tests

```bash
cd backend && uv run pytest                     # 142 tests (integration tests need the db container)
cd backend && uv run pytest -m "not integration" # unit tests only
cd frontend && npm test                          # 7 tests
```

Integration tests use a separate `coo_test` database, rebuilt through the real Alembic migrations on every run, so dev data is never touched. Agora, Groq and the models are faked in tests; `app.check_setup` is the live check.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `check_setup`: AI model fails | Check `LLM_PROVIDER`, `LLM_MODEL` and the matching key, then restart the backend. |
| "Groq rate limit reached" | You hit 8,000 tokens a minute. Pause about 15 s, or move to Groq's pay-as-you-go plan. |
| `check_setup`: Public URL fails | ngrok isn't running (`ngrok http 8000`), or `PUBLIC_BASE_URL` doesn't match its URL. |
| Portal says "Voice: Browser speech" | `GET /voice/config` lists the missing `.env` keys. Restart the backend after filling them. |
| Database "unavailable" on Windows | Start uvicorn with `--loop asyncio:SelectorEventLoop`. |
| Murf voice rejected | `check_setup` lists valid Falcon voice IDs (for example `en-IN-isha`, `en-IN-aarav`). |

## Database migrations

Table models are `SQLModel` classes with `table=True` in `backend/app/models/`. Import each new model in `app/models/__init__.py`, then:

```bash
cd backend
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

Alembic reads `DATABASE_URL` from the app settings and ignores LangGraph's checkpoint tables.

## Layout

```
docker-compose.yml          PostgreSQL for local development
.env.example                every setting the app reads (copy to .env)
backend/
  app/
    main.py                 app factory, lifespan (checkpointer, crash recovery), CORS
    config.py               settings from environment / .env
    db.py                   async engine, SQLModel sessions, DB ping
    seed.py                 demo users + knowledge base (python -m app.seed)
    check_setup.py          live check of every external service (python -m app.check_setup)
    search.py               shared full-text search helpers
    api/                    health, auth, conversations, turns, voice
    auth/                   passwords, tokens, permissions, CurrentUser / require_permission
    agents/                 LangGraph graph, turn runner, approvals, executors, prompt,
                            model adapters (llm.py: Claude; llm_openai.py: Groq), tools,
                            specialists, mock data, summaries
    conversations/store.py  user-scoped storage for conversations, messages, actions
    knowledge/              load, chunk and search the knowledge base (RAG)
    voice/                  Agora client and tokens, OpenAI-style streaming bridge
    models/                 SQLModel tables (users, conversations, messages, pending actions,
                            knowledge chunks, voice sessions)
  knowledge/alrium/         the demo company documents (markdown)
  migrations/               Alembic environment and revisions
  tests/
frontend/
  src/components/           Login, Portal, Orb, Transcript, Cards
  src/portal/               the voice loop / call handling and its pure helpers (tested)
  src/voice/                Agora call (agora-rtc-sdk-ng) and the browser-speech stand-in
```

## Secrets

`.env` is git-ignored. Never commit it or paste its values into chat. If a secret leaks, rotate it in that service's console.
