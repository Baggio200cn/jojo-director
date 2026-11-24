import { useEffect, useMemo, useState } from 'react'
import { api } from './api'

type Review = {
  id: string; reviewer: string; decision: 'sample_passed' | 'needs_evidence' | 'needs_revision'
  comment: string; checks: string[]; created_at: string; object_version: string
}

type SkillItem = {
  kind: 'skill'; objectId: string; version: string; contentHash: string
  latestReview?: Review | null; reviews: Review[]
  manifest: {
    skillId: string; title: string; description: string; status: string
    classification: { primary: string; labels: string[] }
    source: { type: string; id: string; locator: string; rights: string }[]
    inputs: { name: string; unit?: string; required?: boolean }[]
    routeCandidates: string[]; forbiddenRoutes: string[]; evidenceCards: string[]
    reviewStatus: { status: string; reviewer: string }
    qc: { positiveSamples: number; negativeSamples: number }
  }
  spec: { teachingGoal: string; observations: string[]; invariants: string[]; assertions: string[]; reviewRequired: boolean }
  qc: { rules: { id: string; severity: string; check: string }[] }
}

type DiscoveryItem = {
  kind: 'discovery'; objectId: string; version: string; contentHash: string
  title: string; channel: string; summary: string; url: string; candidateL1: string
  categories: { id: string; name: string; area_name: string }[]
  rights: string; evidenceStatus: string; usage: string
  latestReview?: Review | null; reviews: Review[]
}

type TaxonomyNode = { code: string; name_zh: string; status: string; children?: TaxonomyNode[] }
type LibraryData = {
  taxonomy: { levels: TaxonomyNode[] }
  skills: SkillItem[]; discoveries: DiscoveryItem[]
  stats: { skills: number; discoveries: number; approvedSkills: number; sampled: number; pendingReview: number }
}

type Item = SkillItem | DiscoveryItem
const decisionNames = { sample_passed: '抽检通过', needs_evidence: '退回补证', needs_revision: '退回修改' }
const checkNames: Record<string, string> = {
  source_checked: '已核对来源与定位', rights_checked: '已核对权利与使用边界',
  scope_checked: '已核对适用范围', claim_checked: '已核对科学论断',
  reproducibility_checked: '已核对可复现性',
}

