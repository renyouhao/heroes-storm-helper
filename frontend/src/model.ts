import type { Message, Snapshot } from './types'

export const previewSnapshot: Snapshot = {
  generation: 0,
  messages: [
    { id: 'preview-1', sender: 'Raven', channel: '团队', timestamp: '14:21', original: 'Take the camp first, then rotate bot.', translation: '先拿营地，然后一起去下路。', status: 'done' },
    { id: 'preview-2', sender: 'Jin', channel: '团队', timestamp: '14:22', original: 'My ult is up in 10 seconds.', translation: '等我一下，大招还有 10 秒。', status: 'done' },
    { id: 'preview-3', sender: 'Mochi', channel: '团队', timestamp: '14:22', original: 'Got it. I will check vision.', translation: '收到，我先去看一下视野。', status: 'done' },
    { id: 'preview-4', sender: 'Raven', channel: '团队', timestamp: '14:23', original: 'Let them use their abilities first.', translation: '别急，等对面先交技能。', status: 'done' },
  ],
  captureState: '静态预览', serviceState: '静态预览', cloudConfigured: false,
  autoTranslate: true, targetLanguage: 'en', channelFilter: '全部聊天', channelOptions: ['全部聊天', '团队与所有玩家', '仅团队', '仅队伍', '仅密语', '仅所有玩家'], theme: 'lavender',
  draft: '我们等大招好了再开团', preview: "Let’s wait until our ult is ready, then start the fight.",
  previewSource: '我们等大招好了再开团', manualSource: '示例译文', manualBusy: false,
  status: '静态预览 · 所有示例内容仅用于展示', isDemo: true, updateAvailable: false,
  manualState: 'idle', manualError: '',
  management: {
    preferences: { font_size: 12, topmost: false, mode: 'normal', hotkeys: { focus: 'Ctrl+T' }, repeat_hotkey: 'F8',
      overlay_enabled: true, overlay_style: 'subtitle', overlay_font_size: 11, overlay_alpha: .88,
      overlay_width: 420, overlay_timeout: 6, overlay_original: false },
    update: { state: 'idle', detail: '手动检查最新正式版本。', version: '', notes: '', downloaded: 0, total: 0, busy: false, canInstall: false, archivePath: '' },
    account: { configured: false, state: 'unconfigured', busy: false, maskedKey: '', account: '', message: '尚未配置翻译服务，可继续接收原文。' },
    diagnostics: { enabled: false, busy: false, message: '', exportPath: '' }, processes: [],
  },
  helpers: {
    loot: { state: 'idle', detail: '先在游戏中打开补给箱页面，再准备开箱。' },
    repeat: { state: 'idle', detail: '保存配置后，在游戏中按 F8 开始或结束连按。', action: 'S', interval: 20 },
    talent: { state: 'disabled', detail: '在游戏中标记爱心天赋，开启后自动检测。', enabled: false },
    board: { state: 'disabled', detail: '开启后，在游戏 Tab 面板中双击具体项目。', enabled: false },
  },
  phrases: [
    { id: 'p1', category: '团战', source_text: '我们等大招好了再开团', translations: { en: "Let’s wait until our ult is ready, then start the fight." }, favorite: true, reviewed: true },
    { id: 'p2', category: '机制', source_text: '先拿营地，然后一起去下路。', translations: { en: 'Take the camp first, then rotate bot.' }, favorite: true, reviewed: true },
    { id: 'p3', category: '礼貌', source_text: '收到，谢谢！', translations: { en: 'Got it, thanks!', ko: '알겠습니다, 감사합니다!' }, favorite: false, reviewed: true },
  ],
  settings: { quickInputHotkey: 'Ctrl+T', repeatHotkey: 'F8', repeatAction: 'S', repeatInterval: 20 },
}

export function normalizeSnapshot(raw: Partial<Snapshot>): Snapshot {
  return {
    ...previewSnapshot, messages: [], draft: '', preview: '', previewSource: '', manualSource: '',
    ...raw,
    theme: 'lavender',
    helpers: { ...previewSnapshot.helpers, ...raw.helpers },
    settings: { ...previewSnapshot.settings, ...raw.settings },
    management: {
      preferences: { ...previewSnapshot.management.preferences, ...raw.management?.preferences },
      update: { ...previewSnapshot.management.update, ...raw.management?.update },
      account: { ...previewSnapshot.management.account, ...raw.management?.account },
      diagnostics: { ...previewSnapshot.management.diagnostics, ...raw.management?.diagnostics },
      processes: raw.management?.processes ?? [],
    },
    phrases: raw.phrases ?? [],
  }
}

export function visibleMessages(messages: Message[], channel: string) {
  if (!channel || channel === 'all' || channel === '全部聊天') return messages
  const channels: Record<string, string[]> = {
    team: ['team', 'Team', '团队', '队伍'], party: ['party', 'Party', '小队'],
    '仅团队': ['team', 'Team', '团队'], '仅队伍': ['party', 'Party', '队伍'],
    '仅密语': ['密语', 'whisper'], '仅所有玩家': ['所有玩家', 'all'],
    '团队与所有玩家': ['team', 'Team', '团队', '所有玩家', 'all'],
  }
  return messages.filter(message => (channels[channel] ?? [channel]).includes(message.channel))
}

export function canCopyPreview(snapshot: Snapshot, draft: string) {
  return Boolean(snapshot.preview.trim() && !snapshot.manualBusy && draft.trim()
    && snapshot.previewSource === draft
    && (!snapshot.previewTarget || snapshot.previewTarget === snapshot.targetLanguage))
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  return typeof error === 'string' ? error : '操作没有完成，请重试。'
}

export function selectDraft(local: string, remote: string, pending: string | null) {
  return pending !== null && remote !== pending ? local : remote
}
