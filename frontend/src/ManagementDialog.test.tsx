import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { accountNeedsCancel, cancelAccountAfterRequest, editablePreferences, editPhraseText, ManagementDialog, modalFocusTarget, overlayPreferencesAfterAction, parseLegacyEnv, preparePhrase, ReleaseNotes, updateCapabilities, validatePreferences, withAccountRequest } from './ManagementDialog'
import { previewSnapshot } from './model'
import type { DesktopPreferences, Phrase, Snapshot, UpdateInfo } from './types'

const preferences: DesktopPreferences = {
  font_size: 12, topmost: false, mode: 'normal', hotkeys: { focus: 'Ctrl+T' }, repeat_hotkey: 'F8',
  overlay_enabled: true, overlay_style: 'subtitle', overlay_font_size: 11, overlay_alpha: .88,
  overlay_width: 420, overlay_timeout: 6, overlay_original: false,
}
const update: UpdateInfo = { state: 'idle', detail: '', version: '', notes: '', downloaded: 0, total: 0, busy: false, canInstall: false, archivePath: '' }
const phrase: Phrase = { id: 'p-1', category: '团战', source_text: '等大家集合', translations: { en: 'Wait for everyone.', ko: '모두 모일 때까지 기다리세요.' }, favorite: true, reviewed: true, revision: 4 }
const snapshot: Snapshot = {
  ...previewSnapshot,
  management: {
    preferences, update,
    account: { configured: false, state: 'idle', busy: false, maskedKey: '', account: '', message: '' },
    diagnostics: { enabled: false, busy: false, exportPath: '', message: '' }, processes: [],
  },
}

