import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ReactFlow, Background, Controls, MiniMap, Handle, Position,
  applyNodeChanges, type Node, type Edge, type NodeChange,
  type Connection, type NodeProps, type ReactFlowInstance,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { api } from './api'
import TrainingLibrary from './TrainingLibrary'
import DocumentView from './DocumentView'
import BrandFilm from './BrandFilm'
import CommunityCenter from './CommunityCenter'
import ProviderConfig from './ProviderConfig'
import { localizedRoute, useI18n } from './I18n'
import { localizedContent } from './contentI18n'

const BASE_PATH = import.meta.env.BASE_URL.replace(/\/$/, '')
const assetUrl = (url: string) => url.startsWith('/assets/') ? `${BASE_PATH}${url}` : url
const publicAsset = (name: string) => `${BASE_PATH}/${name}`
const MAX_UPLOAD_FILES = 200
const MAX_UPLOAD_BYTES = 500 * 1024 * 1024
const UPLOAD_EXTENSIONS = new Set(['mp4', 'mov', 'webm', 'png', 'jpg', 'jpeg', 'webp', 'pdf', 'docx', 'txt', 'md', 'csv'])

type IngestionBatch = {
  batchId?: string; id?: string; status?: string
  results: { status: string; path: string; folder?: string; reason?: string }[]
  summary: { accepted: number; rejected: number; duplicate: number; receiptIncomplete?: boolean }
  reviews?: { id: string; action: string; comment: string; actor_role: string; created_at: string }[]
}

type TeachingProduction = {
  candidateId: string; version: number; status: string; videoUrl?: string
  subtitleAssetId?: string; unchangedSceneIds?: string[]
  missingEvidence?: { sceneId: string; reason: string }[]
}

type BNode = {
  id: string; type: string; title: string; status: string
  position_x: number; position_y: number
  inputs: Record<string, unknown>; outputs: Record<string, unknown>
}

type Field = {
  key: string; label: string; kind: 'textarea' | 'text' | 'number' | 'select'
  options?: string[]; placeholder?: string
}

const TYPE_META: Record<string, { label: string; icon: string; hint: string; fields: Field[] }> = {
  script: {
    label: '脚本', icon: '📄',
    hint: '输入知识点，生成分段口播脚本',
    fields: [
      { key: 'goal', label: '知识点 / 创作目标', kind: 'textarea', placeholder: '例：工业镜头焦距选型的三角平衡公式' },
      { key: 'duration', label: '目标时长（秒）', kind: 'number' },
    ],
  },
  storyboard: {
    label: '分镜', icon: '🎞',
    hint: '连接上游脚本节点后执行，自动拆分镜头并标注 AI 生成 / 代码渲染双路线。镜头数/总时长填了就是硬约束（每镜上限 10 秒是视频模型的物理限制）',
    fields: [
      { key: 'shot_count', label: '镜头数（0 = 自动决定）', kind: 'number' },
      { key: 'total_duration', label: '总时长秒（0 = 自动；每镜只能 5 或 10 秒）', kind: 'number' },
    ],
  },
  image: {
    label: '图像', icon: '🖼',
    hint: '生成关键帧。提示词留空时自动取上游分镜的镜头提示词；也可上传 TapNow / GPT Image 出的图',
    fields: [
      { key: 'prompt', label: '画面提示词（留空 = 自动取上游分镜）', kind: 'textarea', placeholder: '留空则取上游分镜第 N 镜的首帧提示词' },
      { key: 'edit_delta', label: '编辑式变化指令（尾帧推荐：以参考图为基准只写一句话变化，填了则忽略上面提示词）', kind: 'textarea', placeholder: '例：the signal flow lines glow slightly brighter with a subtle pulse' },
      { key: 'shot_index', label: '取上游第几镜', kind: 'number' },
      { key: 'size', label: '尺寸', kind: 'select', options: ['2560x1440', '1440x2560', '2048x2048'] },
    ],
  },
  video: {
    label: '视频', icon: '🎬',
    hint: '连接上游图像节点后，其成果自动作为首帧；生成约需 1-3 分钟',
    fields: [
      { key: 'prompt', label: '运镜 / 动作提示词', kind: 'textarea', placeholder: '例：Slow push-in shot, camera moves closer to the bottle cap' },
      { key: 'resolution', label: '分辨率（正式成片用 1080p）', kind: 'select', options: ['480p', '720p', '1080p'] },
      { key: 'duration', label: '时长（秒）', kind: 'select', options: ['3', '5', '10'] },
      { key: 'skip_pair_check', label: '跳过首尾帧配对预检（已人工确认两帧可用时选"是"）', kind: 'select', options: ['否', '是'] },
    ],
  },
  code_render: {
    label: '代码渲染', icon: '⚙',
    hint: '严格几何/波形动画：每帧按公式计算、内置物理断言，本地渲染零费用。适合原理演示镜头',
    fields: [
      { key: 'template', label: '模板', kind: 'select', options: ['lens_focus', 'pwm_waveform', 'spectrum_recipe', 'block_diagram', 'rotary_drill_station', 'michelson_fringes', 'michelson_lightpath'] },
    ],
  },
  compose: {
    label: '合成', icon: '🎞️',
    hint: '把上游所有视频节点按画布从左到右顺序拼接成完整微课，可烧录脚本台词字幕并混入 BGM。本地 ffmpeg，零费用',
    fields: [
      { key: 'burn_subtitles', label: '烧录中文字幕（取项目脚本台词）', kind: 'select', options: ['是', '否'] },
      { key: 'bgm', label: '背景音乐', kind: 'select', options: ['舒缓垫乐', '无'] },
    ],
  },
  qc: {
    label: '质检', icon: '🔍',
    hint: '科学性质检：抽帧后按领域规则包+分镜断言逐条裁决。接在图像/视频节点后面。未配置视觉裁判模型时转人工验收模式',
    fields: [
      { key: 'domain', label: '领域规则包', kind: 'select', options: ['optics', 'machine_vision', 'mechanics', 'kinematics', 'general'] },
      { key: 'shot_index', label: '对应分镜第几镜（取其断言）', kind: 'number' },
    ],
  },
  ref_video: {
    label: '参考视频', icon: '🎥',
    hint: '上传参考视频后执行：自动场景切分→每段抽关键帧+切片段→逐段生成"复刻卡"（含必须保真的科学事实断言）。然后点"按参考视频生成分镜"进入复刻产线',
    fields: [
      { key: 'domain', label: '学科（决定复刻卡提取重点与保真规则包）', kind: 'select', options: ['general', 'optics', 'machine_vision', 'mechanics', 'kinematics'] },
      { key: 'focus', label: '补充分析重点（可选）', kind: 'textarea', placeholder: '例：重点记录第二段的游标卡尺读数' },
    ],
  },
  tts: {
    label: '配音', icon: '🎙',
    hint: '解说文本→豆包语音合成（留空自动取项目脚本全部解说词）。拼接时自动作为主音轨混入，BGM 自动压低',
    fields: [
      { key: 'text', label: '配音文本（留空 = 自动取项目脚本解说）', kind: 'textarea' },
      { key: 'voice_type', label: '音色', kind: 'select', options: ['zh_female_vv_uranus_bigtts', 'zh_male_M392_conversation_wvae_bigtts'] },
      { key: 'speed_ratio', label: '语速（0.8-1.5，1=常速）', kind: 'number' },
    ],
  },
  enhance: {
    label: '素材增强', icon: '✂️',
    hint: 'R1 真实素材增强：对真实片段做慢放/区域特写/标注框/画中画，本地处理零 API 费、零幻觉——实验类微课的默认路线',
    fields: [
      { key: 'segment_index', label: '取上游参考视频第几段（连了参考视频时生效）', kind: 'number' },
      { key: 'own_material', label: '素材为自有/已授权（权利硬闸：非自有片段拼接时会被排除）', kind: 'select', options: ['否', '是'] },
      { key: 'slow_factor', label: '慢放倍数（1=原速，2=慢一倍）', kind: 'number' },
      { key: 'zoom_region', label: '特写区域 x,y,w,h（百分比，留空不裁）', kind: 'text', placeholder: '例：25,25,50,50 = 画面中央放大' },
      { key: 'label_box', label: '标注框 x,y,w,h（百分比，留空无框）', kind: 'text', placeholder: '例：40,30,20,25' },
      { key: 'label_text', label: '标注文字（顶部黄字，留空无）', kind: 'text', placeholder: '例：注意此处光斑变化' },
      { key: 'pip_url', label: '画中画素材URL（如代码渲染波形，留空无）', kind: 'text', placeholder: '/assets/xxx.mp4' },
      { key: 'caption', label: '字幕字卡（合成时烧录）', kind: 'text' },
    ],
  },
}

// code_render 各模板的专属参数（按 template 动态渲染）
const CR_FIELDS: Record<string, Field[]> = {
  lens_focus: [
    { key: 'focal_length', label: '焦距（场景单位）', kind: 'number' },
    { key: 'num_rays', label: '光线条数', kind: 'number' },
    { key: 'duration', label: '时长（秒）', kind: 'number' },
  ],
  pwm_waveform: [
    { key: 'duty', label: '占空比（%）', kind: 'number' },
    { key: 'duration', label: '时长（秒）', kind: 'number' },
  ],
  spectrum_recipe: [
    { key: 'stage', label: '生长阶段（标题用）', kind: 'text', placeholder: '例：育苗期' },
    { key: 'duty_blue', label: '蓝光 450nm 占空比（%）', kind: 'number' },
    { key: 'duty_red', label: '红光 660nm 占空比（%）', kind: 'number' },
    { key: 'duty_farred', label: '远红光 730nm 占空比（%）', kind: 'number' },
    { key: 'duration', label: '时长（秒）', kind: 'number' },
  ],
  block_diagram: [
    { key: 'duration', label: '时长（秒）', kind: 'number' },
  ],
  michelson_fringes: [
    { key: 'mode', label: '模式（expand=吐条纹 / contract=吞条纹）', kind: 'select', options: ['expand', 'contract'] },
    { key: 'duration', label: '时长（秒）', kind: 'number' },
  ],
  rotary_drill_station: [
    { key: 'phase', label: '演示段（cycle=四步循环 / interlock=安全联锁）', kind: 'select', options: ['cycle', 'interlock'] },
    { key: 'duration', label: '时长（秒）', kind: 'number' },
  ],
}
const CR_DEFAULTS: Record<string, object> = {
  lens_focus: { template: 'lens_focus', focal_length: 2.2, num_rays: 7, duration: 6 },
  pwm_waveform: { template: 'pwm_waveform', duty: 50, duration: 10 },
  spectrum_recipe: { template: 'spectrum_recipe', stage: '育苗期', duty_blue: 40, duty_red: 50, duty_farred: 10, duration: 11 },
  block_diagram: { template: 'block_diagram', duration: 12 },
  rotary_drill_station: { template: 'rotary_drill_station', phase: 'cycle', duration: 18 },
  michelson_fringes: { template: 'michelson_fringes', mode: 'expand', d_um: 20, delta_um: 1.5, duration: 10 },
}

function excerpt(v: unknown, n = 60): string {
  const s = typeof v === 'string' ? v : ''
  return s.length > n ? s.slice(0, n) + '…' : s
}

function JojoNode({ id, data, selected }: NodeProps) {
  const b = data.b as BNode
  const run = data.run as (nid: string) => void
  const del = data.del as (nid: string) => void
  const sel = data.sel as ((nid: string) => void) | undefined
  const meta = TYPE_META[b.type]
  const out = b.outputs || {}
  const promptText = excerpt(b.inputs?.goal ?? b.inputs?.prompt)
  return (
    <div className={`jojo-node${selected ? ' selected' : ''}`}>
      <Handle type="target" position={Position.Left} />
      <div className="head">
        <span className={`dot ${b.status}`} />
        <span className="node-title">{b.title || meta?.label || b.type}</span>
        <span className="type-badge">{meta?.icon} {meta?.label}</span>
        {typeof out.asset_url === 'string' && (
          <button className="run-btn nodrag" title="放大预览"
            onClick={e => {
              e.stopPropagation()
              ;(data.zoom as ((url: string, title: string) => void) | undefined)?.(
                out.asset_url as string, b.title || meta?.label || '')
            }}>🔍</button>
        )}
        <button
          className="run-btn nodrag"
          title="运行到此节点（上游未完成的会先跑）"
          disabled={b.status === 'running'}
          onClick={e => { e.stopPropagation(); run(id) }}
        >{b.status === 'running' ? '⏳' : '▶'}</button>
        <button
          className="del-btn nodrag" title="删除此节点"
          onClick={e => { e.stopPropagation(); del(id) }}
        >✕</button>
      </div>
      <div className="body">
        {promptText && <div className="prompt-line">{promptText}</div>}
        {b.status === 'failed' && (
          <div className="err">✖ {excerpt(out.error, 80) || '执行失败'}</div>
        )}
        {typeof out.asset_url === 'string' && !(out.asset_url as string).endsWith('.mp4') && (
          <img src={assetUrl(out.asset_url as string)} alt="" draggable={false} />
        )}
        {typeof out.asset_url === 'string' && (out.asset_url as string).endsWith('.mp4') && (
          <video src={assetUrl(out.asset_url as string)} muted loop autoPlay playsInline />
        )}
        {out.script != null && <div className="ok">📄 脚本已生成，点击查看</div>}
        {out.storyboard != null && <div className="ok">🎞 分镜已生成，点击查看</div>}
        {out.motion_card != null && <div className="ok">🎥 运动特征卡已提取</div>}
        {Array.isArray(out.segments) && (out.segments as unknown[]).length > 0 && (<div className="ok">🎬 已切分 {(out.segments as unknown[]).length} 段·复刻卡就绪</div>)}
        {b.type === 'qc' && typeof out.verdict === 'string' && (
          <div className={`qc-badge ${out.verdict}`}>
            {{ pass: '✅ 质检通过', reject: '⛔ 不合格', needs_human: '👁 待人工验收' }[out.verdict as string] ?? out.verdict}
            {out.human_override ? '（人工终裁）' : ''}
          </div>
        )}
        {b.type !== 'qc' && (out.qc as { verdict?: string; qc_node_id?: string } | undefined)?.verdict === 'reject' && (
          <div className="err clickable" title="点击查看质检报告（不合格原因与整改建议）"
            onClick={e => {
              e.stopPropagation()
              const qid = (out.qc as { qc_node_id?: string })?.qc_node_id
              if (qid && sel) sel(qid)
            }}>⛔ 质检不合格 · 点击看原因</div>
        )}
        {b.status === 'idle' && Object.keys(out).length === 0 && !promptText && (
          <div className="muted">点击节点，在右侧填写参数</div>
        )}
        {b.status === 'running' && (() => {
          const st = data.startedAt as number | undefined
          const secs = st ? Math.max(0, Math.round((Date.now() - st) / 1000)) : 0
          const t = secs >= 60 ? `${Math.floor(secs / 60)}分${secs % 60}秒` : `${secs}秒`
          const stage = b.type === 'video' ? '视频生成通常 2-5 分钟' : b.type === 'qc' ? '判卷中' : b.type === 'image' ? '出图中' : '处理中'
          return <div className="running-line">⏳ 生成中 · 已 {t}（{stage}）</div>
        })()}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  )
}

const nodeTypes = { jojo: JojoNode }