export default function TrainingLibrary({ onBack }: { onBack: () => void }) {
  const [data, setData] = useState<LibraryData | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<'skill' | 'discovery'>('skill')
  const [domain, setDomain] = useState('')
  const [query, setQuery] = useState('')
  const [reviewFilter, setReviewFilter] = useState('')
  const [selectedKey, setSelectedKey] = useState('')
  const [reviewer, setReviewer] = useState('')
  const [decision, setDecision] = useState<Review['decision']>('sample_passed')
  const [comment, setComment] = useState('')
  const [checks, setChecks] = useState<string[]>([])

  const load = async () => {
    setError('')
    try { setData(await api.trainingLibrary()) }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }
  useEffect(() => { void load() }, [])

  const items = useMemo(() => {
    if (!data) return [] as Item[]
    const source: Item[] = tab === 'skill' ? data.skills : data.discoveries
    const q = query.trim().toLowerCase()
    return source.filter(item => {
      const itemDomain = item.kind === 'skill' ? item.manifest.classification.primary : item.candidateL1
      const matchesDomain = !domain || itemDomain.startsWith(domain)
      const matchesQuery = !q || `${item.objectId} ${item.kind === 'skill' ? item.manifest.title : item.title} ${item.kind === 'skill' ? item.manifest.description : item.summary}`.toLowerCase().includes(q)
      const matchesReview = !reviewFilter || (reviewFilter === 'reviewed' ? !!item.latestReview : !item.latestReview)
      return matchesDomain && matchesQuery && matchesReview
    })
  }, [data, tab, domain, query, reviewFilter])

  useEffect(() => {
    if (!items.some(item => `${item.kind}:${item.objectId}` === selectedKey)) {
      setSelectedKey(items[0] ? `${items[0].kind}:${items[0].objectId}` : '')
    }
  }, [items, selectedKey])

  const allItems: Item[] = data ? [...data.skills, ...data.discoveries] : []
  const selected = allItems.find(item => `${item.kind}:${item.objectId}` === selectedKey)
  useEffect(() => {
    setComment(''); setChecks([]); setDecision('sample_passed'); setMessage('')
  }, [selectedKey])

  const submitReview = async () => {
    if (!selected || !reviewer.trim()) { setError('请填写审核人姓名或单位'); return }
    setBusy(true); setError(''); setMessage('')
    try {
      await api.submitTrainingReview({
        objectType: selected.kind, objectId: selected.objectId, contentHash: selected.contentHash,
        reviewer, decision, comment, checks,
      })
      await load()
      setMessage('审核记录已保存；候选投产状态未被修改')
      setComment(''); setChecks([])
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }

  const nextUnreviewed = () => {
    const next = items.find(item => !item.latestReview)
    if (next) setSelectedKey(`${next.kind}:${next.objectId}`)
  }

  if (!data && !error) return <div className="training-page"><div className="training-empty">正在读取训练库…</div></div>
  if (!data) return <div className="training-page"><button onClick={onBack}>← 返回首页</button><div className="training-error">{error}</div></div>

  return (
    <div className="training-page notranslate" translate="no">
      <header className="training-head">
        <button onClick={onBack}>← 返回首页</button>
        <div><h1>科学素材训练库</h1><p>分类目录、证据边界与人工抽检记录</p></div>
        <span className="training-badge">候选预览 · 未批准投产</span>
        <button onClick={() => void load()}>刷新</button>
      </header>
      <div className="training-notice">
        外部视频仅用于发现与备课参考，不是科学真值。此处的“抽检通过”只保存人工意见，不改变候选状态，也不授权生成或投产。
      </div>
      <div className="training-stats">
        <span><b>{data.stats.skills}</b> 个候选 Skill</span>
        <span><b>{data.stats.discoveries}</b> 条外部发现</span>
        <span><b>{data.stats.pendingReview}</b> 条待抽检</span>
        <span><b>{data.stats.approvedSkills}</b> 个已批准投产</span>
      </div>
      <div className="training-workspace">
        <aside className="training-taxonomy">
          <h2>学科目录</h2>
          <button className={!domain ? 'on' : ''} onClick={() => setDomain('')}>全部学科</button>
          {data.taxonomy.levels.map(node => (
            <div className="taxonomy-group" key={node.code}>
              <button className={domain === node.code ? 'on' : ''} onClick={() => setDomain(node.code)}>
                <span>{node.name_zh}</span><small>{node.status === 'active' ? '已启用' : '草案'}</small>
              </button>
              {domain === node.code && <div className="taxonomy-children">
                {(node.children ?? []).map(child => <span key={child.code}>{child.name_zh}<small>{child.status === 'draft' ? ' · 草案' : ''}</small></span>)}
              </div>}
            </div>
          ))}
        </aside>
        <section className="training-browser">
          <div className="training-tabs">
            <button className={tab === 'skill' ? 'on' : ''} onClick={() => setTab('skill')}>Skill 候选 ({data.stats.skills})</button>
            <button className={tab === 'discovery' ? 'on' : ''} onClick={() => setTab('discovery')}>外部发现 ({data.stats.discoveries})</button>
          </div>
          <input className="training-search" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索名称、编号或摘要" />
          <div className="training-filter">
            <select value={reviewFilter} onChange={e => setReviewFilter(e.target.value)}>
              <option value="">全部审核状态</option><option value="pending">待抽检</option><option value="reviewed">已有记录</option>
            </select>
            <button onClick={nextUnreviewed} disabled={!items.some(item => !item.latestReview)}>下一条未抽检</button>
          </div>
          <div className="training-results">{items.length} 条结果</div>
          <div className="training-cards">
            {items.map(item => {
              const key = `${item.kind}:${item.objectId}`
              const title = item.kind === 'skill' ? item.manifest.title : item.title
              return <button key={key} className={`training-card${selectedKey === key ? ' on' : ''}`} onClick={() => setSelectedKey(key)}>
                <b>{title}</b><code>{item.objectId}</code>
                <span className={`review-state ${item.latestReview?.decision ?? 'pending'}`}>
                  {item.latestReview ? decisionNames[item.latestReview.decision] : '待人工抽检'}
                </span>
              </button>
            })}
            {!items.length && <div className="training-empty">
              {domain === 'SCI-MED' ? '基础医学尚无证据卡。请先与合作人确认课题、真值来源和验收人，再建立首张卡。' : '当前筛选条件下没有候选。'}
            </div>}
          </div>
        </section>
        <main className="training-detail">
          {selected && <>
            <div className="training-detail-head">
              <div><span className="training-kind">{selected.kind === 'skill' ? '候选 Skill' : '外部发现参考'}</span><h2>{selected.kind === 'skill' ? selected.manifest.title : selected.title}</h2></div>
              <span className={`review-state ${selected.latestReview?.decision ?? 'pending'}`}>{selected.latestReview ? decisionNames[selected.latestReview.decision] : '待人工抽检'}</span>
            </div>
            <code className="training-id">{selected.objectId} · 版本 {selected.version}</code>
            {selected.kind === 'skill' ? <SkillDetail item={selected} /> : <DiscoveryDetail item={selected} />}
            <section className="review-editor">
              <h3>新增人工抽检记录</h3>
              <p>记录绑定当前内容哈希。候选变化后必须重新审核；历史记录不可覆盖。</p>
              <label>审核人<input value={reviewer} onChange={e => setReviewer(e.target.value)} placeholder="姓名 / 单位，例如：张老师 · 医学教研室" /></label>
              <label>抽检结论<select value={decision} onChange={e => setDecision(e.target.value as Review['decision'])}>
                <option value="sample_passed">抽检通过（仍不批准投产）</option>
                <option value="needs_evidence">退回补证</option><option value="needs_revision">退回修改</option>
              </select></label>
              <div className="review-checks">{Object.entries(checkNames).map(([key, name]) => <label key={key}>
                <input type="checkbox" checked={checks.includes(key)} onChange={e => setChecks(prev => e.target.checked ? [...prev, key] : prev.filter(x => x !== key))} />{name}
              </label>)}</div>
              <label>审核意见<textarea rows={3} value={comment} onChange={e => setComment(e.target.value)} placeholder={decision === 'sample_passed' ? '记录抽检范围、样本和仍需补充的事项' : '必填：指出缺少的证据或需要修改的内容'} /></label>
              <button className="accent" disabled={busy} onClick={() => void submitReview()}>{busy ? '保存中…' : '保存审核记录'}</button>
              {message && <div className="training-success">{message}</div>}
              {error && <div className="training-error">{error}</div>}
            </section>
            <section className="review-history">
              <h3>审核历史 ({selected.reviews.length})</h3>
              {selected.reviews.map(review => <div className="review-record" key={review.id}>
                <b>{decisionNames[review.decision]}</b><span>{review.reviewer} · {new Date(review.created_at).toLocaleString('zh-CN')}</span>
                {review.comment && <p>{review.comment}</p>}
                <small>{review.checks.map(key => checkNames[key]).filter(Boolean).join('；') || '未勾选检查项'}</small>
              </div>)}
              {!selected.reviews.length && <p className="muted">尚无人工抽检记录。</p>}
            </section>
          </>}
        </main>
      </div>
    </div>
  )
}

function SkillDetail({ item }: { item: SkillItem }) {
  const m = item.manifest
  return <div className="training-sections">
    <section><h3>用途与边界</h3><p>{m.description}</p><p><b>教学目的：</b>{item.spec.teachingGoal}</p>
      <div className="training-tags">{m.classification.labels.map(x => <span key={x}>{x}</span>)}</div>
      <p><b>可选路线：</b>{m.routeCandidates.join('、')}<br /><b>禁止路线：</b>{m.forbiddenRoutes.join('、')}</p>
    </section>
    <section><h3>来源 ({m.source.length})</h3>{m.source.map((source, index) => <div className="source-row" key={`${source.id}-${index}`}>
      <b>{source.locator}</b><code>{source.id}</code><span>{source.type} · 权利：{source.rights}</span>
    </div>)}</section>
    <section><h3>输入与验收</h3><p>{m.inputs.map(x => `${x.name}${x.unit ? `（${x.unit}）` : ''}${x.required ? ' · 必需' : ''}`).join('；')}</p>
      <ul>{item.qc.rules.map(rule => <li key={rule.id}><b>{rule.id}</b> {rule.check}</li>)}</ul>
      <p><b>观察点：</b>{item.spec.observations.join('；')}</p><p><b>不变量：</b>{item.spec.invariants.join('；')}</p>
    </section>
    <div className="training-warning">证据卡 {m.evidenceCards.length} 张；科学断言 {item.spec.assertions.length} 条；正样本 {m.qc.positiveSamples}、负样本 {m.qc.negativeSamples}。缺少的内容必须补齐，当前不得批准投产。</div>
  </div>
}

function DiscoveryDetail({ item }: { item: DiscoveryItem }) {
  return <div className="training-sections">
    <section><h3>发现信息</h3><p><b>{item.channel}</b></p><p>{item.summary}</p>
      <a href={item.url} target="_blank" rel="noreferrer">打开原始视频页面</a>
      <div className="training-tags">{item.categories.map(x => <span key={x.id}>{x.name}</span>)}</div>
    </section>
    <section><h3>使用边界</h3><p>{item.usage}</p><p><b>证据状态：</b>{item.evidenceStatus}<br /><b>权利状态：</b>{item.rights}</p></section>
    <div className="training-warning">标题、AI 摘要和社区标签只能帮助发现候选。须另找教材、标准、论文或实测证据，才能形成科学论断与 Skill。</div>
  </div>
}
