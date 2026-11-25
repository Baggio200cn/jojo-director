import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

type Language = 'zh' | 'en'
type I18nValue = { language: Language; setLanguage: (language: Language) => void; t: (text: string) => string }

const en: Record<string, string> = {
  '语言': 'Language', '中文': '中文', '英文': 'English',
  '科学实验类创作画布': 'Scientific Scene Studio', '科学场景生产与验收': 'Scientific scene production and review', '正在验证登录状态…': 'Checking your session…',
  '管理员': 'Administrator', '邀请码进入': 'Enter with invite code', '账号': 'Username', '密码': 'Password',
  '输入邀请码，如 JOJO-A1B2C3': 'Invite code, e.g. JOJO-A1B2C3', '登录中…': 'Signing in…', '进入画布': 'Enter studio',
  '邀请码由管理员发放，含每日生成额度；生成费用由平台承担': 'Invite codes are issued by the administrator. Generation is covered by the platform within the configured limits.',
  '邀请码管理': 'Invite code management', '退出': 'Sign out',
  '把科学场景，转化为可验证的教学影像。': 'Turn scientific scenes into verifiable teaching videos.',
  '从教学证据与场景规范出发，调度真实素材、2D、3D、代码与生成式媒体，完成逐镜生产与质量验收。': 'Start with teaching evidence and scene specifications, then coordinate real footage, 2D, 3D, code and generative media for scene-level production and review.',
  '一句话描述你要的微课，例：讲清楚 PWM 调光原理，60 秒，最后拼成完整视频': 'Describe the lesson, e.g. explain PWM dimming in 60 seconds and assemble a complete video',
  '导入驭课 ZIP': 'Import Yuke ZIP', '导入教学视频包': 'Import teaching video package', '导入中…': 'Importing…', '审核中…': 'Reviewing…',
  '规划中…': 'Planning…', '↑ 开工': '↑ Start', '教师投稿': 'Contribute teaching media',
  '把您的实验录屏 / 图片 / 教案投进素材库，丰富 JOJO 的训练素材': 'Contribute experiment recordings, images or lesson plans to the teaching media library.',
  '新建空白项目': 'New blank project', '科学素材训练库 · 浏览与人工抽检': 'Science training library · Browse and sample review',
  '教师研修报告 · AGENTS.md 七条军规': 'Teacher development report · Seven AGENTS.md rules',
  'JOJO 的“自我学习”是什么？': 'What does “self-learning” mean in JOJO?',
  '机器“学会”的不是梯度，而是越来越厚的规则库 + 样例库 + 改判记录。': 'The system accumulates rules, examples and review corrections rather than updating model gradients.',
  '任务包': 'Package', '素材与依据': 'Media & evidence', '验收记录': 'Review records', '验收台': 'Review board',
  '节点': 'Nodes', '素材库': 'Media library', '新建项目': 'New project', '返回首页': 'Back to home',
  '点击画布节点可在此编辑参数；': 'Select a canvas node to edit its settings;',
  '或直接在下方对话，让 Agent 继续创作。': 'or ask the Agent below to continue the work.',
  '右栏宽度': 'Right panel width', '本项目已花': 'Project cost', '变窄': 'Narrower', '加宽': 'Wider', '默认': 'Reset',
  '和 Agent 说下一步（回车发送）': 'Tell the Agent the next step (Enter to send)',
  '发送': 'Send', '联网调研': 'Web research',
  '教学视频任务包': 'Teaching video package', '方案已确认 · 无需付费生成': 'Plan confirmed · No paid generation',
  '已接收 · 待方案确认': 'Received · Plan confirmation required',
  '导入任务：': 'Import task: ', '状态：': 'Status: ', '包哈希：': 'Package hash: ',
  '确认方案（无需付费生成）': 'Confirm plan (no paid generation)', '正在确认…': 'Confirming…',
  '确认记录：': 'Confirmation: ', '方案哈希：': 'Plan hash: ', '确认时间：': 'Confirmed at: ',
  '生成候选视频（无付费）': 'Generate candidate video (no paid calls)', '正在生成…': 'Generating…',
  '候选视频正在后台生成，页面会自动刷新。': 'The candidate is rendering in the background. This page refreshes automatically.',
  '候选生成失败，请查看任务记录。': 'Candidate generation failed. Check the task record.',
  '待教学验收': 'Pending teaching review', '成片已生成，尚未表示教学验收通过。': 'The video is ready, but teaching review has not passed yet.',
  '下载字幕 SRT': 'Download SRT subtitles', '未修改场景哈希保持一致：': 'Unchanged scene hashes: ',
  '未生成场景：': 'Scenes not generated: ',
  '本包含合成契约素材，不能作为真实课堂验收证据。': 'This package contains synthetic contract fixtures and cannot serve as real classroom evidence.',
  '待补项': 'Missing items', '可选': 'Optional', '必需': 'Required',
  '方案与教学断言待老师复核': 'The plan and teaching assertions require teacher review',
  '依据：': 'Evidence: ', '口播定位：': 'Narration source: ', '课件定位：': 'Slide source: ', '核查：': 'Check: ',
  '科学场景规格': 'Scientific scene specification', '退回此场景': 'Revise this scene',
  '退回场景：': 'Revise scene: ', '关闭': 'Close', '修改意见（必填）': 'Revision note (required)',
  '例如：缩短波形展示并调整口播': 'E.g. shorten the waveform section and revise the narration',
  '新时长（秒）': 'New duration (seconds)', '制作路线': 'Production route', '新口播': 'New narration',
  '生成送修候选': 'Generate revised candidate',
  '确定性 2D': 'Deterministic 2D', '真实素材': 'Live action', '课件合成': 'Slide composite',
  '返回画布': 'Back to workspace', '刷新': 'Refresh', '重试全部网络失败': 'Retry all network failures', '续跑产线': 'Resume pipeline',
  '科学素材训练库': 'Science training library', '分类目录、证据边界与人工抽检记录': 'Taxonomy, evidence boundaries and manual sample review records',
  '候选预览 · 未批准投产': 'Candidate preview · Not approved for production',
  '外部视频仅用于发现与备课参考，不是科学真值。此处的“抽检通过”只保存人工意见，不改变候选状态，也不授权生成或投产。': 'External videos are for discovery and lesson preparation only, not scientific ground truth. “Sample passed” only records a human opinion; it does not change candidate status or authorize generation or production.',
  '个候选 Skill': 'candidate Skills', '条外部发现': 'external discoveries', '条待抽检': 'pending review', '个已批准投产': 'approved for production',
  '学科目录': 'Subject taxonomy', '全部学科': 'All subjects', '已启用': 'Active', '草案': 'Draft',
  'Skill 候选': 'Skill candidates', '外部发现': 'External discoveries',
  '搜索名称、编号或摘要': 'Search title, ID or summary', '全部审核状态': 'All review states', '待抽检': 'Pending review', '已有记录': 'Reviewed',
  '下一条未抽检': 'Next unreviewed', '条结果': 'results',
  '基础医学尚无证据卡。请先与合作人确认课题、真值来源和验收人，再建立首张卡。': 'No medical evidence cards yet. Confirm the topic, ground-truth sources and reviewer with the domain partner before creating the first card.',
  '当前筛选条件下没有候选。': 'No candidates match the current filters.',
  '候选 Skill': 'Candidate Skill', '外部发现参考': 'External discovery reference', '待人工抽检': 'Awaiting manual review',
  '抽检通过': 'Sample passed', '退回补证': 'Evidence required', '退回修改': 'Revision required',
  '版本': 'Version', '用途与边界': 'Purpose and boundaries', '教学目的：': 'Teaching goal: ',
  '可选路线：': 'Allowed routes: ', '禁止路线：': 'Forbidden routes: ', '来源': 'Sources', '权利：': 'Rights: ',
  '输入与验收': 'Inputs and acceptance', '观察点：': 'Observations: ', '不变量：': 'Invariants: ',
  '证据卡': 'Evidence cards', '张；科学断言': '; scientific assertions', '条；正样本': '; positive samples', '、负样本': ', negative samples',
  '。缺少的内容必须补齐，当前不得批准投产。': '. Missing content must be supplied; production approval is not allowed yet.',
  '发现信息': 'Discovery details', '打开原始视频页面': 'Open original video page', '使用边界': 'Usage boundary',
  '证据状态：': 'Evidence status: ', '权利状态：': 'Rights status: ',
  '标题、AI 摘要和社区标签只能帮助发现候选。须另找教材、标准、论文或实测证据，才能形成科学论断与 Skill。': 'Titles, AI summaries and community tags only support discovery. Textbooks, standards, papers or measured evidence are required before creating scientific claims or Skills.',
  '新增人工抽检记录': 'New manual sample review', '记录绑定当前内容哈希。候选变化后必须重新审核；历史记录不可覆盖。': 'The review is bound to the current content hash. Changed candidates require a new review; history is append-only.',
  '审核人': 'Reviewer', '姓名 / 单位，例如：张老师 · 医学教研室': 'Name / organization, e.g. Dr. Zhang · Medical Teaching Department',
  '抽检结论': 'Review decision', '抽检通过（仍不批准投产）': 'Sample passed (not approved for production)',
  '已核对来源与定位': 'Source and locator checked', '已核对权利与使用边界': 'Rights and usage boundary checked',
  '已核对适用范围': 'Applicable scope checked', '已核对科学论断': 'Scientific claims checked', '已核对可复现性': 'Reproducibility checked',
  '审核意见': 'Review comments', '记录抽检范围、样本和仍需补充的事项': 'Record the sample scope and outstanding items',
  '必填：指出缺少的证据或需要修改的内容': 'Required: identify missing evidence or required changes',
  '保存审核记录': 'Save review record', '保存中…': 'Saving…',
  '审核记录已保存；候选投产状态未被修改': 'Review saved; production status was not changed',
  '审核历史': 'Review history', '未勾选检查项': 'No checks selected', '尚无人工抽检记录。': 'No manual review records yet.',
  '正在读取训练库…': 'Loading training library…',
  '（新学科…）': '(New subject…)', '课题（可选，如 迈克尔逊干涉）': 'Topic (optional, e.g. Michelson interference)',
  '新学科目录名（如 电子类）': 'New subject folder (e.g. Electronics)',
  '本人自制': 'Created by me', '已获授权': 'Licensed', '仅参考不进片': 'Reference only; not for production',
  '简述（可选）': 'Short note (optional)', '智能建议目录': 'Suggest a folder', '建议：': 'Suggestion: ',
  '采用': 'Use', '可提交：mp4/mov/webm 实验录屏 · png/jpg 图片 · pdf 教案': 'Accepted: MP4/MOV/WebM experiment recordings · PNG/JPG images · PDF lesson plans',
  '上传中…': 'Uploading…', '提交入库': 'Add to library',
  '后端服务未运行，当前内容可查看，但生成和保存会失败。': 'The backend is unavailable. Content can be viewed, but generation and saving will fail.',
  '和 Agent 对话继续创作：加镜头、改脚本、重新规划……': 'Continue with the Agent: add scenes, revise scripts or replan…',
  '例：给这条微课加一段 PWM 波形的代码渲染镜头': 'Example: add a code-rendered PWM waveform scene to this lesson',
  '先联网查真实事实再规划（需开通方舟联网插件）': 'Research real sources before planning (requires the Ark web plugin)',
}

