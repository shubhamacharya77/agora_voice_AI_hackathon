import { describe, expect, it } from 'vitest'

import type { Message } from '../types'
import { callStatus, freshReply, isSettledProposal, statusLabel, visibleMessages, waitingProposal } from './state'

function message(role: Message['role'], content: string, meta: Record<string, unknown> = {}): Message {
  return { id: content, sequence: 1, role, content, meta, created_at: '2026-11-21T10:00:00Z' }
}

const proposal = (status: string, expires_at: string | null = '2026-11-21T10:15:00Z') => ({
  cards: [{ type: 'proposal', status, expires_at, description: 'Send email to Akshay Rao' }],
})

describe('visibleMessages', () => {
  it('shows what was said and hides tool runs and system notes', () => {
    const shown = visibleMessages([
      message('user', 'hi'),
      message('tool', 'web_get_news: ok'),
      message('system', 'resumed'),
      message('assistant', 'hello'),
    ])
    expect(shown.map((m) => m.role)).toEqual(['user', 'assistant'])
  })
})

describe('waitingProposal', () => {
  const now = new Date('2026-11-21T10:05:00Z')

  it('finds the proposal the latest reply left waiting', () => {
    const messages = [message('user', 'email Akshay'), message('assistant', 'To confirm…', proposal('pending'))]
    expect(waitingProposal(messages, now)?.description).toBe('Send email to Akshay Rao')
  })

  it('ignores a proposal once it is done, cancelled or expired', () => {
    expect(waitingProposal([message('assistant', 'Done.', proposal('succeeded'))], now)).toBeNull()
    expect(waitingProposal([message('assistant', 'Okay.', proposal('cancelled'))], now)).toBeNull()
    const late = new Date('2026-11-21T10:20:00Z')
    expect(waitingProposal([message('assistant', 'To confirm…', proposal('pending'))], late)).toBeNull()
  })

  it('only looks at the latest reply', () => {
    const messages = [
      message('assistant', 'To confirm…', proposal('pending')),
      message('user', 'what is the news'),
      message('assistant', 'Here are the headlines.'),
    ]
    expect(waitingProposal(messages, now)).toBeNull()
  })
})

describe('callStatus (Agora call)', () => {
  it('follows who is speaking, and thinks in between', () => {
    expect(callStatus({ agentSpeaking: true, userSpeaking: false, awaitingReply: false })).toBe('speaking')
    expect(callStatus({ agentSpeaking: false, userSpeaking: true, awaitingReply: false })).toBe('listening')
    expect(callStatus({ agentSpeaking: false, userSpeaking: false, awaitingReply: true })).toBe('thinking')
    expect(callStatus({ agentSpeaking: false, userSpeaking: false, awaitingReply: false })).toBe('listening')
  })
})

describe('statusLabel', () => {
  it('tells the user how to answer while a proposal waits', () => {
    expect(statusLabel('listening', true, true)).toBe('Listening · say yes, no, or a change')
    expect(statusLabel('idle', true, true)).toBe('Tap me to answer')
    expect(statusLabel('idle', false, true)).toBe('Tap me to talk')
  })

  it('says when the browser has no speech recognition', () => {
    expect(statusLabel('idle', false, false)).toBe('Voice needs Chrome or Edge')
  })
})

describe('freshReply', () => {
  it('only surfaces replies that arrived after the page opened', () => {
    const old = { ...message('assistant', 'yesterday'), created_at: '2026-11-20T10:00:00Z' }
    const now = { ...message('assistant', 'just now'), created_at: '2026-11-21T10:00:05Z' }
    const opened = Date.parse('2026-11-21T10:00:00Z')
    expect(freshReply([old], opened)).toBeNull()
    expect(freshReply([old, message('user', 'hi'), now], opened)?.content).toBe('just now')
  })
})

describe('isSettledProposal', () => {
  it('is true once a proposal is no longer waiting', () => {
    expect(isSettledProposal({ type: 'proposal', status: 'succeeded' })).toBe(true)
    expect(isSettledProposal({ type: 'proposal', status: 'pending' })).toBe(false)
    expect(isSettledProposal({ type: 'headlines' })).toBe(false)
  })
})
