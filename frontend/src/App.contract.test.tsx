import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactElement } from 'react'
import type { Snapshot } from './types'
import { previewSnapshot } from './model'

const bridge = vi.hoisted(() => ({ snapshot: null as unknown as Snapshot, connected: true, command: vi.fn(), setNotice: vi.fn() }))
vi.mock('./useAssistant', () => ({ useAssistant: () => ({ ...bridge, bridgeState: bridge.connected ? 'connected' : 'disconnected', bridgeError: '', notice: '' }) }))
vi.mock('react', async importOriginal => {
  const original = await importOriginal<typeof import('react')>()
  return { ...original, useEffect: () => {}, useId: () => 'contract-test',
    useState: (initial: unknown) => [typeof initial === 'function' ? initial() : initial, vi.fn()],
    useRef: (initial: unknown) => ({ current: initial }) }
})
import App from './App'

type Element = ReactElement<{ children?: unknown; onClick?: () => unknown; disabled?: boolean; role?: string }>
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
  })
  it('成功译文复制只发送后台允许的空参数，不传译文字段', async () => {
    const copy = button(App(), '复制译文')
    expect(copy.props.disabled).toBe(false)
    await copy.props.onClick?.()
    expect(bridge.command).toHaveBeenCalledExactlyOnceWith('copy', {})
    expect(bridge.setNotice).toHaveBeenCalledWith('译文已复制，回到游戏粘贴即可。')
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
})
