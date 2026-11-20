# CoO — Chief of Operations

A voice-first operations assistant: speak to it, see and hear the answer, and approve any consequential action by voice before it runs.

**Stack:** FastAPI · SQLModel (SQLAlchemy + Pydantic) · PostgreSQL · Alembic · LangGraph · Agora Conversational AI Engine · Murf Falcon · Claude Haiku 5.5 on Amazon Bedrock · React + TypeScript (Vite)

## Status

| Phase | State |
| --- | --- |
| 1. Backend foundation: config, PostgreSQL, SQLModel, Alembic, `/health` | Done |
| 2. Auth: users table, argon2 passwords, JWT, demo logins, role permissions | Done |
| 3. Conversation storage: conversations, messages, summaries, pending-action records | Done |
| 4. Central agent: LangGraph loop, permission-filtered tools, six mock specialists | Done (tested with a scripted model; live Bedrock call pending `aws login`) |
| 5. Human approval flow | Next |
| 6–8. Recall and resume, voice, voice portal | Not started |

## Prerequisites

- Docker Desktop (running)
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.13 for the backend if needed)
- A `.env` in the repo root: copy `.env.example` and fill it in. Setup steps for every external key are in the *CoO Setup Checklist* doc.

## Run it

```bash
# 1. Start PostgreSQL (host port 5433; this machine already has another PostgreSQL on 5432)
docker compose up -d db

# 2. Install backend dependencies, apply migrations, create the demo users
cd backend
uv sync
uv run alembic upgrade head
uv run python -m app.seed

# 3. Start the API
uv run uvicorn app.main:app --port 8000 --loop asyncio:SelectorEventLoop
```

Interactive API docs: http://127.0.0.1:8000/docs

Check it: `curl http://127.0.0.1:8000/health` returns `{"status":"ok","database":"ok"}`. If the database is unreachable it returns HTTP 503 with `"database":"unavailable"`.

**Why `--loop asyncio:SelectorEventLoop`:** on Windows, uvicorn's default event loop is the Proactor loop, and psycopg's async mode can't run on it, so `/health` would report the database as unavailable. The flag is harmless on macOS and Linux. Add `--reload` during development if you like.

## Auth

| Endpoint | What it does |
| --- | --- |
| `POST /auth/demo-login` `{"role": "admin"}` or `"employee"` | One-click login as a demo user. Only when `DEMO_LOGIN_ENABLED=true`. |
| `POST /auth/login` `{"email", "password"}` | Normal login. Unknown email and wrong password get the same error. |
| `GET /auth/me` | The current user and their permissions. Needs `Authorization: Bearer <token>`. |

- **Demo users** (`app.seed`): `admin@coo.example` and `employee@coo.example`. Their optional passwords are `DEMO_ADMIN_PASSWORD` and `DEMO_EMPLOYEE_PASSWORD` in `.env`.
- **Passwords** are stored as argon2 hashes, never in plain text.
- **Tokens** carry only the user id. Role and permissions are read from the database on every request, so deactivating a user takes effect immediately.
- **Permissions** live in `backend/app/auth/permissions.py`. Endpoints and agent tools check a permission, never a role name:

| Permission | Employee | Admin |
| --- | --- | --- |
| `chat`, `web:search`, `knowledge:read`, `hr:read_own`, `leave:request`, `tickets:create` | Yes | Yes |
| `email:read`, `email:send`, `hr:read_all`, `meetings:manage`, `inventory:manage`, `tickets:manage` | No | Yes |

Protect an endpoint with `Depends(require_permission(Permission.EMAIL_SEND))`, or use the `CurrentUser` dependency when any logged-in user may call it.

## Conversations

| Endpoint | What it does |
| --- | --- |
| `POST /conversations` | Start a new, separate conversation (optional `{"title"}`). |
| `GET /conversations` | Your conversations, most recently active first (`limit`, `offset`). |
| `GET /conversations/{id}` | One conversation, including its summary. |
| `GET /conversations/{id}/messages` | The original messages in order (`after`, `limit` for paging). |
| `GET /conversations/{id}/pending-actions` | Actions proposed in this conversation and their approval state. |

