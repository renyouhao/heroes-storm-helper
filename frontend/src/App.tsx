import { useEffect, useId, useRef, useState } from 'react'
import {
  ArrowDownToLine, ArrowUpFromLine, Check, CheckCheck, ChevronDown, ChevronRight,
  CircleHelp, Clipboard, Gamepad2, Heart, Info, Keyboard, Languages, LoaderCircle,
  MessageCircle, MessageSquare, Minus, MousePointer2, Package, Pencil, Plus,
  RefreshCw, Search, Settings, ShieldCheck, Sparkles, Square, Star, Trash2,
  Volume2, Workflow, X, Zap,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { canCopyPreview, selectDraft, visibleMessages } from './model'
import { useAssistant } from './useAssistant'
import type { Command, HelperState, Message, Phrase, Snapshot } from './types'

type Page = 'chat' | 'phrases' | 'loot' | 'repeat' | 'talent' | 'board'
const navigation: { id: Page; label: string; icon: LucideIcon }[] = [
  { id: 'chat', label: '实时消息', icon: MessageSquare },
  { id: 'phrases', label: '常用短句', icon: MessageCircle },
  { id: 'loot', label: '开箱助手', icon: Package },
  { id: 'repeat', label: '动作连按', icon: Keyboard },
  { id: 'talent', label: '天赋辅助', icon: Workflow },
  { id: 'board', label: '双击播报', icon: Volume2 },
]

function BrandMark() {
  return <svg className="brand-mark" viewBox="0 0 48 52" fill="none" aria-hidden="true">
    <defs><linearGradient id="brand-violet" x1="8" y1="0" x2="40" y2="52" gradientUnits="userSpaceOnUse"><stop stopColor="#9270ff"/><stop offset=".55" stopColor="#6235e8"/><stop offset="1" stopColor="#4625b4"/></linearGradient></defs>
    <path d="M21.4 2.1a5.3 5.3 0 0 1 5.2 0L44 12.2a5.3 5.3 0 0 1 2.6 4.5v20.2a5.3 5.3 0 0 1-2.6 4.5L26.6 51.5a5.3 5.3 0 0 1-5.2 0L4 41.4a5.3 5.3 0 0 1-2.6-4.5V16.7A5.3 5.3 0 0 1 4 12.2Z" fill="url(#brand-violet)"/>
    <path d="M25.4 12.4c-7.5-.5-13.8 4.9-13.8 12.3 0 4.1 2 8.2 5.6 10.4-2.1-5.1-.9-10.5 4.6-12.8-4 .1-7.1 2-8.3 4.1-.7-6.3 4.5-12.3 11.9-14Z" fill="white"/>
    <path d="M35.5 20.3c-3.2-6.6-11-9.4-17.5-5.9-3.6 1.9-6.3 5.6-6.6 9.8 3.5-4.3 8.9-5.7 13.6-2-2-3.5-5.2-5.3-7.7-5.4 5.2-3.7 12.9-1.9 18.2 3.5Z" fill="white" opacity=".9"/>
    <path d="M33.7 35.6c4.3-6.1 3-14.2-3.2-18.3-3.4-2.3-7.9-2.8-11.7-1.1 5.4 1 9.4 4.8 8.4 10.7 2.1-3.5 2-7.2.9-9.3 5.7 2.8 7.9 10.4 5.6 18Z" fill="white"/>
    <path d="M19.7 40.5c7.4.7 13.9-4.6 14.1-12 .1-4.1-1.9-8.2-5.4-10.5 2 5.2.6 10.5-4.9 12.7 4 0 7.2-1.9 8.4-3.9.5 6.3-4.9 12.2-12.2 13.7Z" fill="white" opacity=".85"/>
  </svg>
}

function Avatar({ name }: { name: string }) {
  const id = useId().replace(/:/g, '')
  let kind = name.toLowerCase().includes('mochi') ? 2 : name.toLowerCase().includes('jin') ? 1 : 0
  if (!['raven', 'jin', 'mochi'].includes(name.toLowerCase())) kind = Array.from(name).reduce((sum, c) => sum + c.charCodeAt(0), 0) % 3
  return <svg className={`avatar avatar-${kind}`} role="img" aria-label={`${name}的头像`} viewBox="0 0 80 80">
    <defs>
      <linearGradient id={`${id}-bg`} x2="1" y2="1"><stop stopColor={['#152044', '#75426a', '#c6c8e7'][kind]}/><stop offset="1" stopColor={['#6975ba', '#67b6d4', '#f3e4f0'][kind]}/></linearGradient>
      <linearGradient id={`${id}-face`} x2=".9" y2="1"><stop stopColor={kind === 0 ? '#d6b999' : '#ffdfcf'}/><stop offset="1" stopColor={kind === 0 ? '#8d807e' : '#dfa6a2'}/></linearGradient>
      <clipPath id={`${id}-clip`}><circle cx="40" cy="40" r="40"/></clipPath>
    </defs>
    <g clipPath={`url(#${id}-clip)`}>
      <path fill={`url(#${id}-bg)`} d="M0 0h80v80H0z"/>
      {kind === 0 ? <>
        <path d="m12 80 5-24L7 12l16 10L40 6l17 16 16-10-9 44 6 24" fill="#171e38"/>
        <path d="m22 32 7-12 11 8 11-8 8 12-6 26-13 13-14-13Z" fill={`url(#${id}-face)`}/>
        <path d="m13 11 10 9 9-10 8 19-14 8-7 21-5-20M67 11l-10 9-9-10-8 19 14 8 7 21 5-20" fill="#30364c" stroke="#a99469" strokeWidth="1.1"/>
        <path d="m26 40 9 2m10 0 9-2" stroke="#75ceff" strokeWidth="2.6" strokeLinecap="round"/>
        <path d="m38 43-3 11 8 1m-14 6 6-5 5 5 6-5 6 5-2 13-10 6-10-6" fill="#29293e"/>
        <path d="m16 69 13-8 11 19 11-19 14 8 7 11H8" fill="#3b4b7a"/>
        <path d="m5 80 12-13 7 13m51 0L63 67l-7 13" fill="#6b81c8"/>
      </> : kind === 1 ? <>
        <path d="M10 56C2 24 14 7 38 6c32-2 41 22 31 48l-6 24H16" fill="#85285f"/>
        <path d="m21 38 5-20 28 2 6 23-9 20-12 7-13-12Z" fill={`url(#${id}-face)`}/>
        <path d="M13 43c0-25 13-33 28-33 20 0 30 17 25 34L53 25c-9 13-16 11-29 11l-5 18Z" fill="#c64289"/>
        <path d="M17 33c10-19 23-15 35-16-15-6-26-5-35 16" fill="#e78cb6"/>
        <path d="m25 43 8 1m12 0 8-2" stroke="#38456b" strokeWidth="2.3" strokeLinecap="round"/>
        <path d="M35 56q5 4 10-1" stroke="#aa5c77" strokeWidth="1.8" fill="none" strokeLinecap="round"/>
        <path d="m23 62 17 9 15-9 13 8 8 10H8l5-10" fill="#307a9b"/><path d="m35 68 5 7 8-9-3 14H33" fill="#94def0"/>
        <path d="M59 32h8v22h-8z" fill="#5cacc5"/><path d="M62 39h7v6h-7z" fill="#b0e8f8"/>
      </> : <>
        <path d="m13 44 1-33 21 18M47 28l21-17-1 34" fill="#e9ecf7" stroke="#c5c9df" strokeWidth="2"/>
        <path d="m18 17 3 20 10-9m22 0 10-11-1 20" fill="#efbdd6"/>
        <path d="M14 43c0-20 52-22 53 0 7 25-8 35-27 35S7 67 14 43" fill="#f0f2fb"/>
        <path d="M27 26q10 9 0 20m25-20q-10 9 0 20" fill="#c7cada"/>
        <ellipse cx="26" cy="48" rx="6" ry="8" fill="#7393c8"/><ellipse cx="53" cy="48" rx="6" ry="8" fill="#7393c8"/>
        <ellipse cx="27" cy="48" rx="2.4" ry="5" fill="#2a3154"/><ellipse cx="52" cy="48" rx="2.4" ry="5" fill="#2a3154"/>
        <circle cx="25" cy="45" r="1.8" fill="white"/><circle cx="51" cy="45" r="1.8" fill="white"/>
        <path d="m36 57 4 4 4-4" fill="#da97ba"/><path d="M40 61v3m-7 0q4 4 7 0 3 4 7 0" fill="none" stroke="#b3a2b6" strokeWidth="1.5"/>
        <path d="m13 58 13 2m-14 3 14 1m28-4 13-2m-13 6 14-1" stroke="#a3a7c0"/><path d="M20 75q20-11 40 0v5H20Z" fill="#9282c5"/>
      </>}
    </g>
  </svg>
}

function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (value: boolean) => void; label: string; disabled?: boolean }) {
  return <button className={`toggle ${checked ? 'is-on' : ''}`} role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)}><span /></button>
}