export default function App() {
  const { t, language } = useI18n()
  const [projectId, setProjectId] = useState('')
  const [projects, setProjects] = useState<{ id: string; title: string }[]>([])
  const [bnodes, setBnodes] = useState<Record<string, BNode>>({})
  const [rfNodes, setRfNodes] = useState<Node[]>([])
  const [rfEdges, setRfEdges] = useState<Edge[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const [toast, setToast] = useState('')
  const [leftW, setLeftW] = useState(() => Number(localStorage.getItem('jojo_left_width')) || 250)
  const [rightW, setRightW] = useState(() => Number(localStorage.getItem('jojo_right_width')) || 390)
  const [tab, setTab] = useState<'nodes' | 'assets'>('nodes')
  const [view, setView] = useState<'home' | 'canvas' | 'review' | 'training' | 'document' | 'communities'>('home')
  const [documentName, setDocumentName] = useState<'project' | 'guide'>('project')
  const [communityId, setCommunityId] = useState('')
  const [communityOptions, setCommunityOptions] = useState<{ id: string; title: string; my_role: string }[]>([])
  type ReviewItem = {
    qc_node_id: string; qc_title: string; target_node_id: string
    target_title: string; target_type: string; asset_url: string
    verdict: string; remediation: string; summary: string
    fails: { id: string; evidence: string }[]; suggested_prompt: string
  }
  type FailItem = { node_id: string; title: string; type: string
    error: string; error_class: string }
  const [review, setReview] = useState<{ reviews: ReviewItem[]; failures: FailItem[] }>(
    { reviews: [], failures: [] })
  const [reviewBusy, setReviewBusy] = useState('')
  const [thumbs, setThumbs] = useState<Record<string, string>>({})
  const [homeInput, setHomeInput] = useState('')
  const [homeBusy, setHomeBusy] = useState(false)
  const [yukeImportBusy, setYukeImportBusy] = useState(false)
  const [teachingImportBusy, setTeachingImportBusy] = useState(false)
  const [teachingConfirmBusy, setTeachingConfirmBusy] = useState(false)
  const [productionBusy, setProductionBusy] = useState(false)
  const [revisionScene, setRevisionScene] = useState<Record<string, unknown> | null>(null)
  const [revisionComment, setRevisionComment] = useState('')
  const [revisionSeconds, setRevisionSeconds] = useState(10)
  const [revisionRoute, setRevisionRoute] = useState('live_action')
  const [revisionNarration, setRevisionNarration] = useState('')
  const [selectedSceneId, setSelectedSceneId] = useState('')
  const [chat, setChat] = useState<{ role: string; text: string }[]>([])
  const [chatInput, setChatInput] = useState('')
  const [agentBusy, setAgentBusy] = useState(false)
  const [agentModels, setAgentModels] = useState<{ label: string; model: string }[]>([])
  const [agentModel, setAgentModel] = useState(localStorage.getItem('jojo_agent_model') ?? '')
  const [agentResearch, setAgentResearch] = useState(!!localStorage.getItem('jojo_agent_research'))
  const [assets, setAssets] = useState<{ id: string; kind: string; url: string; starred?: boolean; folder?: string; rights?: string; library?: boolean; created_at?: string }[]>([])
  const [assetScope, setAssetScope] = useState<'project' | 'starred' | 'library'>('project')
  // ── 素材库（工作流 A2）：文件夹树 + 过滤 + 搜索 ──
  const [libFolders, setLibFolders] = useState<string[]>([])
  const [libFolder, setLibFolder] = useState('')
  const [libKind, setLibKind] = useState('')
  const [libQ, setLibQ] = useState('')
  const [libStarOnly, setLibStarOnly] = useState(false)
  const [libCollapsed, setLibCollapsed] = useState<Record<string, boolean>>({})
  const [versionDlg, setVersionDlg] = useState<{ nid: string; list: { id: string; url: string; kind: string; created_at?: string }[] } | null>(null)
  // ── 教师投稿（2026-09-07）：文件直接入素材库 ──
  const [uploadOpen, setUploadOpen] = useState(false)
  const [upFile, setUpFile] = useState<File | null>(null)
  const [upFiles, setUpFiles] = useState<File[]>([])
  const [upReport, setUpReport] = useState<IngestionBatch | null>(null)
  const [recentBatches, setRecentBatches] = useState<IngestionBatch[]>([])
  const [upReviewComment, setUpReviewComment] = useState('')
  const [upDomain, setUpDomain] = useState('光学类')
  const [upDomainCustom, setUpDomainCustom] = useState('')
  const [upTopic, setUpTopic] = useState('')
  const [upRights, setUpRights] = useState('own')
  const [upNote, setUpNote] = useState('')
  const [upBusy, setUpBusy] = useState(false)
  const [upMsg, setUpMsg] = useState('')
  const [upCommunityId, setUpCommunityId] = useState('')
  const [upLastCommunityId, setUpLastCommunityId] = useState('')
  const [upProgress, setUpProgress] = useState<{ percent: number; label: string } | null>(null)
  const [upMoveFolder, setUpMoveFolder] = useState('')
  const [upSuggest, setUpSuggest] = useState<{ folder: string; score: number } | null>(null)
  // ── QC 改判沉淀：人工终裁与机器原判不一致时，收集类型+原因进学习库 ──
  const [overrideDlg, setOverrideDlg] = useState<{ nid: string; verdict: string; orig: string } | null>(null)
  const [corrFailType, setCorrFailType] = useState('误判')
  const [corrReason, setCorrReason] = useState('')
  const [menu, setMenu] = useState<{ x: number; y: number; id: string } | null>(null)
  // ── 鉴权 / 手感升级新状态 ──
  const [authRole, setAuthRole] = useState<'checking' | 'none' | 'admin' | 'invite'>('checking')
  const [authInfo, setAuthInfo] = useState<{ auth_enabled?: boolean; account?: string; limits?: { daily_video_limit: number; daily_cost_limit_cny: number }; usage_today?: { videos: number; cost: number } }>({})
  const [loginMode, setLoginMode] = useState<'admin' | 'invite'>('admin')
  const [loginU, setLoginU] = useState(''); const [loginP, setLoginP] = useState(''); const [loginCode, setLoginCode] = useState('')
  const [loginErr, setLoginErr] = useState(''); const [loginBusy, setLoginBusy] = useState(false)
  const [inviteOpen, setInviteOpen] = useState(false)
  const [providerOpen, setProviderOpen] = useState(false)
  const [accountOpen, setAccountOpen] = useState(false)
  const [invites, setInvites] = useState<{ code: string; label: string; daily_video_limit: number; daily_cost_limit_cny: number; disabled: number; usage_today?: { videos: number; cost: number } }[]>([])
  const [lightbox, setLightbox] = useState<{ url: string; title: string } | null>(null)
  const [connectMenu, setConnectMenu] = useState<{ x: number; y: number; fx: number; fy: number; src: string } | null>(null)
  const connectSrcRef = useRef<string | null>(null)
  const [reviewCount, setReviewCount] = useState(0)
  const [projCost, setProjCost] = useState<number | null>(null)
  const runStartRef = useRef<Record<string, number>>({})
  const flowRef = useRef<ReactFlowInstance | null>(null)
  const focusAll = () => setTimeout(() => flowRef.current?.fitView({ padding: 0.25, duration: 400 }), 80)
  const undoRef = useRef<{ nodes: BNode[]; edges: { id: string; source: string; target: string }[] }[]>([])
  const redoRef = useRef<typeof undoRef.current>([])
  const fileRef = useRef<HTMLInputElement>(null)
  const singleUploadRef = useRef<HTMLInputElement>(null)
  const folderUploadRef = useRef<HTMLInputElement>(null)
  const yukeFileRef = useRef<HTMLInputElement>(null)
  const teachingFileRef = useRef<HTMLInputElement>(null)
  const bnodesRef = useRef(bnodes)
  bnodesRef.current = bnodes
  const rfEdgesRef = useRef(rfEdges)
  rfEdgesRef.current = rfEdges
  const draftRef = useRef(draft)
  draftRef.current = draft
  const selectedIdRef = useRef(selectedId)
  selectedIdRef.current = selectedId

  // 拖动分隔条调整左右面板宽度
  const startResize = (side: 'left' | 'right') => (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    const startX = e.clientX
    const startL = leftW, startR = rightW
    const previousCursor = document.body.style.cursor
    const previousSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const move = (ev: PointerEvent) => {
      const delta = ev.clientX - startX
      if (side === 'left') {
        const max = Math.max(200, Math.min(520, window.innerWidth - startR - 420))
        const next = Math.min(max, Math.max(200, startL + delta))
        setLeftW(next)
        localStorage.setItem('jojo_left_width', String(next))
      } else {
        const max = Math.max(300, Math.min(720, window.innerWidth - startL - 420))
        const next = Math.min(max, Math.max(300, startR - delta))
        setRightW(next)
        localStorage.setItem('jojo_right_width', String(next))
      }
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', up)
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousSelect
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', up)
  }
  const resizeKeys = (side: 'left' | 'right') => (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    e.preventDefault()
    const delta = e.key === 'ArrowRight' ? 20 : -20
    if (side === 'left') {
      const next = Math.min(520, Math.max(200, leftW + delta))
      setLeftW(next)
      localStorage.setItem('jojo_left_width', String(next))
    } else {
      const next = Math.min(720, Math.max(300, rightW - delta))
      setRightW(next)
      localStorage.setItem('jojo_right_width', String(next))
    }
  }
  const resetWidths = () => {
    setLeftW(250); setRightW(390)
    localStorage.removeItem('jojo_left_width')
    localStorage.removeItem('jojo_right_width')
  }
  const adjustRightWidth = (delta: number) => {
    setRightW(previous => {
      const next = Math.min(720, Math.max(300, previous + delta))
      localStorage.setItem('jojo_right_width', String(next))
      return next
    })
  }

  const say = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 4000) }

  const syncGraph = useCallback(async (pid: string) => {
    if (!pid) return
    const g = await api.getGraph(pid)
    const map: Record<string, BNode> = {}
    for (const n of g.nodes as BNode[]) map[n.id] = n
    // 运行状态迁移：登记开跑时刻；完成/失败时弹提示（含耗时）并刷新成本
    let transitioned = false
    for (const n of g.nodes as BNode[]) {
      const prev = bnodesRef.current[n.id]
      if (n.status === 'running' && !runStartRef.current[n.id]) {
        runStartRef.current[n.id] = prev?.status === 'running' ? Date.now() : Date.now()
      }
      if (prev?.status === 'running' && n.status !== 'running') {
        const secs = Math.round((Date.now() - (runStartRef.current[n.id] ?? Date.now())) / 1000)
        const dur = secs >= 60 ? `${Math.floor(secs / 60)}分${secs % 60}秒` : `${secs}秒`
        say(n.status === 'succeeded'
          ? `✅ ${n.title || TYPE_META[n.type]?.label} 完成（${dur}）`
          : `❌ ${n.title || TYPE_META[n.type]?.label} 失败（${dur}）——节点上看原因`)
        delete runStartRef.current[n.id]
        transitioned = true
      }
    }
    if (transitioned) {
      api.projectStats(pid).then(s => setProjCost(s.total_cost_cny)).catch(() => {})
      api.reviewQueue(pid).then(r => setReviewCount((r.reviews?.length ?? 0) + (r.failures?.length ?? 0))).catch(() => {})
    }
    setBnodes(map)
    setRfNodes(prev => (g.nodes as BNode[]).map(n => {
      const old = prev.find(p => p.id === n.id)
      return {
        id: n.id, type: 'jojo',
        position: old?.position ?? { x: n.position_x, y: n.position_y },
        data: { b: n, run: runChain, del: delNode, sel: setSelectedId,
                zoom: (url: string, title: string) => setLightbox({ url, title }),
                startedAt: runStartRef.current[n.id] },
      }
    }))
    setRfEdges((g.edges as { id: string; source_node_id: string; target_node_id: string }[])
      .map(e => ({ id: e.id, source: e.source_node_id, target: e.target_node_id, animated: true, type: 'default' })))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const projectRef = useRef(projectId)
  projectRef.current = projectId

  const runChain = useCallback(async (nid: string) => {
    try {
      // 统一规则：执行前若目标节点正被选中编辑，先保存右侧未落盘的参数
      if (nid === selectedIdRef.current && Object.keys(draftRef.current).length) {
        await api.updateNode(nid, { inputs: draftRef.current })
      }
      await api.executeChain(nid)
      say('已开始执行（含未完成的上游节点）')
    } catch (e) { say(`执行失败: ${e}`) }
    await syncGraph(projectRef.current)
  }, [syncGraph])

  const snapshotForUndo = useCallback((nids: string[], edgeIds: string[] = []) => {
    const nodes = nids.map(id => bnodesRef.current[id]).filter(Boolean)
    const edges = rfEdgesRef.current
      .filter(e => edgeIds.includes(e.id) || nids.includes(e.source) || nids.includes(e.target))
      .map(e => ({ id: e.id, source: e.source, target: e.target }))
    if (nodes.length || edges.length) {
      undoRef.current.push({ nodes, edges })
      redoRef.current = []
    }
  }, [])

  const delNode = useCallback(async (nid: string) => {
    snapshotForUndo([nid])
    await api.deleteNode(nid)
    setSelectedId(s => (s === nid ? '' : s))
    setMenu(null)
    await syncGraph(projectRef.current)
    say('节点已删除（Ctrl+Z 可撤销）')
  }, [syncGraph, snapshotForUndo])

  const onEdgeDoubleClick = useCallback(async (_e: unknown, edge: Edge) => {
    await api.deleteEdge(edge.id)
    await syncGraph(projectRef.current)
    say('连线已删除')
  }, [syncGraph])

  // ── 鉴权：进门先验会话；任何 401 都会把用户送回登录页 ──
  useEffect(() => {
    const onNeedAuth = () => setAuthRole('none')
    window.addEventListener('jojo-auth-required', onNeedAuth)
    api.authMe()
      .then(me => { setAuthInfo(me); setAuthRole(me.role) })
      .catch(() => setAuthRole('none'))
    return () => window.removeEventListener('jojo-auth-required', onNeedAuth)
  }, [])

  const doLogin = async () => {
    if (loginBusy) return
    setLoginBusy(true); setLoginErr('')
    try {
      await api.authLogin(loginMode === 'admin'
        ? { username: loginU, password: loginP }
        : { invite_code: loginCode })
      const me = await api.authMe()
      setAuthInfo(me); setAuthRole(me.role)
    } catch (e) {
      setLoginErr(String(e).replace('Error: ', ''))
    } finally { setLoginBusy(false) }
  }

  const loadInvites = async () => setInvites(await api.adminInvites())

  useEffect(() => {
    if (authRole !== 'admin' && authRole !== 'invite') return
    (async () => {
      let list = await api.listProjects()
      if (!list.length) list = [await api.createProject('微课示例项目')]
      list = [...list].reverse()   // 最新项目在前
      const params = new URLSearchParams(window.location.search)
      const requestedPid = params.get('project')
      const requestedView = params.get('view')
      const initial = list.find((p: { id: string }) => p.id === requestedPid) ?? list[0]
      setProjects(list)
      setProjectId(initial.id)
      setChat(JSON.parse(localStorage.getItem(`jojo_chat_${initial.id}`) ?? '[]'))
      const models = await api.agentModels().catch(() => [])
      setAgentModels(models)
      api.listCommunities().then(setCommunityOptions).catch(() => {})
      if (models.length && !models.some((m: { model: string }) => m.model === localStorage.getItem('jojo_agent_model'))) {
        setAgentModel(models[0].model)
      }
      await syncGraph(initial.id)
      if (requestedView === 'training') setView('training')
      else if (initial.id === requestedPid) setView('canvas')
      api.projectStats(initial.id).then(s => setProjCost(s.total_cost_cny)).catch(() => {})
      api.reviewQueue(initial.id).then(r => setReviewCount((r.reviews?.length ?? 0) + (r.failures?.length ?? 0))).catch(() => {})
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syncGraph, authRole])

  const dupNode = useCallback(async (nid: string) => {
    const b = bnodesRef.current[nid]
    if (!b) return
    const c = await api.createNode(projectRef.current, {
      type: b.type, title: (b.title || TYPE_META[b.type]?.label) + ' 副本',
      inputs: b.inputs, position: { x: b.position_x + 60, y: b.position_y + 90 },
    })
    setMenu(null)
    await syncGraph(projectRef.current)
    setSelectedId(c.id)
    say('已复制节点')
  }, [syncGraph])

  const switchProject = async (pid: string) => {
    setSelectedSceneId('')
    setProjectId(pid)
    setSelectedId('')
    setRfNodes([])
    undoRef.current = []
    redoRef.current = []
    setChat(JSON.parse(localStorage.getItem(`jojo_chat_${pid}`) ?? '[]'))
    await syncGraph(pid)
    // 视口记忆：回到上次看的位置；没有记录则全景
    const saved = localStorage.getItem(`jojo_vp_${pid}`)
    if (saved) {
      try {
        const vp = JSON.parse(saved)
        setTimeout(() => flowRef.current?.setViewport(vp, { duration: 300 }), 150)
      } catch { focusAll() }
    } else {
      setTimeout(() => flowRef.current?.fitView({ padding: 0.2, duration: 400 }), 250)
    }
    api.projectStats(pid).then(s => setProjCost(s.total_cost_cny)).catch(() => setProjCost(null))
    api.reviewQueue(pid).then(r => setReviewCount((r.reviews?.length ?? 0) + (r.failures?.length ?? 0))).catch(() => {})
  }

  // ── Agent 对话（常驻右侧；首页大输入框也走这里） ──
  const sendAgent = async (msgArg?: string, pidArg?: string) => {
    const msg = (msgArg ?? chatInput).trim()
    const pid = pidArg ?? projectRef.current
    if (!msg || agentBusy) return
    setChatInput('')
    setAgentBusy(true)
    const next = [...chat, { role: 'user', text: msg }]
    setChat(next)
    try {
      const r = await api.agent(pid, msg, agentModel || undefined, agentResearch,
        next.slice(-9, -1))  // 带上最近的对话历史（不含本条，后端会拼接）
      const replyText = r.research
        ? `【联网调研】${String(r.research).slice(0, 400)}\n\n${r.reply}`
        : r.reply
      const done = [...next, { role: 'assistant', text: replyText }]
      setChat(done)
      localStorage.setItem(`jojo_chat_${pid}`, JSON.stringify(done.slice(-40)))
      await syncGraph(pid)
      // 镜头对准 Agent 新建的节点（而非全景），延时避开渲染竞态
      const ids: string[] = r.created_ids ?? []
      if (ids.length) {
        setTimeout(() => flowRef.current?.fitView({
          nodes: ids.map(id => ({ id })), padding: 0.35, duration: 400 }), 450)
      } else if (r.created > 0) focusAll()
      const parts = []
      if (r.created > 0) parts.push(`新建 ${r.created} 个节点（画布已对准）`)
      if (r.ran > 0) parts.push(`已开始执行 ${r.ran} 个，结果稍后显示在节点上`)
      say(parts.length ? parts.join('，') : 'Agent 已回复')
    } catch (e) {
      setChat(c => [...c, { role: 'assistant', text: `处理失败：${e}` }])
    } finally { setAgentBusy(false) }
  }

  // ── 首页：一句话开工 ──
  const [homeErr, setHomeErr] = useState('')
  const submitHome = async () => {
    const msg = homeInput.trim()
    if (!msg || homeBusy) return
    setHomeBusy(true)
    setHomeErr('')
    try {
      const title = msg.length > 18 ? msg.slice(0, 18) + '…' : msg
      const p = await api.createProject(title)
      setProjects(ps => [p, ...ps])
      await switchProject(p.id)
      setView('canvas')
      setHomeInput('')
      await sendAgent(msg, p.id)
    } catch (e) {
      setHomeErr(`开工失败：${e}。多半是后端服务没有运行——请双击 jojo-studio 文件夹里的 启动JOJO.bat`)
    } finally { setHomeBusy(false) }
  }

  const importYuke = async (file: File) => {
    if (yukeImportBusy) return
    setYukeImportBusy(true)
    setHomeErr('')
    try {
      const r = await api.importYuke(file)
      setProjects(await api.listProjects())
      await switchProject(r.project_id)
      setView('canvas')
      say(`已导入 ${r.shots} 镜，请先复核制作路线和教学断言；尚未发起付费生成`)
    } catch (e) {
      setHomeErr(`导入失败：${e}`)
    } finally {
      setYukeImportBusy(false)
      if (yukeFileRef.current) yukeFileRef.current.value = ''
    }
  }

  const importTeachingVideo = async (file: File) => {
    if (teachingImportBusy) return
    setTeachingImportBusy(true)
    setHomeErr('')
    try {
      const r = await api.importTeachingVideo(file)
      setProjects(await api.listProjects())
      await switchProject(r.project_id)
      setView('canvas')
      say(r.reused ? '该包已导入，已返回原任务，没有重复建项目'
        : `任务包已接收：${r.scenePlans.length} 个场景，请核对路线和待补项`)
    } catch (e) {
      setHomeErr(`教学视频包导入失败：${e}`)
    } finally {
      setTeachingImportBusy(false)
      if (teachingFileRef.current) teachingFileRef.current.value = ''
    }
  }

  const confirmTeachingPlan = async () => {
    const node = Object.values(bnodesRef.current).find(n => n.outputs?.teachingVideo)
    if (!node || teachingConfirmBusy) return
    const review = node.outputs.importReview as { importTaskId: string; packageHash: string }
    const data = node.outputs.teachingVideo as { scenes: { sceneId: string }[] }
    setTeachingConfirmBusy(true)
    try {
      await api.confirmTeachingVideo(projectRef.current, {
        importTaskId: review.importTaskId, packageHash: review.packageHash,
        sceneIds: data.scenes.map(s => s.sceneId),
        generativeMediaAuthorized: false, paidGenerationApproved: false,
      })
      await syncGraph(projectRef.current)
      say('方案已确认：仅确定性 2D 与已有素材，无需付费生成')
    } catch (e) {
      say(`方案确认失败：${e}`)
    } finally { setTeachingConfirmBusy(false) }
  }

  const produceTeachingVideo = async (revision?: boolean) => {
    const node = Object.values(bnodesRef.current).find(n => n.outputs?.teachingVideo)
    if (!node || productionBusy) return
    setProductionBusy(true)
    try {
      const production = revision ? (node.outputs.production as { candidateId: string }) : null
      await api.produceTeachingVideo(projectRef.current, revision ? {
        revisionOf: production!.candidateId,
        patch: {
          sceneId: revisionScene!.sceneId,
          comment: revisionComment,
          targetSeconds: revisionSeconds,
          selectedRoute: revisionRoute,
          narration: revisionNarration,
        },
      } : {})
      setRevisionScene(null)
      await syncGraph(projectRef.current)
      say(revision ? '已生成送修候选，原版本保留，其他场景不变' : '候选视频正在后台生成，不调用付费模型')
    } catch (e) {
      say(`候选生成失败：${e}`)
    } finally { setProductionBusy(false) }
  }

  // ── 后端健康监测：离线时全局红条提示 ──
  const [backendUp, setBackendUp] = useState(true)
  useEffect(() => {
    const check = () => fetch(`${BASE_PATH}/api/health`).then(r => setBackendUp(r.ok)).catch(() => setBackendUp(false))
    check()
    const t = setInterval(check, 8000)
    return () => clearInterval(t)
  }, [])
  const offlineBanner = !backendUp && (
    <div className="offline-banner">
      ⚠ {t('后端服务未运行，当前内容可查看，但生成和保存会失败。')}
    </div>
  )

  // 首页项目卡缩略图：取各项目最新图像资产
  useEffect(() => {
    if (view !== 'home' || !projects.length) return
    ;(async () => {
      const entries = await Promise.all(projects.slice(0, 12).map(async p => {
        try {
          const as = await api.listAssets(p.id) as { kind: string; url: string }[]
          const img = [...as].reverse().find(a => a.kind === 'image')
          return [p.id, img?.url ?? ''] as const
        } catch { return [p.id, ''] as const }
      }))
      setThumbs(Object.fromEntries(entries.filter(e => e[1])))
    })()
  }, [view, projects])

  // ── 素材库 / 个人资产库 ──
  const scopeRef = useRef(assetScope)
  scopeRef.current = assetScope
  const libFilterRef = useRef({ folder: libFolder, kind: libKind, q: libQ, starred: libStarOnly })
  libFilterRef.current = { folder: libFolder, kind: libKind, q: libQ, starred: libStarOnly }
  const loadAssets = useCallback(async () => {
    if (scopeRef.current === 'starred') {
      setAssets(await api.listStarred())
    } else if (scopeRef.current === 'library') {
      setAssets(await api.listLibrary(libFilterRef.current))
    } else {
      setAssets(await api.listAssets(projectRef.current))
    }
  }, [])
  useEffect(() => {
    if (tab !== 'assets') return
    loadAssets()
    if (assetScope === 'library') api.listAssetFolders().then(setLibFolders).catch(() => {})
  }, [tab, assetScope, libFolder, libKind, libQ, libStarOnly, loadAssets])

  // 首页投稿板块也需要目录树
  useEffect(() => {
    if (view === 'home') {
      api.listAssetFolders().then(setLibFolders).catch(() => {})
      api.listIngestionBatches().then(setRecentBatches).catch(() => {})
    }
  }, [view])

  const toggleStar = async (aid: string, starred: boolean) => {
    await api.starAsset(aid, starred)
    say(starred ? '已存入个人资产库（跨项目可用）' : '已从资产库移除')
    await loadAssets()
  }

  const removeAsset = async (aid: string) => {
    if (!window.confirm('确定删除这个素材文件吗？（不可撤销）')) return
    await api.deleteAsset(aid)
    say('素材已删除')
    await loadAssets()
  }

  const useAsset = (url: string) => {
    const sel = bnodesRef.current[selectedId]
    if (!sel) { say('先在画布上选中一个图像/视频节点，再点素材'); return }
    if (sel.type === 'video') {
      setDraft(d => ({ ...d, first_frame_url: url }))
      say('已设为该视频节点的首帧（记得点保存）')
    } else if (sel.type === 'image') {
      setDraft(d => ({ ...d, ref_asset_url: url }))
      say('已设为该图像节点的参考图（记得点保存）')
    } else say('素材只能用于图像（参考图）或视频（首帧）节点')
  }

  // ── A3：节点右键「保存到素材库」（落盘≠入库，入库=library+文件夹+权利声明）──
  const saveToLibrary = async (nid: string) => {    setMenu(null)
    const vers = await api.nodeVersions(nid)
    if (!vers.length) { say('该节点还没有素材记录'); return }
    const folder = window.prompt('保存到文件夹（格式：学科/课题，如 光学/迈克尔逊干涉）', libFolders[0] || '')
    if (folder === null) return
    const rights = window.prompt('权利声明：own=本人自制 / licensed=已获授权 / reference_only=仅参考不进片', 'own')
    if (rights === null) return
    try {
      await api.patchAsset(vers[0].id, { library: true, folder, rights: rights || 'own' })
      say(`已入库：${folder || '（无文件夹）'} · 权利=${rights || 'own'}`)
      api.listAssetFolders().then(setLibFolders).catch(() => {})
      if (assetScope === 'library') loadAssets()
    } catch (e) { say(`入库失败: ${e}`) }
  }

  // ── 教师投稿：选择文件 → 学科/课题 → 权利声明 → 直接入素材库 ──
  const askSuggest = async () => {
    const text = [upNote, upFile?.name || upFiles[0]?.name || ''].join(' ').trim()
    if (!text) { setUpMsg('先选文件或写一句简述，再让机器建议目录'); return }
    try {
      const r = await api.suggestFolder(text)
      setUpSuggest(r.folder ? { folder: r.folder, score: r.score } : null)
      setUpMsg(r.folder ? '' : '库内还没有相似素材，暂无法建议（embedding 未开通时此功能静默）')
    } catch { setUpSuggest(null) }
  }

  const adoptSuggest = () => {
    if (!upSuggest) return
    const [dom, ...rest] = upSuggest.folder.split('/')
    const known = [...new Set(libFolders.map(f => f.split('/')[0]))]
    if (known.includes(dom)) { setUpDomain(dom); setUpTopic(rest.join('/')) }
    else { setUpDomain('__custom__'); setUpDomainCustom(dom); setUpTopic(rest.join('/')) }
    setUpSuggest(null)
  }

  const doUpload = async () => {
    if (!upFile && !upFiles.length) { setUpMsg(t('先选择要上传的文件或文件夹')); say(t('先选择要上传的文件或文件夹')); return }
    if (upFilesError) { setUpMsg(upFilesError); say(upFilesError); return }
    const dom = upDomain === '__custom__' ? upDomainCustom.trim() : upDomain
    if (!dom) { setUpMsg(t('请填写学科目录')); say(t('请填写学科目录')); return }
    const folder = upDomain === '__auto__' ? '__auto__' : upTopic.trim() ? `${dom}/${upTopic.trim()}` : dom
    setUpBusy(true); setUpReport(null); setUpLastCommunityId(''); setUpMoveFolder('')
    setUpProgress(upFiles.length ? { percent: 0, label: `准备上传 ${upFiles.length} 个文件…` } : null)
    setUpMsg(upFiles.length ? t('正在上传并分析文件夹，请保持当前页面…') : t('正在上传文件，请保持当前页面…'))
    try {
      if (upFiles.length) {
        const uploadPromise = api.uploadFolderToLibrary(upFiles, {
          folder, rights: upRights, note: upNote, communityId: upCommunityId || undefined,
          onProgress: percent => setUpProgress({ percent, label: `正在上传 ${upFiles.length} 个文件，请保持当前页面…` }),
        })
        setUpProgress({ percent: 100, label: '文件已发送，服务器正在校验、去重、解析与归类…' })
        const report = await uploadPromise
        setUpReport(report)
        if (report.communityId) setUpLastCommunityId(report.communityId)
        setUpProgress({ percent: 100, label: '上传与分析完成，目录回执如下。' })
        setUpMsg(t('文件夹处理完成，请逐项核对分析与归类结果；全部内容仍需人工审核'))
        say(t('文件夹上传与分析已完成'))
      } else {
        const result = await api.uploadToLibrary(upFile!, { folder, rights: upRights, note: upNote, communityId: upCommunityId || undefined })
        if (result.communityId) setUpLastCommunityId(result.communityId)
        setUpMsg(`${t('投稿成功：已入库')} ${folder}`)
        say(`${t('投稿成功：已入库')} ${folder}`)
      }
      setUploadOpen(false); setUpFile(null); setUpFiles([]); setUpTopic(''); setUpNote(''); setUpSuggest(null)
      api.listAssetFolders().then(setLibFolders).catch(() => {})
      api.listIngestionBatches().then(setRecentBatches).catch(() => {})
      loadAssets()
    } catch (e) { setUpProgress(null); setUpMsg(`${t('投稿失败')}: ${e}`); say(`${t('投稿失败')}: ${e}`) } finally { setUpBusy(false) }
  }

  const submitIngestionReview = async (action: 'needs_revision' | 'confirm_archive' | 'admin_alignment') => {
    const id = upReport?.id || upReport?.batchId
    if (!id) return
    if (action === 'needs_revision' && !upReviewComment.trim()) {
      setUpMsg(t('请先填写具体修改意见')); return
    }
    setUpBusy(true)
    try {
      const updated = await api.reviewIngestionBatch(id, action, upReviewComment)
      setUpReport(updated)
      setUpReviewComment('')
      setUpMsg(t('审核状态与意见已保存'))
      setRecentBatches(await api.listIngestionBatches())
    } catch (e) { setUpMsg(`${t('审核失败')}: ${e}`) }
    finally { setUpBusy(false) }
  }

  const moveUploadBatch = async () => {
    const id = upReport?.id || upReport?.batchId
    if (!id || !upMoveFolder) return
    setUpBusy(true)
    try {
      const updated = await api.moveIngestionBatch(id, upMoveFolder)
      setUpReport(updated)
      setUpMsg(`已将 ${updated.movedCount} 个接收文件整理到：${upMoveFolder}`)
      setUpMoveFolder('')
      setRecentBatches(await api.listIngestionBatches())
      api.listAssetFolders().then(setLibFolders).catch(() => {})
      loadAssets()
    } catch (e) { setUpMsg(`目录整理失败：${e}`) }
    finally { setUpBusy(false) }
  }

  // ── A4：节点历史版本切换 ──
  const openVersions = async (nid: string) => {
    setMenu(null)
    const list = await api.nodeVersions(nid)
    if (!list.length) { say('该节点还没有历史版本'); return }
    setVersionDlg({ nid, list })
  }

  const upFilesTotalBytes = upFiles.reduce((total, file) => total + file.size, 0)
  const upFilesUnsupported = upFiles.filter(file => !UPLOAD_EXTENSIONS.has(file.name.split('.').pop()?.toLowerCase() || ''))
  const upFilesError = upFiles.length > MAX_UPLOAD_FILES
    ? `已选择 ${upFiles.length} 个文件，超过单批 ${MAX_UPLOAD_FILES} 个上限，请拆分后重新选择。`
    : upFilesTotalBytes > MAX_UPLOAD_BYTES
      ? `所选文件共 ${(upFilesTotalBytes / 1024 / 1024).toFixed(1)}MB，超过单批 500MB 上限，请拆分后重新选择。`
      : upFiles.length > 0 && upFilesUnsupported.length === upFiles.length
        ? '所选文件均不在支持格式范围内，请重新选择教学资料。'
        : ''
  const uploadTargetDomain = upDomain === '__custom__' ? upDomainCustom.trim() : upDomain
  const uploadTargetFolder = upDomain === '__auto__' ? '自动归类（待审核）' : upTopic.trim() ? `${uploadTargetDomain}/${upTopic.trim()}` : uploadTargetDomain || '待确认'
  const uploadedFolders = [...new Set(upReport?.results.filter(item => item.status === 'accepted' && item.folder).map(item => item.folder!) || [])]

  // ── 教师投稿表单（首页投稿板块 & 画布素材库面板共用）──
  const uploadForm = (
    <div className="lib-upload">
      <input ref={singleUploadRef} type="file" hidden accept=".mp4,.mov,.webm,.png,.jpg,.jpeg,.webp,.pdf,.docx"
        onChange={e => { setUpFile(e.target.files?.[0] ?? null); setUpFiles([]); setUpSuggest(null); setUpReport(null); setUpProgress(null); setUpMsg('') }} />
      <input ref={node => { folderUploadRef.current = node; node?.setAttribute('webkitdirectory', '') }}
        type="file" hidden multiple accept=".mp4,.mov,.webm,.png,.jpg,.jpeg,.webp,.pdf,.docx,.txt,.md,.csv"
        onChange={e => { const files = Array.from(e.target.files ?? []); setUpFiles(files); setUpFile(null); setUpSuggest(null); setUpReport(null); setUpProgress(null); setUpMsg(files.length ? `${files.length} ${t('个文件待上传')}` : '') }} />
      <div className="upload-source-buttons">
        <button type="button" onClick={() => singleUploadRef.current?.click()}>{t('选择文件')}</button>
        <button type="button" onClick={() => folderUploadRef.current?.click()}>{t('选择文件夹')}</button>
      </div>
      <select value={upCommunityId} onChange={e => setUpCommunityId(e.target.value)}>
        <option value="">仅提交素材库（不关联课题社区）</option>
        {communityOptions.filter(item => item.my_role).map(item => <option key={item.id} value={item.id}>投稿到课题：{item.title}</option>)}
      </select>
      {upFile && <div className="muted upload-selection">{upFile.name}</div>}
      {upFiles.length > 0 && <div className="muted upload-selection">{upFiles[0].webkitRelativePath || upFiles[0].name} · {upFiles.length} {t('个文件')}</div>}
      {upFiles.length > 0 && <div className={`upload-preflight ${upFilesError ? 'error' : ''}`} role={upFilesError ? 'alert' : 'status'}>
        <b>{upFilesError || `预检通过：${upFiles.length} 个文件，共 ${(upFilesTotalBytes / 1024 / 1024).toFixed(1)}MB`}</b>
        {upFilesUnsupported.length > 0 && upFilesUnsupported.length < upFiles.length && <span>
          {upFilesUnsupported.length} 个不支持格式的文件将在上传时被拒绝：{upFilesUnsupported.slice(0, 3).map(file => file.name).join('、')}{upFilesUnsupported.length > 3 ? ' 等' : ''}
        </span>}
        {!upFilesError && <span>单批最多 200 个文件、总大小不超过 500MB；建议按课次拆分并排除 .git、依赖和缓存文件。</span>}
      </div>}
      <div className="lib-filter">
        <select value={upDomain} onChange={e => setUpDomain(e.target.value)}>
          {[...new Set(libFolders.map(f => f.split('/')[0]))].map(d => (
            <option key={d} value={d}>{d}</option>
          ))}
          <option value="__custom__">{t('（新学科…）')}</option>
          <option value="__auto__">{t('自动归类（待审核）')}</option>
        </select>
        <input placeholder={t('课题（可选，如 迈克尔逊干涉）')} value={upTopic} list="existing-topic-folders"
          onChange={e => setUpTopic(e.target.value)} />
        <datalist id="existing-topic-folders">{libFolders.filter(path => path.startsWith(`${uploadTargetDomain}/`)).map(path => (
          <option key={path} value={path.slice(uploadTargetDomain.length + 1)}>{path}</option>
        ))}</datalist>
      </div>
      {upDomain === '__custom__' && (
        <input placeholder={t('新学科目录名（如 电子类）')} value={upDomainCustom}
          onChange={e => setUpDomainCustom(e.target.value)} />
      )}
      <div className="upload-target"><b>目标目录：{uploadTargetFolder}</b><span>选择已有课题即合并到原目录；同名不同内容不会覆盖，重复内容自动跳过。</span></div>
      <div className="lib-filter">
        <select value={upRights} onChange={e => setUpRights(e.target.value)}>
          <option value="own">{t('本人自制')}</option>
          <option value="licensed">{t('已获授权')}</option>
          <option value="reference_only">{t('仅参考不进片')}</option>
        </select>
        <input placeholder={t('简述（可选）')} value={upNote}
          onChange={e => setUpNote(e.target.value)} />
      </div>
      <div className="lib-filter">
        <button onClick={askSuggest}>✨ {t('智能建议目录')}</button>
        {upSuggest && (
          <span className="up-suggest">
            {t('建议：')}<b>{upSuggest.folder}</b>（{upSuggest.score.toFixed(2)}）
            <button className="mini" onClick={adoptSuggest}>{t('采用')}</button>
          </span>
        )}
      </div>
      {upMsg && <div className="muted" style={{ fontSize: 11 }}>{upMsg}</div>}
      <div className="muted" style={{ fontSize: 11 }}>
        {t('可提交：mp4/mov/webm 实验录屏 · png/jpg 图片 · pdf 教案 · docx Word 文档')}
      </div>
      <button disabled={upBusy || !!upFilesError} onClick={doUpload}>
        {upBusy ? t('正在上传并分析…') : upFiles.length ? t('上传并分析文件夹') : t('提交入库')}
      </button>
      {upProgress && <div className="upload-progress" role="status" aria-live="polite">
        <div><span>{upProgress.label}</span><b>{upProgress.percent}%</b></div>
        <progress max={100} value={upProgress.percent} />
      </div>}
      {upReport && <div className="upload-report">
        <div className="upload-report-summary">
          <b>{t('批次回执')}：{upReport.id || upReport.batchId}</b>
          <div>{t('接收')} {upReport.summary.accepted} · {t('拒绝')} {upReport.summary.rejected} · {t('重复')} {upReport.summary.duplicate}</div>
          {upReport.status && <div>{t('当前状态')}：{t(upReport.status)}</div>}
          {upReport.summary.receiptIncomplete && <div className="muted">{t('历史批次仅保留通过文件，拒绝与重复数量不可回推。')}</div>}
        </div>
        <div className="upload-destination">
          <b>{uploadedFolders.length ? '新文件已保存到素材库目录' : '本批没有新文件写入素材目录'}</b>
          {uploadedFolders.length > 0 && <span>{uploadedFolders.join('；')}</span>}
          {!uploadedFolders.length && <span>重复文件仍保留在原目录，拒绝文件不会写入。</span>}
          {uploadedFolders.length > 0 && <div className="upload-move">
            <select value={upMoveFolder} onChange={e => setUpMoveFolder(e.target.value)}>
              <option value="">将本批接收文件整理到已有目录…</option>
              {libFolders.map(path => <option key={path} value={path}>{path}</option>)}
            </select>
            <button disabled={upBusy || !upMoveFolder} onClick={() => void moveUploadBatch()}>确认整理</button>
          </div>}
        </div>
        {upReport.results.map((result, index) => <div className={`upload-report-row ${result.status}`} key={`${result.path}-${index}`}>
          <span>{result.status === 'accepted' ? t('已接收') : result.status === 'duplicate' ? t('重复') : t('未接收')}</span>
          <code>{result.path}</code>
          <small>{result.folder ? `${t('目录')}：${result.folder}` : result.reason}</small>
        </div>)}
        <div className="muted">{t('本地规则分析不等于专业内容审核；全部结果均待人工复核。')}</div>
        <div className="upload-review-actions">
          <label>{t('修改意见或审核说明')}<textarea rows={2} value={upReviewComment} onChange={e => setUpReviewComment(e.target.value)}
            placeholder={t('指出需要调整的目录、依据、素材或规则；仅确认归档可留空')} /></label>
          <div><button disabled={upBusy} onClick={() => void submitIngestionReview('needs_revision')}>{t('提出修改意见')}</button>
            <button disabled={upBusy || !upReport.summary.accepted} onClick={() => void submitIngestionReview('confirm_archive')}>{t('确认并暂时归档')}</button>
            {authRole === 'admin' && <button disabled={upBusy} onClick={() => void submitIngestionReview('admin_alignment')}>{t('管理员对齐')}</button>}
          </div>
          {(upReport.reviews?.length ?? 0) > 0 && <div className="upload-review-history">
            <b>{t('审核历史')}</b>{upReport.reviews!.slice(-3).map(review => <div key={review.id}>
              <span>{t(review.action)} · {review.actor_role} · {new Date(review.created_at).toLocaleString()}</span>
              {review.comment && <small>{review.comment}</small>}
            </div>)}
          </div>}
        </div>
      </div>}
      {upLastCommunityId && <div className="upload-community-next">
        <b>素材已进入课题社区，接下来邀请老师一起讨论和审校。</b>
        <button onClick={() => { setCommunityId(upLastCommunityId); setView('communities') }}>进入课题继续共建 →</button>
      </div>}
      {recentBatches.length > 0 && <details className="recent-batches">
        <summary>{t('最近上传批次与审核状态')}</summary>
        {recentBatches.map(batch => <button key={batch.id} onClick={() => setUpReport(batch)}>
          <span>{batch.id}</span><small>{t(batch.status || '')} · {t('接收')} {batch.summary.accepted}</small>
        </button>)}
      </details>}
    </div>
  )

  // ── A3：素材拖入画布 = 建节点（按 MIME/kind 定型，成果即素材本身）──
  const onAssetDrop = async (e: React.DragEvent) => {
    const data = e.dataTransfer.getData('application/x-jojo-asset')
    if (!data) return
    e.preventDefault()
    const a = JSON.parse(data) as { id: string; kind: string; url: string }
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    const inst = flowRef.current as unknown as { screenToFlowPosition?: (p: { x: number; y: number }) => { x: number; y: number }; project?: (p: { x: number; y: number }) => { x: number; y: number } } | null
    const fp = inst?.screenToFlowPosition?.({ x: e.clientX, y: e.clientY })
      ?? inst?.project?.({ x: e.clientX - rect.left, y: e.clientY - rect.top })
      ?? { x: 120, y: 120 }
    const type = a.kind === 'video' ? 'video' : 'image'
    try {
      const created = await api.createNode(projectRef.current, {
        type, title: '', inputs: {}, position: { x: fp.x, y: fp.y },
      })
      await api.updateNode(created.id, {
        outputs: { asset_url: a.url, asset_id: a.id, source: 'library' },
        status: 'succeeded',
      })
      await syncGraph(projectRef.current)
      setSelectedId(created.id)
      say(`已从素材库落位：${type === 'video' ? '视频' : '图像'}节点（成果=库内素材）`)
    } catch (err) { say(`落位失败: ${err}`) }
  }

  // ── 撤销 / 重做（删除操作） ──
  const undo = useCallback(async () => {
    const entry = undoRef.current.pop()
    if (!entry) { say('没有可撤销的删除'); return }
    await api.restore(projectRef.current, entry.nodes, entry.edges)
    redoRef.current.push(entry)
    await syncGraph(projectRef.current)
    say('已撤销删除')
  }, [syncGraph])

  const redo = useCallback(async () => {
    const entry = redoRef.current.pop()
    if (!entry) { say('没有可重做的操作'); return }
    for (const e of entry.edges) await api.deleteEdge(e.id).catch(() => {})
    for (const n of entry.nodes) await api.deleteNode(n.id).catch(() => {})
    undoRef.current.push(entry)
    await syncGraph(projectRef.current)
    say('已重做删除')
  }, [syncGraph])

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const t = (e.target as HTMLElement)?.tagName
      if (t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT') return
      if (e.ctrlKey && e.key.toLowerCase() === 'z') { e.preventDefault(); undo() }
      if (e.ctrlKey && e.key.toLowerCase() === 'y') { e.preventDefault(); redo() }
      if (e.key === 'Escape') { setLightbox(null); setConnectMenu(null); setMenu(null) }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [undo, redo])

  const newProject = async () => {
    const title = window.prompt('新项目名称：', '新微课项目')
    if (!title) return
    const p = await api.createProject(title)
    setProjects(ps => [p, ...ps])
    await switchProject(p.id)
    setView('canvas')
  }

  // ── 验收台 ──
  const loadReview = async () => {
    setReview(await api.reviewQueue(projectRef.current))
  }
  const openReview = async () => { await loadReview(); setView('review') }

  const reviewRelease = async (it: ReviewItem) => {
    setReviewBusy(it.qc_node_id)
    try {
      await api.qcOverride(it.qc_node_id, 'pass_human')
      say(`已放行：${it.target_title || it.qc_title}`)
      await loadReview()
    } finally { setReviewBusy('') }
  }

  const reviewRetry = async (it: ReviewItem) => {
    setReviewBusy(it.qc_node_id)
    try {
      const t = await api.getNode(it.target_node_id)
      const isEdit = Boolean(t.inputs?.ref_node || t.inputs?.edit_delta)
      if (it.suggested_prompt) {
        const patch = isEdit ? { edit_delta: it.suggested_prompt }
                             : { prompt: it.suggested_prompt }
        await api.updateNode(it.target_node_id, { inputs: { ...t.inputs, ...patch } })
      }
      say(`重跑中：${it.target_title || it.qc_title}（完成后自动复检）`)
      await api.executeNode(it.target_node_id)
      await api.executeNode(it.qc_node_id)
      await loadReview()
      await syncGraph(projectRef.current)
    } catch (e) { say(`重跑失败: ${e}`) }
    finally { setReviewBusy('') }
  }

  const retryInfraAll = async () => {
    const r = await api.retryInfra(projectRef.current)
    say(`已重试 ${r.retrying} 个基础设施失败节点`)
    await loadReview()
  }

  const resumeLineAll = async () => {
    const r = await api.resumeLine(projectRef.current)
    say(`产线续跑：已启动 ${r.started} 个视频节点`)
    await syncGraph(projectRef.current)
  }

  // ── 一键整理布局：镜头行 × 阶段列 泳道网格 ──
  const STAGE_COLS = ['首帧', '首帧质检', '尾帧', '尾帧质检', '视频', '视频质检',
                      '代码渲染', '质检']
  const tidyLayout = async () => {
    const rows: Record<number, Record<string, string>> = {}
    const others: string[] = []
    let composeId = ''
    Object.values(bnodesRef.current).forEach(n => {
      const m = (n.title || '').match(/^镜头(\d+)·(.+)$/)
      if (m) {
        const k = parseInt(m[1], 10)
        rows[k] = rows[k] || {}
        rows[k][m[2]] = n.id
      } else if (n.type === 'compose') composeId = n.id
      else others.push(n.id)
    })
    const pos: Record<string, { x: number; y: number }> = {}
    others.forEach((id, i) => { pos[id] = { x: 60 + i * 300, y: 40 } })
    const shotNums = Object.keys(rows).map(Number).sort((a, b) => a - b)
    shotNums.forEach((num, ri) => {
      const y = 340 + ri * 320
      STAGE_COLS.forEach((stage, ci) => {
        const id = rows[num][stage]
        if (id) pos[id] = { x: 60 + ci * 290, y }
      })
    })
    if (composeId) pos[composeId] = {
      x: 60 + STAGE_COLS.length * 290,
      y: 340 + Math.max(0, (shotNums.length - 1)) * 160 }
    await Promise.all(Object.entries(pos).map(([id, p]) =>
      api.updateNode(id, { position: p })))
    setRfNodes(ns => ns.map(n => pos[n.id] ? { ...n, position: pos[n.id] } : n))
    setTimeout(() => focusAll(), 120)
    say('布局已按 镜头行×阶段列 整理')
  }

  // 轮询：有节点在运行时每 3 秒刷新
  useEffect(() => {
    const t = setInterval(() => {
      if (Object.values(bnodesRef.current).some(n => n.status === 'running' ||
        (n.outputs.production as { status?: string } | undefined)?.status === 'running')) {
        syncGraph(projectRef.current)
      }
    }, 3000)
    return () => clearInterval(t)
  }, [syncGraph])

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setRfNodes(ns => applyNodeChanges(changes, ns))
    const removed = changes.filter(c => c.type === 'remove').map(c => c.id)
    if (removed.length) snapshotForUndo(removed)
    for (const c of changes) {
      if (c.type === 'select' && c.selected) setSelectedId(c.id)
      if (c.type === 'position' && !c.dragging && c.position) {
        api.updateNode(c.id, { position: c.position })
      }
      if (c.type === 'remove') api.deleteNode(c.id)
    }
  }, [snapshotForUndo])

  const onConnect = useCallback(async (c: Connection) => {
    if (!c.source || !c.target) return
    await api.createEdge(projectRef.current, c.source, c.target)
    await syncGraph(projectRef.current)
  }, [syncGraph])

  const onEdgesDelete = useCallback((edges: Edge[]) => {
    snapshotForUndo([], edges.map(e => e.id))
    edges.forEach(e => api.deleteEdge(e.id))
  }, [snapshotForUndo])

  const onNodeContextMenu = useCallback((e: React.MouseEvent, node: Node) => {
    e.preventDefault()
    const rect = (e.currentTarget as HTMLElement).closest('.canvas')?.getBoundingClientRect()
    setMenu({ x: e.clientX - (rect?.left ?? 0), y: e.clientY - (rect?.top ?? 0), id: node.id })
    setSelectedId(node.id)
  }, [])

  // ── 拖线到空白处 → 弹"接什么节点"建议菜单（TapNow 式核心交互） ──
  const CONNECT_SUGGEST: Record<string, { type: string; label: string }[]> = {
    script: [{ type: 'storyboard', label: '🎞 分镜拆解' }, { type: 'tts', label: '🎙 配音' }],
    tts: [{ type: 'compose', label: '🎞 拼接成片' }],
    storyboard: [{ type: 'image', label: '🖼 图像（帧）' }, { type: 'qc', label: '🔍 质检' }],
    image: [{ type: 'qc', label: '🔍 质检' }, { type: 'video', label: '🎬 视频' }, { type: 'image', label: '🖼 图像（尾帧）' }],
    video: [{ type: 'qc', label: '🔍 质检' }, { type: 'compose', label: '🎞 拼接成片' }],
    code_render: [{ type: 'qc', label: '🔍 质检' }, { type: 'compose', label: '🎞 拼接成片' }],
    ref_video: [{ type: 'enhance', label: '✂️ 素材增强' }, { type: 'video', label: '🎬 视频' }],
    enhance: [{ type: 'qc', label: '🔍 质检' }, { type: 'compose', label: '🎞 拼接成片' }],
  }
  const onConnectStart = useCallback((_e: unknown, params: { nodeId: string | null }) => {
    connectSrcRef.current = params.nodeId
  }, [])
  const onConnectEnd = useCallback((e: MouseEvent | TouchEvent) => {
    const src = connectSrcRef.current
    connectSrcRef.current = null
    if (!src) return
    const target = e.target as HTMLElement
    if (!target?.classList?.contains('react-flow__pane')) return
    const srcNode = bnodesRef.current[src]
    if (!srcNode || !CONNECT_SUGGEST[srcNode.type]?.length) return
    const ev = e as MouseEvent
    const rect = target.closest('.canvas')?.getBoundingClientRect()
    const inst = flowRef.current as unknown as { screenToFlowPosition?: (p: { x: number; y: number }) => { x: number; y: number }; project?: (p: { x: number; y: number }) => { x: number; y: number } }
    const fp = inst?.screenToFlowPosition?.({ x: ev.clientX, y: ev.clientY })
      ?? inst?.project?.({ x: ev.clientX - (rect?.left ?? 0), y: ev.clientY - (rect?.top ?? 0) })
      ?? { x: 0, y: 0 }
    setConnectMenu({ x: ev.clientX - (rect?.left ?? 0), y: ev.clientY - (rect?.top ?? 0), fx: fp.x, fy: fp.y, src })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const createFromConnect = async (type: string) => {
    const cm = connectMenu
    setConnectMenu(null)
    if (!cm) return
    const defaults: Record<string, object> = {
      storyboard: {}, image: { prompt: '', shot_index: 1, size: '2560x1440' },
      video: { prompt: '', resolution: '720p', duration: 5 },
      qc: { domain: 'general', shot_index: 1 }, compose: { burn_subtitles: '是' },
      enhance: { slow_factor: 1, segment_index: 1 },
      tts: { text: '', voice_type: 'zh_female_vv_uranus_bigtts', speed_ratio: 1 },
    }
    const created = await api.createNode(projectRef.current, {
      type, title: '', inputs: defaults[type] ?? {}, position: { x: cm.fx, y: cm.fy },
    })
    await api.createEdge(projectRef.current, cm.src, created.id)
    await syncGraph(projectRef.current)
    setSelectedId(created.id)
    say(`已创建并连线：${TYPE_META[type]?.label}——右侧填参数`)
  }

  // ── 双击节点：有成果开大图预览，无成果聚焦右侧编辑 ──
  const onNodeDoubleClick = useCallback((_e: unknown, node: Node) => {
    const b = bnodesRef.current[node.id]
    const url = b?.outputs?.asset_url
    if (typeof url === 'string' && url) {
      setLightbox({ url, title: b.title || TYPE_META[b.type]?.label || '' })
    } else {
      setSelectedId(node.id)
      say('该节点还没有成果——右侧编辑参数后点 ▶')
    }
  }, [])

  // 新建节点：若当前选中了节点，自动连线并放到它右侧
  const addNode = async (type: string) => {
    const sel = bnodesRef.current[selectedId]
    const selRf = rfNodes.find(n => n.id === selectedId)
    const pos = selRf
      ? { x: selRf.position.x + 300, y: selRf.position.y }
      : { x: 120 + Math.random() * 80, y: 120 + Math.random() * 120 }
    const defaults: Record<string, object> = {
      script: { goal: '', duration: 60 },
      storyboard: {},
      image: { prompt: '', shot_index: 1, size: '2560x1440' },
      video: { prompt: '', resolution: '480p', duration: 5 },
      code_render: { template: 'lens_focus', focal_length: 2.2, num_rays: 7, duration: 6 },
      compose: { burn_subtitles: '是' },
      qc: { domain: 'optics', shot_index: 1 },
      ref_video: { focus: '' },
      enhance: { slow_factor: 1, segment_index: 1 },
    }
    const created = await api.createNode(projectRef.current, {
      type, title: '', inputs: defaults[type] ?? {}, position: pos,
    })
    if (sel) {
      await api.createEdge(projectRef.current, sel.id, created.id)
      say(`已自动连线：${TYPE_META[sel.type]?.label} → ${TYPE_META[type]?.label}`)
    }
    await syncGraph(projectRef.current)
    setSelectedId(created.id)
  }

  // 一键搭建完整微课链
  const buildChain = async () => {
    const pid = projectRef.current
    const mk = (type: string, title: string, inputs: object, x: number) =>
      api.createNode(pid, { type, title, inputs, position: { x, y: 320 } })
    const s = await mk('script', '', { goal: '', duration: 60 }, 60)
    const b = await mk('storyboard', '', {}, 360)
    const i = await mk('image', '', { prompt: '', shot_index: 1, size: '2560x1440' }, 660)
    const v = await mk('video', '', { prompt: '', resolution: '480p', duration: 5 }, 960)
    await api.createEdge(pid, s.id, b.id)
    await api.createEdge(pid, b.id, i.id)
    await api.createEdge(pid, i.id, v.id)
    await syncGraph(pid)
    focusAll()
    setSelectedId(s.id)
    say('已搭好：脚本→分镜→图像→视频。先在右侧填脚本的知识点，然后点视频节点上的 ▶ 一键跑完整链')
  }

  const selected = bnodes[selectedId]
  const meta = selected ? TYPE_META[selected.type] : null
  const teachingNode = Object.values(bnodes).find(n => n.outputs?.teachingVideo)
  const taskMode = Boolean(teachingNode)
  const selectedSceneTitle = selectedSceneId && teachingNode
    ? (teachingNode.outputs.teachingVideo as { scenes: { sceneId: string; title: string }[] }).scenes
      .find(scene => scene.sceneId === selectedSceneId)?.title
    : ''

  // 画布上所有已有成果的图像节点，供参考图/首尾帧下拉选择
  const imageAssets = Object.values(bnodes)
    .filter(n => n.type === 'image' && typeof n.outputs?.asset_url === 'string')
    .map(n => ({ url: n.outputs.asset_url as string, name: n.title || `图像 ${n.id.slice(-4)}` }))

  useEffect(() => {
    setDraft(selected ? { ...selected.inputs } : {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, selected?.status])

  const renderField = (f: Field) => (
    <div key={f.key} className="field">
      <label>{f.label}</label>
      {f.kind === 'textarea' && (
        <textarea rows={4} placeholder={f.placeholder}
          value={String(draft[f.key] ?? '')}
          onChange={e => setDraft(d => ({ ...d, [f.key]: e.target.value }))} />
      )}
      {f.kind === 'text' && (
        <input type="text" placeholder={f.placeholder} value={String(draft[f.key] ?? '')}
          onChange={e => setDraft(d => ({ ...d, [f.key]: e.target.value }))} />
      )}
      {f.kind === 'number' && (
        <input type="number" value={Number(draft[f.key] ?? 0)}
          onChange={e => setDraft(d => ({ ...d, [f.key]: Number(e.target.value) }))} />
      )}
      {f.kind === 'select' && (
        <select value={String(draft[f.key] ?? f.options?.[0])}
          onChange={e => {
            const v = e.target.value
            // 切换 code_render 模板时载入该模板的默认参数集
            if (f.key === 'template') setDraft({ ...(CR_DEFAULTS[v] ?? { template: v }) })
            else setDraft(d => ({ ...d, [f.key]: v }))
          }}>
          {f.options?.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      )}
    </div>
  )

  const save = async () => {
    await api.updateNode(selectedId, { inputs: draft })
    await syncGraph(projectRef.current)
    say('已保存')
  }

  const runSelf = async () => {
    await api.updateNode(selectedId, { inputs: draft })
    try { await api.executeNode(selectedId) } catch (e) { say(`执行失败: ${e}`) }
    await syncGraph(projectRef.current)
  }

  const runChainSelected = async () => {
    await api.updateNode(selectedId, { inputs: draft })
    await runChain(selectedId)
  }

  const onUpload = async (f: File | undefined) => {
    if (!f || !selected) return
    await api.uploadImage(selected.id, f)
    await syncGraph(projectRef.current)
    say('图片已上传并挂到该节点')
  }

  // ── 登录页（管理员账密 / 邀请码 双通道） ──
  if (authRole === 'checking') return (
    <div className="login-page"><div className="login-card"><h1>{t('科学场景生产与验收')}</h1><div className="muted">{t('正在验证登录状态…')}</div></div></div>
  )
  if (authRole === 'none') return (
    <div className="login-page notranslate" translate="no">
      <div className="login-card">
        <img src={publicAsset('jojo-director-mark.svg')} alt="JOJO Director" className="login-logo" />
        <h1>{t('科学场景生产与验收')}</h1>
        <div className="sub">JOJO Director · Scientific Scene Production Engine</div>
        <div className="tab-bar login-tabs">
          <button className={loginMode === 'admin' ? 'on' : ''} onClick={() => { setLoginMode('admin'); setLoginErr('') }}>{t('管理员')}</button>
          <button className={loginMode === 'invite' ? 'on' : ''} onClick={() => { setLoginMode('invite'); setLoginErr('') }}>{t('邀请码进入')}</button>
        </div>
        {loginMode === 'admin' ? (
          <>
            <input type="text" placeholder={t('账号')} value={loginU} autoComplete="username"
              onChange={e => setLoginU(e.target.value)} />
            <input type="password" placeholder={t('密码')} value={loginP} autoComplete="current-password"
              onChange={e => setLoginP(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && doLogin()} />
          </>
        ) : (
          <input type="text" placeholder={t('输入邀请码，如 JOJO-A1B2C3')} value={loginCode}
            onChange={e => setLoginCode(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && doLogin()} />
        )}
        {loginErr && <div className="login-err">{loginErr}</div>}
        <button className="accent login-go" disabled={loginBusy} onClick={doLogin}>
          {loginBusy ? t('登录中…') : t('进入画布')}
        </button>
        {loginMode === 'invite' && (
          <div className="muted" style={{ fontSize: 12 }}>
            {t('邀请码由管理员发放，含每日生成额度；生成费用由平台承担')}
          </div>
        )}
      </div>
    </div>
  )

  const authBar = (
    <div className="auth-bar">
      <button onClick={() => setAccountOpen(true)}>👤 {localizedContent(authInfo.account || t('账号'), language)}</button>
      {authRole === 'admin' && <button onClick={() => setProviderOpen(true)}>API 配置</button>}
      {authRole === 'admin' && authInfo.auth_enabled !== false && (
        <button onClick={async () => { await loadInvites(); setInviteOpen(true) }}>🎟 {t('邀请码管理')}</button>
      )}
      {authRole === 'invite' && authInfo.usage_today && (
        <span className="muted" style={{ fontSize: 12 }}>
          今日已用 视频{authInfo.usage_today.videos}/{authInfo.limits?.daily_video_limit ?? '-'}条
          · ¥{authInfo.usage_today.cost.toFixed(1)}/{authInfo.limits?.daily_cost_limit_cny ?? '-'}
        </span>
      )}
    </div>
  )

  const accountModal = accountOpen && (
    <div className="lightbox" onClick={() => setAccountOpen(false)}>
      <div className="lightbox-inner account-panel" onClick={e => e.stopPropagation()}>
        <div className="lightbox-head">
          <b>👤 {t('账号与登录')}</b>
          <button onClick={() => setAccountOpen(false)}>✕ {t('关闭')}</button>
        </div>
        <div className="seg">
          <div>{t('当前账号')}：<b>{localizedContent(authInfo.account || t('账号'), language)}</b></div>
          <div className="muted">{authInfo.auth_enabled === false ? t('开发模式') : authRole === 'admin' ? t('管理员账号') : t('测试账号')}</div>
        </div>
        <p className="muted">{t('退出后可使用管理员账号或其他受邀账号重新登录。')}</p>
        <button className="accent" onClick={async () => {
          await api.authLogout()
          setAccountOpen(false)
          setAuthRole('none')
        }}>{t('退出登录 / 切换账号')}</button>
      </div>
    </div>
  )

  const inviteModal = inviteOpen && (
    <div className="lightbox" onClick={() => setInviteOpen(false)}>
      <div className="lightbox-inner invite-panel" onClick={e => e.stopPropagation()}>
        <div className="lightbox-head">
          <b>🎟 邀请码管理</b>
          <button onClick={() => setInviteOpen(false)}>✕ 关闭</button>
        </div>
        <button className="accent" onClick={async () => {
          const label = window.prompt('备注（给谁用，如：伟东老师）：') ?? ''
          const v = Number(window.prompt('每日视频条数上限：', '3') ?? '3') || 3
          const c = Number(window.prompt('每日成本上限（元）：', '10') ?? '10') || 10
          const inv = await api.adminCreateInvite({ label, daily_video_limit: v, daily_cost_limit_cny: c })
          await loadInvites()
          say(`新邀请码：${inv.code}（已生成，发给对方即可）`)
        }}>＋ 新建邀请码</button>
        <table className="invite-table">
          <thead><tr><th>邀请码</th><th>备注</th><th>今日用量</th><th>限额</th><th>状态</th><th></th></tr></thead>
          <tbody>
            {invites.map(iv => (
              <tr key={iv.code} className={iv.disabled ? 'off' : ''}>
                <td><code>{iv.code}</code>
                  <button className="mini" title="复制" onClick={() => { navigator.clipboard.writeText(iv.code); say('邀请码已复制') }}>📋</button>
                </td>
                <td>{iv.label || '—'}</td>
                <td>{iv.usage_today ? `视频${iv.usage_today.videos}条 · ¥${iv.usage_today.cost}` : '—'}</td>
                <td>{iv.daily_video_limit}条/天 · ¥{iv.daily_cost_limit_cny}/天</td>
                <td>{iv.disabled ? '已停用' : '生效中'}</td>
                <td><button className="mini" onClick={async () => { await api.adminToggleInvite(iv.code); await loadInvites() }}>
                  {iv.disabled ? '启用' : '停用'}</button></td>
              </tr>
            ))}
            {!invites.length && <tr><td colSpan={6} className="muted">还没有邀请码，点上面新建</td></tr>}
          </tbody>
        </table>
        <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          全站每日总预算由服务器 DAILY_BUDGET_CNY 控制（当前默认 ¥50），超出后所有邀请码当日停止生成，管理员不受限。
        </div>
      </div>
    </div>
  )

  if (view === 'home') return (
    <div className="home notranslate" translate="no">
      {offlineBanner}
      {authBar}
      {inviteModal}
      {accountModal}
      <ProviderConfig open={providerOpen} onClose={() => setProviderOpen(false)} />
      <div className="home-center">
        <div className="home-brand-film"><BrandFilm /></div>
        <div className="home-kicker">SCIENTIFIC SCENE PRODUCTION ENGINE</div>
        <h1 className="home-title">{t('把科学场景，转化为可验证的教学影像。')}</h1>
        <p className="home-intro">{t('从教学证据与场景规范出发，调度真实素材、2D、3D、代码与生成式媒体，完成逐镜生产与质量验收。')}</p>
        <div className="home-box">
          <textarea rows={3} value={homeInput}
            placeholder={t('一句话描述你要的微课，例：讲清楚 PWM 调光原理，60 秒，最后拼成完整视频')}
            onChange={e => setHomeInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitHome() } }} />
          <div className="home-box-foot">
            {agentModels.length > 0 && (
              <select value={agentModel} onChange={e => {
                setAgentModel(e.target.value)
                localStorage.setItem('jojo_agent_model', e.target.value)
              }}>
                {agentModels.map(m => <option key={m.model} value={m.model}>{localizedContent(m.label, language)}</option>)}
              </select>
            )}
            <input ref={yukeFileRef} type="file" accept=".zip,application/zip" hidden
              onChange={e => { const f = e.target.files?.[0]; if (f) void importYuke(f) }} />
            <button disabled={yukeImportBusy} onClick={() => yukeFileRef.current?.click()}>
              {yukeImportBusy ? t('导入中…') : t('导入驭课 ZIP')}
            </button>
            <input ref={teachingFileRef} type="file" accept=".zip,application/zip" hidden
              onChange={e => { const f = e.target.files?.[0]; if (f) void importTeachingVideo(f) }} />
            <button disabled={teachingImportBusy} onClick={() => teachingFileRef.current?.click()}>
              {teachingImportBusy ? t('审核中…') : t('导入教学视频包')}
            </button>
            <button className="send" disabled={homeBusy || !homeInput.trim()} onClick={submitHome}>
              {homeBusy ? t('规划中…') : t('↑ 开工')}
            </button>
          </div>
        </div>
        {homeErr && <div className="home-err">{homeErr}</div>}
        <section className="community-entry">
          <div><img src={publicAsset('jojo-director-mark.svg')} alt="" aria-hidden="true" /><div><h2>课题共建社区</h2><p>不止上传素材：参与讨论、认领任务、学科审校、场景接驳与版本发布。</p></div></div>
          <button onClick={() => { setCommunityId(''); setView('communities') }}>进入共建 →</button>
        </section>
        <div className="home-documents">
          <section className="home-document-card">
            <h3 className="brand-section-title scene"><img src={publicAsset('jojo-director-mark.svg')} alt="" aria-hidden="true" /><span>{t('项目介绍')}</span></h3>
            <p>{t('项目定位、技术架构、当前能力与验收边界。')}</p>
            <div><button onClick={() => { setDocumentName('project'); setView('document') }}>{t('在线阅读')}</button>
              <a href={api.documentDownload('project')} download>{t('下载 Markdown 文件')}</a></div>
          </section>
          <section className="home-document-card">
            <h3 className="brand-section-title produce"><img src={publicAsset('jojo-director-mark.svg')} alt="" aria-hidden="true" /><span>{t('教师投稿合作手册')}</span></h3>
            <p>{t('素材准备、上传步骤、规则审核、退回补证与联系方式。')}</p>
            <div><button onClick={() => { setDocumentName('guide'); setView('document') }}>{t('在线阅读')}</button>
              <a href={api.documentDownload('guide')} download>{t('下载 Markdown 文件')}</a></div>
          </section>
        </div>
        <div className="home-dual">
          <div className="home-upload">
            <h3 className="brand-section-title verify"><img src={publicAsset('jojo-director-mark.svg')} alt="" aria-hidden="true" /><span>{t('教师投稿')}</span></h3>
            <div className="muted" style={{ fontSize: 12 }}>
              {t('把您的实验录屏 / 图片 / 教案投进素材库，丰富 JOJO 的训练素材')}
            </div>
            {uploadForm}
          </div>
          <div className="jojo-ad" aria-hidden="true">
            <div className="jojo-ad-stage">
              <img className="base" src={publicAsset('jojo-director-mark.svg')} alt="" />
            </div>
            <div className="jojo-ad-text">
              <b>{t('JOJO 的“自我学习”是什么？')}</b>
              <span>{t('机器“学会”的不是梯度，而是越来越厚的规则库 + 样例库 + 改判记录。')}</span>
            </div>
          </div>
        </div>
        <div className="proj-grid">
          <div className="proj-card new" onClick={newProject}>
            <div className="plus">＋</div>
            <div className="pname">{t('新建空白项目')}</div>
          </div>
          <div className="proj-card" onClick={() => setView('training')}>
            <div className="ph">SCI</div>
            <div className="pname">{t('科学素材训练库 · 浏览与人工抽检')}</div>
          </div>
          <div className="proj-card" onClick={() => window.open('/agents-md-course/', '_blank')}>
            <div className="ph">📖</div>
            <div className="pname">{t('教师研修报告 · AGENTS.md 七条军规')}</div>
          </div>
          {projects.slice(0, 11).map(p => (
            <div key={p.id} className="proj-card"
              onClick={async () => { await switchProject(p.id); setView('canvas') }}>
              {thumbs[p.id] ? <img src={assetUrl(thumbs[p.id])} alt="" loading="lazy" /> : <div className="ph">🎬</div>}
              <div className="pname">{localizedContent(p.title, language)}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )

  if (view === 'communities') return <CommunityCenter authRole={authRole} initialCommunityId={communityId} onBack={() => { setView('home'); void api.listCommunities().then(setCommunityOptions) }} />

  if (view === 'training') return <TrainingLibrary onBack={() => setView('home')} />

  if (view === 'document') return <DocumentView name={documentName} onBack={() => setView('home')} />

  if (view === 'review') return (
    <div className="review notranslate" translate="no">
      {offlineBanner}
      <div className="review-head">
        <button onClick={() => setView('canvas')}>← {t('返回画布')}</button>
        <h1>🩺 {t('验收台')} · {localizedContent(projects.find(p => p.id === projectId)?.title, language)}</h1>
        <div className="review-actions">
          <button onClick={loadReview}>↻ {t('刷新')}</button>
          {review.failures.some(f => f.error_class === 'infra') && (
            <button onClick={retryInfraAll}>🔁 {t('重试全部网络失败')}
              （{review.failures.filter(f => f.error_class === 'infra').length}）</button>
          )}
          <button className="accent" onClick={resumeLineAll}>▶ {t('续跑产线')}</button>
        </div>
      </div>
      <div className="review-hint">
        逐卡裁决：✅放行=瑕疵可接受，帧放行后对应视频即可生产；🔄重跑=采纳教练建议重新生成并自动复检。
        全部处理完点「▶ 续跑产线」批量恢复视频生产。
      </div>
      {review.reviews.length === 0 && review.failures.length === 0 && (
        <div className="review-empty">🎉 没有待裁项——产线全绿</div>
      )}
      <div className="review-grid">
        {review.reviews.map(it => (
          <div key={it.qc_node_id} className="review-card">
            {it.asset_url && (it.asset_url.endsWith('.mp4')
              ? <video src={assetUrl(it.asset_url)} controls muted />
              : <img src={assetUrl(it.asset_url)} alt="" loading="lazy" />)}
            <div className="rc-body">
              <div className="rc-title">
                {it.target_title || it.qc_title}
                <span className={`status-chip ${it.verdict === 'reject' ? 'failed' : 'running'}`}>
                  {it.verdict === 'reject' ? '不合格' : '待人工'}
                </span>
                {it.remediation === 'human' && <span className="rc-tag">光效/颜色类·建议目测</span>}
              </div>
              <div className="rc-summary">{it.summary}</div>
              {it.fails.map((f, i) => (
                <div key={i} className="rc-fail">⛔ [{f.id}] {f.evidence}</div>
              ))}
              <div className="rc-btns">
                <button disabled={reviewBusy === it.qc_node_id}
                  onClick={() => reviewRelease(it)}>✅ 放行</button>
                {it.suggested_prompt && (
                  <button disabled={reviewBusy === it.qc_node_id}
                    onClick={() => reviewRetry(it)}>
                    {reviewBusy === it.qc_node_id ? '重跑中…' : '🔄 按建议重跑'}
                  </button>
                )}
                <button onClick={() => { setSelectedId(it.target_node_id); setView('canvas') }}>
                  ✏️ 去画布改</button>
              </div>
            </div>
          </div>
        ))}
      </div>
      {review.failures.length > 0 && (
        <div className="review-fails">
          <h2>执行失败节点</h2>
          {review.failures.map(f => (
            <div key={f.node_id} className="rf-row">
              <span className={`rc-tag ${f.error_class}`}>
                {f.error_class === 'infra' ? '网络/服务' : '内容'}</span>
              <b>{f.title || f.type}</b>
              <span className="muted">{f.error}</span>
              <button onClick={async () => {
                try { await api.executeNode(f.node_id); say('已重新执行') }
                catch (e) { say(`失败: ${e}`) }
                await loadReview()
              }}>重跑</button>
            </div>
          ))}
        </div>
      )}
      {toast && <div className="toast">{toast}</div>}
    </div>
  )

  return (
    <div className="layout notranslate" translate="no">
      {offlineBanner}
      <div className="toolbar" style={{ width: leftW }}>
        <div className="brand-row" onClick={() => setView('home')} title="返回首页">
          <img src={publicAsset('jojo-director-mark.svg')} alt="JOJO Director" />
          <div>
            <h1>JOJO DIRECTOR</h1>
            <div className="sub">Scientific Scene Production Engine</div>
          </div>
        </div>
        <div className="project-bar">
          <select value={projectId} onChange={e => switchProject(e.target.value)}>
            {projects.map(p => <option key={p.id} value={p.id}>{localizedContent(p.title, language)}</option>)}
          </select>
          <button className="mini" title="新建项目" onClick={newProject}>＋</button>
        </div>
        {taskMode && teachingNode && (
          <div className="task-nav">
            <button className={tab === 'nodes' ? 'on' : ''} onClick={() => {
              setTab('nodes'); setSelectedId(teachingNode.id); setSelectedSceneId('')
            }}>📦 {t('任务包')}</button>
            <button className={tab === 'assets' ? 'on' : ''} onClick={() => setTab('assets')}>📚 {t('素材与依据')}</button>
            <button onClick={openReview} className="badge-host">🩺 {t('验收记录')}
              {reviewCount > 0 && <span className="count-badge">{reviewCount}</span>}
            </button>
            {(teachingNode.outputs.teachingVideo as { scenes: { sceneId: string; title: string }[] }).scenes.map(scene => (
              <button key={scene.sceneId} className={selectedSceneId === scene.sceneId ? 'on' : ''}
                onClick={() => { setSelectedId(teachingNode.id); setSelectedSceneId(scene.sceneId); setTab('nodes') }}>
                {selectedSceneId === scene.sceneId ? '● ' : '○ '}{localizedContent(scene.title, language)}
              </button>
            ))}
            {projCost != null && <div className="cost-chip">💰 {t('本项目已花')} ¥{projCost.toFixed(2)}</div>}
          </div>
        )}
        {!taskMode && <div className="tab-bar">
          <button className={tab === 'nodes' ? 'on' : ''} onClick={() => setTab('nodes')}>节点</button>
          <button className={tab === 'assets' ? 'on' : ''} onClick={() => setTab('assets')}>素材库</button>
        </div>}
        {tab === 'nodes' && !taskMode && (
          <>
            <button className="accent" onClick={buildChain}>⚡ 一键搭建微课链</button>
            <button onClick={openReview} className="badge-host">🩺 验收台
              {reviewCount > 0 && <span className="count-badge">{reviewCount}</span>}
            </button>
            <button onClick={tidyLayout}>🧹 一键整理布局</button>
            {projCost != null && (
              <div className="cost-chip" title="本项目累计模型调用成本（MAAO 台账实时计）">
                💰 {t('本项目已花')} ¥{projCost.toFixed(2)}
              </div>
            )}
            <div className="style-bar">
              <select value={(projects.find(p => p.id === projectId) as { style?: string } | undefined)?.style ?? ''}
                onChange={async e => {
                  let v = e.target.value
                  if (v === '__custom__') {
                    v = window.prompt('自定义美术风格描述：') || ''
                    if (!v) return
                  }
                  await api.patchProject(projectRef.current, { style: v })
                  setProjects(ps => ps.map(p => p.id === projectId ? { ...p, style: v } : p))
                  say(v ? `风格锚已设为「${v}」，将注入分镜/图像/质检全链` : '已清除风格锚')
                }}>
                <option value="">🎨 风格锚：未设置</option>
                <option value="二维扁平信息图风格（flat 2D infographic style）">二维扁平信息图</option>
                <option value="卡通3D渲染风格（stylized 3D cartoon render）">卡通 3D</option>
                <option value="半写实游戏CG风格（semi-realistic game cinematic render）">半写实游戏 CG</option>
                <option value="黑板手绘粉笔风格（chalkboard hand-drawn style）">黑板手绘</option>
                <option value="__custom__">自定义…</option>
              </select>
            </div>
            <div className="divider" />
            {Object.entries(TYPE_META).map(([t, m]) => (
              <button key={t} onClick={() => addNode(t)}>{m.icon} 添加{localizedContent(m.label, language)}节点</button>
            ))}
            <div className="tips">
              <b>操作提示</b><br />
              连线：拖右侧圆点到目标左侧圆点<br />
              删线：双击连线 · 删节点：✕ / Delete<br />
              撤销删除：Ctrl+Z · 重做：Ctrl+Y<br />
              框选多个：左键拖框 · 平移：中/右键拖<br />
              右键节点：快捷菜单
            </div>
          </>
        )}
        {tab === 'assets' && (
          <div className="assets">
            <div className="tab-bar">
              <button className={assetScope === 'project' ? 'on' : ''}
                onClick={() => setAssetScope('project')}>本项目</button>
              <button className={assetScope === 'starred' ? 'on' : ''}
                onClick={() => setAssetScope('starred')}>⭐ 我的资产</button>
              <button className={assetScope === 'library' ? 'on' : ''}
                onClick={() => setAssetScope('library')}>📚 素材库</button>
            </div>
            {assetScope === 'library' && (
              <>
                <button onClick={() => setUploadOpen(v => !v)}>
                  {uploadOpen ? '收起投稿表单' : '📤 教师投稿（直接入素材库）'}
                </button>
                {uploadOpen && uploadForm}
                <div className="lib-tree">
                  <div className={`lib-folder ${libFolder === '' ? 'on' : ''}`}
                    onClick={() => setLibFolder('')}>全部</div>
                  {Object.entries(libFolders.reduce((tree: Record<string, string[]>, f) => {
                    const parts = f.split('/')
                    const dom = parts[0]
                    ;(tree[dom] = tree[dom] || []).push(parts.slice(1).join('/'))
                    return tree
                  }, {})).map(([dom, subs]) => (
                    <div key={dom}>
                      <div className="lib-domain"
                        onClick={() => setLibCollapsed(c => ({ ...c, [dom]: !c[dom] }))}>
                        {libCollapsed[dom] ? '▸' : '▾'} {dom}
                      </div>
                      {!libCollapsed[dom] && subs.map(sub => {
                        const full = sub ? `${dom}/${sub}` : dom
                        return (
                          <div key={full}
                            className={`lib-folder sub ${libFolder === full ? 'on' : ''}`}
                            onClick={() => setLibFolder(full)}>{sub || '（未分组）'}</div>
                        )
                      })}
                    </div>
                  ))}
                </div>
                <div className="lib-filter">
                  <select value={libKind} onChange={e => setLibKind(e.target.value)}>
                    <option value="">全部类型</option>
                    <option value="image">图片</option>
                    <option value="video">视频</option>
                  </select>
                  <input placeholder="搜索文件名/文件夹…" value={libQ}
                    onChange={e => setLibQ(e.target.value)} />
                  <button className={libStarOnly ? 'on' : ''} title="只看收藏"
                    onClick={() => setLibStarOnly(v => !v)}>⭐</button>
                </div>
              </>
            )}
            <div className="muted" style={{ fontSize: 11 }}>
              {assetScope === 'library'
                ? '拖素材到画布 = 建节点 · 点图片 = 填入选中节点 · 节点右键可入库'
                : '点图片 = 填入选中节点（图像→参考图 / 视频→首帧）· ⭐ 收藏入资产库 · ✕ 删除文件'}
            </div>
            {assets.length === 0 && (
              <div className="muted">{
                assetScope === 'starred' ? '还没有收藏的资产，点素材上的 ⭐ 收藏'
                : assetScope === 'library' ? '素材库还是空的——在画布节点上右键「保存到素材库」入库'
                : '暂无素材'}</div>
            )}
            <div className="asset-grid">
              {assets.map(a => (
                <div key={a.id} className="asset-item" title={`${a.id}${a.folder ? `\n${a.folder}` : ''}`}
                  draggable
                  onDragStart={e => {
                    e.dataTransfer.setData('application/x-jojo-asset',
                      JSON.stringify({ id: a.id, kind: a.kind, url: a.url }))
                    e.dataTransfer.effectAllowed = 'copy'
                  }}
                  onClick={() => a.kind === 'image' ? useAsset(a.url) : window.open(a.url)}>
                  {a.kind === 'image'
                    ? <img src={assetUrl(a.url)} alt="" loading="lazy" />
                    : <video src={assetUrl(a.url)} muted />}
                  <span className="kind">{a.kind === 'image' ? '图' : '视频'}</span>
                  <button className="star" title={a.starred ? '移出资产库' : '收藏到我的资产'}
                    onClick={e => { e.stopPropagation(); toggleStar(a.id, !a.starred) }}>
                    {a.starred ? '⭐' : '☆'}
                  </button>
                  <button className="rm" title="删除素材文件"
                    onClick={e => { e.stopPropagation(); removeAsset(a.id) }}>✕</button>
                </div>
              ))}
            </div>
            <button onClick={loadAssets}>↻ 刷新</button>
          </div>
        )}
      </div>
      <div className="resizer" role="separator" aria-label="调整左侧导航宽度" aria-orientation="vertical"
        aria-valuenow={leftW} aria-valuemin={200} aria-valuemax={520} tabIndex={0}
        title="拖动调整宽度；方向键微调；双击恢复三栏默认宽度"
        onPointerDown={startResize('left')} onKeyDown={resizeKeys('left')} onDoubleClick={resetWidths} />
      <div className="canvas"
        onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy' }}
        onDrop={onAssetDrop}>
        {!taskMode && (() => {
          const groups: Record<number, string[]> = {}
          Object.values(bnodes).forEach(n => {
            const m = (n.title || '').match(/^镜头(\d+)·/)
            if (m) {
              const k = parseInt(m[1], 10)
              groups[k] = groups[k] || []
              groups[k].push(n.id)
            }
          })
          const nums = Object.keys(groups).map(Number).sort((a, b) => a - b)
          // 没有镜头编号的项目也保留「看全部」——否则用户在大画布上找不到散落的节点
          return (
            <div className="shot-nav">
              {nums.map(k => (
                <button key={k} onClick={() => flowRef.current?.fitView({
                  nodes: groups[k].map(id => ({ id })), padding: 0.25, duration: 300 })}>
                  {k}</button>
              ))}
              <button onClick={() => focusAll()}>⛶ 全部</button>
            </div>
          )
        })()}
        {taskMode && teachingNode ? (
          <TeachingTimeline data={teachingNode.outputs.teachingVideo}
            review={teachingNode.outputs.importReview} selectedSceneId={selectedSceneId}
            onOpen={sceneId => { setSelectedId(teachingNode.id); setSelectedSceneId(sceneId) }} />
        ) : (
        <ReactFlow
          nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes}
          onInit={inst => { flowRef.current = inst }}
          onNodesChange={onNodesChange} onConnect={onConnect}
          onEdgesDelete={onEdgesDelete} onEdgeDoubleClick={onEdgeDoubleClick}
          onNodeContextMenu={onNodeContextMenu}
          onConnectStart={onConnectStart} onConnectEnd={onConnectEnd}
          onNodeDoubleClick={onNodeDoubleClick}
          onMoveEnd={(_e, vp) => localStorage.setItem(`jojo_vp_${projectRef.current}`, JSON.stringify(vp))}
          onPaneClick={() => { setMenu(null); setConnectMenu(null) }}
          onMoveStart={() => { setMenu(null); setConnectMenu(null) }}
          fitView proOptions={{ hideAttribution: true }}
          defaultEdgeOptions={{ type: 'default', animated: true }}
          deleteKeyCode={['Delete', 'Backspace']}
          connectionRadius={36}
          selectionOnDrag panOnDrag={[1, 2]} panOnScroll zoomOnDoubleClick={false}
        >
          <Background color="#d5d9e0" />
          <Controls />
          <MiniMap pannable zoomable nodeColor="#c7ccd6"
            maskColor="rgba(243,244,246,0.75)" style={{ background: '#f8f9fb' }} />
        </ReactFlow>
        )}
        {toast && <div className="toast">{toast}</div>}
        {overrideDlg && (
          <div className="lightbox" onClick={() => setOverrideDlg(null)}>
            <div className="lightbox-inner" style={{ maxWidth: 440 }} onClick={e => e.stopPropagation()}>
              <div className="lightbox-head">
                <b>📝 改判沉淀（进入自我学习样例库）</b>
                <button onClick={() => setOverrideDlg(null)}>✕ 关闭</button>
              </div>
              <div className="muted" style={{ fontSize: 12, lineHeight: 1.7 }}>
                机器原判「{overrideDlg.orig === 'reject' ? '不合格' : overrideDlg.orig === 'pass' ? '合格' : '待人工'}」
                → 您的终裁「{overrideDlg.verdict === 'pass_human' ? '放行' : '不合格'}」。
                这条改判将教会机器下次判得更准。
              </div>
              <div className="lib-filter">
                <select value={corrFailType} onChange={e => setCorrFailType(e.target.value)}>
                  <option value="误判">误判（机器判错了）</option>
                  <option value="漏判">漏判（机器没看出来）</option>
                  <option value="标准不清">标准不清（规则模糊）</option>
                </select>
                <input placeholder="一句话原因（可选，如：条纹疏密不算错误）"
                  value={corrReason} onChange={e => setCorrReason(e.target.value)}
                  style={{ flex: 1, border: '1px solid #d9dde3', borderRadius: 6, padding: '4px 8px', fontSize: 12 }} />
              </div>
              <div className="row" style={{ display: 'flex', gap: 8 }}>
                <button className="accent" style={{ background: '#2563eb', color: '#fff', border: 'none', borderRadius: 6, padding: '8px 14px', cursor: 'pointer' }}
                  onClick={async () => {
                    await api.qcOverride(overrideDlg.nid, overrideDlg.verdict, corrFailType, corrReason)
                    setOverrideDlg(null)
                    await syncGraph(projectRef.current)
                    say(overrideDlg.verdict === 'pass_human' ? '已人工放行，改判已沉淀 ✓' : '已人工判不合格，改判已沉淀 ✓')
                  }}>提交并终裁</button>
                <button style={{ border: '1px solid #d9dde3', borderRadius: 6, padding: '8px 14px', background: '#fff', cursor: 'pointer' }}
                  onClick={async () => {
                    await api.qcOverride(overrideDlg.nid, overrideDlg.verdict)
                    setOverrideDlg(null)
                    await syncGraph(projectRef.current)
                    say(overrideDlg.verdict === 'pass_human' ? '已人工放行' : '已人工判不合格')
                  }}>仅终裁，不记录</button>
              </div>
            </div>
          </div>
        )}
        {menu && (
          <div className="ctx-menu" style={{ left: menu.x, top: menu.y }}>
            <button onClick={() => { setMenu(null); runChain(menu.id) }}>⏩ 运行到此</button>
            <button onClick={async () => {
              setMenu(null)
              try {
                if (menu.id === selectedIdRef.current && Object.keys(draftRef.current).length) {
                  await api.updateNode(menu.id, { inputs: draftRef.current })  // 先存后跑
                }
                await api.executeNode(menu.id)
              } catch (e) { say(`执行失败: ${e}`) }
              await syncGraph(projectRef.current)
            }}>▶ 只跑本节点</button>
            <button onClick={() => dupNode(menu.id)}>⧉ 复制节点</button>
            {typeof bnodes[menu.id]?.outputs?.asset_url === 'string' && (
              <button onClick={() => {
                const b = bnodes[menu.id]
                setMenu(null)
                setLightbox({ url: b.outputs.asset_url as string, title: b.title || '' })
              }}>🔍 大图预览</button>
            )}
            {typeof bnodes[menu.id]?.outputs?.asset_url === 'string' && (
              <button onClick={() => saveToLibrary(menu.id)}>📚 保存到素材库</button>
            )}
            <button onClick={() => openVersions(menu.id)}>🕘 历史版本</button>
            <button className="danger" onClick={() => delNode(menu.id)}>🗑 删除节点</button>
          </div>
        )}
        {connectMenu && (
          <div className="ctx-menu" style={{ left: connectMenu.x, top: connectMenu.y }}>
            <div className="ctx-title">接一个…</div>
            {(CONNECT_SUGGEST[bnodes[connectMenu.src]?.type] ?? []).map(s => (
              <button key={s.type + s.label} onClick={() => createFromConnect(s.type)}>{s.label}</button>
            ))}
            <button className="danger" onClick={() => setConnectMenu(null)}>取消</button>
          </div>
        )}
        {revisionScene && (
          <div className="lightbox" onClick={() => setRevisionScene(null)}>
            <div className="lightbox-inner" style={{ maxWidth: 560 }} onClick={e => e.stopPropagation()}>
              <div className="lightbox-head">
                <b>{t('退回场景：')}{String(revisionScene.title)}</b>
                <button onClick={() => setRevisionScene(null)}>✕ {t('关闭')}</button>
              </div>
              <div className="field"><label>{t('修改意见（必填）')}</label>
                <textarea rows={3} value={revisionComment} onChange={e => setRevisionComment(e.target.value)}
                  placeholder={t('例如：缩短波形展示并调整口播')} /></div>
              <div className="field"><label>{t('新时长（秒）')}</label>
                <input type="number" min={1} max={300} value={revisionSeconds}
                  onChange={e => setRevisionSeconds(Number(e.target.value))} /></div>
              <div className="field"><label>{t('制作路线')}</label>
                <select value={revisionRoute} onChange={e => setRevisionRoute(e.target.value)}>
                  <option value="diagram_2d">{t('确定性 2D')}</option>
                  <option value="live_action">{t('真实素材')}</option>
                  <option value="slide_composite">{t('课件合成')}</option>
                </select></div>
              <div className="field"><label>{t('新口播')}</label>
                <textarea rows={4} value={revisionNarration} onChange={e => setRevisionNarration(e.target.value)} /></div>
              <button className="accent" disabled={productionBusy || !revisionComment.trim()}
                onClick={() => produceTeachingVideo(true)}>{t('生成送修候选')}</button>
            </div>
          </div>
        )}
        {lightbox && (
          <div className="lightbox" onClick={() => setLightbox(null)}>
            <div className="lightbox-inner" onClick={e => e.stopPropagation()}>
              <div className="lightbox-head">
                <b>{lightbox.title}</b>
                <button onClick={() => setLightbox(null)}>✕ 关闭（Esc）</button>
              </div>
              {lightbox.url.endsWith('.mp4')
                ? <video src={assetUrl(lightbox.url)} controls autoPlay />
                : <img src={assetUrl(lightbox.url)} alt="" />}
            </div>
          </div>
        )}
        {versionDlg && (
          <div className="lightbox" onClick={() => setVersionDlg(null)}>
            <div className="lightbox-inner" onClick={e => e.stopPropagation()}>
              <div className="lightbox-head">
                <b>🕘 历史版本（点击封面切换，新→旧）</b>
                <button onClick={() => setVersionDlg(null)}>✕ 关闭</button>
              </div>
              <div className="asset-grid">
                {versionDlg.list.map((v, i) => (
                  <div key={v.id} className="asset-item" title={v.created_at || v.id}
                    onClick={async () => {
                      try {
                        await api.useNodeVersion(versionDlg.nid, v.id)
                        setVersionDlg(null)
                        await syncGraph(projectRef.current)
                        say(`已切换到第 ${i + 1} 个版本（共 ${versionDlg.list.length} 个，新→旧）`)
                      } catch (e) { say(`切换失败: ${e}`) }
                    }}>
                    {v.kind === 'image'
                      ? <img src={assetUrl(v.url)} alt="" loading="lazy" />
                      : <video src={assetUrl(v.url)} muted />}
                    <span className="kind">{i === 0 ? '最新' : `v-${i}`}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
      <div className="resizer" role="separator" aria-label="调整右侧详情宽度" aria-orientation="vertical"
        aria-valuenow={rightW} aria-valuemin={300} aria-valuemax={720} tabIndex={0}
        title="拖动调整宽度；方向键微调；双击恢复三栏默认宽度"
        onPointerDown={startResize('right')} onKeyDown={resizeKeys('right')} onDoubleClick={resetWidths} />
      <div className="inspector" style={{ width: rightW }}>
        <div className="inspector-top">
        <div className="panel-width-controls">
          <span>{t('右栏宽度')}</span>
          <button onClick={() => adjustRightWidth(-60)} disabled={rightW <= 300}>{t('变窄')}</button>
          <button onClick={() => adjustRightWidth(60)} disabled={rightW >= 720}>{t('加宽')}</button>
          <button onClick={resetWidths}>{t('默认')}</button>
        </div>
        {!selected && (
          <div className="muted">
            {t('点击画布节点可在此编辑参数；')}<br />
            {t('或直接在下方对话，让 Agent 继续创作。')}
          </div>
        )}
        {selected && taskMode && teachingNode && (
          <h2>{selectedSceneTitle ? `📍 ${localizedContent(selectedSceneTitle, language)}` : `📦 ${t('教学视频任务包')}`}
            <span className="status-chip running">
              {(teachingNode.outputs.importReview as { confirmation?: unknown }).confirmation ? t('方案已确认 · 无需付费生成') : t('已接收 · 待方案确认')}
            </span>
          </h2>
        )}
        {selected && meta && !taskMode && (
          <>
            <h2>{meta.icon} {selected.title || meta.label}
              <span className={`status-chip ${selected.status}`}>{
                { idle: '未执行', running: '执行中', succeeded: '已完成', failed: '失败' }[selected.status] ?? selected.status
              }</span>
            </h2>
            <div className="hint">{meta.hint}</div>
            {selected.type !== 'qc' &&
              (selected.outputs?.qc as { verdict?: string; qc_node_id?: string } | undefined)?.verdict === 'reject' && (
              <div className="qc-alert">
                <div>⛔ 本节点成果被质检判不合格。整改：在下方修改提示词 → 保存 → ▶ 重新生成 → 回质检节点 ▶ 复检（或人工放行）。</div>
                <button onClick={() => {
                  const qid = (selected.outputs?.qc as { qc_node_id?: string })?.qc_node_id
                  if (qid) setSelectedId(qid)
                }}>📋 查看质检报告（不合格原因）</button>
              </div>
            )}
            {meta.fields.map(renderField)}
            {selected.type === 'code_render' &&
              (CR_FIELDS[String(draft.template ?? 'lens_focus')] ?? []).map(renderField)}
            {selected.type === 'image' && (
              <div className="field">
                <label>参考图（可选：保持参考图不变做增改，如"同一镜头剖面加光路"）</label>
                <select value={String(draft.ref_asset_url ?? '')}
                  onChange={e => setDraft(d => ({ ...d, ref_asset_url: e.target.value }))}>
                  <option value="">无（纯文生图）</option>
                  {imageAssets.filter(a => a.url !== selected.outputs?.asset_url)
                    .map(a => <option key={a.url} value={a.url}>{a.name}</option>)}
                </select>
              </div>
            )}
            {selected.type === 'video' && (
              <>
                <div className="field">
                  <label>首帧</label>
                  <select value={String(draft.first_frame_url ?? '')}
                    onChange={e => setDraft(d => ({ ...d, first_frame_url: e.target.value }))}>
                    <option value="">自动取上游图像节点成果</option>
                    {imageAssets.map(a => <option key={a.url} value={a.url}>{a.name}</option>)}
                  </select>
                </div>
                <div className="field">
                  <label>尾帧（可选：选了即为首尾帧模式，适合原理动画）</label>
                  <select value={String(draft.last_frame_url ?? '')}
                    onChange={e => setDraft(d => ({ ...d, last_frame_url: e.target.value }))}>
                    <option value="">无</option>
                    {imageAssets.map(a => <option key={a.url} value={a.url}>{a.name}</option>)}
                  </select>
                </div>
              </>
            )}
            <div className="row">
              <button onClick={save}>保存</button>
              <button onClick={runSelf} disabled={selected.status === 'running'}>▶ 只跑本节点</button>
              <button className="primary" onClick={async () => {
                await api.updateNode(selectedId, { inputs: draft })
                try {
                  await api.updateNode(selectedId, { inputs: draft })  // 先存后跑
                  const r = await api.executeStep(selectedId)
                  say(`单步执行中：${r.ran_title}（本步完成后停下等你验收，还剩 ${r.remaining} 步）`)
                } catch (e) { say(`执行失败: ${e}`) }
                await syncGraph(projectRef.current)
              }} disabled={selected.status === 'running'}>⏭ 单步</button>
              <button className="primary" onClick={runChainSelected}
                disabled={selected.status === 'running'}>⏩ 自动到此</button>
            </div>
            <div className="hint" style={{ fontSize: 11 }}>
              ⏭ 单步 = 每次只跑链上下一个未完成节点，跑完停下让你预览验收，满意再点下一步；⏩ 自动 = 一口气跑到本节点
            </div>
            <div className="row">
              <button onClick={async () => {
                const c = await api.createNode(projectRef.current, {
                  type: selected.type, title: (selected.title || meta.label) + ' 副本',
                  inputs: draft, position: { x: selected.position_x + 60, y: selected.position_y + 80 },
                })
                await syncGraph(projectRef.current)
                setSelectedId(c.id)
                say('已复制节点（参数一并复制）')
              }}>⧉ 复制节点</button>
              <button className="danger" onClick={() => delNode(selected.id)}>🗑 删除节点</button>
            </div>
            {selected.type === 'image' && (
              <div className="field">
                <label>或：上传本地图片（TapNow / GPT Image 网页出的图）</label>
                <input ref={fileRef} type="file" accept="image/*"
                  onChange={e => onUpload(e.target.files?.[0])} />
              </div>
            )}
            {selected.type === 'ref_video' && (
              <div className="field">
                <label>上传参考视频（mp4，上传后点 ▶ 只跑本节点开始分析）</label>
                <input ref={fileRef} type="file" accept="video/mp4,video/quicktime,video/webm"
                  onChange={e => onUpload(e.target.files?.[0])} />
              </div>
            )}
            {typeof selected.outputs?.asset_url === 'string' && (
              <div className="preview-wrap">
                {(selected.outputs.asset_url as string).endsWith('.mp4')
                  ? <video src={assetUrl(selected.outputs.asset_url as string)} controls />
                  : <img src={assetUrl(selected.outputs.asset_url as string)} alt=""
                      title="点击放大预览"
                      onClick={() => setLightbox({ url: selected.outputs.asset_url as string, title: selected.title || '' })} />}
                <button className="zoom-btn" onClick={() =>
                  setLightbox({ url: selected.outputs.asset_url as string, title: selected.title || '' })
                }>🔍 放大预览</button>
              </div>
            )}
            {selected.outputs?.script != null && <ScriptView script={selected.outputs.script} />}
            {selected.outputs?.teachingVideo != null && (
              <TeachingVideoView data={selected.outputs.teachingVideo} review={selected.outputs.importReview}
                onConfirm={confirmTeachingPlan} confirming={teachingConfirmBusy}
                production={selected.outputs.production as TeachingProduction} onProduce={() => produceTeachingVideo(false)} producing={productionBusy}
                onRevise={scene => {
                  setRevisionScene(scene); setRevisionComment(''); setRevisionSeconds(Number(scene.targetSeconds))
                  setRevisionRoute(String(scene.route)); setRevisionNarration(String(scene.narration || ''))
                }} />
            )}
            {selected.outputs?.storyboard != null && (
              <StoryboardView sb={selected.outputs.storyboard} onExpand={async () => {
                try {
                  const r = await api.expandStoryboard(selected.id)
                  await syncGraph(projectRef.current)
                  focusAll()
                  say(`已展开 ${r.shots} 个镜头、共 ${r.created} 个节点：帧→质检→视频→质检→拼接。建议 ⏭ 单步逐步验收`)
                } catch (e) { say(`展开失败: ${e}`) }
              }} />
            )}
            {selected.outputs?.motion_card != null && (
              <MotionCardView card={selected.outputs.motion_card as Record<string, unknown>} />
            )}
            {selected.type === 'ref_video' && selected.outputs?.intake != null && (() => {
              const ik = selected.outputs.intake as { kind: string; label: string; default_route: string; rationale: string; faces_ratio: number; speech_chars: number; moves?: string[]; rights?: string[] }
              const tr = selected.outputs.transcript as { text?: string } | undefined
              return (
                <div className="intake-card">
                  <b>📋 素材体检报告</b>
                  <div>类型：<b>{ik.label}</b>（人脸段占比 {Math.round(ik.faces_ratio * 100)}% · 语音 {ik.speech_chars} 字）</div>
                  <div>建议路线：<b className="route">{ik.default_route}</b> —— {ik.rationale}</div>
                  {(ik.rights ?? []).length > 0 && (
                    <div className="rights">⚖ {ik.rights![0]}</div>
                  )}
                  {tr?.text && (
                    <details><summary>📝 讲稿转写（{tr.text.length}字，点开预览）</summary>
                      <div className="transcript">{tr.text.slice(0, 600)}{tr.text.length > 600 ? '…' : ''}</div>
                    </details>
                  )}
                  {tr?.text && (
                    <button className="accent" onClick={async () => {
                      say('R4 重演绎：正在把讲稿转成微课脚本（约1分钟）…')
                      const r = await api.scriptFromRef(selected.id)
                      say('R4 脚本已生成——审阅修改后接分镜即可')
                      await syncGraph(projectRef.current)
                      setSelectedId(r.script_node)
                      setTimeout(() => flowRef.current?.fitView({
                        nodes: [{ id: r.script_node }], padding: 0.4, duration: 400 }), 400)
                    }}>📝 R4 内容重演绎（讲稿→微课脚本）</button>
                  )}
                </div>
              )
            })()}
            {selected.type === 'ref_video' && Array.isArray(selected.outputs?.segments)
              && (selected.outputs.segments as unknown[]).length > 0 && (
              <button className="accent" style={{ marginTop: 8 }} onClick={async () => {
                const r = await api.storyboardFromRef(selected.id)
                say(`已按参考视频生成分镜（${r.shots} 镜，含真实帧锚点与 R1/R2/R3 路线）`)
                await syncGraph(projectRef.current)
                setSelectedId(r.storyboard_node)
                setTimeout(() => flowRef.current?.fitView({
                  nodes: [{ id: r.storyboard_node }], padding: 0.4, duration: 400 }), 400)
              }}>🎬 按参考视频生成分镜（复刻产线）</button>
            )}
            {selected.type === 'qc' && selected.outputs?.verdict != null && (
              <QcReportView out={selected.outputs}
                onGoTarget={tid => setSelectedId(tid)}
                onAdopt={async (tid, prompt) => {
                  const t = bnodesRef.current[tid]
                  if (!t) { say('被检节点不存在'); return }
                  // 编辑式节点（有 ref_node/edit_delta）：建议写入 edit_delta 走最小编辑通道
                  const isEdit = Boolean((t.inputs as Record<string, unknown>).ref_node
                    || (t.inputs as Record<string, unknown>).edit_delta)
                  const patch = isEdit ? { edit_delta: prompt } : { prompt }
                  await api.updateNode(tid, { inputs: { ...t.inputs, ...patch } })
                  try { await api.executeNode(tid) } catch (e) { say(`执行失败: ${e}`) }
                  await syncGraph(projectRef.current)
                  say(isEdit ? '已采纳为编辑式变化指令（以首帧为基准最小编辑），生成后回质检节点 ▶ 复检'
                             : '已采纳建议提示词，正在重新生成；完成后回本质检节点 ▶ 复检')
                }}
                onOverride={async v => {
                const orig = String(selected.outputs?.verdict ?? '')
                const human = v === 'pass_human' ? 'pass' : 'reject'
                if (orig && orig !== human) {
                  // 与机器原判不一致 → 弹改判沉淀窗（飞轮入料）
                  setCorrFailType(orig === 'reject' ? '误判' : orig === 'pass' ? '漏判' : '标准不清')
                  setCorrReason('')
                  setOverrideDlg({ nid: selected.id, verdict: v, orig })
                  return
                }
                await api.qcOverride(selected.id, v)
                await syncGraph(projectRef.current)
                say(v === 'pass_human' ? '已人工放行' : '已人工判不合格')
              }} />
            )}
            {typeof selected.outputs?.error === 'string' && (
              <div className="err">✖ {selected.outputs.error as string}</div>
            )}
          </>
        )}
        {selected && taskMode && selected.outputs?.teachingVideo != null && (
          <TeachingVideoView data={selected.outputs.teachingVideo} review={selected.outputs.importReview}
            sceneId={selectedSceneId} onConfirm={confirmTeachingPlan} confirming={teachingConfirmBusy}
            production={selected.outputs.production as TeachingProduction} onProduce={() => produceTeachingVideo(false)} producing={productionBusy}
            onRevise={scene => {
              setRevisionScene(scene); setRevisionComment(''); setRevisionSeconds(Number(scene.targetSeconds))
              setRevisionRoute(String(scene.route)); setRevisionNarration(String(scene.narration || ''))
            }} />
        )}
        </div>
        <div className="agent-dock">
          <div className="chat-log">
            {chat.length === 0 && (
              <div className="muted">{t('和 Agent 对话继续创作：加镜头、改脚本、重新规划……')}<br />{t('例：给这条微课加一段 PWM 波形的代码渲染镜头')}</div>
            )}
            {chat.map((m, i) => (
              <div key={i} className={`msg ${m.role}`}>{m.text}</div>
            ))}
            {agentBusy && <div className="msg assistant">{t('规划中…')}</div>}
          </div>
          <div className="dock-input">
            <textarea rows={2} value={chatInput} placeholder={t('和 Agent 说下一步（回车发送）')}
              onChange={e => setChatInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendAgent() } }} />
            <button className="send" disabled={agentBusy} onClick={() => sendAgent()} title={t('发送')}>
              {agentBusy ? '…' : '↑'}
            </button>
          </div>
          <div className="dock-foot">
            {agentModels.length > 0 && (
              <select value={agentModel} onChange={e => {
                setAgentModel(e.target.value)
                localStorage.setItem('jojo_agent_model', e.target.value)
              }}>
                {agentModels.map(m => <option key={m.model} value={m.model}>{localizedContent(m.label, language)}</option>)}
              </select>
            )}
            <label className="check-line" title={t('先联网查真实事实再规划（需开通方舟联网插件）')}>
              <input type="checkbox" checked={agentResearch} onChange={e => {
                setAgentResearch(e.target.checked)
                localStorage.setItem('jojo_agent_research', e.target.checked ? '1' : '')
              }} />
              🌐 {t('联网调研')}
            </label>
          </div>
        </div>
      </div>
    </div>
  )
}

function ScriptView({ script }: { script: unknown }) {
  const s = script as { title?: string; duration_seconds?: number; segments?: { index: number; narration: string; visual: string; seconds: number }[] }
  return (
    <div className="result">
      <label>脚本：{s.title}（{s.duration_seconds}s）</label>
      {s.segments?.map(seg => (
        <div key={seg.index} className="seg">
          <b>#{seg.index}（{seg.seconds}s）</b>
          <div>🎙 {seg.narration}</div>
          <div className="muted">🎨 {seg.visual}</div>
        </div>
      ))}
    </div>
  )
}

function QcReportView({ out, onOverride, onGoTarget, onAdopt }: {
  out: Record<string, unknown>
  onOverride: (v: 'pass_human' | 'reject_human') => void
  onGoTarget?: (targetNodeId: string) => void
  onAdopt?: (targetNodeId: string, prompt: string) => void
}) {
  const frames = (out.frames as string[]) ?? []
  const results = (out.results as { id: string; verdict: string; confidence?: number; evidence?: string; on_fail?: string }[]) ?? []
  const checklist = (out.checklist as { id: string; name: string; severity: string; check: string }[]) ?? []
  const names: Record<string, string> = {}
  checklist.forEach(c => { names[c.id] = c.name })
  const mark = (v: string) => v === 'pass' ? '✅' : v === 'fail' ? '⛔' : '❓'
  return (
    <div className="result">
      <label>质检报告（{out.mode === 'manual' ? '人工验收模式' : '自动裁决'}）</label>
      {typeof out.summary === 'string' && out.summary && <div className="seg">{out.summary}</div>}
      {typeof out.note === 'string' && out.note && <div className="seg">{out.note}</div>}
      <div className="qc-frames">
        {frames.map(f => <img key={f} src={assetUrl(f)} alt="" />)}
      </div>
      {out.mode === 'manual' && checklist.length > 0 && (
        <div className="seg">
          <b>验收清单（对照上方帧图逐条检查）：</b>
          {checklist.map(c => (
            <div key={c.id}>{c.severity === 'blocker' ? '🔴' : c.severity === 'warning' ? '🟡' : '⚪'} [{c.id}] {c.name}：{c.check}</div>
          ))}
        </div>
      )}
      {results.map((r, i) => (
        <div key={i} className="seg">
          {mark(r.verdict)} <b>{names[r.id] ?? r.id}</b>
          {typeof r.confidence === 'number' && ` · 置信度 ${(r.confidence * 100).toFixed(0)}%`}
          {r.evidence && <div className="muted">{r.evidence}</div>}
          {r.verdict === 'fail' && r.on_fail && <div style={{ color: '#f59e0b' }}>💡 {r.on_fail}</div>}
        </div>
      ))}
      {typeof out.suggested_prompt === 'string' && out.suggested_prompt.trim() !== '' && (
        <div className="seg suggest">
          <b>💡 机器建议的修正提示词</b>
          <div className="muted" style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>
            {out.suggested_prompt as string}
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            {onAdopt && typeof out.target_node_id === 'string' && (
              <button className="adopt" onClick={() =>
                onAdopt(out.target_node_id as string, String(out.suggested_prompt))}>
                ✅ 采纳并重新生成
              </button>
            )}
            <button onClick={() => navigator.clipboard.writeText(String(out.suggested_prompt))}>
              📋 复制
            </button>
          </div>
        </div>
      )}
      <div className="row">
        <button onClick={() => onOverride('pass_human')}>✅ 人工放行</button>
        <button className="danger" onClick={() => onOverride('reject_human')}>⛔ 人工判不合格</button>
        {onGoTarget && typeof out.target_node_id === 'string' && (
          <button className="primary" onClick={() => onGoTarget(out.target_node_id as string)}>
            ✏️ 去修改并重新生成
          </button>
        )}
      </div>
      <div className="muted" style={{ fontSize: 11 }}>
        机器裁决仅供参考，最终放行权在人（终裁会记录在报告中）。<br />
        整改流程：✏️ 去被检节点改提示词 → 保存 → ▶ 重新生成 → 回到本质检节点 ▶ 复检；
        轻微瑕疵可直接 ✅ 人工放行。
      </div>
    </div>
  )
}

function MotionCardView({ card }: { card: Record<string, unknown> }) {
  const rows: [string, string][] = [
    ['场景', String(card.scene_summary ?? '')],
    ['运动主体', String(card.subject ?? '')],
    ['轨迹', String(card.trajectory ?? '')],
    ['速度变化', String(card.speed_profile ?? '')],
    ['机位', String(card.camera ?? '')],
  ]
  const moments = (card.key_moments as { time_ratio: number; desc: string }[]) ?? []
  return (
    <div className="result">
      <label>🎥 运动特征卡（已剥离真实人物身份）</label>
      {rows.filter(r => r[1]).map(([k, v]) => (
        <div key={k} className="seg"><b>{k}</b>：{v}</div>
      ))}
      {moments.length > 0 && (
        <div className="seg">
          <b>关键瞬间</b>
          {moments.map((m, i) => (
            <div key={i}>· {(m.time_ratio * 100).toFixed(0)}%：{m.desc}</div>
          ))}
        </div>
      )}
      {typeof card.generation_prompt_zh === 'string' && (
        <div className="seg"><b>生成提示词（中文对照）</b><div className="muted">{card.generation_prompt_zh}</div></div>
      )}
      {typeof card.generation_prompt_en === 'string' && (
        <div className="seg">
          <b>生成提示词（下游视频节点留空自动引用）</b>
          <div className="muted">{card.generation_prompt_en}</div>
          <button style={{ marginTop: 6 }} onClick={() => {
            navigator.clipboard.writeText(String(card.generation_prompt_en))
          }}>📋 复制</button>
        </div>
      )}
    </div>
  )
}

function TeachingTimeline({ data, review, selectedSceneId, onOpen }: {
  data: unknown; review: unknown; selectedSceneId: string; onOpen: (sceneId: string) => void }) {
  const { t, language } = useI18n()
  const value = data as { manifest?: { teachingGoal?: string; packageVersion?: string }
    scenes?: { sceneId: string; title: string; targetSeconds: number; route: string
      narration?: string; visual?: string }[] }
  const audit = review as { status?: string; confirmation?: { confirmationId: string; paidGeneration: string } }
  return (
    <div className="teaching-timeline">
      <div className="timeline-head">
        <div>
          <span className="eyebrow">{t('教学视频任务包')} · {value.manifest?.packageVersion}</span>
          <h2>{localizedContent(value.manifest?.teachingGoal, language)}</h2>
        </div>
        <span className="status-chip running">
          {audit.confirmation ? t('方案已确认 · 无需付费生成') : t('已接收 · 待方案确认')}
        </span>
      </div>
      <div className="timeline-track">
        {value.scenes?.map((scene, index) => (
          <button key={scene.sceneId} className={`timeline-scene ${selectedSceneId === scene.sceneId ? 'selected' : ''}`}
            onClick={() => onOpen(scene.sceneId)}>
            <span className="scene-order">{index + 1}</span>
            <span className="scene-time">{scene.targetSeconds}s</span>
            <b>{localizedContent(scene.title, language)}</b>
            <span className="scene-route">{localizedRoute(scene.route, language)}</span>
            <span className="scene-narration">{localizedContent(scene.narration || scene.visual, language)}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

function TeachingVideoView({ data, review, sceneId, onConfirm, confirming, production, onProduce, producing, onRevise }: {
  data: unknown; review: unknown; sceneId?: string; onConfirm: () => void; confirming: boolean
  production?: TeachingProduction
  onProduce: () => void; producing: boolean; onRevise: (scene: Record<string, unknown>) => void }) {
  const { t, language } = useI18n()
  const value = data as { manifest?: { packageId?: string; packageVersion?: string; teachingGoal?: string; contentLanguage?: string }
    scenes?: { sceneId: string; title: string; targetSeconds: number; route: string; routeReason: string
      assertions?: string[]; routeStatus?: string; evidenceRefs?: string[]; narrationRefs?: string[]
      slideRefs?: string[]; visualSpec?: Record<string, unknown> }[] }
  const scenes = value.scenes?.filter(scene => !sceneId || scene.sceneId === sceneId)
  const audit = review as { importTaskId?: string; packageHash?: string; status?: string
    missingEvidence?: { description: string; required: boolean }[]; fixtureOnly?: boolean
    confirmation?: { confirmationId: string; planHash: string; paidGeneration: string; confirmedAt: string } }
  return (
    <div className="result">
      <label>{t('教学视频任务包')} · {value.manifest?.packageId} / {value.manifest?.packageVersion}</label>
      <div>{localizedContent(value.manifest?.teachingGoal, language)}</div>
      <div className="muted">{t('导入任务：')}{audit.importTaskId} · {t('状态：')}{localizedContent(audit.status, language)}</div>
      <div className="muted">{t('包哈希：')}{audit.packageHash}</div>
      {!audit.confirmation && (
        <button className="accent" disabled={confirming} onClick={onConfirm}>
          {confirming ? t('正在确认…') : t('确认方案（无需付费生成）')}
        </button>
      )}
      {audit.confirmation && (
        <div className="seg">
          <b>{t('方案已确认 · 无需付费生成')}</b>
          <div>{t('确认记录：')}{audit.confirmation.confirmationId}</div>
          <div className="muted">{t('方案哈希：')}{audit.confirmation.planHash}</div>
          <div className="muted">{t('确认时间：')}{audit.confirmation.confirmedAt}</div>
        </div>
      )}
      {audit.confirmation && !production && (
        <button className="accent" disabled={producing} onClick={onProduce}>
          {producing ? t('正在生成…') : t('生成候选视频（无付费）')}
        </button>
      )}
      {production?.status === 'running' && <div className="seg">{t('候选视频正在后台生成，页面会自动刷新。')}</div>}
      {production?.status === 'failed' && <div className="err">{t('候选生成失败，请查看任务记录。')}</div>}
      {production?.status === 'pending_acceptance' && (
        <div className="seg">
          <b>{language === 'zh' ? '候选视频' : 'Candidate video'} v{production.version} · {t('待教学验收')}</b>
          {production.videoUrl && <video src={assetUrl(production.videoUrl)} controls style={{ width: '100%' }} />}
          {language === 'en' && (value.manifest?.contentLanguage ?? 'zh-CN').startsWith('zh') && <div className="muted">{t('原始候选视频语言：中文；切换中文可查看原始口播与证据文本。')}</div>}
          <div className="muted">{t('成片已生成，尚未表示教学验收通过。')}</div>
          {production.subtitleAssetId && <a href={assetUrl(`/assets/${production.subtitleAssetId}.srt`)} download>{language === 'en' && (value.manifest?.contentLanguage ?? 'zh-CN').startsWith('zh') ? 'Download Chinese SRT subtitles' : t('下载字幕 SRT')}</a>}
          {(production.unchangedSceneIds?.length ?? 0) > 0 &&
            <div className="muted">{t('未修改场景哈希保持一致：')}{production.unchangedSceneIds!.join('、')}</div>}
          {(production.missingEvidence?.length ?? 0) > 0 &&
            <div className="muted">{t('未生成场景：')}{production.missingEvidence!.map(x => `${x.sceneId} (${localizedContent(x.reason, language)})`).join(language === 'zh' ? '；' : '; ')}</div>}
        </div>
      )}
      {audit.fixtureOnly && <div className="muted">{t('本包含合成契约素材，不能作为真实课堂验收证据。')}</div>}
      {(audit.missingEvidence?.length ?? 0) > 0 && (
        <div className="seg"><b>{t('待补项')}</b>{audit.missingEvidence!.map((item, i) =>
          <div key={i}>{item.required ? t('必需') : t('可选')}：{localizedContent(item.description, language)}</div>)}</div>
      )}
      {scenes?.map(scene => (
        <div key={scene.sceneId} className="seg">
          <b>{localizedContent(scene.title, language)} · {scene.targetSeconds}s</b>
          <div>{language === 'zh' ? '路线：' : 'Route: '}{localizedRoute(scene.route, language)} —— {localizedContent(scene.routeReason, language)}</div>
          {scene.routeStatus === 'needs_review' && <div className="muted">{t('方案与教学断言待老师复核')}</div>}
          {(scene.evidenceRefs?.length ?? 0) > 0 && <div className="muted">{t('依据：')}{scene.evidenceRefs!.join('、')}</div>}
          {(scene.narrationRefs?.length ?? 0) > 0 && <div className="muted">{t('口播定位：')}{scene.narrationRefs!.join('、')}</div>}
          {(scene.slideRefs?.length ?? 0) > 0 && <div className="muted">{t('课件定位：')}{scene.slideRefs!.join('、')}</div>}
          {(scene.assertions ?? []).map((assertion, i) => <div key={i} className="muted">{t('核查：')}{localizedContent(assertion, language)}</div>)}
          {scene.visualSpec && <details><summary>{t('科学场景规格')}</summary>
            <pre>{JSON.stringify(scene.visualSpec, null, 2)}</pre></details>}
          {production?.status === 'pending_acceptance' && (
            <button onClick={() => onRevise(scene)}>{t('退回此场景')}</button>
          )}
        </div>
      ))}
    </div>
  )
}

function StoryboardView({ sb, onExpand }: { sb: unknown; onExpand?: () => void }) {
  const s = sb as { shots?: { index: number; type: string; motion: string; seconds: number;
    first_frame_prompt?: string; narration?: string; visual?: string; review_notes?: string
    route_status?: string; assertion_status?: string }[] }
  const pending = s.shots?.some(shot => shot.route_status === 'needs_review' || shot.assertion_status === 'needs_review')
  return (
    <div className="result">
      <label>分镜（{s.shots?.length} 个镜头）</label>
      {onExpand && (
        <>
          <button className="expand-btn" onClick={onExpand} disabled={pending}>
            {pending ? '路线与断言待复核' : '⚡ 展开为逐镜生产线'}
          </button>
          <div className="muted" style={{ fontSize: 11, lineHeight: 1.6 }}>
            {pending ? '导入稿只作为候选。请先复核制作路线并补齐教学断言，本版不会自动发起付费生成。'
              : '每镜自动生成：首帧(+尾帧) → 帧质检 → 视频 → 视频质检 → 汇入拼接。建议用单步逐步验收。'}
          </div>
        </>
      )}
      {s.shots?.map(shot => (
        <div key={shot.index} className="seg">
          <b>镜头{shot.index}（{shot.seconds}s）</b>
          <span className={`route-badge ${shot.type}`}>
            {shot.type === 'code_render' ? '⚙ 代码渲染' : shot.type === 'live_action' ? '教师实拍' : '✨ AI 生成'}
          </span>
          {shot.route_status === 'needs_review' && <div className="muted">路线待复核 · 教学断言待补齐</div>}
          {shot.visual && <div>{shot.visual}</div>}
          {shot.narration && <div className="muted">口播：{shot.narration}</div>}
          {shot.motion && <div>{shot.motion}</div>}
          {shot.review_notes && <div className="muted">核对：{shot.review_notes}</div>}
        </div>
      ))}
    </div>
  )
}
