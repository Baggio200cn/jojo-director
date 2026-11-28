import { useEffect, useState } from 'react'
import { api } from './api'
import { useI18n } from './I18n'

type RouteSetting = { key: string; label: string; provider: 'moma' | 'ark'; model: string }
type Settings = {
  providers: Record<'moma' | 'ark', { label: string; baseUrl: string; apiKeyConfigured: boolean; apiKeyMask: string }>
  runtime: { publicBase: string; pubSignConfigured: boolean; ttsAppId: string; ttsVoice: string; ttsTokenConfigured: boolean; whisperModelDir: string }
  routes: RouteSetting[]
}

export default function ProviderConfig({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t, language } = useI18n()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [env, setEnv] = useState<Record<string, string>>({})
  const [routes, setRoutes] = useState<Record<string, RouteSetting>>({})
  const [catalogs, setCatalogs] = useState<Record<string, string[]>>({ moma: [], ark: [] })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    if (!open) return
    setError(''); setNotice('')
    api.providerSettings().then((data: Settings) => {
      setSettings(data)
      setRoutes(Object.fromEntries(data.routes.map(route => [route.key, route])))
      setEnv({
        BJMOMA_BASE: data.providers.moma.baseUrl,
        PUBLIC_BASE: data.runtime.publicBase,
        TTS_APPID: data.runtime.ttsAppId,
        TTS_VOICE: data.runtime.ttsVoice,
        WHISPER_MODEL_DIR: data.runtime.whisperModelDir,
      })
    }).catch(e => setError(String(e).replace('Error: ', '')))
  }, [open])

  if (!open) return null

  const updateRoute = (key: string, field: 'provider' | 'model', value: string) => {
    setRoutes(previous => ({ ...previous, [key]: { ...previous[key], [field]: value } }))
  }

  const save = async () => {
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api.saveProviderSettings({ env, routes }) as Settings
      setSettings(result)
      setEnv(previous => ({ ...previous, ARK_API_KEY: '', BJMOMA_API_KEY: '', PUB_SIGN_KEY: '', TTS_TOKEN: '' }))
      setNotice(t('配置已保存并即时生效。API Key 未回显；图片和视频仍需最小生成预检。'))
    } catch (e) { setError(String(e).replace('Error: ', '')) }
    finally { setBusy(false) }
  }

  const testCatalog = async (provider: 'moma' | 'ark') => {
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api.providerCatalog(provider) as { count: number; models: string[]; message: string }
      setCatalogs(previous => ({ ...previous, [provider]: result.models }))
      setNotice(`${provider === 'ark' ? t('火山引擎') : 'MoMA'}: ${language === 'en' ? `Model catalog connected. Image and video generation still require a minimal preflight. ${result.count} models found.` : `${result.message}，共 ${result.count} 个模型。`}`)
    } catch (e) { setError(String(e).replace('Error: ', '')) }
    finally { setBusy(false) }
  }

  return <div className="lightbox" onClick={onClose}>
    <div className="lightbox-inner provider-config" onClick={e => e.stopPropagation()}>
      <div className="lightbox-head"><div><b>{t('API 调用配置')}</b><p>{t('图片、视频、Agent 与辅助模型的实际生产路由')}</p></div><button onClick={onClose}>✕ {t('关闭')}</button></div>
      <div className="provider-notice">{t('API Key 仅提交到服务端保存，留空表示不修改。模型目录连通不等于生成端点、余额或配额已验收。')}</div>
      {error && <div className="provider-error">{error}</div>}
      {notice && <div className="provider-success">{notice}</div>}
      {!settings ? <div className="community-empty">{t('正在读取配置…')}</div> : <div className="provider-config-body">
        <section className="provider-section"><h3>{t('当前生产路由')}</h3>
          <div className="provider-route-table"><div className="provider-route-row head"><span>{t('能力')}</span><span>{t('供应商')}</span><span>{t('模型或 EP 接入点 ID')}</span></div>
            {Object.values(routes).map(route => <div className="provider-route-row" key={route.key}>
              <label>{t(route.label)}</label>
              <select value={route.provider} onChange={e => updateRoute(route.key, 'provider', e.target.value)}><option value="ark">{t('火山引擎')}</option><option value="moma">MoMA</option></select>
              <input list={`provider-models-${route.provider}`} value={route.model} onChange={e => updateRoute(route.key, 'model', e.target.value)} placeholder={route.key === 'research' || route.key === 'script' || route.key === 'storyboard' ? t('模型 ID 或 EP 接入点 ID') : t('模型 ID')} />
            </div>)}
          </div>
          <small>{t('火山的文本、调研等自定义接入点通常填写 ep-...；图片和视频按实际可用模型 ID 配置。联网调研与 Embedding 当前固定使用火山。')}</small>
        </section>
        <datalist id="provider-models-ark">{catalogs.ark.map(model => <option key={model} value={model} />)}</datalist>
        <datalist id="provider-models-moma">{catalogs.moma.map(model => <option key={model} value={model} />)}</datalist>
        <div className="provider-columns">
          <section className="provider-section"><h3>MoMA</h3>
            <label>{t('服务地址')}<input value={env.BJMOMA_BASE || ''} onChange={e => setEnv({ ...env, BJMOMA_BASE: e.target.value })} /></label>
            <label>API Key<input type="password" autoComplete="off" placeholder={settings.providers.moma.apiKeyConfigured ? `${settings.providers.moma.apiKeyMask} · ${t('已配置，留空不修改')}` : t('请输入 MoMA API Key')} value={env.BJMOMA_API_KEY || ''} onChange={e => setEnv({ ...env, BJMOMA_API_KEY: e.target.value })} /></label>
            <button disabled={busy} onClick={() => void testCatalog('moma')}>{t('测试 MoMA 模型目录')}</button>
          </section>
          <section className="provider-section"><h3>{t('火山引擎')}</h3>
            <label>{t('服务地址')}<input value={settings.providers.ark.baseUrl} readOnly /></label>
            <label>API Key<input type="password" autoComplete="off" placeholder={settings.providers.ark.apiKeyConfigured ? `${settings.providers.ark.apiKeyMask} · ${t('已配置，留空不修改')}` : t('请输入火山引擎 API Key')} value={env.ARK_API_KEY || ''} onChange={e => setEnv({ ...env, ARK_API_KEY: e.target.value })} /></label>
            <button disabled={busy} onClick={() => void testCatalog('ark')}>{t('测试火山模型目录')}</button>
          </section>
        </div>
        <section className="provider-section"><h3>{t('图生视频与本地运行')}</h3>
          <label>JOJO PUBLIC_BASE<input placeholder="https://115.190.155.2" value={env.PUBLIC_BASE || ''} onChange={e => setEnv({ ...env, PUBLIC_BASE: e.target.value })} /></label>
          <label>{t('素材签名密钥 PUB_SIGN_KEY')}<input type="password" autoComplete="off" placeholder={settings.runtime.pubSignConfigured ? t('已配置，留空不修改') : t('留空由服务端生成')} value={env.PUB_SIGN_KEY || ''} onChange={e => setEnv({ ...env, PUB_SIGN_KEY: e.target.value })} /></label>
          <details><summary>{t('语音与本地模型（可选）')}</summary>
            <label>TTS APPID<input value={env.TTS_APPID || ''} onChange={e => setEnv({ ...env, TTS_APPID: e.target.value })} /></label>
            <label>TTS Token<input type="password" autoComplete="off" placeholder={settings.runtime.ttsTokenConfigured ? t('已配置，留空不修改') : t('请输入 TTS Token')} value={env.TTS_TOKEN || ''} onChange={e => setEnv({ ...env, TTS_TOKEN: e.target.value })} /></label>
            <label>TTS Voice<input value={env.TTS_VOICE || ''} onChange={e => setEnv({ ...env, TTS_VOICE: e.target.value })} /></label>
            <label>Whisper 模型目录<input value={env.WHISPER_MODEL_DIR || ''} onChange={e => setEnv({ ...env, WHISPER_MODEL_DIR: e.target.value })} /></label>
          </details>
        </section>
      </div>}
      <div className="provider-footer"><span>{t('保存只改变配置和路由，不会自动发起付费生成。')}</span><button className="accent" disabled={busy || !settings} onClick={() => void save()}>{busy ? t('正在保存…') : t('保存并应用配置')}</button></div>
    </div>
  </div>
}
