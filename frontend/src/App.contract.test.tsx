import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactElement } from 'react'
import type { Snapshot } from './types'
import { previewSnapshot } from './model'

const bridge = vi.hoisted(() => ({ snapshot: null as unknown as Snapshot, connected: true, command: vi.fn(), setNotice: vi.fn() }))
const stateWrites = vi.hoisted(() => [] as unknown[])
vi.mock('./useAssistant', () => ({ useAssistant: () => ({ ...bridge, bridgeState: bridge.connected ? 'connected' : 'disconnected', bridgeError: '', notice: '' }) }))
vi.mock('react', async importOriginal => {
  const original = await importOriginal<typeof import('react')>()
  return { ...original, useEffect: () => {}, useId: () => 'contract-test',
    useState: (initial: unknown) => [typeof initial === 'function' ? initial() : initial, vi.fn((value: unknown) => stateWrites.push(value))],
    useRef: (initial: unknown) => ({ current: initial }) }
})
import App, { HelperPage } from './App'

type Element = ReactElement<{ children?: unknown; onClick?: () => unknown; onChange?: (event: { target: { value: string } }) => unknown; disabled?: boolean; role?: string; id?: string; value?: string }>
function elements(value: unknown): Element[] {
  if (Array.isArray(value)) return value.flatMap(elements)
  if (!value || typeof value !== 'object' || !('props' in value)) return []
  const element = value as Element
  return [element, ...elements(element.props.children)]
}
function text(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map(text).join('')
  if (value && typeof value === 'object' && 'props' in value) return text((value as Element).props.children)
  return ''
}
function button(tree: unknown, label: string) {
  const found = elements(tree).find(element => element.type === 'button' && text(element.props.children) === label)
  if (!found) throw new Error('Missing player action: ' + label)
  return found
}

