import { createElement, useEffect, useId, useRef, useState } from 'react'
import type { CSSProperties, FormEvent, KeyboardEvent, ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Check, Download, KeyRound, LoaderCircle, RefreshCw, Settings, ShieldCheck, Trash2, X } from 'lucide-react'
import type { AccountInfo, Command, DesktopPreferences, Phrase, Snapshot, UpdateInfo } from './types'
import './management.css'

export type ManagementPanel = 'settings' | 'updates' | 'account' | 'phrase'
type SettingsTab = 'appearance' | 'overlay' | 'hotkeys' | 'connection' | 'diagnostics'
type NoteBlock = NonNullable<UpdateInfo['noteBlocks']>[number]

interface Props {
  panel: ManagementPanel
  snapshot: Snapshot
  command: Command
  onClose: () => void
  onNavigate?: (panel: ManagementPanel) => void
  phrase?: Phrase
}

const titles: Record<ManagementPanel, string> = { settings: '偏好设置', updates: '检查更新', account: '个人翻译账户', phrase: '编辑常用短句' }
const tabs: [SettingsTab, string][] = [['appearance', '外观'], ['overlay', '游戏内'], ['hotkeys', '快捷键'], ['connection', '连接'], ['diagnostics', '诊断']]
const categories = ['对线', '团战', '机制', '礼貌']
const hotkeyFields = [['focus', '呼出快捷输入', 'Ctrl+T'], ['translate', '翻译主窗口中文', 'Ctrl+Enter'], ['copy', '复制主窗口译文', 'Ctrl+Shift+C']] as const
type HotkeyAction = typeof hotkeyFields[number][0]

const legacyEnvError = '本机 .env 缺失或格式无效，请检查两个访问密钥字段。'
export function parseLegacyEnv(source: string): { accessKeyId: string; accessKeySecret: string } {
  const names = ['ALIBABA_CLOUD_ACCESS_KEY_ID', 'ALIBABA_CLOUD_ACCESS_KEY_SECRET']
  const values = new Map<string, string>()
  const invalid = () => { throw new Error(legacyEnvError) }
  if (new TextEncoder().encode(source).byteLength > 16384) return invalid()
  for (const raw of source.replace(/^\uFEFF/, '').split(/\r\n|[\n\r\v\f\u001c-\u001e\u0085\u2028\u2029]/)) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    const separator = line.indexOf('=')
    if (separator < 0) return invalid()
    const key = line.slice(0, separator)
    if (!names.includes(key) || values.has(key)) return invalid()
    let value = line.slice(separator + 1).trim()
    if (value.startsWith('"') || value.startsWith("'")) {
      const end = value.indexOf(value[0], 1)
      if (end < 0 || (value.slice(end + 1).trim() && !value.slice(end + 1).trimStart().startsWith('#'))) return invalid()
      value = value.slice(1, end)
    } else value = value.split(' #', 1)[0].trim()
    if (!/^[A-Za-z0-9_./+\-=]{1,256}$/.test(value)) return invalid()
    values.set(key, value)
  }
  if (!names.every(name => values.has(name))) return invalid()
  return { accessKeyId: values.get(names[0])!, accessKeySecret: values.get(names[1])! }
}

export function accountNeedsCancel(account: Pick<AccountInfo, 'busy' | 'state'>, requestPending = false): boolean {
  return requestPending || account.busy || !['ready', 'unconfigured'].includes(account.state)
}

export async function withAccountRequest<T>(pending: { current: boolean }, operation: () => Promise<T>): Promise<T> {
  pending.current = true
  try { return await operation() }
  finally { pending.current = false }
}

export function cancelAccountAfterRequest<T>(request: Promise<unknown> | null, cancel: () => Promise<T>): Promise<T> {
  return request ? request.then(() => cancel(), () => cancel()) : cancel()
}

export function modalFocusTarget<T>(items: T[], current: T | null, backwards: boolean, container: T): T | null {
  if (!items.length) return container
  if (backwards && (current === items[0] || current === container)) return items[items.length - 1]
  if (!backwards && (current === items[items.length - 1] || current === null || !items.includes(current))) return items[0]
  return null
}

function focusInitial(dialog: HTMLElement | null) {
  const initial = dialog?.querySelector<HTMLElement>('[data-initial-focus]')
  if (initial && !initial.matches(':disabled')) initial.focus()
  else dialog?.focus()
}

