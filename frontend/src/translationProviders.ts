import type { AccountInfo, TranslationProvider } from './types'

export const translationProviders = [
  { id: 'aliyun', name: '阿里云机器翻译', billing: '按字符计费', credentials: 'AccessKey ID + Secret',
    description: '在阿里云控制台查询免费额度与用量。', portalLabel: '打开阿里云官方入口', portalName: '阿里云机器翻译控制台' },
  { id: 'deepseek', name: 'DeepSeek V4.1 Flash', billing: '按 token 计费', credentials: '一把 API Key',
    description: '可选相关会话上下文，在账户页查询余额。', portalLabel: '打开 DeepSeek 官方入口', portalName: 'DeepSeek API Key 管理页' },
] as const

export function hasConfiguredTranslationProfile(account: AccountInfo): boolean {
  return account.configured || !!account.providers?.some(profile => profile.configured)
}

export function initialTranslationProvider(account: AccountInfo): TranslationProvider | null {
  return hasConfiguredTranslationProfile(account) ? account.provider || 'aliyun' : null
}
