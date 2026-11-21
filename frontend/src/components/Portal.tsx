import { type FormEvent, useState } from 'react'

import { firstName, statusLabel } from '../portal/state'
import { usePortal } from '../portal/usePortal'
import type { Session } from '../session'
import { Orb } from './Orb'
import { Transcript } from './Transcript'

const COMPANY = import.meta.env.VITE_COMPANY_NAME ?? 'Alrium'
// Typing is a testing aid only (add ?text to the address); the portal itself is voice-first.
const TEXT_INPUT = new URLSearchParams(window.location.search).has('text')

export function Portal({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const portal = usePortal(session, onSignOut)
  const label =
    portal.conversationId === null && portal.status === 'idle'
      ? 'Getting ready…'
      : statusLabel(portal.status, portal.waiting !== null, portal.voiceSupported)
  const [draft, setDraft] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    const words = draft.trim()
    if (!words) return
    setDraft('')
    void portal.sendText(words)
  }

  return (
    <div className="portal">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span>
            CoO <span className="muted">· {COMPANY}</span>
          </span>
        </div>
        <div className="who">
          <span className="chip">
            {session.user.name} · {session.user.role}
          </span>
          <button type="button" className="link" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </header>

      <main className="stage">
        <section className="presence">
          <Orb
            status={portal.status}
            waiting={portal.waiting !== null}
            active={portal.active}
            label={label}
            onToggle={portal.toggle}
          />
          <p className="status" data-state={portal.status}>
            {label}
          </p>
          <p className="heard" aria-live="polite">
            {portal.heard ? `“${portal.heard}”` : ' '}
          </p>
          {portal.error && (
            <p className="error" role="alert">
              {portal.error}
            </p>
          )}
        </section>

        <Transcript messages={portal.messages} name={firstName(session.user.name)} />

        {TEXT_INPUT && (
          <form className="typebox" onSubmit={submit}>
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Testing only: type what you'd say"
              aria-label="Type a message (testing only)"
            />
            <button type="submit" className="secondary" disabled={portal.status === 'thinking'}>
              Send
            </button>
          </form>
        )}
      </main>

      <footer className="voice-note">Voice: {portal.voiceName}</footer>
    </div>
  )
}
