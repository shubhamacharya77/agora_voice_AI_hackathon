import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { ApiError, api } from '../api'
import type { Session } from '../session'
import type { Message, TurnResponse } from '../types'
import { type LiveCall, joinCall } from '../voice/agoraCall'
import { createBrowserVoice } from '../voice/browserVoice'
import type { VoiceEngine } from '../voice/types'
import { type Status, callStatus, visibleMessages, waitingProposal } from './state'

// Browser stand-in: after this many silent listens in a row, wait for a tap.
const QUIET_LISTENS_BEFORE_PAUSE = 3
// Agora call: how often the transcript refreshes from CoO's saved conversation.
const CALL_REFRESH_MS = 1500
// Agora call: show "thinking" for at most this long after the user stops talking.
const THINKING_TIMEOUT_MS = 12000

type VoiceMode = 'agora' | 'browser'

export function usePortal(session: Session, onSignedOut: () => void) {
  const browserVoice: VoiceEngine = useMemo(() => createBrowserVoice(), [])
  const [mode, setMode] = useState<VoiceMode>('browser')
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [status, setStatus] = useState<Status>('idle')
  const [heard, setHeard] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [active, setActive] = useState(false)
  const activeRef = useRef(false)
  const conversationRef = useRef<string | null>(null)
  // Resolves once the portal has its conversation; words said before that wait for it.
  const ready = useRef<Promise<string | null>>(Promise.resolve(null))
  const messagesRef = useRef<Message[]>([])
  const call = useRef<{ live: LiveCall; sessionId: string; timer: number } | null>(null)
  const callState = useRef({ agentSpeaking: false, userSpeaking: false, awaitingReply: false, thinkingSince: 0 })
  const token = session.token

  const showMessages = useCallback((next: Message[]) => {
    messagesRef.current = next
    setMessages(next)
  }, [])

  const fail = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 401) {
        onSignedOut() // the login expired
        return
      }
      setError(err instanceof Error ? err.message : 'Something went wrong.')
      setStatus('error')
    },
    [onSignedOut],
  )

  const openConversation = useCallback(
    async (id: string, carryOver: Message[] = []) => {
      conversationRef.current = id
      setConversationId(id)
      const history = await api.messages(token, id)
      showMessages([...visibleMessages(history), ...carryOver])
    },
    [token, showMessages],
  )

  // Opening the portal: which voice is available, then continue recent work or start fresh.
  useEffect(() => {
    let cancelled = false
    api
      .voiceConfig(token)
      .then((config) => !cancelled && setMode(config.mode))
      .catch(() => undefined) // the browser stand-in still works
    ready.current = api
      .startSession(token)
      .then(async (start) => {
        if (cancelled) return null
        await openConversation(start.conversation.id)
        return start.conversation.id
      })
      .catch((err) => {
        fail(err)
        return null
      })
    return () => {
      cancelled = true
    }
  }, [token, openConversation, fail])

  // --- browser stand-in: listen -> send -> speak, in a loop ---

  const send = useCallback(
    async (words: string): Promise<TurnResponse | null> => {
      const id = conversationRef.current ?? (await ready.current)
      if (!id) return null
      setStatus('thinking')
      setHeard('')
      const pending: Message = {
        id: `pending-${Date.now()}`,
        sequence: Number.MAX_SAFE_INTEGER,
        role: 'user',
        content: words,
        meta: { pending: true },
        created_at: new Date().toISOString(),
      }
      showMessages([...messagesRef.current, pending])
      const result = await api.turn(token, id, words)
      const switchTo = result.switch_to_conversation_id
      if (switchTo && switchTo !== id) {
        // A resumed or new conversation: show it, with this exchange carried over at the end.
        await openConversation(switchTo, [result.user_message, result.assistant_message])
      } else {
        showMessages([
          ...messagesRef.current.filter((m) => m.id !== pending.id),
          result.user_message,
          result.assistant_message,
        ])
      }
      return result
    },
    [token, openConversation, showMessages],
  )

  const speak = useCallback(
    async (reply: string) => {
      setStatus('speaking')
      await browserVoice.speak(reply)
    },
    [browserVoice],
  )

  const loop = useCallback(async () => {
    let quiet = 0
    try {
      while (activeRef.current) {
        setStatus('listening')
        const words = await browserVoice.listen(setHeard)
        if (!activeRef.current) break
        if (!words) {
          if (++quiet >= QUIET_LISTENS_BEFORE_PAUSE) break
          continue
        }
        quiet = 0
        const result = await send(words)
        if (!result || !activeRef.current) break // tapped to stop while CoO was working
        await speak(result.reply)
      }
      setStatus('idle')
    } catch (err) {
      fail(err)
    } finally {
      activeRef.current = false
      setActive(false)
      setHeard('')
    }
  }, [browserVoice, send, speak, fail])

  // --- Agora call: Agora hears the user, CoO answers, Murf speaks ---

  const showCallStatus = useCallback(() => {
    const state = callState.current
    if (state.awaitingReply && Date.now() - state.thinkingSince > THINKING_TIMEOUT_MS) state.awaitingReply = false
    setStatus(callStatus(state))
  }, [])

  const refreshDuringCall = useCallback(async () => {
    const current = call.current
    if (!current) return
    const state = await api.voiceState(token, current.sessionId)
    if (state.conversation_id !== conversationRef.current) {
      // CoO resumed or started a conversation during the call: follow it.
      await openConversation(state.conversation_id, messagesRef.current.slice(-2))
      return
    }
    const history = visibleMessages(await api.messages(token, state.conversation_id))
    if (history.length !== messagesRef.current.length) {
      showMessages(history)
      callState.current.awaitingReply = false
      showCallStatus()
    }
  }, [token, openConversation, showMessages, showCallStatus])

  const hangUp = useCallback(async () => {
    const current = call.current
    call.current = null
    activeRef.current = false
    setActive(false)
    setStatus('idle')
    if (!current) return
    window.clearInterval(current.timer)
    await current.live.hangUp().catch(() => undefined)
    await api.stopVoice(token, current.sessionId).catch(() => undefined)
  }, [token])

  const startCall = useCallback(async () => {
    const id = conversationRef.current ?? (await ready.current)
    if (!id) return
    activeRef.current = true
    setActive(true)
    setError(null)
    setStatus('thinking') // connecting
    try {
      const join = await api.startVoice(token, id)
      const live = await joinCall(join, {
        onAgentSpeaking: (speaking) => {
          callState.current.agentSpeaking = speaking
          if (speaking) callState.current.awaitingReply = false
          showCallStatus()
        },
        onUserSpeaking: (speaking) => {
          const state = callState.current
          if (state.userSpeaking && !speaking) {
            state.awaitingReply = true // the user just finished: CoO is working on it
            state.thinkingSince = Date.now()
          }
          state.userSpeaking = speaking
          showCallStatus()
        },
        onDropped: (reason) => {
          void hangUp()
          fail(new Error(`The voice call dropped (${reason}). Tap the orb to reconnect.`))
        },
      })
      const timer = window.setInterval(() => {
        refreshDuringCall().catch(() => undefined)
        showCallStatus()
      }, CALL_REFRESH_MS)
      call.current = { live, sessionId: join.session_id, timer }
      if (!activeRef.current) {
        await hangUp() // tapped to stop while connecting
        return
      }
      setStatus('listening')
    } catch (err) {
      await hangUp()
      fail(err)
    }
  }, [token, hangUp, fail, showCallStatus, refreshDuringCall])

  /** The orb: tap to start talking, tap again to stop. */
  const toggle = useCallback(() => {
    if (activeRef.current) {
      if (mode === 'agora') {
        void hangUp()
      } else {
        activeRef.current = false
        browserVoice.stop()
        setActive(false)
        setStatus('idle')
      }
      return
    }
    if (mode === 'agora') {
      void startCall()
      return
    }
    if (!browserVoice.supported) {
      setError("This browser can't do speech recognition. Please use Chrome or Edge.")
      setStatus('error')
      return
    }
    setError(null)
    activeRef.current = true
    setActive(true)
    void loop()
  }, [mode, browserVoice, loop, startCall, hangUp])

  /** Testing aid (?text in the address): type instead of speaking. */
  const sendText = useCallback(
    async (words: string) => {
      try {
        const result = await send(words)
        if (result && mode === 'browser') await speak(result.reply)
        setStatus('idle')
      } catch (err) {
        fail(err)
      }
    },
    [send, speak, fail, mode],
  )

  // Leaving the page ends the call and silences the stand-in voice.
  useEffect(
    () => () => {
      browserVoice.stop()
      void hangUp()
    },
    [browserVoice, hangUp],
  )

  return {
    conversationId,
    messages,
    status,
    heard,
    error,
    active,
    voiceName: mode === 'agora' ? 'Agora Conversational AI with Murf Falcon' : browserVoice.name,
    voiceSupported: mode === 'agora' || browserVoice.supported,
    waiting: waitingProposal(messages),
    toggle,
    sendText,
  }
}