function App() {
  const { snapshot, connected, bridgeError, command, notice, setNotice } = useAssistant()
  const [page, setPage] = useState<Page>('chat')
  const [draft, setDraft] = useState(snapshot.draft)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [channel, setChannel] = useState('全部聊天')
  const pendingDraft = useRef<string | null>(null)
  const draftTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const draftVersion = useRef(0)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const manualBusyRef = useRef(false)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    setDraft(current => selectDraft(current, snapshot.draft, pendingDraft.current))
    if (pendingDraft.current === snapshot.draft) pendingDraft.current = null
  }, [snapshot.draft])

  useEffect(() => { setChannel(snapshot.channelFilter) }, [snapshot.channelFilter])
  useEffect(() => () => { if (draftTimer.current) clearTimeout(draftTimer.current) }, [])

  const updateDraft = (text: string) => {
    setDraft(text)
    pendingDraft.current = text
    const version = ++draftVersion.current
    if (draftTimer.current) clearTimeout(draftTimer.current)
    draftTimer.current = setTimeout(() => {
      if (version === draftVersion.current && window.pywebview?.api) void command('draft', { text })
    }, 240)
  }
  const translate = async () => {
    if (!draft.trim() || manualBusyRef.current || snapshot.manualBusy) return
    manualBusyRef.current = true
    setSubmitting(true)
    if (draftTimer.current) clearTimeout(draftTimer.current)
    draftVersion.current++
    try { await command('translate', { text: draft, target: snapshot.targetLanguage }) }
    finally { manualBusyRef.current = false; setSubmitting(false) }
  }
  const quickInput = () => { void command('quick_input'); inputRef.current?.focus() }
  const usePhrase = async (phrase: Phrase) => {
    const result = await command('phrase_use', { id: phrase.id })
    if (result.ok) {
      pendingDraft.current = null
      setDraft(phrase.source_text)
      setPage('chat')
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }
  const isPreview = !connected || snapshot.isDemo
  const copyReady = canCopyPreview(snapshot, draft) && !submitting
  const title = navigation.find(item => item.id === page)!.label

  return <div className="desktop" data-theme="lavender">
    <div className="app-shell">
      <aside className="sidebar" aria-label="主导航">
        <div className="brand pywebview-drag-region"><BrandMark /><span>风暴英雄助手</span></div>
        <nav>{navigation.map(item => <button key={item.id} className={`nav-item ${page === item.id ? 'active' : ''}`} onClick={() => setPage(item.id)} aria-current={page === item.id ? 'page' : undefined}>
          <span className="nav-icon"><item.icon size={29} strokeWidth={1.8}/></span><span>{item.label}</span>{page === item.id && <ChevronRight className="nav-arrow" size={20}/>}
        </button>)}</nav>
        <div className="sidebar-bottom">
          <button className="nav-item" onClick={() => void command('open_settings')}><span className="nav-icon"><Settings size={29} strokeWidth={1.8}/></span><span>偏好设置</span></button>
          <button className="nav-item update-link" onClick={() => void command('check_updates')}><span className="nav-icon"><RefreshCw size={23} strokeWidth={1.7}/></span><span>检查更新</span><i className="update-dot"/></button>
        </div>
      </aside>
      <main className={`main-workspace page-${page}`}>
        <div className="titlebar pywebview-drag-region">
          <div className="environment-label">{isPreview && <span><span className="preview-dot"/>{connected ? '离线演示' : '静态预览'}</span>}</div>
          <div className="window-controls">
            <button aria-label="最小化" onClick={() => void command('minimize')}><Minus size={16}/></button>
            <button aria-label="最大化或还原" onClick={() => void command('maximize')}><Square size={14}/></button>
            <button className="close-window" aria-label="关闭助手" onClick={() => void command('close')}><X size={18}/></button>
          </div>
        </div>
        <header className="workspace-header">
          <h1>{title}</h1>
          <div className="header-tools">
            <div className="connection-group">
              <span className="connection" title={snapshot.captureState}><Gamepad2 size={24}/><i className={connectionTone(snapshot.captureState, isPreview)}/><span>{isPreview ? (connected ? '游戏离线演示' : '游戏静态预览') : compactState(snapshot.captureState, '游戏')}</span></span>
              <span className="connection service-connection" title={snapshot.serviceState}><Languages size={24}/><i className={connectionTone(snapshot.serviceState, isPreview)}/><span>{isPreview ? (connected ? '演示翻译' : '示例翻译') : compactState(snapshot.serviceState, '翻译服务')}</span></span>
            </div>
            {page === 'chat' && <div className="chat-tools">
              <label className="select-wrap"><span className="sr-only">聊天频道</span><select value={channel} onChange={event => { setChannel(event.target.value); void command('set_channel', { value: event.target.value }) }}>{(snapshot.channelOptions || ['全部聊天', '仅团队', '仅队伍']).map(option => <option key={option} value={option}>{option}</option>)}</select><ChevronDown size={18}/></label>
              <label className="auto-label"><Toggle checked={snapshot.autoTranslate} onChange={enabled => void command('set_auto', { enabled })} label="自动翻译"/><span>自动翻译</span></label>
            </div>}
          </div>
        </header>
        {bridgeError && <div className="connection-error" role="alert"><Info size={16}/>{bridgeError}</div>}
        {page === 'chat' ? <>
          <ChatFeed snapshot={snapshot} selectedId={selectedId} setSelectedId={setSelectedId} channel={channel} quickInput={quickInput} command={command}/>
          <section className="composer panel" aria-label="回复与翻译">
            <div className="compose-source">
              <label htmlFor="reply-input" className="field-title">用中文回复</label>
              <textarea ref={inputRef} id="reply-input" value={draft} placeholder="输入想对队友说的话…" onChange={event => updateDraft(event.target.value)} onKeyDown={event => {
                if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) { event.preventDefault(); void translate() }
              }}/>
              <div className="compose-hint"><span>{quickInputHotkey(snapshot)} 快捷输入</span><span className="submit-shortcut">Ctrl+Enter 翻译</span></div>
            </div>
            <div className="compose-result">
              <div className="result-topline"><span className="field-title muted">译文预览（{snapshot.targetLanguage === 'ko' ? '韩语' : '英语'}）</span><div className="language-switch"><span>目标语言：</span><div role="group" aria-label="目标语言">{[['en', '英语'], ['ko', '韩语']].map(([value, label]) => <button key={value} aria-pressed={snapshot.targetLanguage === value} className={snapshot.targetLanguage === value ? 'selected' : ''} onClick={() => void command('set_language', { target: value })}>{label}</button>)}</div></div></div>
              <div className={`translation-preview ${snapshot.manualBusy || submitting ? 'is-loading' : ''}`} aria-live="polite">
                {snapshot.manualBusy || submitting ? <span className="preview-placeholder"><LoaderCircle className="spin" size={19}/>正在翻译…</span> : snapshot.preview && snapshot.previewSource === draft ? snapshot.preview : <span className="preview-placeholder">{draft.trim() ? '点击“翻译中文”，在这里预览译文。' : '译文将显示在这里。'}</span>}
              </div>
              <div className="compose-actions">
                <button className="button secondary copy-button" disabled={!copyReady} onClick={async () => { const result = await command('copy', { text: snapshot.preview }); if (result.ok) setNotice('译文已复制，回到游戏粘贴即可。') }}><Clipboard size={22}/><span>复制译文</span></button>
                <button className="button primary translate-button" disabled={!draft.trim() || submitting || snapshot.manualBusy} onClick={() => void translate()}>{snapshot.manualBusy || submitting ? <LoaderCircle className="spin" size={22}/> : <Languages size={22}/>}<span>{snapshot.manualBusy || submitting ? '翻译中' : '翻译中文'}</span></button>
                <span className="paste-hint">复制后回游戏粘贴发送</span>
              </div>
            </div>
          </section>
        </> : page === 'phrases' ? <PhrasePage snapshot={snapshot} command={command} usePhrase={usePhrase}/> : <HelperPage page={page} snapshot={snapshot} command={command}/>}
        <div className="workspace-status" aria-live="polite">{snapshot.status && !['就绪', 'Ready'].includes(snapshot.status) && <><span className="status-small-dot"/>{snapshot.status}</>}</div>
      </main>
      {notice && <div className="toast" role="status"><Info size={19}/><span>{notice}</span><button aria-label="关闭提示" onClick={() => setNotice('')}><X size={17}/></button></div>}
    </div>
  </div>
}