describe('React 管理窗口的保存与信任边界', () => {
  it('Tab 在首尾循环，没有可用控件时留在对话框，中间控件保留原生顺序', () => {
    const container = {}, first = {}, middle = {}, last = {}, outside = {}
    const items = [first, middle, last]
    expect(modalFocusTarget(items, first, true, container)).toBe(last)
    expect(modalFocusTarget(items, last, false, container)).toBe(first)
    expect(modalFocusTarget(items, middle, false, container)).toBeNull()
    expect(modalFocusTarget(items, middle, true, container)).toBeNull()
    expect(modalFocusTarget(items, outside, false, container)).toBe(first)
    expect(modalFocusTarget([], null, false, container)).toBe(container)
  })

  it('旧 .env 只解析两字段，兼容 BOM/引号/注释，不执行文件中的表达式', () => {
    const text = '\uFEFF# personal key\r\n\r\nALIBABA_CLOUD_ACCESS_KEY_ID="test-ID_123" # id\r\nALIBABA_CLOUD_ACCESS_KEY_SECRET=\'test-secret_./+=\'# secret'
    expect(parseLegacyEnv(text)).toEqual({ accessKeyId: 'test-ID_123', accessKeySecret: 'test-secret_./+=' })
    expect(parseLegacyEnv('ALIBABA_CLOUD_ACCESS_KEY_ID=testID # comment\nALIBABA_CLOUD_ACCESS_KEY_SECRET=testSecret')).toEqual({ accessKeyId: 'testID', accessKeySecret: 'testSecret' })
    const base = 'ALIBABA_CLOUD_ACCESS_KEY_ID=testID\nALIBABA_CLOUD_ACCESS_KEY_SECRET=testSecret'
    for (const invalid of [base + '\nOTHER=value', base + '\nALIBABA_CLOUD_ACCESS_KEY_ID=duplicate', base.replace('testSecret', '$(execute)'), base.replace('testSecret', 'value#comment'), base.replace('testSecret', '"missing-quote'), base.replace('testSecret', '"value" extra'), base.replace('testSecret', '值'), '#'.repeat(16385), 'ALIBABA_CLOUD_ACCESS_KEY_ID=onlyID']) {
      expect(() => parseLegacyEnv(invalid)).toThrow('本机 .env 缺失或格式无效，请检查两个访问密钥字段。')
    }
  })

  it('已就绪或未配置的账户关闭不取消激活，仍在进行的请求才取消', () => {
    expect(accountNeedsCancel({ state: 'ready', busy: false })).toBe(false)
    expect(accountNeedsCancel({ state: 'unconfigured', busy: false })).toBe(false)
    expect(accountNeedsCancel({ state: 'ready', busy: true })).toBe(true)
    expect(accountNeedsCancel({ state: 'connecting', busy: true })).toBe(true)
    expect(accountNeedsCancel({ state: 'error', busy: false })).toBe(true)
  })

  it('连接或删除的 RPC 尚未返回时，关闭不能信任旧的空闲快照', () => {
    // 连接请求刚发出时旧快照仍为 unconfigured；删除时旧快照仍为 ready。
    for (const state of ['unconfigured', 'ready']) {
      const oldSnapshot = { state, busy: false }
      expect(accountNeedsCancel(oldSnapshot, true)).toBe(true)
      expect(accountNeedsCancel(oldSnapshot, false)).toBe(false)
    }
  })

  it.each([['account_connect', 'unconfigured'], ['account_delete', 'ready']])('关闭在途 %s 时，取消排在请求之后且不遗漏', async (action, state) => {
    const pending = { current: false }
    const trace: string[] = []
    let finish!: () => void
    const acknowledgment = new Promise<void>(resolve => { finish = resolve })
    let tail = Promise.resolve()
    const serialCommand = (name: string) => {
      const next = tail.then(async () => { trace.push(name); if (name === action) await acknowledgment })
      tail = next
      return next
    }
    const request = withAccountRequest(pending, () => serialCommand(action))
    expect(pending.current).toBe(true)
    const cancel = accountNeedsCancel({ state, busy: false }, pending.current) ? cancelAccountAfterRequest(request, () => serialCommand('account_cancel')) : Promise.resolve()
    await Promise.resolve()
    expect(trace).toEqual([action])
    finish()
    await Promise.all([request, cancel])
    expect(trace).toEqual([action, 'account_cancel'])
    expect(pending.current).toBe(false)
  })

  it.each([['account_connect', 'unconfigured'], ['account_delete', 'ready']])('%s 回执前即使后端允许并行，取消也必须等响应', async (action, state) => {
    const pending = { current: false }
    const trace: string[] = []
    let finish!: () => void
    const response = new Promise<void>(resolve => { finish = resolve })
    const request = withAccountRequest(pending, async () => { trace.push(action); await response; trace.push('response') })
    const issued = true
    const cancellation = accountNeedsCancel({ state, busy: false }, pending.current || issued)
      ? cancelAccountAfterRequest(request, async () => { trace.push('account_cancel') })
      : Promise.resolve()
    await Promise.resolve()
    expect(trace).toEqual([action])
    finish()
    await cancellation
    expect(trace).toEqual([action, 'response', 'account_cancel'])
  })

  it('账户 RPC 已完成但刷新跳过时，issued 标记仍保护旧空闲快照下的关闭', async () => {
    const pending = { current: false }
    const request = withAccountRequest(pending, async () => ({ ok: true }))
    await request
    expect(pending.current).toBe(false)
    const oldSnapshot = { state: 'unconfigured', busy: false }
    const issued = true
    expect(accountNeedsCancel(oldSnapshot, pending.current || issued)).toBe(true)
    const cancel = vi.fn().mockResolvedValue({ ok: true })
    await cancelAccountAfterRequest(request, cancel)
    expect(cancel).toHaveBeenCalledOnce()
    expect(accountNeedsCancel({ state: 'ready', busy: false }, false)).toBe(false)
  })

  it('账户 RPC 拒绝后仍发送取消，不把拒绝原因暴露到取消接口', async () => {
    const cancel = vi.fn().mockResolvedValue({ ok: true })
    await expect(cancelAccountAfterRequest(Promise.reject(new Error('request failed')), cancel)).resolves.toEqual({ ok: true })
    expect(cancel).toHaveBeenCalledWith()
  })

  it('账户 RPC 失败也释放在途标记，错误不会永久锁住本地请求保护', async () => {
    const pending = { current: false }
    await expect(withAccountRequest(pending, async () => { throw new Error('safe error') })).rejects.toThrow('safe error')
    expect(pending.current).toBe(false)
  })

  it('只提交允许编辑的设置，不把扩展快照字段或其他快捷键带回写入接口', () => {
    const expanded = { ...preferences, target_language: 'ko', auto_translate: false, overlay_editing: true,
      hotkeys: { focus: 'Ctrl+T', internal_only: 'F2' }, account: 'private-account' }
    const value = editablePreferences(expanded)
    expect(value).toEqual(preferences)
    expect(Object.keys(value)).toHaveLength(12)
    value.hotkeys.focus = 'Ctrl+Y'
    expect(expanded.hotkeys.focus).toBe('Ctrl+T')
  })

  it('关闭的面板模式放到底部后再完成调整，保留后端刚启用的透明字幕', () => {
    const original: DesktopPreferences = { ...preferences, overlay_enabled: false, overlay_style: 'panel' }
    const afterBottom = overlayPreferencesAfterAction(original, 'bottom')
    const nextSave = editablePreferences(afterBottom)
    expect(nextSave.overlay_enabled).toBe(true)
    expect(nextSave.overlay_style).toBe('subtitle')
    expect(overlayPreferencesAfterAction(nextSave, 'finish')).toMatchObject({ overlay_enabled: true, overlay_style: 'subtitle' })
    expect(overlayPreferencesAfterAction(original, 'edit')).toMatchObject({ overlay_enabled: true, overlay_style: 'panel' })
    expect(original.overlay_enabled).toBe(false)
    expect(original.overlay_style).toBe('panel')
  })

  it('拒绝空白快捷键、冲突按键和输入中缺失的数字，允许明确禁用', () => {
    expect(validatePreferences(preferences)).toBeNull()
    expect(validatePreferences({ ...preferences, hotkeys: {} })).toBeNull()
    expect(validatePreferences({ ...preferences, hotkeys: { focus: '' } })).toContain('不能为空')
    expect(validatePreferences({ ...preferences, hotkeys: { focus: 'f8' } })).toContain('重复')
    expect(validatePreferences({ ...preferences, overlay_timeout: Number.NaN })).toContain('停留时间')
    expect(validatePreferences({ ...preferences, font_size: 24 })).toBeNull()
    expect(validatePreferences({ ...preferences, font_size: 32.5 })).toContain('内容字号')
  })

  it('修改原文或任一译文取消校对，保留其他语言及收藏，不自动重设为已校对', () => {
    const changed = editPhraseText(phrase, 'en', 'Wait here.')
    expect(changed.reviewed).toBe(false)
    expect(changed.translations.ko).toBe(phrase.translations.ko)
    expect(changed.favorite).toBe(true)
    expect(changed.revision).toBe(4)
    expect(phrase.reviewed).toBe(true)
    expect(editPhraseText(phrase, 'source_text', '别急着开团').reviewed).toBe(false)
    expect(editPhraseText(phrase, 'ko', '기다려 주세요.').reviewed).toBe(false)
  })

  it('省略空语言并完整发送保存字段，缺少译文不保存，也不自动给予校对信任', () => {
    const input = { ...phrase, reviewed: false, translations: { en: '  Wait.  ', ko: ' ' } }
    expect(preparePhrase(input).value).toEqual({ ...input, translations: { en: 'Wait.' } })
    expect(preparePhrase({ ...input, translations: {} }).error).toContain('至少')
    expect(preparePhrase({ ...input, source_text: ' ' }).error).toContain('中文')
    expect(preparePhrase({ ...input, category: '未知分类' }).error).toContain('分类')
    expect(preparePhrase({ ...input, source_text: '😀'.repeat(1000) }).error).toBeUndefined()
    expect(preparePhrase({ ...input, source_text: '😀'.repeat(1001) }).error).toContain('1000')
  })
})

