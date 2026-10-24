export interface Message {
  id: string
  sender: string
  channel: string
  timestamp: string
  original: string
  translation: string
  status: string
  historical?: boolean
}

export interface Phrase {
  id: string
  category: string
  source_text: string
  translations: { en?: string; ko?: string }
  favorite: boolean
  reviewed: boolean
}

export interface HelperState {
  state: string
  detail: string
  enabled?: boolean
  running?: boolean
  action?: string
  interval?: number
  hotkey?: string
  lastResult?: string
}

export interface Snapshot {
  generation: number
  messages: Message[]
  captureState: string
  serviceState: string
  cloudConfigured: boolean
  autoTranslate: boolean
  targetLanguage: string
  channelFilter: string
  channelOptions?: string[]
  theme: string
  draft: string
  preview: string
  previewSource: string
  previewTarget?: string
  manualSource: string
  manualBusy: boolean
  status: string
  isDemo: boolean
  helpers: { loot: HelperState; repeat: HelperState; talent: HelperState; board: HelperState }
  phrases: Phrase[]
  settings: { quickInputHotkey?: string; repeatHotkey?: string; repeatAction?: string; repeatInterval?: number; [key: string]: unknown }
}

export interface CommandResult {
  ok: boolean
  error?: string
  message?: string
  [key: string]: unknown
}
export type Command = (name: string, payload?: Record<string, unknown>) => Promise<CommandResult>
export interface NativeApi {
  snapshot: () => Promise<Snapshot>
  command: Command
}
declare global {
  interface Window { pywebview?: { api?: NativeApi } }
}
