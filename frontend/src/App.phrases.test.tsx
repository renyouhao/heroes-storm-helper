import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactElement } from 'react'
import type { Phrase, Snapshot } from './types'
import { previewSnapshot } from './model'

const hooks = vi.hoisted(() => ({ slots: [] as unknown[], cursor: 0 }))
const bridge = vi.hoisted(() => ({ snapshot: null as unknown as Snapshot, command: vi.fn(), setNotice: vi.fn() }))
vi.mock('./useAssistant', () => ({ useAssistant: () => ({ ...bridge, connected: true, bridgeState: 'connected', bridgeError: '', notice: '' }) }))
vi.mock('react', async importOriginal => {
  const original = await importOriginal<typeof import('react')>()
  return {
    ...original, useEffect: () => {}, useId: () => 'phrases-test',
    useState: (initial: unknown) => {
      const index = hooks.cursor++
      if (!(index in hooks.slots)) hooks.slots[index] = typeof initial === 'function' ? initial() : initial
      return [hooks.slots[index], (value: unknown) => {
        hooks.slots[index] = typeof value === 'function' ? value(hooks.slots[index]) : value
      }]
    },
    useRef: (initial: unknown) => {
      const index = hooks.cursor++
      if (!(index in hooks.slots)) hooks.slots[index] = { current: initial }
      return hooks.slots[index]
    },
  }
})
import App from './App'

type Element = ReactElement<{
  children?: unknown
  id?: string
  value?: string
  onClick?: () => unknown
  onChange?: (event: { target: { value: string } }) => void
  usePhrase?: (phrase: Phrase) => Promise<void>
}>
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
function renderApp() {
  hooks.cursor = 0
  return App()
}
function navigate(tree: unknown, label: string) {
  const button = elements(tree).find(element => element.type === 'button' && text(element.props.children) === label)
  if (!button) throw new Error('Missing navigation: ' + label)
  button.props.onClick?.()
  return renderApp()
}
function input(tree: unknown) {
  const found = elements(tree).find(element => element.type === 'textarea' && element.props.id === 'reply-input')
  if (!found) throw new Error('Missing reply input')
  return found
}
function choosePhrase(tree: unknown, phrase: Phrase) {
  const page = elements(tree).find(element => typeof element.type === 'function' && element.type.name === 'PhrasePage')
  if (!page?.props.usePhrase) throw new Error('Missing phrase action')
  return page.props.usePhrase(phrase)
}

const chosen: Phrase = { id: 'chosen', category: '团战', source_text: '选择的短句', translations: { en: 'Chosen phrase.' }, favorite: true, reviewed: true }

describe('主界面短句选择与草稿同步竞态', () => {
  beforeEach(() => {
    hooks.slots = []; hooks.cursor = 0
    bridge.snapshot = structuredClone({ ...previewSnapshot, isDemo: false, draft: '', preview: '', previewSource: '' })
    bridge.command.mockReset().mockResolvedValue({ ok: true })
    bridge.setNotice.mockReset()
    vi.useFakeTimers()
    vi.stubGlobal('window', { pywebview: { api: {} } })
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback: (time: number) => void) => { callback(0); return 1 }))
  })
  afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals() })

  it('选择短句会取消尚未发出的旧草稿同步，240ms 后不会反向覆盖', async () => {
    input(renderApp()).props.onChange?.({ target: { value: '旧草稿' } })
    const phrases = navigate(renderApp(), '常用短句')
    await choosePhrase(phrases, chosen)
    await vi.advanceTimersByTimeAsync(240)
    expect(bridge.command.mock.calls).toEqual([['phrase_use', { id: chosen.id }]])
    expect(input(renderApp()).props.value).toBe(chosen.source_text)
    expect(requestAnimationFrame).toHaveBeenCalledTimes(1)
  })

  it('选择请求在途又输入时，迟到的成功回执不覆盖新稿或抢回焦点', async () => {
    let complete!: (result: { ok: boolean }) => void
    bridge.command.mockImplementation((name: string) => name === 'phrase_use'
      ? new Promise(resolve => { complete = resolve }) : Promise.resolve({ ok: true }))
    input(renderApp()).props.onChange?.({ target: { value: '旧草稿' } })
    const phrases = navigate(renderApp(), '常用短句')
    const pending = choosePhrase(phrases, chosen)
    const chat = navigate(renderApp(), '实时消息')
    input(chat).props.onChange?.({ target: { value: '请求在途时的新稿' } })
    complete({ ok: true })
    await pending
    await vi.advanceTimersByTimeAsync(240)
    expect(input(renderApp()).props.value).toBe('请求在途时的新稿')
    expect(bridge.command.mock.calls).toEqual([
      ['phrase_use', { id: chosen.id }], ['draft', { text: '请求在途时的新稿' }],
    ])
    expect(requestAnimationFrame).not.toHaveBeenCalled()
  })

  it('选择失败后重新同步保留草稿，仍不提交或复制译文', async () => {
    bridge.command.mockImplementation((name: string) => Promise.resolve(name === 'phrase_use' ? { ok: false, error: '短句已删除' } : { ok: true }))
    input(renderApp()).props.onChange?.({ target: { value: '保留的草稿' } })
    const phrases = navigate(renderApp(), '常用短句')
    await choosePhrase(phrases, chosen)
    await vi.advanceTimersByTimeAsync(240)
    const chat = navigate(renderApp(), '实时消息')
    expect(input(chat).props.value).toBe('保留的草稿')
    expect(bridge.command.mock.calls).toEqual([
      ['phrase_use', { id: chosen.id }], ['draft', { text: '保留的草稿' }],
    ])
    expect(requestAnimationFrame).not.toHaveBeenCalled()
  })
})
