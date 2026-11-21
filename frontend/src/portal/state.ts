import type { Card, Message } from '../types'

// What CoO is doing, as the orb shows it. Purely visual: the backend's own state
// (pending actions, results) is what decides whether anything actually happened.
export type Status = 'idle' | 'listening' | 'thinking' | 'speaking' | 'error'

/** Only what was said is shown; tool runs and system notes stay in the record. */
export function visibleMessages(messages: Message[]): Message[] {
  return messages.filter((m) => m.role === 'user' || m.role === 'assistant')
}

export function cardsOf(message: Message): Card[] {
  const cards = message.meta.cards
  return Array.isArray(cards) ? (cards as Card[]) : []
}

/** The proposal waiting for a yes or no, if the latest reply left one open. */
export function waitingProposal(messages: Message[], now: Date = new Date()): Card | null {
  const lastReply = [...messages].reverse().find((m) => m.role === 'assistant')
  if (!lastReply) return null
  const proposal = cardsOf(lastReply).find((c) => c.type === 'proposal' && c.status === 'pending')
  if (!proposal) return null
  const expiresAt = typeof proposal.expires_at === 'string' ? new Date(proposal.expires_at) : null
  return expiresAt && expiresAt <= now ? null : proposal
}

export function statusLabel(status: Status, waiting: boolean, supported: boolean): string {
  switch (status) {
    case 'listening':
      return waiting ? 'Listening. Say yes, no, or what to change.' : 'Listening…'
    case 'thinking':
      return 'Working on it…'
    case 'speaking':
      return 'Speaking…'
    case 'error':
      return 'Something went wrong'
    default:
      if (!supported) return 'Voice needs Chrome or Edge'
      return waiting ? 'Waiting for your yes or no. Tap to answer.' : 'Tap the orb and speak'
  }
}

/** The orb during an Agora call, from who is speaking right now. */
export function callStatus(call: { agentSpeaking: boolean; userSpeaking: boolean; awaitingReply: boolean }): Status {
  if (call.agentSpeaking) return 'speaking'
  if (call.userSpeaking) return 'listening'
  return call.awaitingReply ? 'thinking' : 'listening'
}

export function firstName(name: string): string {
  return name.split(' ')[0] || name
}
