import type { TokenResponse, UserPublic } from './types'

// The login token is a per-browser convenience: losing it just means logging in again.
const KEY = 'coo.session'

export interface Session {
  token: string
  user: UserPublic
  expiresAt: number // ms since epoch
}

export function loadSession(): Session | null {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return null
    const session = JSON.parse(raw) as Session
    return session.expiresAt > Date.now() ? session : null
  } catch {
    return null
  }
}

export function saveSession(login: TokenResponse): Session {
  const session = { token: login.access_token, user: login.user, expiresAt: Date.now() + login.expires_in * 1000 }
  try {
    localStorage.setItem(KEY, JSON.stringify(session))
  } catch {
    // private mode: the session still works until the page closes
  }
  return session
}

export function clearSession(): void {
  try {
    localStorage.removeItem(KEY)
  } catch {
    // nothing stored
  }
}
