export interface Message {
  id: string
  sender: string
  channel: string
  timestamp: string
  original: string
  translation: string
  status: string
  historical?: boolean
  translationState?: string
  translationReason?: string
  canRetry?: boolean
}

export interface Phrase {
  id: string
  category: string
  source_text: string
  translations: { en?: string; ko?: string }
  favorite: boolean
  reviewed: boolean
  revision?: number
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
  outputLanguage?: 'zh' | 'en'
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
  updateAvailable: boolean
  manualState?: 'idle' | 'translating' | 'done' | 'failed'
  manualError?: string
  management: ManagementState
  helpers: { loot: HelperState; repeat: HelperState; talent: HelperState; board: HelperState }
  phrases: Phrase[]
  settings: { quickInputHotkey?: string; repeatHotkey?: string; repeatAction?: string; repeatInterval?: number; [key: string]: unknown }
}

export interface DesktopPreferences {
  font_size: number
  topmost: boolean
  mode: 'normal' | 'compact'
  hotkeys: { focus?: string; translate?: string; copy?: string }
  repeat_hotkey: string
  overlay_enabled: boolean
  overlay_style: 'subtitle' | 'panel'
  overlay_font_size: number
  overlay_alpha: number
  overlay_width: number
  overlay_timeout: number
  overlay_original: boolean
}
export interface NoteBlock {
  kind: string
  spans: { text: string; styles: string[] }[]
  level: number
  marker: string
  depth: number
}
export interface UpdateInfo {
  currentVersion?: string
  state: string; detail: string; version: string; notes: string; noteBlocks?: NoteBlock[]
  downloaded: number; total: number; busy: boolean; canInstall: boolean; archivePath: string
}
export type TranslationProvider = 'aliyun' | 'deepseek'
export interface TranslationProviderProfile {
  id: TranslationProvider
  name: string
  configured: boolean
  maskedKey: string
  contextEnabled?: boolean
}
export interface AccountBalanceInfo {
  busy: boolean
  available: boolean | null
  balances: { currency: string; totalBalance: string | number; grantedBalance: string | number; toppedUpBalance: string | number }[]
  updatedAt: string
  message: string
}
export interface AccountInfo {
  configured: boolean; state: string; busy: boolean; maskedKey: string; account: string; message: string
  provider?: TranslationProvider
  providers?: TranslationProviderProfile[]
  contextEnabled?: boolean
  balance?: AccountBalanceInfo
}
export interface DiagnosticsInfo {
  enabled: boolean; busy?: boolean; exportPath?: string; message?: string
  sizeBytes?: number; maxBytes?: number
}
export interface ProcessInfo { pid: number; path?: string; build?: number | string; name?: string; selected?: boolean }
export interface ManagementState {
  preferences: DesktopPreferences
  update: UpdateInfo
  account: AccountInfo
  diagnostics: DiagnosticsInfo
  processes: ProcessInfo[]
  monitors?: { id: number; label: string; primary: boolean; selected: boolean; width: number; height: number }[]
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
