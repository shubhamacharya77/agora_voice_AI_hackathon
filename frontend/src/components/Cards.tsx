import type { ReactNode } from 'react'

import type { Card } from '../types'

// Structured results shown next to CoO's spoken reply: the full detail lives on screen.

type Row = Record<string, unknown>

const text = (value: unknown): string => (value === null || value === undefined ? '' : String(value))
const rows = (value: unknown): Row[] => (Array.isArray(value) ? (value as Row[]) : [])
const row = (value: unknown): Row => (value && typeof value === 'object' ? (value as Row) : {})

function when(value: unknown): string {
  const date = new Date(text(value))
  if (Number.isNaN(date.getTime())) return text(value)
  return date.toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

const STATUS_LABELS: Record<string, string> = {
  requested: 'Prepared',
  pending: 'Waiting for your answer',
  approved: 'Approved',
  executing: 'Running…',
  succeeded: 'Done',
  failed: 'Failed',
  uncertain: 'Not confirmed',
  cancelled: 'Cancelled',
  expired: 'Expired',
}

const ACTION_TITLES: Record<string, string> = {
  'email.send': 'Email to send',
  'meeting.cancel': 'Meeting to cancel',
  'leave.request': 'Leave request',
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="field">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

function ProposalCard({ card }: { card: Card }) {
  const status = text(card.status)
  const proposal = row(card.proposal)
  const result = row(card.result)
  let details: ReactNode = null
  if (card.action_type === 'email.send') {
    const names = rows(proposal.to_names).map(text)
    const addresses = rows(proposal.to).map(text)
    details = (
      <>
        <Field label="To">{addresses.map((a, i) => (names[i] && names[i] !== a ? `${names[i]} <${a}>` : a)).join(', ')}</Field>
        <Field label="Subject">{text(proposal.subject)}</Field>
        <Field label="Message">
          <p className="body-text">{text(proposal.body)}</p>
        </Field>
      </>
    )
  } else if (card.action_type === 'meeting.cancel') {
    const meeting = row(proposal.meeting)
    details = (
      <>
        <Field label="Meeting">{text(meeting.title)}</Field>
        <Field label="When">{when(meeting.start)}</Field>
        <Field label="Attendees">{rows(meeting.attendees).map(text).join(', ')}</Field>
        {proposal.note ? <Field label="Note">{text(proposal.note)}</Field> : null}
      </>
    )
  } else if (card.action_type === 'leave.request') {
    details = (
      <>
        <Field label="From">{text(proposal.start_date)}</Field>
        <Field label="To">{text(proposal.end_date)}</Field>
        <Field label="Working days">{text(proposal.working_days)}</Field>
        {proposal.reason ? <Field label="Reason">{text(proposal.reason)}</Field> : null}
        {proposal.exceeds_balance ? <p className="warning">This is more than the current leave balance.</p> : null}
      </>
    )
  }
  return (
    <section className="card card--proposal" data-status={status}>
      <header>
        <h3>{ACTION_TITLES[text(card.action_type)] ?? 'Proposed action'}</h3>
        <span className="badge" data-status={status}>
          {STATUS_LABELS[status] ?? status}
        </span>
      </header>
      <dl>{details}</dl>
      {status === 'pending' && card.expires_at ? (
        <p className="hint">Say yes, no, or what to change, before {when(card.expires_at)}.</p>
      ) : null}
      {status === 'succeeded' && (result.message_id || result.request_id) ? (
        <p className="hint">Confirmation: {text(result.message_id ?? result.request_id)}</p>
      ) : null}
      {card.error ? <p className="warning">{text(card.error)}</p> : null}
    </section>
  )
}

function ListCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card">
      <header>
        <h3>{title}</h3>
      </header>
      <ul className="card-list">{children}</ul>
    </section>
  )
}