function quickInputHotkey(snapshot: Snapshot) {
  const hotkeys = snapshot.settings.hotkeys as { focus?: string } | undefined
  return hotkeys?.focus || snapshot.settings.quickInputHotkey || 'Ctrl+T'
}

function compactState(state: string, name: string) {
  if (/已连接|正常|就绪|ready|connected/i.test(state) && !/未|断开|not|disconnected/i.test(state)) return `${name}已连接`
  if (/未配置/.test(state)) return '翻译待配置'
  if (/离线|未连接|等待|未发现/.test(state)) return `${name}未连接`
  return state || `${name}连接中`
}

function connectionTone(state: string, preview: boolean) {
  return !preview && /已连接|正常|就绪|ready|connected/i.test(state) && !/未|断开|not|disconnected/i.test(state) ? 'connection-dot connected' : 'connection-dot pending'
}

function ChatFeed({ snapshot, selectedId, setSelectedId, channel, quickInput, command }: {
  snapshot: Snapshot; selectedId: string | null; setSelectedId: (id: string) => void; channel: string; quickInput: () => void; command: Command
}) {
  const messages = visibleMessages(snapshot.messages, channel)
  const scrollRef = useRef<HTMLDivElement>(null)
  const atBottomRef = useRef(true)
  const [unread, setUnread] = useState(false)
  const lastId = messages.at(-1)?.id
  useEffect(() => {
    if (atBottomRef.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    else if (lastId) setUnread(true)
  }, [lastId])
  const selected = selectedId ?? messages[1]?.id
  return <section className="chat-feed panel" aria-label="实时聊天消息">
    <div className="feed-toolbar">
      <span className="feed-context">{!snapshot.cloudConfigured && !snapshot.isDemo && <button className="inline-link" onClick={() => void command('open_cloud')}><Languages size={15}/>配置翻译服务</button>}</span>
      <button className="button soft quick-input" onClick={quickInput}><Zap size={21} fill="currentColor"/><span>快捷输入</span></button>
    </div>
    <div className="message-list" ref={scrollRef} onScroll={() => {
      const node = scrollRef.current
      if (node) { atBottomRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 55; if (atBottomRef.current) setUnread(false) }
    }}>
      {messages.length ? messages.map(message => <MessageRow key={message.id} message={message} selected={selected === message.id} onSelect={() => setSelectedId(message.id)} command={command}/>) : <div className="empty-state"><span className="empty-icon"><MessageSquare size={37} strokeWidth={1.4}/></span><h2>{!['all', '全部聊天'].includes(channel) ? '这个频道还没有消息' : '等待游戏里的第一条消息'}</h2><p>{!['all', '全部聊天'].includes(channel) ? '切换“全部聊天”查看其他频道。' : '打开风暴英雄，聊天内容会自动出现在这里。'}</p>{!snapshot.cloudConfigured && <button className="button secondary" onClick={() => void command('open_cloud')}><Languages size={18}/>配置翻译服务</button>}</div>}
    </div>
    {unread && <button className="new-message" onClick={() => { if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; setUnread(false); atBottomRef.current = true }}><ArrowDownToLine size={16}/>有新消息</button>}
  </section>
}

function MessageRow({ message, selected, onSelect, command }: { message: Message; selected: boolean; onSelect: () => void; command: Command }) {
  const busy = /pending|translating|queued|翻译中|等待中/i.test(message.status)
  const failed = /error|failed|失败/i.test(message.status)
  const channel = /team/i.test(message.channel) ? '团队' : /party/i.test(message.channel) ? '小队' : message.channel
  return <article className={`message-row ${selected ? 'is-selected' : ''} ${failed ? 'has-error' : ''}`} onClick={onSelect} tabIndex={0} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect() } }} aria-label={`${message.sender}，${channel}，${message.translation || message.original}`}>
    <Avatar name={message.sender}/>
    <div className="message-content">
      <div className="message-meta"><strong>{message.sender}</strong><span className="channel-badge">{channel || '聊天'}</span>{message.historical && <span className="history-badge">历史</span>}</div>
      <div className="message-translated">{message.translation || message.original}</div>
      {message.translation && <div className="message-original">{message.original}</div>}
      {busy && <span className="message-state"><LoaderCircle size={14} className="spin"/>正在翻译…</span>}
      {failed && <span className="message-state error-text">翻译没有完成<button className="inline-link" onClick={event => { event.stopPropagation(); void command('retry_message', { id: message.id }) }}>重试</button></span>}
    </div>
    <time className="message-time">{message.timestamp}</time>
    <div className="message-actions"><button aria-label="消息详情" title="消息详情" onClick={event => { event.stopPropagation(); void command('message_details', { id: message.id }) }}><Info size={17}/></button>{failed && <button aria-label="重试翻译" title="重试翻译" onClick={event => { event.stopPropagation(); void command('retry_message', { id: message.id }) }}><RefreshCw size={17}/></button>}</div>
  </article>
}

