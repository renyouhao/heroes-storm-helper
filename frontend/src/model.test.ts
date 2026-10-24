import { describe, expect, it } from 'vitest'
import { canCopyPreview, errorMessage, normalizeSnapshot, previewSnapshot, selectDraft, visibleMessages } from './model'

describe('聊天工作区状态边界', () => {
  it('遗留主题快照统一柔雾紫，草稿和消息保持原值', () => {
    for (const legacy of ['dark', 'light', 'graphite', 'moonlight', 'mint', 'amethyst', 'ice', 'unknown']) {
      const snapshot = normalizeSnapshot({ theme: legacy, draft: '保留草稿', messages: previewSnapshot.messages })
      expect(snapshot.theme).toBe('lavender')
      expect(snapshot.draft).toBe('保留草稿')
      expect(snapshot.messages).toEqual(previewSnapshot.messages)
    }
  })
  it('团队筛选支持本地和标准频道，保留原来的消息顺序', () => {
    const messages = [
      { ...previewSnapshot.messages[0], channel: 'party' },
      { ...previewSnapshot.messages[1], channel: '团队' },
      { ...previewSnapshot.messages[2], channel: 'team' },
    ]
    expect(visibleMessages(messages, 'team')).toEqual(messages.slice(1))
    expect(visibleMessages(messages, 'all')).toEqual(messages)
  })
  it('新草稿不能被较旧的轮询响应覆盖', () => {
    expect(selectDraft('正在输入的新草稿', '旧草稿', '正在输入的新草稿')).toBe('正在输入的新草稿')
    expect(selectDraft('新草稿', '新草稿', '新草稿')).toBe('新草稿')
    expect(selectDraft('旧草稿', '短句填入', null)).toBe('短句填入')
  })
  it('草稿或目标语言变化以及翻译中均不能复制旧译文', () => {
    expect(canCopyPreview(previewSnapshot, previewSnapshot.draft)).toBe(true)
    expect(canCopyPreview(previewSnapshot, '更新后的草稿')).toBe(false)
    expect(canCopyPreview({ ...previewSnapshot, manualBusy: true }, previewSnapshot.draft)).toBe(false)
    expect(canCopyPreview({ ...previewSnapshot, previewTarget: 'ko' }, previewSnapshot.draft)).toBe(false)
    expect(canCopyPreview({ ...previewSnapshot, preview: '' }, previewSnapshot.draft)).toBe(false)
  })
  it('真实后端没有消息时不渲染示例消息，错误能读懂', () => {
    expect(normalizeSnapshot({}).messages).toEqual([])
    expect(normalizeSnapshot({}).draft).toBe('')
    expect(errorMessage(new Error('翻译服务不可用'))).toBe('翻译服务不可用')
    expect(errorMessage(null)).toBe('操作没有完成，请重试。')
  })
})
