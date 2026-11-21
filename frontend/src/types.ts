// Shapes returned by the CoO backend (see backend/app/api/*.py).

export type Role = 'admin' | 'employee'

export interface UserPublic {
  id: string
  email: string
  name: string
  role: Role
  permissions: string[]
}

export interface TokenResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: UserPublic
}

export interface Conversation {
  id: string
  title: string
  status: string
  message_count: number
  summary: string | null
  created_at: string
  updated_at: string
  last_activity_at: string
}

export interface SessionStart {
  conversation: Conversation
  continued: boolean
}

export interface Card {
  type: string
  [key: string]: unknown
}

export interface Message {
  id: string
  sequence: number
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  meta: Record<string, unknown>
  created_at: string
}

export type ActionStatus =
  | 'requested'
  | 'pending'
  | 'approved'
  | 'executing'
  | 'succeeded'
  | 'failed'
  | 'uncertain'
  | 'cancelled'
  | 'expired'

export interface PendingAction {
  id: string
  conversation_id: string
  action_type: string
  description: string
  proposal: Record<string, unknown>
  version: number
  status: ActionStatus
  expires_at: string | null
  completed_at: string | null
  result: Record<string, unknown> | null
  error: string | null
  created_at: string
  updated_at: string
}

export interface VoiceConfig {
  mode: 'agora' | 'browser'
  missing: string[]
}

/** What the browser needs to join an Agora voice call with CoO. */
export interface VoiceJoin {
  session_id: string
  app_id: string
  channel: string
  uid: number
  token: string
  agent_uid: number
  conversation_id: string
}

export interface VoiceState {
  session_id: string
  status: 'starting' | 'active' | 'ended' | 'failed'
  conversation_id: string
  error: string | null
}

export interface TurnResponse {
  reply: string
  cards: Card[]
  pending_actions: PendingAction[]
  error: boolean
  switch_to_conversation_id: string | null
  user_message: Message
  assistant_message: Message
}
