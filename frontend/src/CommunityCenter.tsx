import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { MarkdownView } from './DocumentView'
import { RepositoryBrowser, RepositoryImporter, type RepositorySummary } from './RepositoryCenter'

const BASE_PATH = import.meta.env.BASE_URL.replace(/\/$/, '')
const assetUrl = (url: string) => url.startsWith('/assets/') ? `${BASE_PATH}${url}` : url
const screenshotUrl = (url: string) => `${BASE_PATH}${url}`

type Community = {
  id: string; title: string; domain: string; description: string; goals: string
  current_version: number; member_count: number; contribution_count: number
  open_task_count: number; my_role: string; updated_at: string
}
type Member = { id: string; actor_id: string; display_name: string; role: string; joined_at: string }
type Asset = { id: string; kind: string; url: string; folder: string; rights: string; meta: { orig_name?: string; relativePath?: string } }
type Contribution = {
  id: string; community_id: string; kind: string; title: string; body: string; status: string
  version: number; author_id: string; author_name: string; comment_count: number
  asset?: Asset; repository?: RepositorySummary; bound_project_id?: string; bound_node_id?: string; updated_at: string
}
type Screenshot = { id: string; url: string; orig_name: string; size: number; analysis_status: string; analysis_text: string; analysis_model: string; analysis_error: string }
type Comment = { id: string; parent_id?: string; body: string; author_id: string; author_name: string; created_at: string; anchor_path?: string; anchor_line?: number; screenshots?: Screenshot[] }
type Review = { id: string; decision: string; comment: string; reviewer_name: string; created_at: string }
type ContributionVersion = { id: string; version: number; body: string; note: string; author_name: string; created_at: string }
type Task = { id: string; title: string; description: string; kind: string; status: string; assignee_id?: string; assignee_name?: string; created_by: string }
type Release = { id: string; version: number; title: string; summary: string; contribution_ids: string[]; creator_name: string; created_at: string }
type Notice = { id: string; contribution_id?: string; kind: string; message: string; read_at?: string; created_at: string }
type CommunityDetail = { community: Community; members: Member[]; contributions: Contribution[]; tasks: Task[]; versions: Release[]; notifications: Notice[] }
type ContributionDetail = { contribution: Contribution; comments: Comment[]; reviews: Review[]; versions: ContributionVersion[] }

const statusLabels: Record<string, string> = {
  under_discussion: '讨论中', changes_requested: '待修改', approved_for_production: '审校通过',
  bound_to_scene: '已接驳场景', open: '待认领', in_progress: '进行中', review: '待验收', done: '已完成',
}
const kindLabels: Record<string, string> = { text: '文本', question: '问题', evidence: '证据', scene: '场景', asset: '素材', repository: '代码仓库' }
const roleLabels: Record<string, string> = { host: '主持人', reviewer: '学科审校', technical: '技术团队', teacher: '共建教师' }
const time = (value: string) => new Date(value).toLocaleString()

