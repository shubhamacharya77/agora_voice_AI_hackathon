import { useEffect } from 'react'

import type { Message } from '../types'
import { CloseIcon } from './Icons'
import { Transcript } from './Transcript'

interface HistoryProps {
  open: boolean
  onClose: () => void
  messages: Message[]
  voiceName: string
}

/** The full conversation, kept out of the way until asked for. */
export function History({ open, onClose, messages, voiceName }: HistoryProps) {
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  return (
    <div className="history" data-open={open || undefined} aria-hidden={!open}>
      <div className="history-scrim" onClick={onClose} />
      <aside className="history-panel" aria-label="Conversation history">
        <header>
          <h2>Conversation</h2>
          <button type="button" className="icon-btn" aria-label="Close history" onClick={onClose} tabIndex={open ? 0 : -1}>
            <CloseIcon />
          </button>
        </header>
        {open && <Transcript messages={messages} />}
        <footer>Voice: {voiceName}</footer>
      </aside>
    </div>
  )
}