export function editablePreferences(source: DesktopPreferences): DesktopPreferences {
  const hotkeys: DesktopPreferences['hotkeys'] = {}
  for (const [action] of hotkeyFields) if (source.hotkeys[action] !== undefined) hotkeys[action] = source.hotkeys[action]
  return {
    font_size: source.font_size, topmost: source.topmost, mode: source.mode, hotkeys,
    repeat_hotkey: source.repeat_hotkey, overlay_enabled: source.overlay_enabled,
    overlay_style: source.overlay_style, overlay_font_size: source.overlay_font_size,
    overlay_alpha: source.overlay_alpha, overlay_width: source.overlay_width,
    overlay_timeout: source.overlay_timeout, overlay_original: source.overlay_original,
  }
}

export function overlayPreferencesAfterAction(values: DesktopPreferences, action: 'edit' | 'bottom' | 'finish'): DesktopPreferences {
  if (action === 'finish') return values
  return { ...values, overlay_enabled: true, ...(action === 'bottom' ? { overlay_style: 'subtitle' as const } : {}) }
}

export function validatePreferences(values: DesktopPreferences): string | null {
  const bounds: [number, number, number, string, boolean][] = [
    [values.font_size, 10, 32, '内容字号', true],
    [values.overlay_font_size, 10, 32, '译文字号', true],
    [values.overlay_width, 280, 900, '浮窗宽度', true],
    [values.overlay_timeout, 5, 120, '停留时间', true],
    [values.overlay_alpha, .35, 1, '面板不透明度', false],
  ]
  for (const [value, min, max, label, integer] of bounds) {
    if (!Number.isFinite(value) || value < min || value > max || (integer && !Number.isInteger(value))) {
      return `${label}请填写 ${min}–${max}${integer ? ' 的整数' : ' 之间的数字'}。`
    }
  }
  const bindings = Object.values(values.hotkeys).filter((binding): binding is string => typeof binding === 'string')
  if (bindings.some(binding => !binding.trim())) return '已开启的快捷键不能为空；不使用时请关闭对应开关。'
  const normalized = [...bindings, values.repeat_hotkey].map(binding => binding.replace(/\s/g, '').toLowerCase())
  if (new Set(normalized).size !== normalized.length) return '快捷键有重复，请为每个操作选择不同按键。'
  return null
}

export function updateCapabilities(update: UpdateInfo) {
  return {
    check: !update.busy && !['preparing', 'restarting'].includes(update.state),
    download: update.state === 'available' && !update.busy,
    install: update.state === 'downloaded' && update.canInstall && !update.busy,
    cancel: ['checking', 'downloading'].includes(update.state),
  }
}

export function editPhraseText(phrase: Phrase, field: 'source_text' | 'en' | 'ko', value: string): Phrase {
  return field === 'source_text'
    ? { ...phrase, source_text: value, reviewed: false }
    : { ...phrase, translations: { ...phrase.translations, [field]: value }, reviewed: false }
}

export function preparePhrase(phrase: Phrase): { value?: Phrase; error?: string } {
  const source = phrase.source_text.trim()
  if (!source) return { error: '请填写这句短句的中文。' }
  if (Array.from(source).length > 1000) return { error: '中文短句请控制在 1000 个字符以内。' }
  if (!categories.includes(phrase.category)) return { error: '请选择对线、团战、机制或礼貌分类。' }
  const translations: Phrase['translations'] = {}
  for (const language of ['en', 'ko'] as const) {
    const text = phrase.translations[language]?.trim()
    if (text && Array.from(text).length > 4000) return { error: '每种语言的译文请控制在 4000 个字符以内。' }
    if (text) translations[language] = text
  }
  if (!Object.keys(translations).length) return { error: '请至少填写一种语言的译文；确认过的译文才能标记为已校对。' }
  return { value: { id: phrase.id, category: phrase.category, source_text: source, translations, reviewed: phrase.reviewed, favorite: phrase.favorite, revision: phrase.revision ?? 1 } }
}

function initialPhrase(phrase?: Phrase): Phrase {
  return phrase ? { ...phrase, translations: { ...phrase.translations } } : {
    id: crypto.randomUUID(), category: '机制', source_text: '', translations: {}, reviewed: false, favorite: false, revision: 1,
  }
}

function Field({ label, help, children }: { label: string; help?: string; children: ReactNode }) {
  return <label className="mg-field"><span className="mg-field-label">{label}</span>{children}{help && <span className="mg-help">{help}</span>}</label>
}