export default function CommunityCenter({ authRole, initialCommunityId, onBack }: {
  authRole: 'admin' | 'invite'; initialCommunityId?: string; onBack: () => void
}) {
  const [communities, setCommunities] = useState<Community[]>([])
  const [detail, setDetail] = useState<CommunityDetail | null>(null)
  const [selected, setSelected] = useState<ContributionDetail | null>(null)
  const [tab, setTab] = useState<'contributions' | 'tasks' | 'members' | 'versions'>('contributions')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [communityForm, setCommunityForm] = useState({ title: '', domain: '', description: '', goals: '' })
  const [contributionForm, setContributionForm] = useState({ kind: 'text', title: '', body: '' })
  const [comment, setComment] = useState('')
  const [parentId, setParentId] = useState('')
  const [commentAnchor, setCommentAnchor] = useState<{ path: string; line: number } | null>(null)
  const [commentScreenshots, setCommentScreenshots] = useState<Screenshot[]>([])
  const [screenshotBusy, setScreenshotBusy] = useState(false)
  const [analyzingScreenshot, setAnalyzingScreenshot] = useState('')
  const [reviewComment, setReviewComment] = useState('')
  const [revision, setRevision] = useState({ body: '', note: '' })
  const [taskForm, setTaskForm] = useState({ title: '', description: '', kind: 'review' })
  const [releaseForm, setReleaseForm] = useState({ title: '', summary: '' })
  const [projects, setProjects] = useState<{ id: string; title: string }[]>([])
  const [nodes, setNodes] = useState<{ id: string; title: string; type: string }[]>([])
  const [binding, setBinding] = useState({ project_id: '', node_id: '' })
  const revisionFile = useRef<HTMLInputElement>(null)

  const loadCommunities = useCallback(async () => {
    const rows = await api.listCommunities() as Community[]
    setCommunities(rows)
    return rows
  }, [])

  const openContribution = useCallback(async (communityId: string, contributionId: string) => {
    setSelected(await api.contributionDetail(communityId, contributionId) as ContributionDetail)
    setComment(''); setParentId(''); setCommentAnchor(null); setCommentScreenshots([]); setReviewComment('')
  }, [])

  const openCommunity = useCallback(async (communityId: string, contributionId?: string) => {
    setBusy(true); setError('')
    try {
      const data = await api.communityDetail(communityId) as CommunityDetail
      setDetail(data)
      if (contributionId) await openContribution(communityId, contributionId)
      else setSelected(null)
    } catch (e) { setError(String(e).replace('Error: ', '')) }
    finally { setBusy(false) }
  }, [openContribution])

  useEffect(() => {
    void loadCommunities().then(rows => {
      if (initialCommunityId && rows.some(row => row.id === initialCommunityId)) void openCommunity(initialCommunityId)
    }).catch(e => setError(String(e).replace('Error: ', '')))
  }, [initialCommunityId, loadCommunities, openCommunity])

  const refresh = async (contributionId?: string) => {
    if (!detail) return
    const data = await api.communityDetail(detail.community.id) as CommunityDetail
    setDetail(data)
    const id = contributionId || selected?.contribution.id
    if (id) await openContribution(detail.community.id, id)
    await loadCommunities()
  }

  const run = async (action: () => Promise<unknown>, contributionId?: string) => {
    if (busy) return
    setBusy(true); setError('')
    try { await action(); await refresh(contributionId) }
    catch (e) { setError(String(e).replace('Error: ', '')) }
    finally { setBusy(false) }
  }

  const createCommunity = async () => {
    setBusy(true); setError('')
    try {
      const row = await api.createCommunity(communityForm) as Community
      setCommunityForm({ title: '', domain: '', description: '', goals: '' }); setCreateOpen(false)
      await loadCommunities(); await openCommunity(row.id)
    } catch (e) { setError(String(e).replace('Error: ', '')) }
    finally { setBusy(false) }
  }

  const loadProjects = async () => {
    const rows = await api.listProjects() as { id: string; title: string }[]
    setProjects(rows)
    if (rows.length && !binding.project_id) await chooseProject(rows[0].id)
  }

  const chooseProject = async (projectId: string) => {
    setBinding({ project_id: projectId, node_id: '' })
    if (!projectId) { setNodes([]); return }
    const graph = await api.getGraph(projectId) as { nodes: { id: string; title: string; type: string }[] }
    setNodes(graph.nodes)
  }

  const uploadRevision = async (file: File) => {
    if (!detail || !selected?.contribution.asset) return
    await run(() => api.uploadToLibrary(file, {
      folder: selected.contribution.asset!.folder,
      rights: selected.contribution.asset!.rights || 'own', note: revision.note || '提交社区贡献新版本',
      communityId: detail.community.id, contributionId: selected.contribution.id,
    }), selected.contribution.id)
    setRevision({ body: '', note: '' })
  }

  const uploadScreenshot = async (file: File): Promise<Screenshot | null> => {
    if (!detail || !selected) return null
    if (file.size > 8 * 1024 * 1024) { setError('单张截图不能超过 8MB'); return null }
    setScreenshotBusy(true); setError('')
    try {
      const row = await api.uploadCommentScreenshot(detail.community.id, selected.contribution.id, file) as Screenshot
      setCommentScreenshots(previous => [...previous, row])
      return row
    } catch (e) { setError(String(e).replace('Error: ', '')); return null }
    finally { setScreenshotBusy(false) }
  }

  const analyzeScreenshot = async (screenshotId: string) => {
    setAnalyzingScreenshot(screenshotId); setError('')
    try {
      const row = await api.analyzeCommentScreenshot(screenshotId, comment) as Screenshot
      setCommentScreenshots(previous => previous.map(item => item.id === screenshotId ? row : item))
    } catch (e) {
      setError(String(e).replace('Error: ', ''))
      const refreshed = await api.contributionDetail(detail!.community.id, selected!.contribution.id) as ContributionDetail
      setSelected(refreshed)
    } finally { setAnalyzingScreenshot('') }
  }

  const pasteScreenshot = async (file: File) => {
    const row = await uploadScreenshot(file)
    if (row) await analyzeScreenshot(row.id)
  }

  const myRole = detail?.community.my_role || ''
  const canReview = authRole === 'admin' || ['host', 'reviewer'].includes(myRole)
  const canManage = authRole === 'admin' || myRole === 'host'
  const canBind = authRole === 'admin' || ['host', 'reviewer', 'technical'].includes(myRole)
  const unread = detail?.notifications.filter(item => !item.read_at).length || 0
  const filtered = detail?.contributions.filter(item => `${item.title} ${item.body} ${item.author_name}`.toLowerCase().includes(query.toLowerCase())) || []

  if (!detail) return <div className="community-page">
    <header className="community-hero">
      <button onClick={onBack}>← 返回首页</button>
      <div><img src={`${BASE_PATH}/jojo-director-mark.svg`} alt="" /><span>JOJO Director · 教师共建</span></div>
      <h1>课题共建社区</h1>
      <p>上传只是开始。围绕同一个课题讨论、认领任务、学科审校、接驳场景并发布共同版本。</p>
    </header>
    <main className="community-main">
      <div className="community-section-head"><h2>正在共建的课题</h2>{authRole === 'admin' && <button onClick={() => setCreateOpen(value => !value)}>新建课题社区</button>}</div>
      {createOpen && <section className="community-form-card">
        <input placeholder="课题名称，如 CWC能力地图课程重构" value={communityForm.title} onChange={e => setCommunityForm({ ...communityForm, title: e.target.value })} />
        <input placeholder="学科分类，如 人工智能（AI）" value={communityForm.domain} onChange={e => setCommunityForm({ ...communityForm, domain: e.target.value })} />
        <textarea placeholder="社区说明与当前需要解决的问题" value={communityForm.description} onChange={e => setCommunityForm({ ...communityForm, description: e.target.value })} />
        <textarea placeholder="共建目标与验收标准" value={communityForm.goals} onChange={e => setCommunityForm({ ...communityForm, goals: e.target.value })} />
        <button disabled={busy || !communityForm.title.trim()} onClick={() => void createCommunity()}>创建并进入社区</button>
      </section>}
      {error && <div className="community-error">{error}</div>}
      {!communities.length && <div className="community-empty">还没有课题社区。管理员创建后，受邀老师即可加入共建。</div>}
      <div className="community-grid">{communities.map(item => <button className="community-card" key={item.id} onClick={() => void openCommunity(item.id)}>
        <span className="community-domain">{item.domain || '跨学科课题'}</span>
        <h3>{item.title}</h3><p>{item.description || '共同建设课程内容与教学场景。'}</p>
        <div><span>{item.member_count} 位成员</span><span>{item.contribution_count} 项贡献</span><span>{item.open_task_count} 项待办</span></div>
        <b>{item.current_version ? `当前 V${item.current_version}` : '尚未发布版本'} · 进入共建 →</b>
      </button>)}</div>
    </main>
  </div>

  const { community } = detail
  return <div className="community-page">
    <header className="community-topic-head">
      <div className="community-breadcrumb"><button onClick={() => { setDetail(null); setSelected(null); void loadCommunities() }}>课题社区</button><span>/</span><b>{community.title}</b></div>
      <button onClick={onBack}>返回首页</button>
      <div className="community-title-row"><div><span className="community-domain">{community.domain || '跨学科课题'}</span><h1>{community.title}</h1><p>{community.description}</p></div>
        {!myRole && <button onClick={() => void run(() => api.joinCommunity(community.id))}>加入共建</button>}</div>
      <div className="community-goals"><b>共建目标</b><span>{community.goals || '通过讨论与学科审校，形成可用于教学生产的课题版本。'}</span></div>
      <div className="community-stats"><span>{community.member_count} 位成员</span><span>{community.contribution_count} 项贡献</span><span>{community.open_task_count} 项待办</span><span>当前版本 V{community.current_version}</span>{myRole && <span>我的身份：{roleLabels[myRole]}</span>}</div>
    </header>
    <main className="community-workspace">
      <nav className="community-tabs">{([['contributions', '贡献与讨论'], ['tasks', '共建任务'], ['members', '成员与分工'], ['versions', '课题版本']] as const).map(([key, label]) => <button className={tab === key ? 'on' : ''} key={key} onClick={() => setTab(key)}>{label}</button>)}
        <button className={unread ? 'community-notice unread' : 'community-notice'} onClick={() => void run(() => api.readCommunityNotifications(community.id))}>通知 {unread ? `(${unread})` : ''}</button>
      </nav>
      {error && <div className="community-error">{error}</div>}
      {unread > 0 && <div className="community-notices">{detail.notifications.filter(item => !item.read_at).slice(0, 4).map(item => <button key={item.id} onClick={() => { setTab('contributions'); if (item.contribution_id) void openContribution(community.id, item.contribution_id) }}>{item.message}<small>{time(item.created_at)}</small></button>)}</div>}

      {tab === 'contributions' && <div className="community-contribution-layout">
        <aside className="community-feed">
          <div className="community-section-head"><h2>共建动态</h2><span>{filtered.length} 项</span></div>
          <input className="community-search" placeholder="搜索贡献、内容或老师" value={query} onChange={e => setQuery(e.target.value)} />
          {myRole && <details className="community-composer"><summary>发起一项贡献或问题</summary>
            <select value={contributionForm.kind} onChange={e => setContributionForm({ ...contributionForm, kind: e.target.value })}><option value="text">文本与讲稿</option><option value="question">问题讨论</option><option value="evidence">教学证据</option><option value="scene">场景设计</option></select>
            <input placeholder="标题" value={contributionForm.title} onChange={e => setContributionForm({ ...contributionForm, title: e.target.value })} />
            <textarea placeholder="具体内容、需要讨论的问题或教学依据" value={contributionForm.body} onChange={e => setContributionForm({ ...contributionForm, body: e.target.value })} />
            <button disabled={!contributionForm.title.trim()} onClick={() => void run(async () => {
              const row = await api.createContribution(community.id, contributionForm) as Contribution
              setContributionForm({ kind: 'text', title: '', body: '' }); await openContribution(community.id, row.id)
            })}>提交到社区讨论</button>
          </details>}
          {myRole && <RepositoryImporter communityId={community.id} onImported={async contributionId => { setTab('contributions'); await refresh(contributionId) }} />}
          <div className="community-feed-list">{filtered.map(item => <button className={selected?.contribution.id === item.id ? 'community-feed-item on' : 'community-feed-item'} key={item.id} onClick={() => void openContribution(community.id, item.id)}>
            <span><i>{kindLabels[item.kind]}</i><em className={`status ${item.status}`}>{statusLabels[item.status]}</em></span>
            <b>{item.title}</b><p>{item.body || item.asset?.meta.relativePath || '查看贡献内容与讨论'}</p>
            <small>{item.author_name} · V{item.version} · {item.comment_count} 条回复</small>
          </button>)}</div>
        </aside>
        <section className="community-detail-pane">{selected ? <ContributionView detail={selected} myRole={myRole} canReview={canReview} canBind={canBind}
          comment={comment} setComment={setComment} parentId={parentId} setParentId={setParentId}
          commentAnchor={commentAnchor} setCommentAnchor={setCommentAnchor}
          commentScreenshots={commentScreenshots} setCommentScreenshots={setCommentScreenshots}
          screenshotBusy={screenshotBusy} analyzingScreenshot={analyzingScreenshot}
          pasteScreenshot={pasteScreenshot} analyzeScreenshot={analyzeScreenshot} setError={setError}
          reviewComment={reviewComment} setReviewComment={setReviewComment} revision={revision} setRevision={setRevision}
          revisionFile={revisionFile} uploadRevision={uploadRevision}
          addComment={() => void run(() => api.addContributionComment(community.id, selected.contribution.id, {
            body: comment, parent_id: parentId, anchor_path: commentAnchor?.path || '', anchor_line: commentAnchor?.line || 0,
            screenshot_ids: commentScreenshots.map(item => item.id),
          }), selected.contribution.id)}
          review={(decision: string) => void run(() => api.reviewContribution(community.id, selected.contribution.id, { decision, comment: reviewComment }), selected.contribution.id)}
          revise={() => void run(() => api.reviseContribution(community.id, selected.contribution.id, revision), selected.contribution.id)}
          projects={projects} nodes={nodes} binding={binding} setBinding={setBinding} loadProjects={loadProjects} chooseProject={chooseProject}
          bind={() => void run(() => api.bindContribution(community.id, selected.contribution.id, binding), selected.contribution.id)}
        /> : <div className="community-empty">选择左侧贡献，查看文件、讨论、审校、修订和场景接驳记录。<br />老师上传素材后，合作就从这里继续。</div>}</section>
      </div>}

      {tab === 'tasks' && <section className="community-panel">
        <div className="community-section-head"><h2>共建任务</h2><span>认领一项具体工作，推动课题向前走</span></div>
        {canReview && <div className="community-inline-form"><input placeholder="任务标题，如 复核 L02 讲稿" value={taskForm.title} onChange={e => setTaskForm({ ...taskForm, title: e.target.value })} /><input placeholder="任务要求与验收标准" value={taskForm.description} onChange={e => setTaskForm({ ...taskForm, description: e.target.value })} /><select value={taskForm.kind} onChange={e => setTaskForm({ ...taskForm, kind: e.target.value })}><option value="review">内容审校</option><option value="evidence">补充证据</option><option value="recording">实验录屏</option><option value="scene">场景设计</option></select><button disabled={!taskForm.title.trim()} onClick={() => void run(async () => { await api.createCommunityTask(community.id, taskForm); setTaskForm({ title: '', description: '', kind: 'review' }) })}>发布任务</button></div>}
        <div className="community-task-grid">{detail.tasks.map(task => <article className="community-task" key={task.id}><span className={`status ${task.status}`}>{statusLabels[task.status]}</span><h3>{task.title}</h3><p>{task.description}</p><small>{task.assignee_name ? `认领人：${task.assignee_name}` : '等待老师认领'}</small><div>
          {task.status === 'open' && myRole && <button onClick={() => void run(() => api.communityTaskAction(community.id, task.id, 'claim'))}>认领任务</button>}
          {task.status === 'in_progress' && <button onClick={() => void run(() => api.communityTaskAction(community.id, task.id, 'complete'))}>提交验收</button>}
          {task.status === 'review' && canReview && <button onClick={() => void run(() => api.communityTaskAction(community.id, task.id, 'done'))}>确认完成</button>}
          {task.status !== 'open' && canReview && <button className="secondary" onClick={() => void run(() => api.communityTaskAction(community.id, task.id, 'reopen'))}>重开</button>}
        </div></article>)}</div>
      </section>}

      {tab === 'members' && <section className="community-panel"><div className="community-section-head"><h2>成员与分工</h2><span>专业审校与技术接驳分别留痕</span></div><div className="community-member-list">{detail.members.map(member => <div key={member.id}><img src={`${BASE_PATH}/jojo-director-mark.svg`} alt="" /><span><b>{member.display_name}</b><small>{roleLabels[member.role]} · {time(member.joined_at)} 加入</small></span>{canManage && member.role !== 'host' && <select value={member.role} onChange={e => void run(() => api.setCommunityMemberRole(community.id, member.id, e.target.value))}><option value="teacher">共建教师</option><option value="reviewer">学科审校</option><option value="technical">技术团队</option></select>}</div>)}</div></section>}

      {tab === 'versions' && <section className="community-panel"><div className="community-section-head"><h2>课题版本</h2><span>版本只收录已经通过学科审校的贡献</span></div>
        {canManage && <div className="community-inline-form"><input placeholder="版本标题，如 首轮讲稿共建" value={releaseForm.title} onChange={e => setReleaseForm({ ...releaseForm, title: e.target.value })} /><input placeholder="本版变化与验收说明" value={releaseForm.summary} onChange={e => setReleaseForm({ ...releaseForm, summary: e.target.value })} /><button disabled={!releaseForm.title.trim()} onClick={() => void run(async () => { await api.publishCommunityVersion(community.id, releaseForm); setReleaseForm({ title: '', summary: '' }) })}>发布新版本</button></div>}
        {detail.versions.map(release => <article className="community-release" key={release.id}><b>V{release.version} · {release.title}</b><p>{release.summary}</p><span>{release.contribution_ids.length} 项已审校贡献 · {release.creator_name} · {time(release.created_at)}</span></article>)}
        {!detail.versions.length && <div className="community-empty">贡献通过审校后，主持人即可发布第一个共同版本。</div>}
      </section>}
    </main>
  </div>
}

