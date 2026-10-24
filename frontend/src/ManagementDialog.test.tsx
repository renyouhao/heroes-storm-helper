import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { AccountBalance, accountNeedsCancel, accountProviderProfile, cancelAccountAfterRequest, editablePreferences, editPhraseText, ManagementDialog, modalFocusTarget, OverlayMonitorControls, overlayPreferencesAfterAction, parseLegacyEnv, pendingOverlayMonitor, prepareAccountConnection, preparePhrase, ProviderChoices, ReleaseNotes, safeAccountFeedback, updateCapabilities, validatePreferences, withAccountRequest } from './ManagementDialog'
import { previewSnapshot } from './model'
import { hasConfiguredTranslationProfile, initialTranslationProvider } from './translationProviders'
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
  it('字幕提供带标签的主副屏选择与按钮，不要求用户拖拽才能跨屏', () => {
    const monitors = [{ id: 0, label: '主显示器', primary: true, selected: false, width: 1920, height: 1040 }, { id: 1, label: '显示器 2', primary: false, selected: true, width: 2560, height: 1400 }]
    const markup = renderToStaticMarkup(<OverlayMonitorControls monitors={monitors} selected="0" onChange={vi.fn()} onMove={vi.fn()}/> )
    expect(markup).toContain('字幕显示器')
    expect(markup).toContain('<select')
    expect(markup).toContain('主显示器 · 1920 × 1040')
    expect(markup).toContain('显示器 2 · 2560 × 1400')
    expect(markup).toContain('移动到所选屏幕')
    expect(markup).not.toContain('disabled')
    expect(pendingOverlayMonitor(monitors, '0')).toBe(0)
    expect(pendingOverlayMonitor(monitors, '1')).toBeNull()
    expect(pendingOverlayMonitor(monitors, '99')).toBeNull()
  })

  it('没有可用显示器时禁用选择与移动并解释原因', () => {
    const markup = renderToStaticMarkup(<OverlayMonitorControls monitors={[]} selected="0" onChange={vi.fn()} onMove={vi.fn()}/> )
    expect(markup.match(/disabled=""/g)).toHaveLength(2)
    expect(markup).toContain('显示器列表暂不可用')
  })

  it('双服务分别发送允许的连接字段，不把另一家凭据或额外参数带入请求', () => {
    expect(prepareAccountConnection(null, 'test-id', 'test-secret', true)).toEqual({ error: '请先选择阿里云机器翻译或 DeepSeek。' })
    expect(prepareAccountConnection('aliyun', '  test-id  ', '  test-secret  ', true)).toEqual({ name: 'account_connect', payload: { accessKeyId: 'test-id', accessKeySecret: 'test-secret' } })
    expect(prepareAccountConnection('deepseek', 'unrelated-aliyun-id', '  synthetic-ds-key  ', false)).toEqual({ name: 'account_connect_deepseek', payload: { apiKey: 'synthetic-ds-key', contextEnabled: false } })
    expect(prepareAccountConnection('deepseek', '', 'synthetic-ds-key', true)).toEqual({ name: 'account_connect_deepseek', payload: { apiKey: 'synthetic-ds-key', contextEnabled: true } })
    expect(prepareAccountConnection('deepseek', 'test-id', ' ', true)).toEqual({ error: '请填写 DeepSeek API Key。' })
    expect(prepareAccountConnection('aliyun', ' ', 'test-secret', false)).toEqual({ error: '请填写同一把 Key 的 ID 和 Secret。' })
  })

  it('旧快照仍默认阿里云；未配置的DS默认开启上下文，已存设置不会被默认值覆盖', () => {
    const oldAccount = { ...snapshot.management.account, configured: true, maskedKey: 'LTAI…1234' }
    expect(accountProviderProfile(oldAccount, 'aliyun')).toMatchObject({ configured: true, maskedKey: 'LTAI…1234' })
    expect(accountProviderProfile(oldAccount, 'deepseek')).toMatchObject({ configured: false, maskedKey: '', contextEnabled: true })
    const saved = { id: 'deepseek' as const, name: 'DeepSeek V4.1 Flash', configured: true, maskedKey: 'sk-…test', contextEnabled: false }
    expect(accountProviderProfile({ ...oldAccount, providers: [saved] }, 'deepseek')).toEqual(saved)
    expect(accountProviderProfile({ ...oldAccount, providers: [{ ...saved, configured: false }] }, 'deepseek').contextEnabled).toBe(true)
  })

  it('首次未配置账户不预选服务，已有任一家profile则保留当前选择，兼容旧阿里云快照', () => {
    const fresh = { ...snapshot.management.account, state: 'unconfigured', provider: 'aliyun' as const }
    expect(hasConfiguredTranslationProfile(fresh)).toBe(false)
    expect(initialTranslationProvider(fresh)).toBeNull()
    expect(initialTranslationProvider({ ...fresh, configured: true })).toBe('aliyun')
    expect(initialTranslationProvider({ ...snapshot.management.account, configured: true })).toBe('aliyun')
    const existing = { ...fresh, provider: 'deepseek' as const, providers: [{ id: 'deepseek' as const, name: 'DeepSeek', configured: true, maskedKey: '••••test', contextEnabled: false }] }
    expect(hasConfiguredTranslationProfile(existing)).toBe(true)
    expect(initialTranslationProvider(existing)).toBe('deepseek')
  })

  it('账户回执即使反射提交值，提示也隐藏本次API Key和阿里云凭据', () => {
    expect(safeAccountFeedback('API Key synthetic-ds-key 无效', { apiKey: 'synthetic-ds-key' })).toBe('API Key [密钥已隐藏] 无效')
    expect(safeAccountFeedback('test-id / test-secret 权限不足', { accessKeyId: 'test-id', accessKeySecret: 'test-secret' })).toBe('[密钥已隐藏] / [密钥已隐藏] 权限不足')
    expect(safeAccountFeedback('synthetic-key-long / synthetic-key', { accessKeyId: 'synthetic-key', accessKeySecret: 'synthetic-key-long' })).toBe('[密钥已隐藏] / [密钥已隐藏]')
    expect(safeAccountFeedback('请检查账户余额。')).toBe('请检查账户余额。')
  })

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

  it.each([['account_connect', 'unconfigured'], ['account_connect_deepseek', 'unconfigured'], ['account_select_provider', 'ready'], ['account_delete', 'ready']])('关闭在途 %s 时，取消排在请求之后且不遗漏', async (action, state) => {
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
  it('新用户先看两张未选中的原生单选卡，不展示默认密钥表单，可跳过先看原文', () => {
    const command = vi.fn()
    const value = { ...snapshot, isDemo: false, management: { ...snapshot.management, account: { ...snapshot.management.account, state: 'unconfigured', provider: 'aliyun' as const } } }
    const html = renderToStaticMarkup(<ManagementDialog panel="account" snapshot={value} command={command} onClose={vi.fn()}/>)
    const radios = Array.from(html.matchAll(/<input\b[^>]*type="radio"[^>]*>/g), match => match[0])
    expect(radios).toHaveLength(2)
    expect(radios.every(radio => !radio.includes('checked=""'))).toBe(true)
    expect(html).toContain('阿里云机器翻译')
    expect(html).toContain('DeepSeek V4.1 Flash')
    expect(html).toContain('按字符计费')
    expect(html).toContain('按 token 计费')
    expect(html).toContain('尚未连接翻译服务')
    expect(html).not.toContain('当前服务：阿里云')
    expect(html).not.toContain('type="password"')
    expect(html).not.toContain('type="file"')
    expect(html).not.toContain('连接并开始使用')
    expect(html).toContain('暂时跳过，先看原文')
    expect(html).toContain('打开阿里云官方入口')
    expect(html).toContain('打开 DeepSeek 官方入口')
    expect(command).not.toHaveBeenCalled()
  })

  it('服务卡用同组原生radio表达单选，官方入口是独立button，不在渲染时触发选择或外部请求', () => {
    const onChange = vi.fn(), onOpenPortal = vi.fn()
    const html = renderToStaticMarkup(<ProviderChoices account={snapshot.management.account} selected="deepseek" onChange={onChange} onOpenPortal={onOpenPortal}/>)
    const radios = Array.from(html.matchAll(/<input\b[^>]*type="radio"[^>]*>/g), match => match[0])
    expect(radios).toHaveLength(2)
    expect(radios[0].match(/name="([^"]+)"/)?.[1]).toBe(radios[1].match(/name="([^"]+)"/)?.[1])
    expect(radios[0]).not.toContain('checked=""')
    expect(radios[1]).toContain('checked=""')
    expect(radios[1]).toContain('value="deepseek"')
    expect(html.match(/type="button"/g)).toHaveLength(2)
    expect(html).not.toContain('href=')
    expect(onChange).not.toHaveBeenCalled()
    expect(onOpenPortal).not.toHaveBeenCalled()
  })

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

  it('DS账户只显示单个空密码字段和已存上下文设置，展示真实币种余额且不自动查询', () => {
    const command = vi.fn()
    const value = { ...snapshot, management: { ...snapshot.management, account: {
      ...snapshot.management.account, configured: true, state: 'ready', provider: 'deepseek' as const, maskedKey: 'sk-…test', contextEnabled: false,
      providers: [{ id: 'deepseek' as const, name: 'DeepSeek V4.1 Flash', configured: true, maskedKey: 'sk-…test', contextEnabled: false }],
      apiKey: 'private-ds-key-marker', accessKeySecret: 'private-aliyun-marker',
      balance: { busy: false, available: true, updatedAt: '2026-10-24T10:00:00+08:00', message: '余额已更新。', balances: [
        { currency: 'CNY', totalBalance: '12.50', grantedBalance: '2.00', toppedUpBalance: '10.50' },
        { currency: 'USD', totalBalance: '0.00', grantedBalance: '0.00', toppedUpBalance: '0.00' },
      ] },
    } } }
    const html = renderToStaticMarkup(<ManagementDialog panel="account" snapshot={value} command={command} onClose={vi.fn()}/>)
    expect(html).toContain('当前服务：DeepSeek V4.1 Flash')
    expect(html).toContain('DeepSeek API Key')
    expect(html.match(/type="password"/g)).toHaveLength(1)
    expect(html).not.toContain('mg-field-label">AccessKey ID')
    expect(html).not.toContain('mg-field-label">AccessKey Secret')
    expect(html.match(/<input\b[^>]*type="checkbox"[^>]*>/)?.[0]).not.toContain('checked=""')
    expect(html).toContain('使用已保存账户')
    expect(html).toContain('人民币 CNY')
    expect(html).toContain('美元 USD')
    expect(html).toContain('总余额')
    expect(html).toContain('赠送余额')
    expect(html).toContain('充值余额')
    expect(html).toContain('12.50')
    expect(html).toContain('0.00')
    expect(html).toContain('更新时间')
    expect(html).not.toContain('剩余字符')
    expect(html).not.toContain('private-ds-key-marker')
    expect(html).not.toContain('private-aliyun-marker')
    expect(command).not.toHaveBeenCalled()
    for (const busy of [true, false]) {
      const current = { ...value, isDemo: false, management: { ...value.management, account: { ...value.management.account, balance: { ...value.management.account.balance, busy } } } }
      const markup = renderToStaticMarkup(<ManagementDialog panel="account" snapshot={current} command={command} onClose={vi.fn()}/>)
      const query = Array.from(markup.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g), match => match[0]).find(button => button.includes(busy ? '正在查询余额' : '查询账户余额'))
      expect(query).toBeDefined()
      expect(query?.includes('disabled=""')).toBe(busy)
    }
  })

  it('余额请求有读屏加载反馈，失败说明与服务端文本按文本转义', () => {
    const busy = renderToStaticMarkup(<AccountBalance balance={{ busy: true, available: null, balances: [], updatedAt: '', message: '' }}/>)
    expect(busy).toContain('aria-busy="true"')
    expect(busy).toContain('aria-live="polite"')
    expect(busy).toContain('正在查询账户余额')
    const failed = renderToStaticMarkup(<AccountBalance balance={{ busy: false, available: null, balances: [], updatedAt: '', message: '<script>synthetic()</script>，请重试。' }}/>)
    expect(failed).toContain('&lt;script&gt;')
    expect(failed).not.toContain('<script>')
    expect(failed).toContain('请重试')
  })
})