describe('更新状态与安全呈现', () => {
  it('安装须已经下载且当前运行方式允许；检查/下载进行中只能取消，准备重启时不可重入', () => {
    expect(updateCapabilities({ ...update, state: 'available' })).toMatchObject({ download: true, install: false })
    expect(updateCapabilities({ ...update, state: 'downloaded' }).install).toBe(false)
    expect(updateCapabilities({ ...update, state: 'downloaded', canInstall: true }).install).toBe(true)
    expect(updateCapabilities({ ...update, state: 'downloading', busy: true })).toEqual({ check: false, download: false, install: false, cancel: true })
    expect(updateCapabilities({ ...update, state: 'preparing', busy: false }).check).toBe(false)
    expect(updateCapabilities({ ...update, state: 'restarting', busy: false }).check).toBe(false)
  })

  it('发布说明有标题、段落、列表、代码语义，所有说明作为文本转义', () => {
    const html = renderToStaticMarkup(<ReleaseNotes text="" blocks={[
      { kind: 'heading', level: 1, marker: '', depth: 0, spans: [{ text: '改进与修复', styles: [] }] },
      { kind: 'paragraph', level: 0, marker: '', depth: 0, spans: [{ text: '<script>unsafe()</script>', styles: ['bold'] }] },
      { kind: 'list', level: 0, marker: '-', depth: 0, spans: [{ text: '可读说明', styles: [] }] },
      { kind: 'list', level: 0, marker: '2.', depth: 0, spans: [{ text: '第二步', styles: [] }] },
      { kind: 'code', level: 0, marker: '', depth: 0, spans: [{ text: '<img src=x onerror=unsafe()>', styles: [] }] },
    ]}/>)
    expect(html).toContain('<h3>')
    expect(html).toContain('<strong>')
    expect(html).toContain('<ul>')
    expect(html).toContain('<ol>')
    expect(html).toContain('<li value="2"')
    expect(html).toContain('<pre><code>')
    expect(html).toContain('&lt;script&gt;')
    expect(html).not.toContain('<script>')
    expect(html).not.toContain('<img')
  })

  it('打开设置或更新只呈现内容，不自动联网、保存或执行安装', () => {
    const command = vi.fn().mockResolvedValue({ ok: true })
    for (const panel of ['settings', 'updates'] as const) {
      const html = renderToStaticMarkup(<ManagementDialog panel={panel} snapshot={snapshot} command={command} onClose={vi.fn()}/>)
      expect(html).toContain('role="dialog"')
      expect(html).toContain('aria-modal="true"')
      expect(html).toContain('--content-font-size:16px')
      if (panel === 'settings') {
        expect(html.match(/role="tab"/g)).toHaveLength(5)
        expect(html).toContain('取消不会保存')
      } else expect(html).toContain('检查更新')
    }
    expect(command).not.toHaveBeenCalled()
  })

  it('账户仅展示掩码，凭据输入不从快照预填，错误在窗口里持久呈现', () => {
    const value = { ...snapshot, management: { ...snapshot.management, account: { configured: true, state: 'error', busy: false, maskedKey: 'LTAI…1234', account: 'test-account', message: '请检查 RAM 权限。', accessKeyId: 'should-never-render', accessKeySecret: 'private-secret-marker' } } }
    const html = renderToStaticMarkup(<ManagementDialog panel="account" snapshot={value} command={vi.fn()} onClose={vi.fn()}/>)
    expect(html).toContain('LTAI…1234')
    expect(html).toContain('type="password"')
    expect(html).toContain('role="alert"')
    expect(html).toContain('请检查 RAM 权限')
    expect(html).not.toContain('private-secret-marker')
    expect(html).not.toContain('should-never-render')
  })
})
