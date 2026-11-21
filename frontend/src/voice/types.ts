// The portal talks to a voice engine through this interface only.
// Today: the browser's own speech (a stand-in). Phase 7: Agora's voice engine with Murf.

export interface VoiceEngine {
  readonly name: string
  readonly supported: boolean
  /** Listen for one utterance. Resolves with the final words, or null if nothing was said. */
  listen(onInterim: (words: string) => void): Promise<string | null>
  /** Speak a reply; resolves when it has finished (or was stopped). */
  speak(text: string): Promise<void>
  /** Stop listening and speaking straight away. */
  stop(): void
}

export class VoiceError extends Error {}
