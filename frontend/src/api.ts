import type {
  Message,
  Role,
  SessionStart,
  TokenResponse,
  TurnResponse,
  UserPublic,
  VoiceConfig,
  VoiceJoin,
  VoiceState,
} from './types'

const API_URL = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, options: { method?: string; token?: string; body?: unknown } = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, {
      method: options.method ?? 'GET',
      headers: {
        ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}),
      },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    })
  } catch {
    throw new ApiError(0, "Can't reach CoO. Is the backend running?")
  }
  if (!response.ok) {
    let detail = response.statusText
    try {
      const data = (await response.json()) as { detail?: unknown }
      if (typeof data.detail === 'string') detail = data.detail
    } catch {
      // keep the status text
    }
    throw new ApiError(response.status, detail)
  }
  return (await response.json()) as T
}

export const api = {
  demoLogin: (role: Role) => request<TokenResponse>('/auth/demo-login', { method: 'POST', body: { role } }),
  me: (token: string) => request<UserPublic>('/auth/me', { token }),
  startSession: (token: string) => request<SessionStart>('/conversations/start', { method: 'POST', token }),
  messages: (token: string, conversationId: string) =>
    request<Message[]>(`/conversations/${conversationId}/messages`, { token }),
  turn: (token: string, conversationId: string, text: string) =>
    request<TurnResponse>(`/conversations/${conversationId}/turns`, { method: 'POST', token, body: { text } }),
  voiceConfig: (token: string) => request<VoiceConfig>('/voice/config', { token }),
  startVoice: (token: string, conversationId: string) =>
    request<VoiceJoin>('/voice/sessions', { method: 'POST', token, body: { conversation_id: conversationId } }),
  voiceState: (token: string, sessionId: string) => request<VoiceState>(`/voice/sessions/${sessionId}`, { token }),
  stopVoice: (token: string, sessionId: string) =>
    request<VoiceState>(`/voice/sessions/${sessionId}`, { method: 'DELETE', token }),
}