function AssetPreview({ asset, title }: { asset: Asset; title: string }) {
  const extension = asset.url.split('.').pop()?.toLowerCase() || ''
  const readable = ['docx', 'md', 'txt', 'csv'].includes(extension)
  const [data, setData] = useState<{ text: string; format: 'markdown' | 'text'; truncated: boolean } | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    setData(null); setError('')
    if (!readable) return
    setLoading(true)
    api.assetText(asset.id).then(setData).catch(e => setError(String(e).replace('Error: ', ''))).finally(() => setLoading(false))
  }, [asset.id, readable])
  if (asset.kind === 'image') return <img src={assetUrl(asset.url)} alt={title} />
  if (asset.kind === 'video') return <video src={assetUrl(asset.url)} controls />
  if (extension === 'pdf') return <div className="asset-inline-document"><div className="asset-preview-head"><b>本页 PDF 预览</b><a href={assetUrl(asset.url)} download>下载原文件</a></div><iframe className="asset-pdf-frame" src={assetUrl(asset.url)} title={`${title} PDF 本页预览`} /></div>
  if (!readable) return <a href={assetUrl(asset.url)} download>下载原始文件</a>
  return <div className="asset-inline-document"><div className="asset-preview-head"><b>{extension === 'docx' ? 'Word 正文本页预览' : '文档本页预览'}</b><a href={assetUrl(asset.url)} download>下载原文件</a></div>
    {loading && <div className="community-empty">正在解析并读取文档…</div>}
    {error && <div className="community-error">{error}</div>}
    {data && <div className="asset-readable-content">{data.format === 'markdown' ? <MarkdownView source={data.text} /> : <pre>{data.text}</pre>}</div>}
    {data?.truncated && <div className="asset-preview-note">当前仅显示前 120000 个字符，完整内容请下载原文件。</div>}
  </div>
}

