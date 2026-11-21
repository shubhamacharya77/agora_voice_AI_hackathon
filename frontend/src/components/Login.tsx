import { useState } from 'react'

import { api } from '../api'
import { type Session, saveSession } from '../session'
import type { Role } from '../types'

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
      <div className="login-card">
        <div className="brand-mark" aria-hidden="true" />
        <h1>CoO</h1>
        <p className="lede">
          {COMPANY}&rsquo;s Chief of Operations. Ask by voice, approve by voice, and CoO takes care of the rest.
        </p>
        <div className="login-actions">
          <button type="button" className="primary" disabled={busy !== null} onClick={() => signIn('admin')}>
            {busy === 'admin' ? 'Signing in…' : 'Continue as Admin'}
          </button>
          <button type="button" className="secondary" disabled={busy !== null} onClick={() => signIn('employee')}>
            {busy === 'employee' ? 'Signing in…' : 'Continue as Employee'}
          </button>
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <p className="fineprint">Demo accounts with sample data. Nothing real is sent.</p>
      </div>
    </main>
  )
}