function PhrasePage({ snapshot, command, usePhrase }: { snapshot: Snapshot; command: Command; usePhrase: (phrase: Phrase) => Promise<void> }) {
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('全部')
  const categories = ['全部', '收藏', ...new Set(snapshot.phrases.map(phrase => phrase.category))]
  const phrases = snapshot.phrases.filter(phrase => (category === '全部' || (category === '收藏' ? phrase.favorite : phrase.category === category)) && `${phrase.source_text} ${Object.values(phrase.translations).join(' ')}`.toLowerCase().includes(query.toLowerCase()))
  return <section className="subpage phrase-page panel">
    <div className="subpage-intro"><div><h2>把默契，留给下一场团战</h2><p>收藏常用表达，一键填入回复区。已校对的译文可直接使用。</p></div><button className="button primary" onClick={() => void command('phrase_edit')}><Plus size={19}/>新增短句</button></div>
    <div className="phrase-tools"><label className="search-input"><Search size={20}/><input aria-label="搜索短句" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索中文或译文"/>{query && <button aria-label="清空搜索" onClick={() => setQuery('')}><X size={16}/></button>}</label><button className="button secondary" onClick={() => void command('phrase_import')}><ArrowDownToLine size={18}/>导入</button></div>
    <div className="category-tabs" role="group" aria-label="短句分类">{categories.map(item => <button key={item} className={category === item ? 'selected' : ''} aria-pressed={category === item} onClick={() => setCategory(item)}>{item === '收藏' && <Star size={15}/>} {item}</button>)}</div>
    <div className="phrase-list">{phrases.length ? phrases.map(phrase => <article className="phrase-item" key={phrase.id}>
      <button className={`favorite-button ${phrase.favorite ? 'is-favorite' : ''}`} aria-label={phrase.favorite ? '取消收藏' : '收藏短句'} onClick={() => void command('phrase_favorite', { id: phrase.id })}><Star size={22} fill={phrase.favorite ? 'currentColor' : 'none'}/></button>
      <div className="phrase-copy"><div><span className="channel-badge">{phrase.category}</span>{phrase.reviewed && <span className="reviewed-label"><CheckCheck size={15}/>已校对</span>}</div><h3>{phrase.source_text}</h3><p>{phrase.translations[snapshot.targetLanguage as 'en' | 'ko'] || '此语言暂无译文，使用后可继续翻译。'}</p></div>
      <div className="phrase-actions"><button className="button soft" onClick={() => void usePhrase(phrase)}>使用短句<ChevronRight size={17}/></button><div className="phrase-management"><button aria-label="编辑短句" title="编辑" onClick={() => void command('phrase_edit', { id: phrase.id })}><Pencil size={16}/></button><button aria-label="导出短句" title="导出" onClick={() => void command('phrase_export', { id: phrase.id })}><ArrowUpFromLine size={16}/></button><button aria-label="删除短句" title="删除" onClick={() => void command('phrase_delete', { id: phrase.id })}><Trash2 size={16}/></button></div></div>
    </article>) : <div className="empty-state"><span className="empty-icon"><MessageCircle size={36}/></span><h2>{query ? '没有找到匹配的短句' : '还没有常用短句'}</h2><p>{query ? '试试更短的关键词，或换一个分类。' : '新增一句常用表达，下次沟通更轻松。'}</p><button className="button secondary" onClick={() => void command('phrase_edit')}><Plus size={18}/>新增短句</button></div>}</div>
    <div className="subpage-footer"><ShieldCheck size={16}/><span>短句保存在本机。只有你已核对的译文，才会直接用于快速回复。</span></div>
  </section>
}

