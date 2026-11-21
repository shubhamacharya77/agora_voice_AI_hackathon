# CoO voice portal (frontend)

React + TypeScript + Vite. Sign in with one click, tap the orb, and talk to CoO.

```bash
npm install
npm run dev        # http://localhost:5173 (the backend must be running on :8000)
npm test           # unit tests (Vitest)
npm run build      # type-check and production build
```

- **Voice:** the portal asks `GET /voice/config` which voice to use.
  - With Agora set up, tapping the orb opens a live call (`src/voice/agoraCall.ts`, `agora-rtc-sdk-ng`, loaded lazily): Agora hears you, CoO answers, Murf speaks.
  - Otherwise it falls back to the browser's own speech recognition and synthesis (`src/voice/browserVoice.ts`, Chrome or Edge).
- **Flow:** `src/portal/usePortal.ts` runs the loop: listen → send the turn → show the reply and cards → speak → listen again. Tap the orb to stop. CoO pauses after three silent listens.
- **Conversations:** opening the portal calls `POST /conversations/start`, which continues recent work or starts fresh. When a reply carries `switch_to_conversation_id` (a resumed or new conversation), the page moves there.
- **State:** the orb shows idle, listening, thinking, speaking, waiting for approval (amber) and error. It's visual only: whether anything happened is decided by the backend, and shown by the proposal card's status.
- **Testing aid:** add `?text` to the address for a typing box. The demo itself is voice-only.
- **Settings:** `.env.example` (`VITE_API_URL`, `VITE_SPEECH_LANG`, `VITE_COMPANY_NAME`).
