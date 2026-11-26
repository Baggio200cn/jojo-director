import { useEffect, useState, type ReactNode } from 'react'
import { api } from './api'
import { useI18n } from './I18n'

type DocumentName = 'project' | 'guide'
type DocumentData = { title: string; filename: string; content: string; contentHash: string }

function inline(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g)
  return parts.map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={index}>{part.slice(2, -2)}</strong>
    if (part.startsWith('`') && part.endsWith('`')) return <code key={index}>{part.slice(1, -1)}</code>
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
    if (link) {
      const isManual = link[2].endsWith('JOJO科学素材合作者操作手册.md')
      const href = isManual ? api.documentDownload('guide') : link[2]
      return <a key={index} href={href} target={href.startsWith('http') ? '_blank' : undefined} rel="noreferrer">{link[1]}</a>
    }
    return part
  })
}

function asset(path: string): string {
  const base = import.meta.env.BASE_URL.replace(/\/$/, '')
  if (path.endsWith('jojo-director-readme-hero.svg')) return `${base}/api/docs/assets/readme-hero`
  if (path.endsWith('architecture.png')) return `${base}/api/docs/assets/architecture`
  return path
}

function MarkdownView({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let key = 0
  while (i < lines.length) {
    const line = lines[i].trim()
    if (!line || /^<\/?p(?:\s[^>]*)?>$/.test(line)) { i++; continue }
    if (line.startsWith('```')) {
      const language = line.slice(3).trim()
      const content = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) content.push(lines[i++])
      i++
      blocks.push(<pre key={key++} data-language={language}><code>{content.join('\n')}</code></pre>)
      continue
    }
    const htmlImage = line.match(/<img\s+[^>]*src="([^"]+)"[^>]*alt="([^"]*)"[^>]*>/i)
      ?? line.match(/<img\s+[^>]*alt="([^"]*)"[^>]*src="([^"]+)"[^>]*>/i)
    if (htmlImage) {
      const src = htmlImage[1].includes('jojo-director') ? htmlImage[1] : htmlImage[2]
      const alt = htmlImage[1].includes('jojo-director') ? htmlImage[2] : htmlImage[1]
      blocks.push(<img className="doc-image doc-hero" key={key++} src={asset(src)} alt={alt} />)
      i++; continue
    }
    const image = line.match(/^!\[([^\]]*)\]\(([^)]+)\)$/)
    if (image) {
      blocks.push(<img className="doc-image" key={key++} src={asset(image[2])} alt={image[1]} />)
      i++; continue
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/)
    if (heading) {
      const level = Math.min(heading[1].length + 1, 5)
      const Tag = `h${level}` as 'h2' | 'h3' | 'h4' | 'h5'
      blocks.push(<Tag key={key++}>{inline(heading[2])}</Tag>)
      i++; continue
    }
    if (line.startsWith('|') && i + 1 < lines.length && /^\|?[\s:|-]+\|/.test(lines[i + 1].trim())) {
      const rows = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        if (!/^\|?[\s:|-]+\|/.test(lines[i].trim())) rows.push(lines[i].trim())
        i++
      }
      const cells = (row: string) => row.replace(/^\||\|$/g, '').split('|').map(x => x.trim())
      blocks.push(<div className="doc-table-wrap" key={key++}><table><thead><tr>{cells(rows[0]).map((x, j) => <th key={j}>{inline(x)}</th>)}</tr></thead>
        <tbody>{rows.slice(1).map((row, j) => <tr key={j}>{cells(row).map((x, k) => <td key={k}>{inline(x)}</td>)}</tr>)}</tbody></table></div>)
      continue
    }
    if (/^[-*]\s+/.test(line) || /^\d+\.\s+/.test(line)) {
      const ordered = /^\d+\.\s+/.test(line)
      const items = []
      while (i < lines.length && (ordered ? /^\d+\.\s+/.test(lines[i].trim()) : /^[-*]\s+/.test(lines[i].trim()))) {
        items.push(lines[i].trim().replace(/^[-*]\s+|^\d+\.\s+/, '')); i++
      }
      const Tag = ordered ? 'ol' : 'ul'
      blocks.push(<Tag key={key++}>{items.map((item, j) => <li key={j}>{inline(item)}</li>)}</Tag>)
      continue
    }
    const paragraph = [line]
    i++
    while (i < lines.length) {
      const next = lines[i].trim()
      if (!next || /^(#{1,4}\s|```|[-*]\s|\d+\.\s|\||!?\[|<\/?p|<img)/.test(next)) break
      paragraph.push(next); i++
    }
    blocks.push(<p key={key++}>{inline(paragraph.join(' '))}</p>)
  }
  return <div className="markdown-body">{blocks}</div>
}

export default function DocumentView({ name, onBack }: { name: DocumentName; onBack: () => void }) {
  const { t } = useI18n()
  const [data, setData] = useState<DocumentData | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    setData(null); setError('')
    api.getDocument(name).then(setData).catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [name])
  return <div className="document-page">
    <header className="document-head">
      <button onClick={onBack}>← {t('返回首页')}</button>
      <div><h1>{name === 'project' ? t('项目介绍') : t('教师投稿合作手册')}</h1><p>{name === 'project' ? 'README.md' : 'JOJO科学素材合作者操作手册.md'}</p></div>
      <a className="doc-download" href={api.documentDownload(name)} download>{t('下载 Markdown 文件')}</a>
    </header>
    <div className="document-note">{t('下载后可在浏览器中选择保存位置，或另存到桌面。')}</div>
    {error && <div className="training-error">{error}</div>}
    {!data && !error && <div className="training-empty">{t('正在读取文档…')}</div>}
    {data && <><div className="document-version">{t('内容校验')}：{data.contentHash.slice(0, 19)}…</div><article className="document-card"><MarkdownView source={data.content} /></article></>}
  </div>
}
