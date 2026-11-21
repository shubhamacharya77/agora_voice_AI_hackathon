import { VoiceError, type VoiceEngine } from './types'

// Stand-in voice until Agora + Murf are connected (Phase 7): the browser's own speech
// recognition and speech synthesis. Works in Chrome and Edge.

const LANG = import.meta.env.VITE_SPEECH_LANG ?? 'en-IN'

const MIC_ERRORS: Record<string, string> = {
  'not-allowed': 'Microphone access is blocked. Allow it in the address bar, then tap the orb again.',
  'service-not-allowed': 'Microphone access is blocked. Allow it in the address bar, then tap the orb again.',
  'audio-capture': 'No microphone was found.',
  network: "The browser's speech service is unreachable. Check the internet connection.",
}

export function createBrowserVoice(): VoiceEngine {
  const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition
  let current: SpeechRecognitionLike | null = null

  return {
    name: 'Browser speech (stand-in for Agora + Murf)',
    supported: Boolean(Recognition) && 'speechSynthesis' in window,

    listen(onInterim) {
      return new Promise((resolve, reject) => {
        if (!Recognition) {
          reject(new VoiceError('This browser has no speech recognition. Use Chrome or Edge.'))
          return
        }
        const recognition = new Recognition()
        recognition.lang = LANG
        recognition.continuous = false
        recognition.interimResults = true
        let finalWords = ''
        recognition.onresult = (event) => {
          let interim = ''
          for (let i = event.resultIndex; i < event.results.length; i++) {
            const result = event.results[i]
            if (result.isFinal) finalWords += result[0].transcript
            else interim += result[0].transcript
          }
          onInterim((finalWords + interim).trim())
        }
        recognition.onerror = (event) => {
          if (event.error === 'no-speech' || event.error === 'aborted') return // onend resolves
          reject(new VoiceError(MIC_ERRORS[event.error] ?? `Speech recognition failed (${event.error}).`))
        }
        recognition.onend = () => {
          current = null
          resolve(finalWords.trim() || null)
        }
        current = recognition
        recognition.start()
      })
    },

    speak(text) {
      return new Promise((resolve) => {
        window.speechSynthesis.cancel()
        const utterance = new SpeechSynthesisUtterance(text)
        utterance.lang = LANG
        utterance.onend = () => resolve()
        utterance.onerror = () => resolve()
        window.speechSynthesis.speak(utterance)
      })
    },

    stop() {
      current?.abort()
      current = null
      window.speechSynthesis.cancel()
    },
  }
}