const helperTitles: Record<string, string> = {
  idle: '准备好再开始', preparing: '正在准备', ready: '已准备就绪', running: '正在运行', paused: '已暂停',
  error: '暂时无法继续', disabled: '功能已关闭', waiting: '等待游戏状态', requested: '正在启动',
  deferred: '等待合适的操作时机', sent: '操作已发送', confirming: '正在确认', confirmed: '已确认选择',
  unconfirmed: '请手动检查本次选择', blocked: '请手动检查', unknown: '等待可信的游戏状态',
  armed: '等待第二次点击', pending: '等待发送', cancelled: '本次播报已取消', limited: '短时间内不重复播报',
}
function HelperStatus({ helper, idleTitle }: { helper: HelperState; idleTitle?: string }) {
  const active = ['running', 'ready', 'confirmed', 'sent', 'idle'].includes(helper.state) && helper.enabled !== false
  return <div className={`helper-status-card ${helper.state === 'error' ? 'has-error' : ''}`}><span className="helper-status-symbol">{helper.state === 'error' ? <Info size={26}/> : active ? <Check size={28}/> : <LoaderCircle size={27} className={helper.state === 'preparing' ? 'spin' : ''}/>}</span><div><h3>{helper.state === 'idle' && idleTitle ? idleTitle : helperTitles[helper.state] || helper.state}</h3><p>{helper.detail || '状态更新后会显示在这里。'}</p>{helper.lastResult && <small>{helper.lastResult}</small>}</div><span className={`state-pill ${active ? 'ready' : ''}`}>{active ? '就绪' : helper.state === 'error' ? '需处理' : '待命'}</span></div>
}