describe('真实主界面按钮到原生服务的契约', () => {
  beforeEach(() => {
    bridge.snapshot = structuredClone({ ...previewSnapshot, isDemo: false })
    bridge.connected = true
    bridge.command.mockReset().mockResolvedValue({ ok: true })
    bridge.setNotice.mockReset()
    stateWrites.length = 0
  })
  it('播报语言选择发送独立配置命令，英文已保存时展示英文示例', async () => {
    bridge.snapshot.helpers.board.outputLanguage = 'en'
    const tree = HelperPage({ page: 'board', snapshot: bridge.snapshot, command: bridge.command })
    expect(text(tree)).toContain('发给队友的播报语言')
    expect(text(tree)).toContain('[Ally] Player (Anduin) | Hero damage: 12,345')
    const select = elements(tree).find(element => element.type === 'select' && element.props.id === 'board-output-language')
    expect(select?.props.value).toBe('en')
    select?.props.onChange?.({ target: { value: 'zh' } })
    await Promise.resolve()
    expect(bridge.command).toHaveBeenCalledExactlyOnceWith('board_language', { language: 'zh' })
    expect(stateWrites).toContain('播报语言已保存为 中文。')
  })
  it('语言保存失败恢复原选择，并在选择旁提供错误反馈', async () => {
    bridge.snapshot.helpers.board.outputLanguage = 'zh'
    bridge.command.mockResolvedValue({ ok: false, error: '播报语言未保存，请重试。' })
    const tree = HelperPage({ page: 'board', snapshot: bridge.snapshot, command: bridge.command })
    const select = elements(tree).find(element => element.type === 'select' && element.props.id === 'board-output-language')
    select?.props.onChange?.({ target: { value: 'en' } })
    await Promise.resolve()
    expect(stateWrites).toContain('en')
    expect(stateWrites).toContain('zh')
    expect(stateWrites).toContain('播报语言未保存，请重试。')
    expect(bridge.command).toHaveBeenCalledExactlyOnceWith('board_language', { language: 'en' })
  })
  it('播报语言请求未完成时重复选择不会重复保存', async () => {
    let complete!: (result: { ok: boolean }) => void
    bridge.command.mockReturnValue(new Promise(resolve => { complete = resolve }))
    const tree = HelperPage({ page: 'board', snapshot: bridge.snapshot, command: bridge.command })
    const select = elements(tree).find(element => element.type === 'select' && element.props.id === 'board-output-language')
    select?.props.onChange?.({ target: { value: 'en' } })
    select?.props.onChange?.({ target: { value: 'zh' } })
    expect(bridge.command).toHaveBeenCalledExactlyOnceWith('board_language', { language: 'en' })
    complete({ ok: true })
    await Promise.resolve()
  })
  it('成功译文复制只发送后台允许的空参数，不传译文字段', async () => {
    const copy = button(App(), '复制译文')
    expect(copy.props.disabled).toBe(false)
    await copy.props.onClick?.()
    expect(bridge.command).toHaveBeenCalledExactlyOnceWith('copy', {})
    expect(bridge.setNotice).toHaveBeenCalledWith('译文已复制，回到游戏粘贴即可。')
  })
  it('征召团队筛选沿用后端频道选项并传递独立筛选值', async () => {
    bridge.snapshot.channelOptions = ['全部聊天', '仅团队', '仅征召团队', '仅队伍']
    const tree = App()
    expect(text(tree)).toContain('仅征召团队')
    const select = elements(tree).find(element => element.type === 'select' && text(element).includes('仅征召团队'))
    expect(select).toBeDefined()
    await select?.props.onChange?.({ target: { value: '仅征召团队' } })
    expect(bridge.command).toHaveBeenCalledExactlyOnceWith('set_channel', { value: '仅征召团队' })
  })
  it('打开更新页只准备界面，用户点击检查以前不会发起更新检查', async () => {
    button(App(), '检查更新').props.onClick?.()
    await Promise.resolve()
    expect(bridge.command).toHaveBeenCalledExactlyOnceWith('ui_prepare')
  })
  it('等待准备设置界面时连续点击只产生一次原生准备动作', async () => {
    let complete!: (result: { ok: boolean }) => void
    bridge.command.mockReturnValue(new Promise(resolve => { complete = resolve }))
    const settings = button(App(), '偏好设置')
    settings.props.onClick?.()
    settings.props.onClick?.()
    expect(bridge.command).toHaveBeenCalledExactlyOnceWith('ui_prepare')
    complete({ ok: true })
    await Promise.resolve()
  })
  it('桥接中断后禁止复制旧快照中的译文', () => {
    bridge.connected = false
    expect(button(App(), '复制译文').props.disabled).toBe(true)
  })
  it('手动失败在操作位置显示并保留恢复入口', () => {
    bridge.snapshot.manualState = 'failed'
    bridge.snapshot.manualError = '网络超时，请重试。'
    const tree = App()
    expect(text(tree)).toContain('本次翻译未完成，输入已保留')
    expect(text(tree)).toContain('网络超时，请重试。')
    expect(button(tree, '重试本次翻译')).toBeDefined()
    expect(button(tree, '检查翻译服务')).toBeDefined()
  })
  it('新用户看到自选服务入口，打开前不会连接或保存任何服务', async () => {
    bridge.snapshot.cloudConfigured = false
    bridge.snapshot.management.account = { configured: false, state: 'unconfigured', busy: false, maskedKey: '', account: '', message: '', provider: 'aliyun', providers: [
      { id: 'aliyun', name: '阿里云机器翻译', configured: false, maskedKey: '' },
      { id: 'deepseek', name: 'DeepSeek', configured: false, maskedKey: '', contextEnabled: true },
    ] }
    const tree = App()
    expect(text(tree)).toContain('先选择自己的翻译服务')
    expect(text(tree)).toContain('稍后配置')
    bridge.command.mockClear()
    await button(tree, '选择翻译服务').props.onClick?.()
    expect(bridge.command).toHaveBeenCalledExactlyOnceWith('ui_prepare')
  })
  it('已有账户的老用户不重复显示首次配置引导，暂未启用的已存账户也保留原入口', () => {
    bridge.snapshot.management.account.configured = true
    bridge.snapshot.management.account.provider = 'aliyun'
    expect(text(App())).not.toContain('先选择自己的翻译服务')
    bridge.snapshot.cloudConfigured = false
    bridge.snapshot.management.account.configured = false
    bridge.snapshot.management.account.providers = [{ id: 'deepseek', name: 'DeepSeek', configured: true, maskedKey: '••••test' }]
    expect(text(App())).not.toContain('先选择自己的翻译服务')
  })
})
