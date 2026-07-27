import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ReactFlow, Background, Controls, MiniMap, Handle, Position,
  applyNodeChanges, type Node, type Edge, type NodeChange,
  type Connection, type NodeProps, type ReactFlowInstance,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { api } from './api'

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
    hint: '连接上游脚本节点后执行，自动拆分镜头并标注 AI 生成 / 代码渲染双路线',
    fields: [],
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
      { key: 'resolution', label: '分辨率（1080p 需 2.0 正式版，暂不可选）', kind: 'select', options: ['480p', '720p'] },
      { key: 'duration', label: '时长（秒）', kind: 'select', options: ['3', '5', '10'] },
      { key: 'skip_pair_check', label: '跳过首尾帧配对预检（已人工确认两帧可用时选"是"）', kind: 'select', options: ['否', '是'] },
    ],
  },
  code_render: {
    label: '代码渲染', icon: '⚙',
    hint: '严格几何/波形动画：每帧按公式计算、内置物理断言，本地渲染零费用。适合原理演示镜头',
    fields: [
      { key: 'template', label: '模板', kind: 'select', options: ['lens_focus', 'pwm_waveform', 'spectrum_recipe', 'block_diagram', 'rotary_drill_station'] },
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
      { key: 'domain', label: '领域规则包', kind: 'select', options: ['optics', 'mechanics', 'kinematics', 'general'] },
      { key: 'shot_index', label: '对应分镜第几镜（取其断言）', kind: 'number' },
    ],
  },
  ref_video: {
    label: '参考视频', icon: '🎥',
    hint: '上传你有权观看的参考镜头（录屏/下载），分析后输出"运动特征卡"（轨迹/速度/机位，自动剥离真实人物身份）。下游视频节点提示词留空即自动引用特征卡',
    fields: [
      { key: 'focus', label: '分析重点', kind: 'textarea', placeholder: '例：只分析足球的飞行轨迹、速度变化和机位运动，忽略球员' },
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
          <img src={out.asset_url} alt="" draggable={false} />
        )}
        {typeof out.asset_url === 'string' && (out.asset_url as string).endsWith('.mp4') && (
          <video src={out.asset_url as string} muted loop autoPlay playsInline />
        )}
        {out.script != null && <div className="ok">📄 脚本已生成，点击查看</div>}
        {out.storyboard != null && <div className="ok">🎞 分镜已生成，点击查看</div>}
        {out.motion_card != null && <div className="ok">🎥 运动特征卡已提取</div>}
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
        {b.status === 'running' && <div className="running-line">生成中…</div>}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  )
}

const nodeTypes = { jojo: JojoNode }