function HelperPage({ page, snapshot, command }: { page: Exclude<Page, 'chat' | 'phrases'>; snapshot: Snapshot; command: Command }) {
  const helper = snapshot.helpers[page]
  const [action, setAction] = useState(helper.action || 'S')
  const [interval, setIntervalValue] = useState(String(helper.interval || 20))
  const [saving, setSaving] = useState(false)
  const [configChanged, setConfigChanged] = useState(false)
  const [configError, setConfigError] = useState('')
  useEffect(() => { setAction(helper.action || 'S'); setIntervalValue(String(helper.interval || 20)); setConfigChanged(false) }, [page, helper.action, helper.interval])
  const saveRepeat = async () => {
    const value = Number(interval)
    if (!Number.isInteger(value) || value < 10 || value > 5000) { setConfigError('请输入 10–5000 之间的整数毫秒。'); return }
    setConfigError(''); setSaving(true)
    try { const result = await command('repeat_config', { action, interval: value }); if (result.ok) setConfigChanged(false) }
    finally { setSaving(false) }
  }
  const titles = { loot: '开箱的重复操作，交给助手', repeat: '动作连按，按键掌控', talent: '心仪天赋，及时选中', board: '双击一格，让队友看见' }
  const descriptions = { loot: '在游戏中打开补给箱页面，准备后用空格键开始或暂停。', repeat: '设置连按动作和间隔，在游戏中按快捷键开始，再按一次结束。', talent: '先在游戏中为天赋标记爱心，助手会在待选时触发首选快捷键。', board: '在游戏 Tab 面板双击天赋或统计项目，自动播报给己方团队。' }
  const Icon = navigation.find(item => item.id === page)!.icon
  return <section className="subpage helper-page panel">
    <div className="helper-intro"><div className="helper-heading-icon"><Icon size={35} strokeWidth={1.5}/></div><h2>{titles[page]}</h2><p>{descriptions[page]}</p></div>
    <HelperStatus helper={helper} idleTitle={page === 'repeat' ? '按键即开始' : page === 'board' ? '已连接，等待 Tab 内双击' : page === 'talent' ? '已连接，当前没有待选天赋' : undefined}/>
    {page === 'loot' && <>
      <div className="step-grid"><Step icon={Gamepad2} title="打开补给箱" text="回到游戏，进入补给箱页面。"/><Step icon={Package} title="准备开箱" text="点击下方按钮，再回到游戏。"/><Step icon={Keyboard} title="空格键开始" text="Space 暂停或继续，Esc 结束。"/></div>
      <div className="helper-action-row"><button className="button primary" disabled={helper.state === 'preparing'} onClick={() => void command('loot_prepare')}>{helper.state === 'preparing' ? <LoaderCircle size={20} className="spin"/> : <Package size={20}/>}准备开箱</button><button className="button secondary" disabled={['idle', 'error'].includes(helper.state)} onClick={() => void command('loot_cancel')}>结束本次模式</button></div>
      <div className="helper-note"><Info size={19}/><p>运行中，其他按键、鼠标点击、滚轮或切出游戏会结束本次模式。助手不会替你重掷补给箱。</p></div>
    </>}
    {page === 'repeat' && <>
      <div className="repeat-config"><div><label className="field-title">连按动作</label><div className="action-options">{[['S', 'S', '停止移动'], ['YS', 'Y + S', '交替连按']].map(([value, label, detail]) => <button className={action === value ? 'selected' : ''} key={value} onClick={() => { setAction(value); setConfigChanged(true) }} aria-pressed={action === value}><kbd>{label}</kbd><span>{detail}</span>{action === value && <Check size={18}/>}</button>)}</div></div><div><label htmlFor="repeat-interval" className="field-title">按键间隔</label><div className="number-field"><input id="repeat-interval" type="number" min="10" max="5000" step="1" value={interval} onChange={event => { setIntervalValue(event.target.value); setConfigChanged(true) }}/><span>毫秒</span></div><small>间隔越小，连按频率越高。</small></div></div>
      {configError && <p className="error-text" role="alert">{configError}</p>}
      <div className="hotkey-banner"><Keyboard size={24}/><span>在游戏中按 <kbd>{String(helper.hotkey || snapshot.settings.repeat_hotkey || 'F8')}</kbd> 开始 / 取消，<kbd>Esc</kbd> 也可结束。</span></div>
      <div className="helper-action-row"><button className="button primary" disabled={!configChanged || saving} onClick={() => void saveRepeat()}>{saving ? <LoaderCircle size={19} className="spin"/> : <Check size={19}/>}保存配置</button><button className="button secondary" disabled={!['running', 'ready', 'paused', 'requested'].includes(helper.state)} onClick={() => void command('repeat_cancel')}>取消连按</button></div>
      <div className="helper-note"><Info size={19}/><p>按住其他操作键或切出游戏时停止连按，右键仍可正常操作。修改配置后需保存，下一次启动时生效。</p></div>
    </>}
    {page === 'talent' && <>
      <div className="feature-toggle-row"><span className="feature-toggle-icon"><Heart size={26}/></span><div><h3>自动选择游戏内的爱心天赋</h3><p>开启后持续检测，无需每把重新准备。</p></div><Toggle checked={Boolean(helper.enabled)} label="自动选择爱心天赋" onChange={enabled => void command('talent_toggle', { enabled })}/></div>
      <div className="step-grid"><Step icon={Heart} title="标记爱心" text="在游戏中为各层天赋设置首选。"/><Step icon={Sparkles} title="等待待选" text="助手检测到可信的天赋待选状态。"/><Step icon={ShieldCheck} title="选择并确认" text="触发首选快捷键，回读确认结果。"/></div>
      <div className="hotkey-banner"><Keyboard size={24}/><span>保留游戏“快速选择首选天赋”默认键：<kbd>Ctrl + `</kbd></span></div>
      <div className="helper-action-row"><button className="button secondary" onClick={() => void command('helper_retry', { source: 'talent' })}><RefreshCw size={19}/>重新连接</button></div>
      <div className="helper-note"><Info size={19}/><p>本层没有标记爱心或选择未确认时，请手动处理该层；下一层会继续检测。反引号位于 Esc 下方、数字 1 左边。</p></div>
    </>}
    {page === 'board' && <>
      <div className="feature-toggle-row"><span className="feature-toggle-icon"><Volume2 size={26}/></span><div><h3>开启 Tab 双击播报</h3><p>点哪项，就说哪项；自动发送到己方团队。</p></div><Toggle checked={Boolean(helper.enabled)} label="Tab双击播报" onChange={enabled => void command('board_toggle', { enabled })}/></div>
      <div className="broadcast-examples"><div><span><Workflow size={21}/>天赋</span><p>播报玩家、英雄、天赋等级、名称与关键效果。</p></div><div><span><MousePointer2 size={21}/>单项统计</span><p>双击击杀、助攻、死亡、伤害或治疗中的具体一格。</p></div></div>
      <div className="broadcast-preview"><span className="muted">播报示例</span><p>【我方】玩家（安度因）｜英雄伤害：12,345</p></div>
      <div className="helper-action-row"><button className="button secondary" onClick={() => void command('helper_retry', { source: 'board' })}><RefreshCw size={19}/>重新连接</button></div>
      <div className="helper-note"><Info size={19}/><p>保留 Ctrl+Enter 团队聊天快捷键。双击后松开按键与鼠标，助手核对频道后发送；聊天或菜单打开时暂停，同一项短时间内不重复播报。</p></div>
    </>}
    <div className="subpage-footer"><CircleHelp size={17}/><span>功能状态由游戏与助手实时同步。</span><button className="inline-link" onClick={() => void command('open_settings')}>查看偏好设置</button></div>
  </section>
}

function Step({ icon: Icon, title, text }: { icon: LucideIcon; title: string; text: string }) {
  return <div className="step"><Icon size={26} strokeWidth={1.5}/><h3>{title}</h3><p>{text}</p></div>
}

export default App
