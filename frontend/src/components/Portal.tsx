import { type FormEvent, useEffect, useState } from 'react'

import { cardsOf, firstName, freshReply, isSettledProposal, statusLabel } from '../portal/state'
import { usePortal } from '../portal/usePortal'
import type { Session } from '../session'
import type { Card } from '../types'
import { Backdrop } from './Backdrop'
import { Buddy } from './Buddy'
import { CardView } from './Cards'
import { History } from './History'
import { CloseIcon, HistoryIcon, StopIcon } from './Icons'

// Typing is a testing aid only (add ?text to the address); the portal itself is voice-first.
const TEXT_INPUT = new URLSearchParams(window.location.search).has('text')
const GREETING_MS = 6500 // how long the hello bubble stays
const CAPTION_LINGER_MS = 7000 // how long a reply stays on screen after CoO stops talking
const SETTLED_DRAFT_MS = 9000 // a sent / cancelled draft steps aside after this

export function Portal({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const portal = usePortal(session, onSignOut)
  // Replies from before this visit stay in the history; only new ones come on screen.
  const [openedAt] = useState(() => Date.now() - 3000)
  const reply = freshReply(portal.messages, openedAt)
  const replyId = reply?.id ?? null
  const waiting = portal.waiting
  const name = firstName(session.user.name)

  const [greeting, setGreeting] = useState(true)
  const [captionDone, setCaptionDone] = useState<string | null>(null) // reply whose words have faded
  const [dismissed, setDismissed] = useState<string | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [draft, setDraft] = useState('')

  useEffect(() => {
    const timer = window.setTimeout(() => setGreeting(false), GREETING_MS)
    return () => window.clearTimeout(timer)
  }, [])

  // A new reply's words stay up while CoO speaks, then fade a few seconds later.
  useEffect(() => {
    if (!replyId || portal.status === 'speaking' || portal.status === 'thinking') return
    const timer = window.setTimeout(() => setCaptionDone(replyId), CAPTION_LINGER_MS)
    return () => window.clearTimeout(timer)
  }, [replyId, portal.status])

  // A finished draft (sent, cancelled, failed) shows its outcome briefly, then steps aside.
  const settled = reply !== null && cardsOf(reply).some(isSettledProposal)
  useEffect(() => {
    if (!settled || !replyId) return
    const timer = window.setTimeout(() => setDismissed(replyId), SETTLED_DRAFT_MS)
    return () => window.clearTimeout(timer)
  }, [settled, replyId])

  // What comes on screen besides CoO: the draft waiting for an answer, or fresh results.
  let sheet: { key: string; cards: Card[]; closable: boolean } | null = null
  if (waiting) {
    sheet = { key: `draft-${String(waiting.action_id)}-${String(waiting.version)}`, cards: [waiting], closable: false }
  } else if (reply && reply.id !== dismissed && cardsOf(reply).length > 0) {
    sheet = { key: reply.id, cards: cardsOf(reply), closable: true }
  }

  let bubble: string | null = null
  if (portal.conversationId !== null && greeting && !reply) {
    bubble = `Hi ${name}! Tap me and tell me what you need.`
  } else if (reply && captionDone !== reply.id && !waiting) {
    bubble = reply.content // the draft card already shows a waiting proposal in full
  }

  const label =
    portal.conversationId === null && portal.status === 'idle'
      ? 'Getting ready…'
      : statusLabel(portal.status, waiting !== null, portal.voiceSupported)
  const mood = portal.status === 'error' ? 'sad' : portal.status

  function submit(event: FormEvent) {
    event.preventDefault()
    const words = draft.trim()
    if (!words) return
    setDraft('')
    void portal.sendText(words)
  }

  return (
    <div className="portal" data-sheet={sheet ? true : undefined}>
      <Backdrop />

      <header className="topbar">
        <div className="brand">
          <span className="brand-dot" aria-hidden="true" />
          CoO
        </div>
        <div className="top-actions">
          <button
            type="button"
            className="icon-btn"
            aria-label="Conversation history"
            title="Conversation history"
            onClick={() => setHistoryOpen(true)}
          >
            <HistoryIcon />
          </button>
          <div className="account">
            <button
              type="button"
              className="avatar"
              aria-label="Account"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((open) => !open)}
            >
              {name.slice(0, 1).toUpperCase()}
            </button>
            {menuOpen && (
              <div className="menu" role="menu">
                <p className="menu-name">{session.user.name}</p>
                <p className="menu-role">{session.user.role === 'admin' ? 'Admin' : 'Employee'}</p>
                <button type="button" role="menuitem" className="menu-item" onClick={onSignOut}>
                  Sign out
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      <main className="stage" onClick={() => setMenuOpen(false)}>
        <div className="bubble-slot" aria-live="polite">
          {bubble && (
            <p className="bubble" key={reply?.id ?? 'hello'}>
              {bubble}
            </p>
          )}
        </div>

        <Buddy
          mood={mood}
          waving={greeting && !reply && !portal.active}
          curious={waiting !== null}
          active={portal.active}
          label={portal.active ? `Stop talking. ${label}` : `Start talking. ${label}`}
          onTap={portal.toggle}
        />

        <div className="status-row">
          <p className="status-pill" data-state={portal.status} data-active={portal.active || undefined}>
            <span className="status-dot" aria-hidden="true" />
            {label}
          </p>
          {portal.active && (
            <button type="button" className="end-btn" aria-label="End conversation" title="End" onClick={portal.toggle}>
              <StopIcon />
            </button>
          )}
        </div>
        <p className="heard">{portal.status === 'listening' && portal.heard ? `“${portal.heard}”` : ' '}</p>
      </main>

      {sheet && (
        <section className="sheet" key={sheet.key} aria-label={waiting ? 'Draft waiting for your answer' : 'Details'}>
          {sheet.closable && (
            <button type="button" className="sheet-close" aria-label="Hide" onClick={() => setDismissed(sheet.key)}>
              <CloseIcon />
            </button>
          )}
          {sheet.cards.map((card, i) => (
            <CardView key={i} card={card} />
          ))}
        </section>
      )}

      {portal.error && (
        <p className="toast" role="alert">
          {portal.error}
        </p>
      )}

      {TEXT_INPUT && (
        <form className="typebox" onSubmit={submit}>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Testing only: type what you'd say"
            aria-label="Type a message (testing only)"
          />
          <button type="submit" disabled={portal.status === 'thinking'}>
            Send
          </button>
        </form>
      )}

      <History
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        messages={portal.messages}
        voiceName={portal.voiceName}
      />
    </div>
  )
}