- **Private by construction.** Every function in `app/conversations/store.py` takes the caller's user id. Someone else's conversation answers exactly like a missing one (404), admins included.
- **Clients can't write messages or approvals.** From Phase 4 on, the agent writes them through the store. Nobody can post a fake "assistant" message or invent an approval.
- **Order** comes from a per-conversation `sequence` (1, 2, 3...). One atomic `UPDATE ... RETURNING` hands it out, so messages arriving at the same moment never share a number.
- **Titles** come from the first thing the user says.
- **Summaries** live on the conversation (`summary`, `summary_through_sequence`) and never replace the messages.
- **Pending actions** record:
  - the exact proposal, with a version that goes up on every change
  - an idempotency key, to stop the action running twice
  - when the approval expires
  - what the provider actually returned

  States: `requested → pending → approved → executing → succeeded | failed | uncertain`, or `cancelled` / `expired`.

## Agent

`POST /conversations/{id}/turns` with `{"text": "..."}` runs one turn and returns the reply, the cards for the screen, and any pending actions. In Phase 7, Agora's voice engine will call the same `run_turn`.

```
START -> central_agent --tool calls?--> specialists -> central_agent ... -> END
```

- **Saving:** the user's words are saved first. Each tool run is stored as a `tool` message, and the reply as an `assistant` message with its details in `meta`: spoken text, cards, specialists used, latency.
- **Permissions:** the model only sees the tools the user is allowed to use, and the executor checks again before anything runs.
- **Approvals:** sending email, cancelling a meeting and requesting leave only create a pending action. Nothing happens without approval (Phase 5).
- **Sample data:** every tool runs on sample data (`app/agents/mock_data.py`) and marks its result `"mock": true`.
- **Failures:** if the model fails, the reply honestly says so, and anything half-done (such as a proposal) is rolled back.

| Specialist | Tools |
| --- | --- |
| web | `web_get_news`, `web_search` |
| company_info | `company_search_policies` |
| hr | `hr_get_my_profile`, `hr_find_employee`, `hr_request_leave` (needs approval) |
| mail | `mail_search_inbox`, `mail_read_email`, `mail_send_email` (needs approval) |
| coo (meetings) | `meetings_list`, `meetings_cancel` (needs approval) |
| management | `facilities_report_issue`, `inventory_check_stock` |

**Model:** Claude Haiku 5.5 on Amazon Bedrock, called through the Anthropic SDK's Mantle client with model id `anthropic.claude-haiku-5-5`. Change `LLM_PROVIDER`, `LLM_MODEL` or `LLM_EFFORT` in `.env` to switch models. To check the model answers:

```bash
aws login                                   # or put a Bedrock API key in AWS_BEARER_TOKEN_BEDROCK
cd backend && uv run python -m app.agents.check_model
```

## Tests

```bash
cd backend
uv run pytest                    # all tests (integration tests need the db container)
uv run pytest -m "not integration"   # unit tests only, no database needed
```

Integration tests use a separate `coo_test` database. Each run rebuilds it from scratch through the real Alembic migrations, so your dev data is never touched.

## Database migrations

Table models are `SQLModel` classes with `table=True` under `backend/app/models/`. Import each new model module in `app/models/__init__.py`, then:

```bash
cd backend
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

Alembic reads `DATABASE_URL` from the app settings, not `alembic.ini`. It ignores LangGraph's checkpoint tables, which LangGraph creates and manages itself.

## Layout

```
docker-compose.yml        PostgreSQL for local development
.env.example              every setting the app reads (copy to .env)
backend/
  app/
    main.py               FastAPI app factory, lifespan, CORS
    config.py             settings from environment / .env
    db.py                 async engine, SQLModel sessions, DB ping
    deps.py               shared dependency aliases (SessionDep, SettingsDep)
    seed.py               creates the demo users (python -m app.seed)
    api/health.py         GET /health
    api/auth.py           /auth/login, /auth/demo-login, /auth/me
    api/conversations.py  conversation history endpoints
    api/turns.py          POST /conversations/{id}/turns (talk to CoO)
    agents/               LangGraph graph, model adapter, tools, prompt, specialists, mock data
    auth/                 passwords, tokens, permissions, CurrentUser / require_permission
    conversations/store.py  user-scoped storage for conversations, messages, actions
    models/               SQLModel table models (naming conventions in base.py)
  migrations/             Alembic environment and revisions
  tests/
```

## Secrets

`.env` is git-ignored. Never commit it or paste its values into chat; if a secret leaks, rotate it in that service's console.
