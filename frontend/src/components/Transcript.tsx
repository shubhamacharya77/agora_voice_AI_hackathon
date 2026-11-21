import { useEffect, useRef } from 'react'

import { cardsOf } from '../portal/state'
import type { Message } from '../types'
import { CardView } from './Cards'

/** Everything said in this conversation, with the details CoO showed alongside. */
export function Transcript({ messages }: { messages: Message[] }) {
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [messages])

  if (messages.length === 0) {
    return <p className="transcript-empty">Nothing said yet.</p>
  }

  return (
    <section className="transcript">
      {messages.map((message) => (
        <article
          key={message.id}
          className={`turn turn--${message.role}`}
          data-pending={message.meta.pending ? true : undefined}
        >
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