export default function App() {
  const [projectId, setProjectId] = useState('')
  const [projects, setProjects] = useState<{ id: string; title: string }[]>([])
  const [bnodes, setBnodes] = useState<Record<string, BNode>>({})
  const [rfNodes, setRfNodes] = useState<Node[]>([])
  const [rfEdges, setRfEdges] = useState<Edge[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const [toast, setToast] = useState('')
  const [leftW, setLeftW] = useState(250)
  const [rightW, setRightW] = useState(390)
  const [tab, setTab] = useState<'nodes' | 'assets'>('nodes')
  const [view, setView] = useState<'home' | 'canvas' | 'review'>('home')
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
  const [chat, setChat] = useState<{ role: string; text: string }[]>([])
  const [chatInput, setChatInput] = useState('')
  const [agentBusy, setAgentBusy] = useState(false)
  const [agentModels, setAgentModels] = useState<{ label: string; model: string }[]>([])
  const [agentModel, setAgentModel] = useState(localStorage.getItem('jojo_agent_model') ?? '')
  const [agentResearch, setAgentResearch] = useState(!!localStorage.getItem('jojo_agent_research'))
  const [assets, setAssets] = useState<{ id: string; kind: string; url: string; starred?: boolean }[]>([])
  const [assetScope, setAssetScope] = useState<'project' | 'starred'>('project')
  const [menu, setMenu] = useState<{ x: number; y: number; id: string } | null>(null)
  const flowRef = useRef<ReactFlowInstance | null>(null)
  const focusAll = () => setTimeout(() => flowRef.current?.fitView({ padding: 0.25, duration: 400 }), 80)
  const undoRef = useRef<{ nodes: BNode[]; edges: { id: string; source: string; target: string }[] }[]>([])
  const redoRef = useRef<typeof undoRef.current>([])
  const fileRef = useRef<HTMLInputElement>(null)
  const bnodesRef = useRef(bnodes)
  bnodesRef.current = bnodes
  const rfEdgesRef = useRef(rfEdges)
  rfEdgesRef.current = rfEdges
  const draftRef = useRef(draft)
  draftRef.current = draft
  const selectedIdRef = useRef(selectedId)
  selectedIdRef.current = selectedId

  // 拖动分隔条调整左右面板宽度
  const startResize = (side: 'left' | 'right') => (e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startL = leftW, startR = rightW
    const move = (ev: MouseEvent) => {
      if (side === 'left') setLeftW(Math.min(440, Math.max(56, startL + ev.clientX - startX)))
      else setRightW(Math.min(680, Math.max(56, startR - (ev.clientX - startX))))
    }
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  const say = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 4000) }

  const syncGraph = useCallback(async (pid: string) => {
    if (!pid) return
    const g = await api.getGraph(pid)
    const map: Record<string, BNode> = {}
    for (const n of g.nodes as BNode[]) map[n.id] = n
    setBnodes(map)
    setRfNodes(prev => (g.nodes as BNode[]).map(n => {
      const old = prev.find(p => p.id === n.id)
      return {
        id: n.id, type: 'jojo',
        position: old?.position ?? { x: n.position_x, y: n.position_y },
        data: { b: n, run: runChain, del: delNode, sel: setSelectedId },
      }
    }))
    setRfEdges((g.edges as { id: string; source_node_id: string; target_node_id: string }[])
      .map(e => ({ id: e.id, source: e.source_node_id, target: e.target_node_id, animated: true, type: 'smoothstep' })))
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

  useEffect(() => {
    (async () => {
      let list = await api.listProjects()
      if (!list.length) list = [await api.createProject('微课示例项目')]
      list = [...list].reverse()   // 最新项目在前
      setProjects(list)
      setProjectId(list[0].id)
      setChat(JSON.parse(localStorage.getItem(`jojo_chat_${list[0].id}`) ?? '[]'))
      const models = await api.agentModels().catch(() => [])
      setAgentModels(models)
      if (models.length && !models.some((m: { model: string }) => m.model === localStorage.getItem('jojo_agent_model'))) {
        setAgentModel(models[0].model)
      }
      await syncGraph(list[0].id)
    })()
  }, [syncGraph])

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
    setProjectId(pid)
    setSelectedId('')
    setRfNodes([])
    undoRef.current = []
    redoRef.current = []
    setChat(JSON.parse(localStorage.getItem(`jojo_chat_${pid}`) ?? '[]'))
    await syncGraph(pid)
    focusAll()
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
      if (r.created > 0) focusAll()
      const parts = []
      if (r.created > 0) parts.push(`新建 ${r.created} 个节点`)
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

  // ── 后端健康监测：离线时全局红条提示 ──
  const [backendUp, setBackendUp] = useState(true)
  useEffect(() => {
    const check = () => fetch('/api/health').then(r => setBackendUp(r.ok)).catch(() => setBackendUp(false))
    check()
    const t = setInterval(check, 8000)
    return () => clearInterval(t)
  }, [])
  const offlineBanner = !backendUp && (
    <div className="offline-banner">
      ⚠ 后端服务未运行——画布可以看，但所有生成/保存都会失败。请双击 jojo-studio 文件夹里的 <b>启动JOJO.bat</b>（会弹出 JOJO-backend-8000 窗口），几秒后本提示自动消失
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
  const loadAssets = useCallback(async () => {
    setAssets(scopeRef.current === 'starred'
      ? await api.listStarred()
      : await api.listAssets(projectRef.current))
  }, [])
  useEffect(() => { if (tab === 'assets') loadAssets() }, [tab, assetScope, loadAssets])

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
      if (Object.values(bnodesRef.current).some(n => n.status === 'running')) {
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

  if (view === 'home') return (
    <div className="home notranslate" translate="no">
      {offlineBanner}
      <div className="home-center">
        <img className="home-logo" src="/jojo-logo.png" alt="JOJO DIRECTOR" />
        <h1 className="home-title">今天要做点什么微课？</h1>
        <div className="home-box">
          <textarea rows={3} value={homeInput}
            placeholder="一句话描述你要的微课，例：讲清楚 PWM 调光原理，60 秒，最后拼成完整视频"
            onChange={e => setHomeInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitHome() } }} />
          <div className="home-box-foot">
            {agentModels.length > 0 && (
              <select value={agentModel} onChange={e => {
                setAgentModel(e.target.value)
                localStorage.setItem('jojo_agent_model', e.target.value)
              }}>
                {agentModels.map(m => <option key={m.model} value={m.model}>{m.label}</option>)}
              </select>
            )}
            <button className="send" disabled={homeBusy || !homeInput.trim()} onClick={submitHome}>
              {homeBusy ? '规划中…' : '↑ 开工'}
            </button>
          </div>
        </div>
        {homeErr && <div className="home-err">{homeErr}</div>}
        <div className="proj-grid">
          <div className="proj-card new" onClick={newProject}>
            <div className="plus">＋</div>
            <div className="pname">新建空白项目</div>
          </div>
          {projects.slice(0, 11).map(p => (
            <div key={p.id} className="proj-card"
              onClick={async () => { await switchProject(p.id); setView('canvas') }}>
              {thumbs[p.id] ? <img src={thumbs[p.id]} alt="" loading="lazy" /> : <div className="ph">🎬</div>}
              <div className="pname">{p.title}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )

  if (view === 'review') return (
    <div className="review notranslate" translate="no">
      {offlineBanner}
      <div className="review-head">
        <button onClick={() => setView('canvas')}>← 返回画布</button>
        <h1>🩺 验收台 · {projects.find(p => p.id === projectId)?.title}</h1>
        <div className="review-actions">
          <button onClick={loadReview}>↻ 刷新</button>
          {review.failures.some(f => f.error_class === 'infra') && (
            <button onClick={retryInfraAll}>🔁 重试全部网络失败
              （{review.failures.filter(f => f.error_class === 'infra').length}）</button>
          )}
          <button className="accent" onClick={resumeLineAll}>▶ 续跑产线</button>
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
              ? <video src={it.asset_url} controls muted />
              : <img src={it.asset_url} alt="" loading="lazy" />)}
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
          <img src="/jojo-logo.png" alt="" />
          <div>
            <h1>JOJO DIRECTOR</h1>
            <div className="sub">职教微课创作画布 v0.5</div>
          </div>
        </div>
        <div className="project-bar">
          <select value={projectId} onChange={e => switchProject(e.target.value)}>
            {projects.map(p => <option key={p.id} value={p.id}>{p.title}</option>)}
          </select>
          <button className="mini" title="新建项目" onClick={newProject}>＋</button>
        </div>
        <div className="tab-bar">
          <button className={tab === 'nodes' ? 'on' : ''} onClick={() => setTab('nodes')}>节点</button>
          <button className={tab === 'assets' ? 'on' : ''} onClick={() => setTab('assets')}>素材库</button>
        </div>
        {tab === 'nodes' && (
          <>
            <button className="accent" onClick={buildChain}>⚡ 一键搭建微课链</button>
            <button onClick={openReview}>🩺 验收台</button>
            <button onClick={tidyLayout}>🧹 一键整理布局</button>
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
              <button key={t} onClick={() => addNode(t)}>{m.icon} 添加{m.label}节点</button>
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
            </div>
            <div className="muted" style={{ fontSize: 11 }}>
              点图片 = 填入选中节点（图像→参考图 / 视频→首帧）· ⭐ 收藏入资产库 · ✕ 删除文件
            </div>
            {assets.length === 0 && (
              <div className="muted">{assetScope === 'starred' ? '还没有收藏的资产，点素材上的 ⭐ 收藏' : '暂无素材'}</div>
            )}
            <div className="asset-grid">
              {assets.map(a => (
                <div key={a.id} className="asset-item" title={a.id}
                  onClick={() => a.kind === 'image' ? useAsset(a.url) : window.open(a.url)}>
                  {a.kind === 'image'
                    ? <img src={a.url} alt="" loading="lazy" />
                    : <video src={a.url} muted />}
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
      <div className="resizer" onMouseDown={startResize('left')} />
      <div className="canvas">
        {(() => {
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
          if (!nums.length) return null
          return (
            <div className="shot-nav">
              {nums.map(k => (
                <button key={k} onClick={() => flowRef.current?.fitView({
                  nodes: groups[k].map(id => ({ id })), padding: 0.25, duration: 300 })}>
                  {k}</button>
              ))}
              <button onClick={() => focusAll()}>全部</button>
            </div>
          )
        })()}
        <ReactFlow
          nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes}
          onInit={inst => { flowRef.current = inst }}
          onNodesChange={onNodesChange} onConnect={onConnect}
          onEdgesDelete={onEdgesDelete} onEdgeDoubleClick={onEdgeDoubleClick}
          onNodeContextMenu={onNodeContextMenu}
          onPaneClick={() => setMenu(null)} onMoveStart={() => setMenu(null)}
          fitView proOptions={{ hideAttribution: true }}
          defaultEdgeOptions={{ type: 'smoothstep', animated: true }}
          deleteKeyCode={['Delete', 'Backspace']}
          connectionRadius={36}
          selectionOnDrag panOnDrag={[1, 2]} panOnScroll
        >
          <Background color="#23262f" />
          <Controls />
          <MiniMap pannable zoomable nodeColor="#2c3040"
            maskColor="rgba(15,17,21,0.75)" style={{ background: '#14161c' }} />
        </ReactFlow>
        {toast && <div className="toast">{toast}</div>}
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
            <button className="danger" onClick={() => delNode(menu.id)}>🗑 删除节点</button>
          </div>
        )}
      </div>
      <div className="resizer" onMouseDown={startResize('right')} />
      <div className="inspector" style={{ width: rightW }}>
        <div className="inspector-top">
        {!selected && (
          <div className="muted">
            点击画布节点可在此编辑参数；<br />
            或直接在下方对话，让 Agent 继续创作。
          </div>
        )}
        {selected && meta && (
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
              (selected.outputs.asset_url as string).endsWith('.mp4')
                ? <video src={selected.outputs.asset_url as string} controls />
                : <img src={selected.outputs.asset_url as string} alt="" />
            )}
            {selected.outputs?.script != null && <ScriptView script={selected.outputs.script} />}
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
                await fetch(`/api/nodes/${selected.id}/qc_override`, {
                  method: 'POST', headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ verdict: v }),
                })
                await syncGraph(projectRef.current)
                say(v === 'pass_human' ? '已人工放行' : '已人工判不合格')
              }} />
            )}
            {typeof selected.outputs?.error === 'string' && (
              <div className="err">✖ {selected.outputs.error as string}</div>
            )}
          </>
        )}
        </div>
        <div className="agent-dock">
          <div className="chat-log">
            {chat.length === 0 && (
              <div className="muted">和 Agent 对话继续创作：加镜头、改脚本、重新规划……<br />例：给这条微课加一段 PWM 波形的代码渲染镜头</div>
            )}
            {chat.map((m, i) => (
              <div key={i} className={`msg ${m.role}`}>{m.text}</div>
            ))}
            {agentBusy && <div className="msg assistant">规划中…</div>}
          </div>
          <div className="dock-input">
            <textarea rows={2} value={chatInput} placeholder="和 Agent 说下一步（回车发送）"
              onChange={e => setChatInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendAgent() } }} />
            <button className="send" disabled={agentBusy} onClick={() => sendAgent()} title="发送">
              {agentBusy ? '…' : '↑'}
            </button>
          </div>
          <div className="dock-foot">
            {agentModels.length > 0 && (
              <select value={agentModel} onChange={e => {
                setAgentModel(e.target.value)
                localStorage.setItem('jojo_agent_model', e.target.value)
              }}>
                {agentModels.map(m => <option key={m.model} value={m.model}>{m.label}</option>)}
              </select>
            )}
            <label className="check-line" title="先联网查真实事实再规划（需开通方舟联网插件）">
              <input type="checkbox" checked={agentResearch} onChange={e => {
                setAgentResearch(e.target.checked)
                localStorage.setItem('jojo_agent_research', e.target.checked ? '1' : '')
              }} />
              🌐 联网调研
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
        {frames.map(f => <img key={f} src={f} alt="" />)}
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

function StoryboardView({ sb, onExpand }: { sb: unknown; onExpand?: () => void }) {
  const s = sb as { shots?: { index: number; type: string; motion: string; seconds: number; first_frame_prompt?: string }[] }
  return (
    <div className="result">
      <label>分镜（{s.shots?.length} 个镜头）</label>
      {onExpand && (
        <>
          <button className="expand-btn" onClick={onExpand}>
            ⚡ 展开为逐镜生产线
          </button>
          <div className="muted" style={{ fontSize: 11, lineHeight: 1.6 }}>
            每镜自动生成：首帧(+尾帧) → 帧质检 → 视频 → 视频质检 → 汇入拼接。
            帧未生成或质检不合格时，视频节点会被关卡拦下；建议用 ⏭ 单步逐步验收。
          </div>
        </>
      )}
      {s.shots?.map(shot => (
        <div key={shot.index} className="seg">
          <b>镜头{shot.index}（{shot.seconds}s）</b>
          <span className={`route-badge ${shot.type}`}>
            {shot.type === 'code_render' ? '⚙ 代码渲染' : '✨ AI 生成'}
          </span>
          <div>{shot.motion}</div>
        </div>
      ))}
    </div>
  )
}