const I18nContext = createContext<I18nValue | null>(null)

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguage] = useState<Language>(() => localStorage.getItem('jojo_language') === 'en' ? 'en' : 'zh')
  useEffect(() => {
    localStorage.setItem('jojo_language', language)
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en'
  }, [language])
  const t = (text: string) => language === 'en' ? en[text] ?? text : text
  return <I18nContext.Provider value={{ language, setLanguage, t }}>{children}</I18nContext.Provider>
}

export function useI18n() {
  const value = useContext(I18nContext)
  if (!value) throw new Error('useI18n must be used within I18nProvider')
  return value
}

export function LanguageSwitcher() {
  const { language, setLanguage, t } = useI18n()
  return <div className="language-switcher" aria-label={t('语言')}>
    <span>{t('语言')}</span>
    <button className={language === 'zh' ? 'on' : ''} aria-pressed={language === 'zh'} onClick={() => setLanguage('zh')}>中文</button>
    <button className={language === 'en' ? 'on' : ''} aria-pressed={language === 'en'} onClick={() => setLanguage('en')}>English</button>
  </div>
}

export function localizedRoute(route: string, language: Language) {
  const labels: Record<string, [string, string]> = {
    diagram_2d: ['确定性 2D', 'Deterministic 2D'],
    live_action: ['真实素材', 'Live action'],
    slide_composite: ['课件合成', 'Slide composite'],
    code_execution: ['代码执行', 'Code execution'],
    generated_scene: ['生成式场景', 'Generated scene'],
  }
  return labels[route]?.[language === 'zh' ? 0 : 1] ?? route
}
