import { useCallback, useEffect, useRef, useState } from 'react'
import { errorMessage, normalizeSnapshot, previewSnapshot } from './model'
import type { Command, Snapshot } from './types'

export function useAssistant() {
  const [snapshot, setSnapshot] = useState<Snapshot>(previewSnapshot)
  const [connected, setConnected] = useState(false)
  const [bridgeState, setBridgeState] = useState<'preview' | 'connecting' | 'connected' | 'disconnected'>('preview')
  const [bridgeError, setBridgeError] = useState('')
  const [notice, setNotice] = useState('')
  const refreshRef = useRef<() => Promise<void>>(async () => {})
  const signature = useRef('')

  useEffect(() => {
    let disposed = false
    let loading = false
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      const api = window.pywebview?.api
      if (disposed || loading || !api) return
      loading = true
      setBridgeState(state => state === 'preview' ? 'connecting' : state)
      try {
        const raw = await api.snapshot()
        if ('error' in raw) throw new Error(String(raw.error))
        const next = normalizeSnapshot(raw)
        if (disposed) return
        const nextSignature = JSON.stringify(next)
        if (signature.current !== nextSignature) {
          signature.current = nextSignature
          setSnapshot(next)
        }
        setConnected(true)
        setBridgeState('connected')
        setBridgeError('')
      } catch (error) {
        if (!disposed) {
          setConnected(false)
          setBridgeState('disconnected')
          setBridgeError('连接助手失败：' + errorMessage(error) + '。保留的消息为上次成功连接时的内容。')
        }
      } finally { loading = false }
    }
    refreshRef.current = refresh
    const poll = async () => {
      await refresh()
      if (!disposed) timer = setTimeout(poll, document.hidden ? 2000 : 400)
    }
    const onReady = () => { void refresh() }
    const onVisible = () => { if (!document.hidden) void refresh() }
    window.addEventListener('pywebviewready', onReady)
    document.addEventListener('visibilitychange', onVisible)
    void poll()
    return () => {
      disposed = true
      clearTimeout(timer)
      window.removeEventListener('pywebviewready', onReady)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [])

  useEffect(() => {
    if (!notice) return
    const timer = setTimeout(() => setNotice(''), 4200)
    return () => clearTimeout(timer)
  }, [notice])

  const command: Command = useCallback(async (name, payload = {}) => {
    const api = window.pywebview?.api
    if (!api) {
      const error = '当前是静态预览，请通过启动脚本打开助手后使用此功能。'
      setNotice(error)
      return { ok: false, error }
    }
    try {
      const result = await api.command(name, payload)
      if (!result.ok) setNotice(result.error || '操作没有完成，请重试。')
      else if (result.message) setNotice(result.message)
      await refreshRef.current()
      return result
    } catch (error) {
      const message = errorMessage(error)
      setNotice(message)
      return { ok: false, error: message }
    }
  }, [])

  return { snapshot, connected, bridgeState, bridgeError, command, notice, setNotice }
}
