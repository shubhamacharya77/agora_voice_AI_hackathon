import type { VoiceJoin } from '../types'

// A live voice call with CoO through Agora: the browser publishes the microphone, and
// plays CoO's agent, whose voice is Murf. Agora's agent hears the user and calls CoO's
// backend; the transcript comes from CoO's own saved conversation, not from Agora.

export interface CallEvents {
  onAgentSpeaking: (speaking: boolean) => void
  onUserSpeaking: (speaking: boolean) => void
  onDropped: (reason: string) => void
}

export interface LiveCall {
  hangUp: () => Promise<void>
}

const SPEAKING_LEVEL = 8 // volume-indicator level (0-100) that counts as speech

export async function joinCall(join: VoiceJoin, events: CallEvents): Promise<LiveCall> {
  // Loaded only when Agora voice is set up, so the stand-in portal stays light.
  const { default: AgoraRTC } = await import('agora-rtc-sdk-ng')
  const client = AgoraRTC.createClient({ mode: 'rtc', codec: 'vp8' })

  client.on('user-published', async (user, mediaType) => {
    if (mediaType !== 'audio') return
    const track = await client.subscribe(user, 'audio')
    track.play()
  })
  client.enableAudioVolumeIndicator()
  client.on('volume-indicator', (levels) => {
    for (const { uid, level } of levels) {
      const speaking = level > SPEAKING_LEVEL
      if (String(uid) === String(join.agent_uid)) events.onAgentSpeaking(speaking)
      else if (String(uid) === String(join.uid)) events.onUserSpeaking(speaking)
    }
  })
  client.on('connection-state-change', (current, _previous, reason) => {
    if (current === 'DISCONNECTED' && reason !== 'LEAVE') events.onDropped(String(reason ?? 'disconnected'))
  })

  await client.join(join.app_id, join.channel, join.token, join.uid)
  const microphone = await AgoraRTC.createMicrophoneAudioTrack()
  await client.publish(microphone)

  return {
    async hangUp() {
      microphone.stop()
      microphone.close()
      await client.leave()
    },
  }
}