export function CardView({ card }: { card: Card }) {
  switch (card.type) {
    case 'proposal':
      return <ProposalCard card={card} />
    case 'headlines':
      return (
        <ListCard title="Headlines">
          {rows(card.items).map((item) => (
            <li key={text(item.url) || text(item.title)}>
              <a href={text(item.url)} target="_blank" rel="noreferrer">
                {text(item.title)}
              </a>
              <span>{text(item.summary)}</span>
            </li>
          ))}
        </ListCard>
      )
    case 'links':
      return (
        <ListCard title="Search results">
          {rows(card.items).map((item) => (
            <li key={text(item.url)}>
              <a href={text(item.url)} target="_blank" rel="noreferrer">
                {text(item.title)}
              </a>
              <span>{text(item.snippet)}</span>
            </li>
          ))}
        </ListCard>
      )
    case 'documents':
      return (
        <ListCard title="From the knowledge base">
          {rows(card.items).map((item, i) => (
            <li key={i}>
              <strong>
                {text(item.document)} · {text(item.section)}
              </strong>
              <span>{text(item.excerpt)}</span>
            </li>
          ))}
        </ListCard>
      )
    case 'emails':
      return (
        <ListCard title={card.via === 'gmail' ? 'Gmail inbox' : 'Inbox'}>
          {rows(card.items).length === 0 ? <li>No matching emails.</li> : null}
          {rows(card.items).map((item) => (
            <li key={text(item.id)} data-unread={item.unread ? true : undefined}>
              <strong>
                {text(item.subject)} {item.important ? <span className="badge">Important</span> : null}
              </strong>
              <span>
                {text(item.from).replace(/\s*<[^>]+>/, '')} · {when(item.received)}
              </span>
              {item.snippet ? <span className="snippet">{text(item.snippet)}</span> : null}
            </li>
          ))}
        </ListCard>
      )
    case 'email': {
      const email = row(card.item)
      return (
        <section className="card">
          <header>
            <h3>{text(email.subject)}</h3>
          </header>
          <p className="hint">
            {text(email.from)} · {when(email.received)}
          </p>
          <p className="body-text">{text(email.body)}</p>
        </section>
      )
    }
    case 'meetings':
      return (
        <ListCard title="Meetings">
          {rows(card.items).length === 0 ? <li>No meetings.</li> : null}
          {rows(card.items).map((item) => (
            <li key={text(item.id)}>
              <strong>{text(item.title)}</strong>
              <span>
                {when(item.start)} · {text(item.location)}
              </span>
            </li>
          ))}
        </ListCard>
      )
    case 'inventory':
      return (
        <ListCard title="Supplies">
          {rows(card.items).map((item) => {
            const low = Number(item.quantity) <= Number(item.reorder_at)
            return (
              <li key={text(item.item)} data-low={low || undefined}>
                <strong>
                  {text(item.item)}: {text(item.quantity)} {low ? <span className="badge">Reorder</span> : null}
                </strong>
                <span>{text(item.location)}</span>
              </li>
            )
          })}
        </ListCard>
      )
    case 'ticket': {
      const ticket = row(card.item)
      return (
        <section className="card">
          <header>
            <h3>Ticket {text(ticket.id)}</h3>
            <span className="badge">{text(ticket.status)}</span>
          </header>
          <p>{text(ticket.summary)}</p>
          <p className="hint">
            {text(ticket.location)} · {text(ticket.urgency)} urgency
          </p>
        </section>
      )
    }
    case 'employee': {
      const person = row(card.item)
      return (
        <section className="card">
          <header>
            <h3>{text(person.name)}</h3>
          </header>
          <p className="hint">
            {text(person.title)}, {text(person.department)}
          </p>
          <p>Leave balance: {text(person.leave_balance_days)} days</p>
        </section>
      )
    }
    case 'conversations':
      return (
        <ListCard title="Earlier conversations">
          {rows(card.items).map((item) => (
            <li key={text(item.conversation_id)}>
              <strong>{text(item.title)}</strong>
              <span>
                {when(item.last_active)}
                {rows(item.waiting_for_approval).length ? ' · something is waiting for approval' : ''}
              </span>
            </li>
          ))}
        </ListCard>
      )
    default:
      return null
  }
}
