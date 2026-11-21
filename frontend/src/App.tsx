import { useCallback, useState } from 'react'

import { Login } from './components/Login'
import { Portal } from './components/Portal'
import { type Session, clearSession, loadSession } from './session'

export default function App() {
  const [session, setSession] = useState<Session | null>(() => loadSession())

  const signOut = useCallback(() => {
    clearSession()
    setSession(null)
  }, [])

  return session ? <Portal session={session} onSignOut={signOut} /> : <Login onSignedIn={setSession} />
}