function Checkbox({ checked, onChange, children, disabled }: { checked: boolean; onChange: (checked: boolean) => void; children: ReactNode; disabled?: boolean }) {
  return <label className="mg-checkbox"><input type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)}/><span>{children}</span></label>
}

function bytes(value: number) {
  return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MiB` : `${Math.round(value / 1024)} KiB`
}

function NoteSpans({ spans }: { spans: NoteBlock['spans'] }) {
  return <>{spans.map((span, index) => {
    let node: ReactNode = span.text
    if (span.styles.includes('code')) node = <code>{node}</code>
    if (span.styles.includes('bold') || span.styles.includes('strong')) node = <strong>{node}</strong>
    if (span.styles.includes('italic') || span.styles.includes('emphasis')) node = <em>{node}</em>
    return <span key={index}>{node}</span>
  })}</>
}

export function ReleaseNotes({ blocks, text }: { blocks?: NoteBlock[]; text: string }) {
  if (!blocks?.length) return <p className="mg-notes-fallback">{text || '点击“检查更新”后，这里会显示发布说明。'}</p>
  const output: ReactNode[] = []
  for (let index = 0; index < blocks.length; index++) {
    const block = blocks[index]
    if (block.kind === 'list') {
      const ordered = /^\d/.test(block.marker)
      const items: ReactNode[] = []
      const first = index
      while (index < blocks.length && blocks[index].kind === 'list' && /^\d/.test(blocks[index].marker) === ordered) {
        const item = blocks[index]
        items.push(<li key={index} value={ordered ? Number.parseInt(item.marker, 10) : undefined} style={{ marginInlineStart: `${Math.max(0, item.depth) * 1.2}em` }}><NoteSpans spans={item.spans}/></li>)
        index++
      }
      index--
      output.push(ordered ? <ol key={first}>{items}</ol> : <ul key={first}>{items}</ul>)
    } else if (block.kind === 'heading') {
      output.push(createElement(`h${Math.min(6, Math.max(3, block.level + 2))}`, { key: index }, <NoteSpans spans={block.spans}/>))
    } else if (block.kind === 'code') {
      output.push(<pre key={index}><code>{block.spans.map(span => span.text).join('')}</code></pre>)
    } else if (block.kind === 'quote') {
      output.push(<blockquote key={index}><NoteSpans spans={block.spans}/></blockquote>)
    } else {
      output.push(<p key={index}><NoteSpans spans={block.spans}/></p>)
    }
  }
  return <>{output}</>
}

export function ManagementDialog({ panel, snapshot, command, onClose, onNavigate, phrase }: Props) {
  const id = useId()
  const dialogRef = useRef<HTMLElement>(null)
  const backdropRef = useRef<HTMLDivElement>(null)
  const requestRef = useRef(false)
  const pendingAccountRef = useRef(false)
  const issuedAccountRef = useRef(false)
  const accountRequestPromiseRef = useRef<Promise<unknown> | null>(null)
  const closingRef = useRef(false)
  const mountedRef = useRef(true)
  const importRevision = useRef(0)
  const closeRef = useRef<() => void>(() => {})
  const [activePanel, setActivePanel] = useState(panel)
  const [tab, setTab] = useState<SettingsTab>('appearance')
  const [values, setValues] = useState<DesktopPreferences>(() => editablePreferences(snapshot.management.preferences))
  const [editedPhrase, setEditedPhrase] = useState(() => initialPhrase(phrase))
  const [accessKeyId, setAccessKeyId] = useState('')
  const [secret, setSecret] = useState('')
  const [deleteAccount, setDeleteAccount] = useState(false)
  const [selectedPid, setSelectedPid] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const management = snapshot.management
  const { account, update, diagnostics, processes } = management
  const accountBusy = busy || account.busy
  const closingBlocked = busy && (activePanel === 'settings' || activePanel === 'phrase')

  useEffect(() => { setActivePanel(panel) }, [panel])
  useEffect(() => { setEditedPhrase(initialPhrase(phrase)) }, [phrase?.id])
  useEffect(() => {
    setError(''); setMessage(''); setDeleteAccount(false)
    if (activePanel !== 'account') { importRevision.current++; setAccessKeyId(''); setSecret('') }
  }, [activePanel])
  useEffect(() => {
    if (account.state === 'ready' || account.state === 'removed') { setAccessKeyId(''); setSecret('') }
  }, [account.state])
  useEffect(() => {
    if (processes.length && !processes.some(process => String(process.pid) === selectedPid)) setSelectedPid(String(processes[0].pid))
  }, [processes, selectedPid])

  const cancelOwnedAccountRequest = () => {
    if ((activePanel === 'account' || issuedAccountRef.current) && accountNeedsCancel(account, pendingAccountRef.current || issuedAccountRef.current)) {
      void cancelAccountAfterRequest(accountRequestPromiseRef.current, () => command('account_cancel'))
    }
  }
  const close = () => {
    if (closingBlocked || closingRef.current) return
    closingRef.current = true
    importRevision.current++
    setAccessKeyId(''); setSecret('')
    cancelOwnedAccountRequest()
    onClose()
  }
  closeRef.current = close

  useEffect(() => {
    mountedRef.current = true
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const backdrop = backdropRef.current
    const background = Array.from(document.body.children).filter((element): element is HTMLElement => element instanceof HTMLElement && element !== backdrop)
    const previous = background.map(element => ({ element, inert: element.inert }))
    previous.forEach(({ element }) => { element.inert = true })
    const keepFocus = (event: FocusEvent) => {
      if (event.target instanceof Node && !dialogRef.current?.contains(event.target)) dialogRef.current?.focus()
    }
    document.addEventListener('focusin', keepFocus)
    focusInitial(dialogRef.current)
    return () => {
      mountedRef.current = false
      importRevision.current++
      document.removeEventListener('focusin', keepFocus)
      previous.forEach(({ element, inert }) => { element.inert = inert })
      if (opener?.isConnected) opener.focus()
    }
  }, [])

  useEffect(() => { focusInitial(dialogRef.current) }, [activePanel])

  const trapKeyboard = (event: KeyboardEvent<HTMLElement>) => {
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeRef.current(); return }
    if (event.key !== 'Tab') return
    const items = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex="0"]') || [])
      .filter(element => element.tabIndex >= 0 && element.getClientRects().length > 0)
    const container = dialogRef.current
    if (!container) return
    const target = modalFocusTarget(items, document.activeElement as HTMLElement | null, event.shiftKey, container)
    if (target) { event.preventDefault(); target.focus() }
  }

  const navigate = (next: ManagementPanel) => {
    if (busy) return
    if (activePanel === 'account') { importRevision.current++; setAccessKeyId(''); setSecret(''); cancelOwnedAccountRequest() }
    setActivePanel(next); onNavigate?.(next)
  }

  const perform = async (name: string, payload?: Record<string, unknown>, success?: string): Promise<boolean> => {
    if (requestRef.current) return false
    const accountRequest = name === 'account_connect' || name === 'account_delete'
    requestRef.current = true; setBusy(true); setError(''); setMessage('')
    try {
      let response
      if (accountRequest) {
        issuedAccountRef.current = true
        response = withAccountRequest(pendingAccountRef, () => command(name, payload))
        accountRequestPromiseRef.current = response
      } else response = command(name, payload)
      const result = await response
      if (!result.ok) { if (mountedRef.current) setError(result.error || '操作未完成，请检查当前状态后重试。'); return false }
      if (mountedRef.current) setMessage(success || result.message || '')
      return true
    } catch {
      if (mountedRef.current) setError('未能完成这次操作，请检查助手连接后重试。')
      return false
    } finally { requestRef.current = false; if (mountedRef.current) setBusy(false) }
  }

  const change = <K extends keyof DesktopPreferences>(key: K, value: DesktopPreferences[K]) => setValues(current => ({ ...current, [key]: value }))
  const saveSettings = async (closeAfter = true) => {
    const invalid = validatePreferences(values)
    if (invalid) { setError(invalid); return false }
    const saved = await perform('settings_save', { values: editablePreferences(values) }, '设置已保存。')
    if (saved && closeAfter) onClose()
    return saved
  }
  const overlayAction = async (action: 'edit' | 'bottom' | 'finish') => {
    if (!await saveSettings(false)) return
    const applied = await perform('overlay_action', { action }, action === 'finish' ? '浮窗位置已保存。' : '浮窗设置已保存，可在游戏外的安全空闲时调整位置。')
    if (applied) setValues(current => overlayPreferencesAfterAction(current, action))
  }
  const submitAccount = async (event: FormEvent) => {
    event.preventDefault()
    if (requestRef.current || account.busy || snapshot.isDemo || deleteAccount) return
    if (!accessKeyId.trim() || !secret.trim()) { setError('请填写同一把 Key 的 ID 和 Secret。'); return }
    const credentials = { accessKeyId: accessKeyId.trim(), accessKeySecret: secret.trim() }
    setSecret('')
    await perform('account_connect', credentials)
  }
  const importLegacy = async (file?: File) => {
    if (!file || requestRef.current || account.busy || snapshot.isDemo) return
    if (file.size > 16384) { setError(legacyEnvError); return }
    const revision = ++importRevision.current
    requestRef.current = true; setBusy(true); setError(''); setMessage('')
    try {
      const credentials = parseLegacyEnv(new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer()))
      if (mountedRef.current && revision === importRevision.current) {
        setAccessKeyId(credentials.accessKeyId); setSecret(credentials.accessKeySecret)
        setMessage('已填入本次输入框；确认后点击“连接并开始使用”。')
      }
    } catch {
      if (mountedRef.current && revision === importRevision.current) setError(legacyEnvError)
    } finally { requestRef.current = false; if (mountedRef.current) setBusy(false) }
  }
  const savePhrase = async (event: FormEvent) => {
    event.preventDefault()
    const prepared = preparePhrase(editedPhrase)
    if (!prepared.value) { setError(prepared.error || '请检查短句内容。'); return }
    if (await perform('phrase_save', { phrase: prepared.value })) onClose()
  }
  const setHotkey = (action: HotkeyAction, enabled: boolean, value = '') => {
    const next = { ...values.hotkeys }
    if (enabled) next[action] = value
    else delete next[action]
    change('hotkeys', next)
  }
  const number = (key: 'font_size' | 'overlay_font_size' | 'overlay_width' | 'overlay_timeout' | 'overlay_alpha', min: number, max: number, step = 1) =>
    <input type="number" value={Number.isFinite(values[key]) ? values[key] : ''} min={min} max={max} step={step} onChange={event => change(key, event.target.valueAsNumber)}/>

  const settingsContent = () => {
    if (tab === 'appearance') return <>
      <div className="mg-section-heading"><h3>主窗口</h3><p>柔雾紫配色，按你的阅读习惯调整。</p></div>
      <div className="mg-field-grid"><Field label="内容字号" help="10–32；保存后应用到消息和输入文字。">{number('font_size', 10, 32)}</Field><Field label="界面布局"><select value={values.mode} onChange={event => change('mode', event.target.value as DesktopPreferences['mode'])}><option value="normal">标准布局</option><option value="compact">精简布局</option></select></Field></div>
      <Checkbox checked={values.topmost} onChange={checked => change('topmost', checked)}>主窗口保持置顶</Checkbox>
    </>
    if (tab === 'overlay') return <>
      <div className="mg-section-heading"><h3>游戏内译文</h3><p>字幕与快捷输入保持独立，调整时避免挡住聊天和技能。</p></div>
      <Checkbox checked={values.overlay_enabled} onChange={checked => change('overlay_enabled', checked)}>显示游戏内译文</Checkbox>
      <div className="mg-field-grid"><Field label="显示样式"><select value={values.overlay_style} onChange={event => change('overlay_style', event.target.value as DesktopPreferences['overlay_style'])}><option value="subtitle">透明字幕</option><option value="panel">半透明面板</option></select></Field><Field label="译文字号">{number('overlay_font_size', 10, 32)}</Field><Field label="浮窗宽度" help="280–900，随显示缩放适配。">{number('overlay_width', 280, 900)}</Field><Field label="停留时间（秒）">{number('overlay_timeout', 5, 120)}</Field><Field label="面板不透明度" help="0.35–1；用于半透明面板和位置调整。">{number('overlay_alpha', .35, 1, .05)}</Field></div>
      <Checkbox checked={values.overlay_original} onChange={checked => change('overlay_original', checked)}>同时显示原文</Checkbox>
      <div className="mg-subsection"><h4>浮窗位置</h4><p className="mg-help">以下操作会先保存当前设置。</p><div className="mg-actions"><button type="button" onClick={() => void overlayAction('edit')}>保存并调整位置</button><button type="button" onClick={() => void overlayAction('bottom')}>保存并放到底部</button><button type="button" onClick={() => void overlayAction('finish')}>完成调整</button></div></div>
    </>
    if (tab === 'hotkeys') return <>
      <div className="mg-section-heading"><h3>快捷键</h3><p>勾选后启用；与游戏或其他操作冲突时可重新设置。</p></div>
      {hotkeyFields.map(([action, label, fallback]) => <div className="mg-hotkey-row" key={action}><Checkbox checked={action in values.hotkeys} onChange={checked => setHotkey(action, checked, values.hotkeys[action] || fallback)}>{label}</Checkbox><input aria-label={`${label}快捷键`} disabled={!(action in values.hotkeys)} value={values.hotkeys[action] || ''} placeholder={fallback} onChange={event => setHotkey(action, true, event.target.value)}/></div>)}
      <Field label="动作连按开始 / 取消快捷键"><select value={values.repeat_hotkey} onChange={event => change('repeat_hotkey', event.target.value)}>{Array.from({ length: 12 }, (_, index) => `F${index + 1}`).map(key => <option key={key}>{key}</option>)}</select></Field>
    </>
    if (tab === 'connection') return <>
      <div className="mg-section-heading"><h3>翻译服务与游戏连接</h3><p>{snapshot.serviceState}</p></div>
      <div className="mg-summary"><KeyRound size={22}/><div><strong>{account.configured ? '已保存个人翻译账户' : '尚未配置个人翻译账户'}</strong><p>{account.maskedKey || '未配置时仍可接收游戏原文。'}</p></div><button type="button" onClick={() => navigate('account')}>{account.configured ? '管理账户' : '配置账户'}</button></div>
      <div className="mg-actions"><button type="button" onClick={() => void perform('cloud_quota')}>查询剩余额度</button></div>
      <div className="mg-subsection"><h4>聊天采集</h4><p>{snapshot.captureState}</p><div className="mg-actions"><button type="button" onClick={() => void perform('capture_reconnect')}>重新连接</button><button type="button" onClick={() => void perform('capture_stop')}>停止采集</button><button type="button" onClick={() => void perform('capture_start')}>恢复采集</button></div></div>
      <div className="mg-subsection"><h4>游戏进程</h4><p className="mg-help">检测到多个进程时，请选择要接收聊天的那一个。</p><Field label="已发现的进程"><select value={selectedPid} disabled={!processes.length} onChange={event => setSelectedPid(event.target.value)}>{!processes.length && <option value="">尚未发现游戏进程</option>}{processes.map(process => <option key={process.pid} value={String(process.pid)}>{process.path?.split(/[\\/]/).pop() || '风暴英雄'} · PID {process.pid}{process.build ? ` · ${process.build}` : ''}</option>)}</select></Field><div className="mg-actions"><button type="button" onClick={() => void perform('capture_processes')}>刷新进程列表</button><button type="button" disabled={!selectedPid || !processes.some(process => String(process.pid) === selectedPid)} onClick={() => void perform('capture_select', { pid: Number(selectedPid) })}>使用所选进程</button></div></div>
    </>
    return <>
      <div className="mg-section-heading"><h3>诊断日志</h3><p>遇到问题时开启、复现一次，再导出日志。</p></div>
      <Checkbox checked={diagnostics.enabled} disabled={busy || diagnostics.busy} onChange={enabled => void perform('diagnostics_set', { enabled })}>开启诊断日志（仅本次运行有效）</Checkbox>
      <div className="mg-privacy"><ShieldCheck size={22}/><p>开启后包含聊天原文、译文及复现数据；已配置密钥会脱敏。日志总计最多 100 MiB，滚动覆盖旧记录。分享前请检查私人聊天和昵称；下次启动默认关闭。</p></div>
      <p className="mg-help">{diagnostics.message || (diagnostics.enabled ? '正在记录；关闭后已有日志仍可导出。' : '当前不记录新事件，已有日志仍可导出。')}</p>
      <div className="mg-actions"><button type="button" disabled={busy || diagnostics.busy} onClick={() => void perform('diagnostics_export')}>{diagnostics.busy ? <LoaderCircle size={18} className="mg-spin"/> : <Download size={18}/>}导出诊断日志</button></div>
      {diagnostics.exportPath && <Field label="已导出的 ZIP 路径" help="点选路径后可复制；请自行检查并分享文件。"><input readOnly value={diagnostics.exportPath} onFocus={event => event.target.select()}/></Field>}
    </>
  }

  const capabilities = updateCapabilities(update)
  const title = activePanel === 'phrase' && !phrase ? '新增常用短句' : titles[activePanel]
  const modal = <div ref={backdropRef} className="management-backdrop" style={{ '--content-font-size': `${snapshot.management.preferences.font_size * 4 / 3}px` } as CSSProperties}>
    <section ref={dialogRef} className={`management-dialog mg-${activePanel}`} role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} tabIndex={-1} onKeyDown={trapKeyboard}>
      <header className="mg-header"><div><span className="mg-header-icon">{activePanel === 'account' ? <KeyRound size={23}/> : activePanel === 'updates' ? <RefreshCw size={23}/> : <Settings size={23}/>}</span><h2 id={`${id}-title`}>{title}</h2></div><button type="button" className="mg-close" aria-label="关闭窗口" disabled={closingBlocked} onClick={close}><X size={22}/></button></header>
      {error && <p className="mg-feedback mg-error" role="alert">{error}</p>}
      {message && <p className="mg-feedback mg-success" role="status"><Check size={18}/>{message}</p>}
      {activePanel === 'settings' && <form className="mg-form" onSubmit={event => { event.preventDefault(); void saveSettings() }}>
        <div className="mg-tabs" role="tablist" aria-label="设置分类">{tabs.map(([key, label], index) => <button data-initial-focus={key === tab || undefined} id={`${id}-tab-${key}`} role="tab" type="button" key={key} aria-selected={tab === key} aria-controls={`${id}-settings-page`} tabIndex={tab === key ? 0 : -1} disabled={busy} onClick={() => setTab(key)} onKeyDown={event => {
          const offset = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
          if (offset || event.key === 'Home' || event.key === 'End') { event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + offset + tabs.length) % tabs.length; setTab(tabs[next][0]); document.getElementById(`${id}-tab-${tabs[next][0]}`)?.focus() }
        }}>{label}</button>)}</div>
        <div className="mg-body" id={`${id}-settings-page`} role="tabpanel" aria-labelledby={`${id}-tab-${tab}`}><fieldset disabled={busy}>{settingsContent()}</fieldset></div>
        <footer className="mg-footer"><span className="mg-help">取消不会保存本页修改。</span><button type="button" disabled={busy} onClick={close}>取消</button><button className="mg-primary" type="submit" disabled={busy}>{busy && <LoaderCircle size={18} className="mg-spin"/>}保存设置</button></footer>
      </form>}
      {activePanel === 'updates' && <>
        <div className="mg-body"><div className="mg-section-heading"><h3>客户端更新</h3>{update.currentVersion && <p>当前版本 {update.currentVersion}</p>}{update.version && <p>发行版本 {update.version}</p>}<p className={update.state === 'error' ? 'mg-inline-error' : undefined} role={update.state === 'error' ? 'alert' : 'status'}>{update.detail || '点击检查按钮后才会连接发行服务。'}</p></div>
          {update.total > 0 && <div className="mg-progress"><progress aria-label="下载进度" value={Math.min(update.downloaded, update.total)} max={update.total}/><span>{bytes(update.downloaded)} / {bytes(update.total)}</span></div>}
          <article className="mg-release-notes" aria-label="发布说明"><ReleaseNotes blocks={update.noteBlocks} text={update.notes}/></article>
          {update.archivePath && <Field label="已校验的 ZIP 路径" help="可选中复制；从源码运行时请手动使用发行包。"><input readOnly value={update.archivePath} onFocus={event => event.target.select()}/></Field>}
          {!update.canInstall && update.state === 'downloaded' && <p className="mg-help">当前运行方式不支持自动替换，请使用上方 ZIP 路径手动升级。</p>}
        </div><footer className="mg-footer mg-footer-wrap"><button data-initial-focus type="button" disabled={busy || !capabilities.check} onClick={() => void perform('update_check')}><RefreshCw size={18}/>检查更新</button><button type="button" disabled={busy || !capabilities.download} onClick={() => void perform('update_download')}><Download size={18}/>下载更新</button>{capabilities.cancel && <button type="button" disabled={busy} onClick={() => void perform('update_cancel')}>取消任务</button>}<button type="button" className="mg-primary" disabled={busy || !capabilities.install} onClick={() => void perform('update_install')}>重启并更新</button><p className="mg-help">下载后由你决定安装时机；个人密钥和设置保留。</p></footer>
      </>}
      {activePanel === 'account' && <form className="mg-form" onSubmit={event => void submitAccount(event)}>
        <div className="mg-body"><div className="mg-section-heading"><h3>{account.configured ? '个人密钥已加密保存在本机' : '连接自己的阿里云翻译'}</h3><p>未配置也可接收原文；每位用户使用自己的账户。</p></div>
          {snapshot.isDemo && <p className="mg-privacy">离线演示不连接或配置真实密钥。</p>}
          {account.configured && <div className="mg-summary"><KeyRound size={22}/><div><strong>{account.maskedKey || '已保存的个人密钥'}</strong>{account.account && <p>账号：{account.account}</p>}</div></div>}
          <fieldset disabled={accountBusy || snapshot.isDemo}><Field label="AccessKey ID"><input data-initial-focus autoComplete="off" spellCheck={false} value={accessKeyId} onChange={event => setAccessKeyId(event.target.value)}/></Field><Field label="AccessKey Secret" help="只用于本次连接，提交后清空此输入框；不会写入页面快照。"><input type="password" autoComplete="new-password" spellCheck={false} value={secret} onChange={event => setSecret(event.target.value)}/></Field></fieldset>
          <Field label="导入本机旧 .env" help="主动选择自己的文件，最多 16 KiB；只在本次输入框填入，不会自动连接。"><input type="file" accept=".env" disabled={accountBusy || snapshot.isDemo} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; void importLegacy(file) }}/></Field>
          {account.message && <p className={account.state === 'error' ? 'mg-inline-error' : 'mg-help'} role={account.state === 'error' ? 'alert' : 'status'}>{account.message}</p>}
          {accountBusy && <p className="mg-help">关闭会取消尚未完成的操作；已经完成的保存或删除保持当前结果。</p>}
          {deleteAccount ? <div className="mg-confirm"><strong>删除本机已保存的密钥并关闭云翻译？</strong><p>原有聊天与短句仍保留。</p><div className="mg-actions"><button type="button" disabled={accountBusy} onClick={() => setDeleteAccount(false)}>保留密钥</button><button className="mg-danger" type="button" disabled={accountBusy} onClick={async () => { setSecret(''); if (await perform('account_delete')) { setAccessKeyId(''); setDeleteAccount(false) } }}>确认删除密钥</button></div></div> : account.configured && <button type="button" className="mg-text-danger" disabled={accountBusy || snapshot.isDemo} onClick={() => setDeleteAccount(true)}><Trash2 size={17}/>删除本机密钥</button>}
        </div><footer className="mg-footer"><button type="button" onClick={() => navigate('settings')} disabled={busy}>返回设置</button><button type="button" onClick={close}>关闭</button><button className="mg-primary" type="submit" disabled={accountBusy || snapshot.isDemo || deleteAccount || !accessKeyId.trim() || !secret.trim()}>{accountBusy ? <LoaderCircle size={18} className="mg-spin"/> : <KeyRound size={18}/>}连接并开始使用</button></footer>
      </form>}
      {activePanel === 'phrase' && <form className="mg-form" onSubmit={event => void savePhrase(event)}>
        <div className="mg-body"><fieldset disabled={busy}><div className="mg-section-heading"><h3>整理一句常用表达</h3><p>这里只管理短句；保存不会自动发送游戏消息。</p></div><div className="mg-field-grid"><Field label="分类"><select value={editedPhrase.category} onChange={event => setEditedPhrase(current => ({ ...current, category: event.target.value }))}>{categories.map(category => <option key={category}>{category}</option>)}</select></Field><Checkbox checked={editedPhrase.favorite} onChange={favorite => setEditedPhrase(current => ({ ...current, favorite }))}>加入收藏，方便快速找到</Checkbox></div>
          <Field label="中文短句"><textarea data-initial-focus rows={3} value={editedPhrase.source_text} onChange={event => setEditedPhrase(current => editPhraseText(current, 'source_text', event.target.value))}/></Field><Field label="英语译文"><textarea rows={3} lang="en" value={editedPhrase.translations.en || ''} onChange={event => setEditedPhrase(current => editPhraseText(current, 'en', event.target.value))}/></Field><Field label="韩语译文"><textarea rows={3} lang="ko" value={editedPhrase.translations.ko || ''} onChange={event => setEditedPhrase(current => editPhraseText(current, 'ko', event.target.value))}/></Field>
          <Checkbox checked={editedPhrase.reviewed} onChange={reviewed => setEditedPhrase(current => ({ ...current, reviewed }))}>我已核对以上译文，允许精确匹配时直接使用本机译文</Checkbox><p className="mg-help">修改中文或任一译文后会取消“已校对”，请核对后再次勾选。缺少当前语言译文或存在冲突时仍需翻译。</p>
        </fieldset></div><footer className="mg-footer"><button type="button" disabled={busy} onClick={close}>取消</button><button type="submit" className="mg-primary" disabled={busy}>{busy && <LoaderCircle size={18} className="mg-spin"/>}保存短句</button></footer>
      </form>}
    </section>
  </div>
  return typeof document === 'undefined' ? modal : createPortal(modal, document.body)
}
