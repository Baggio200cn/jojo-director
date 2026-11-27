import { useEffect, useMemo, useState } from 'react'
import { api } from './api'

export type RepositorySummary = {
  id: string; contribution_id: string; source_url: string; owner: string; repo: string
  fullName: string; branch: string; default_branch: string; commit_sha: string
  file_count: number; total_bytes: number; author_name: string; created_at: string
}
type Inspection = {
  sourceUrl: string; fullName: string; defaultBranch: string; branch: string; commitSha: string
  description: string; files: { path: string; size: number; language: string }[]
  fileCount: number; truncated: boolean; recommendedPaths: string[]
}
type RepositoryFile = { path: string; text: string; language: string; lines: number; commitSha: string; readOnly: boolean }

export function RepositoryImporter({ communityId, onImported }: {
  communityId: string; onImported: (contributionId: string) => Promise<void>
}) {
  const [sourceUrl, setSourceUrl] = useState('')
  const [branch, setBranch] = useState('')
  const [inspection, setInspection] = useState<Inspection | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const filtered = inspection?.files.filter(item => item.path.toLowerCase().includes(query.toLowerCase())) || []
  const selectedBytes = inspection?.files.filter(item => selected.has(item.path)).reduce((total, item) => total + item.size, 0) || 0

  const inspect = async () => {
    setBusy(true); setError(''); setInspection(null)
    try {
      const data = await api.inspectRepository({ source_url: sourceUrl, branch }) as Inspection
      setInspection(data); setSelected(new Set(data.recommendedPaths))
    } catch (e) { setError(String(e).replace('Error: ', '')) }
    finally { setBusy(false) }
  }

  const toggle = (path: string) => {
    setSelected(previous => {
      const next = new Set(previous)
      if (next.has(path)) next.delete(path); else next.add(path)
      return next
    })
  }

  const importSelected = async () => {
    setBusy(true); setError('')
    try {
      const result = await api.importRepository({
        source_url: sourceUrl, branch, community_id: communityId, paths: [...selected],
      }) as { contribution: { id: string } }
      await onImported(result.contribution.id)
      setInspection(null); setSelected(new Set()); setSourceUrl(''); setBranch('')
    } catch (e) { setError(String(e).replace('Error: ', '')) }
    finally { setBusy(false) }
  }

  return <details className="repository-importer"><summary>导入公开 GitHub 仓库</summary>
    <p>只读导入，不执行仓库代码。单批最多 100 个文件、10MB，敏感文件与依赖目录会被排除。</p>
    <input placeholder="https://github.com/所有者/仓库" value={sourceUrl} onChange={e => setSourceUrl(e.target.value)} />
    <div className="repository-import-actions"><input placeholder="分支（留空使用默认分支）" value={branch} onChange={e => setBranch(e.target.value)} />
      <button disabled={busy || !sourceUrl.trim()} onClick={() => void inspect()}>{busy ? '正在读取…' : '读取目录'}</button></div>
    <button className="repository-jojo-example" onClick={() => { setSourceUrl('https://github.com/Baggio200cn/jojo-director'); setBranch('feat/science-training-library') }}>使用 JOJO 仓库作为测试</button>
    {error && <div className="community-error">{error}</div>}
    {inspection && <div className="repository-inspection">
      <div className="repository-inspection-head"><b>{inspection.fullName}</b><span>{inspection.branch} · {inspection.commitSha.slice(0, 10)}</span></div>
      <small>可导入 {inspection.fileCount} 个文本文件，已选择 {selected.size} 个，约 {(selectedBytes / 1024).toFixed(1)}KB。</small>
      {inspection.truncated && <div className="repository-warning">仓库目录较大，仅显示部分文件；请缩小选择范围。</div>}
      <input placeholder="筛选文件路径" value={query} onChange={e => setQuery(e.target.value)} />
      <div className="repository-select-actions"><button onClick={() => setSelected(new Set(inspection.recommendedPaths))}>选择推荐文件</button><button onClick={() => setSelected(new Set())}>清空</button></div>
      <div className="repository-tree">{filtered.slice(0, 300).map(item => <label key={item.path}><input type="checkbox" checked={selected.has(item.path)} onChange={() => toggle(item.path)} /><span>{item.path}</span><small>{(item.size / 1024).toFixed(1)}KB</small></label>)}</div>
      {filtered.length > 300 && <small>筛选结果较多，仅显示前 300 个文件，请先输入路径筛选。</small>}
      <button className="repository-import-confirm" disabled={busy || !selected.size || selected.size > 100 || selectedBytes > 10 * 1024 * 1024} onClick={() => void importSelected()}>确认导入为只读快照</button>
    </div>}
  </details>
}

export function RepositoryBrowser({ repository, onDiscuss }: {
  repository: RepositorySummary; onDiscuss: (path: string, line: number) => void
}) {
  const [files, setFiles] = useState<{ path: string; size: number; lines: number; language: string }[]>([])
  const [path, setPath] = useState('')
  const [content, setContent] = useState<RepositoryFile | null>(null)
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const filtered = useMemo(() => files.filter(item => item.path.toLowerCase().includes(query.toLowerCase())), [files, query])

  const openFile = async (nextPath: string) => {
    setLoading(true); setError(''); setPath(nextPath)
    try { setContent(await api.repositoryFile(repository.id, nextPath) as RepositoryFile) }
    catch (e) { setError(String(e).replace('Error: ', '')) }
    finally { setLoading(false) }
  }

  useEffect(() => {
    setFiles([]); setContent(null); setPath('')
    api.repositoryFiles(repository.id).then(rows => {
      setFiles(rows)
      if (rows.length) void openFile(rows[0].path)
    }).catch(e => setError(String(e).replace('Error: ', '')))
  }, [repository.id])

  return <div className="repository-browser">
    <div className="repository-browser-head"><div><b>{repository.fullName}</b><span>{repository.branch} · {repository.commit_sha.slice(0, 10)} · 只读快照</span></div><a href={repository.source_url} target="_blank" rel="noreferrer">查看 GitHub 原仓库</a></div>
    <div className="repository-browser-layout">
      <aside><input placeholder="筛选快照文件" value={query} onChange={e => setQuery(e.target.value)} /><div className="repository-file-list">{filtered.map(item => <button className={path === item.path ? 'on' : ''} key={item.path} onClick={() => void openFile(item.path)}>{item.path}<small>{item.lines} 行</small></button>)}</div></aside>
      <section className="repository-code-pane">{error && <div className="community-error">{error}</div>}{loading && <div className="community-empty">正在读取代码…</div>}{content && !loading && <><div className="repository-code-title"><b>{content.path}</b><span>点击代码行发起讨论</span></div><div className="repository-code" role="list">{content.text.split('\n').map((line, index) => <button role="listitem" key={index} onClick={() => onDiscuss(content.path, index + 1)}><span>{index + 1}</span><code>{line || ' '}</code></button>)}</div></>}</section>
    </div>
  </div>
}