function ContributionView({ detail, canReview, canBind, comment, setComment, parentId, setParentId,
  commentAnchor, setCommentAnchor, commentScreenshots, setCommentScreenshots, screenshotBusy,
  analyzingScreenshot, pasteScreenshot, analyzeScreenshot, setError,
  reviewComment, setReviewComment, revision, setRevision, revisionFile, uploadRevision, addComment, review, revise,
  projects, nodes, binding, setBinding, loadProjects, chooseProject, bind }: {
  detail: ContributionDetail; myRole: string; canReview: boolean; canBind: boolean
  comment: string; setComment: (value: string) => void; parentId: string; setParentId: (value: string) => void
  commentAnchor: { path: string; line: number } | null
  setCommentAnchor: (value: { path: string; line: number } | null) => void
  commentScreenshots: Screenshot[]; setCommentScreenshots: (value: Screenshot[]) => void
  screenshotBusy: boolean; analyzingScreenshot: string
  pasteScreenshot: (file: File) => Promise<void>; analyzeScreenshot: (id: string) => Promise<void>
  setError: (value: string) => void
  reviewComment: string; setReviewComment: (value: string) => void
  revision: { body: string; note: string }; setRevision: (value: { body: string; note: string }) => void
  revisionFile: React.RefObject<HTMLInputElement | null>; uploadRevision: (file: File) => Promise<void>
  addComment: () => void; review: (decision: string) => void; revise: () => void
  projects: { id: string; title: string }[]; nodes: { id: string; title: string; type: string }[]
  binding: { project_id: string; node_id: string }; setBinding: (value: { project_id: string; node_id: string }) => void
  loadProjects: () => Promise<void>; chooseProject: (id: string) => Promise<void>; bind: () => void
}) {
  const { contribution, comments, reviews, versions } = detail
  return <div className="contribution-view">
    <div className="contribution-title"><span>{kindLabels[contribution.kind]} · V{contribution.version}</span><h2>{contribution.title}</h2><p>{contribution.author_name} · {time(contribution.updated_at)}</p><b className={`status ${contribution.status}`}>{statusLabels[contribution.status]}</b></div>
    {contribution.repository && <RepositoryBrowser repository={contribution.repository} onDiscuss={(path, line) => {
      setCommentAnchor({ path, line }); setComment(`关于 ${path} 第 ${line} 行：\n`)
      setTimeout(() => document.getElementById('contribution-comment-input')?.focus(), 0)
    }} />}
    {contribution.asset && <div className="contribution-asset"><AssetPreview asset={contribution.asset} title={contribution.title} /><small>{contribution.asset.meta.relativePath || contribution.asset.meta.orig_name} · {contribution.asset.folder}</small></div>}
    {contribution.body && <div className="contribution-body">{contribution.body}</div>}
    {contribution.bound_node_id && <div className="contribution-binding">已接驳项目 {contribution.bound_project_id} · 场景 {contribution.bound_node_id}</div>}
    <section className="contribution-section"><h3>讨论与协作</h3>{comments.map(item => <div className={item.parent_id ? 'contribution-comment reply' : 'contribution-comment'} key={item.id}><b>{item.author_name}</b><small>{time(item.created_at)}</small>{item.anchor_path && <span className="comment-anchor">{item.anchor_path}{item.anchor_line ? ` : ${item.anchor_line}` : ''}</span>}<p>{item.body}</p>{item.screenshots?.map(shot => <div className="comment-screenshot" key={shot.id}><img src={screenshotUrl(shot.url)} alt={shot.orig_name} />{shot.analysis_text && <details><summary>查看 AI 截图分析{shot.analysis_model ? ` · ${shot.analysis_model}` : ''}</summary><p>{shot.analysis_text}</p><small>AI 辅助分析可能误判，请结合原图和教学依据核对。</small></details>}</div>)}<button onClick={() => { setParentId(item.id); setComment(`回复 ${item.author_name}：`) }}>回复</button></div>)}
      {commentAnchor && <div className="comment-anchor active">评论位置：{commentAnchor.path} : {commentAnchor.line}<button onClick={() => setCommentAnchor(null)}>取消定位</button></div>}
      <textarea id="contribution-comment-input" placeholder={parentId ? '回复选中的讨论' : '输入评论，或直接 Ctrl+V 粘贴截图并自动分析'} value={comment} onChange={e => setComment(e.target.value)}
        onPaste={e => {
          const images = Array.from(e.clipboardData.files).filter(file => file.type.startsWith('image/'))
          if (!images.length) return
          e.preventDefault()
          if (images.length > 1) { setError('每次请粘贴一张截图'); return }
          if (screenshotBusy || analyzingScreenshot) { setError('当前截图仍在处理，请完成后再粘贴下一张'); return }
          void pasteScreenshot(images[0])
        }} />
      <div className="comment-screenshot-composer">
        <span>{screenshotBusy ? '正在上传粘贴的截图…' : analyzingScreenshot ? '正在自动分析截图，您可以继续输入评论…' : '可直接 Ctrl+V 粘贴截图，自动上传并分析（可能产生少量费用）；也可以直接输入评论。'}</span>
        {commentScreenshots.map(shot => <div className="comment-screenshot-draft" key={shot.id}>
          <img src={screenshotUrl(shot.url)} alt={shot.orig_name} />
          <div className="comment-screenshot-draft-actions"><button disabled={analyzingScreenshot === shot.id || shot.analysis_status === 'analyzing'} onClick={() => void analyzeScreenshot(shot.id)}>{analyzingScreenshot === shot.id || shot.analysis_status === 'analyzing' ? '正在分析…' : shot.analysis_text ? '重新分析截图' : '分析截图'}</button><button className="secondary" onClick={() => setCommentScreenshots(commentScreenshots.filter(item => item.id !== shot.id))}>移除</button></div>
          {shot.analysis_error && <small className="screenshot-error">{shot.analysis_error}</small>}
          {shot.analysis_text && <div className="screenshot-analysis"><b>AI 辅助分析{shot.analysis_model ? ` · ${shot.analysis_model}` : ''}</b><p>{shot.analysis_text}</p><small>请结合原图和教学依据核对，不作为自动审校结论。</small></div>}
        </div>)}
      </div>
      <div className="contribution-actions">{parentId && <button className="secondary" onClick={() => setParentId('')}>取消回复</button>}<button disabled={!comment.trim() && !commentScreenshots.length} onClick={addComment}>发表回复</button></div>
    </section>
    <section className="contribution-section"><h3>修订记录</h3>{versions.map(item => <div className="contribution-version" key={item.id}><b>V{item.version} · {item.author_name}</b><span>{item.note || '内容更新'} · {time(item.created_at)}</span></div>)}
      {contribution.kind !== 'repository' && (contribution.asset ? <><input placeholder="本次修订说明" value={revision.note} onChange={e => setRevision({ ...revision, note: e.target.value })} /><input ref={revisionFile} type="file" hidden accept=".mp4,.mov,.webm,.png,.jpg,.jpeg,.webp,.pdf,.docx" onChange={e => { const file = e.target.files?.[0]; if (file) void uploadRevision(file); e.target.value = '' }} /><button onClick={() => revisionFile.current?.click()}>上传文件新版本</button></> : <><textarea placeholder="修订后的完整内容" value={revision.body} onChange={e => setRevision({ ...revision, body: e.target.value })} /><input placeholder="修订说明" value={revision.note} onChange={e => setRevision({ ...revision, note: e.target.value })} /><button disabled={!revision.body.trim()} onClick={revise}>提交内容新版本</button></>)}
    </section>
    {canReview && <section className="contribution-section review"><h3>学科审校</h3>{reviews.map(item => <div className="contribution-review" key={item.id}><b>{item.reviewer_name} · {item.decision === 'approved' ? '通过' : '退回修改'}</b><p>{item.comment}</p><small>{time(item.created_at)}</small></div>)}<textarea placeholder="审校意见；退回修改时必填" value={reviewComment} onChange={e => setReviewComment(e.target.value)} /><div className="contribution-actions"><button onClick={() => review('approved')}>通过学科审校</button><button className="secondary" onClick={() => review('changes_requested')}>退回修改</button></div></section>}
    {canBind && ['approved_for_production', 'bound_to_scene'].includes(contribution.status) && <section className="contribution-section"><h3>接驳教学场景</h3><div className="contribution-binding-form"><select value={binding.project_id} onFocus={() => { if (!projects.length) void loadProjects() }} onChange={e => void chooseProject(e.target.value)}><option value="">选择项目</option>{projects.map(project => <option value={project.id} key={project.id}>{project.title}</option>)}</select><select value={binding.node_id} onChange={e => setBinding({ ...binding, node_id: e.target.value })}><option value="">选择场景节点</option>{nodes.map(node => <option value={node.id} key={node.id}>{node.title || node.type}</option>)}</select><button disabled={!binding.project_id || !binding.node_id} onClick={bind}>确认接驳</button></div></section>}
  </div>
}
