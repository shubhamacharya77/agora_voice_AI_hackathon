import { useEffect, useRef } from 'react'

import { cardsOf } from '../portal/state'
import type { Message } from '../types'
import { CardView } from './Cards'

const EXAMPLES = [
  "What's the news today?",
  'How many leave days do I get?',
  'Email Akshay about the deployment delay',
  'Where did we stop?',
]

export function Transcript({ messages, name }: { messages: Message[]; name: string }) {
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  if (messages.length === 0) {
    return (
      <section className="welcome">
        <h2>Hi {name}, I&rsquo;m CoO.</h2>
        <p>Tap the orb and tell me what you need. Try saying:</p>
        <ul>
          {EXAMPLES.map((example) => (
            <li key={example}>&ldquo;{example}&rdquo;</li>
          ))}
        </ul>
      </section>
    )
  }

  return (
    <section className="transcript" aria-live="polite">
      {messages.map((message) => (
        <article key={message.id} className={`turn turn--${message.role}`} data-pending={message.meta.pending ? true : undefined}>
          <p className="said">{message.content}</p>
          {message.role === 'assistant' && (
            <>
              {cardsOf(message).map((card, i) => (
                <CardView key={i} card={card} />
              ))}
              {message.meta.mock ? <span className="tag">Demo data</span> : null}
            </>
          )}
        </article>
      ))}
      <div ref={end} />
    </section>
  )
}
