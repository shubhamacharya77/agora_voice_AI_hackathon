import { useState } from 'react'

import { api } from '../api'
import { type Session, saveSession } from '../session'
import type { Role } from '../types'
import { Backdrop } from './Backdrop'
import { Buddy } from './Buddy'

const COMPANY = import.meta.env.VITE_COMPANY_NAME ?? 'Alrium'

export function Login({ onSignedIn }: { onSignedIn: (session: Session) => void }) {
  const [busy, setBusy] = useState<Role | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function signIn(role: Role) {
    setBusy(role)
    setError(null)
    try {
      onSignedIn(saveSession(await api.demoLogin(role)))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed.')
      setBusy(null)
    }
  }

  return (
    <main className="login">
      <Backdrop />
      <div className="login-body">
        <Buddy mood={busy ? 'thinking' : 'idle'} waving={!busy} />
        <h1>Hi, I&rsquo;m CoO</h1>
        <p className="lede">{COMPANY}&rsquo;s operations assistant. Just talk to me.</p>
        <div className="login-actions">
          <button type="button" className="primary" disabled={busy !== null} onClick={() => signIn('admin')}>
            {busy === 'admin' ? 'Signing in…' : 'Continue as Admin'}
          </button>
          <button type="button" className="secondary" disabled={busy !== null} onClick={() => signIn('employee')}>
            {busy === 'employee' ? 'Signing in…' : 'Continue as Employee'}
          </button>
        </div>
        {error && (
          <p className="toast toast--inline" role="alert">
            {error}
          </p>
        )}
      </div>
    </main>
  )
}
