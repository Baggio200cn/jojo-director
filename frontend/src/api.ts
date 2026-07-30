const j = async (r: Response) => {
  if (!r.ok) {
    if (r.status === 401) window.dispatchEvent(new CustomEvent('jojo-auth-required'))
    let msg = `HTTP ${r.status}`
    try {
      const d = await r.json()
      if (d?.detail) msg = String(d.detail)
    } catch { /* 非 JSON 响应，保留状态码 */ }
    throw new Error(msg)
  }
  return r.json()
}

const jsonReq = (method: string) => (url: string, body?: object) =>
  fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  }).then(j)

const post = jsonReq('POST')
const patch = jsonReq('PATCH')

export const api = {
  listProjects: () => fetch('/api/projects').then(j),
  createProject: (title: string) => post('/api/projects', { title }),
  getGraph: (pid: string) => fetch(`/api/projects/${pid}/graph`).then(j),
  createNode: (pid: string, node: object) => post(`/api/projects/${pid}/nodes`, node),
  updateNode: (nid: string, body: object) => patch(`/api/nodes/${nid}`, body),
  deleteNode: (nid: string) => fetch(`/api/nodes/${nid}`, { method: 'DELETE' }).then(j),
  createEdge: (pid: string, source: string, target: string) =>
    post(`/api/projects/${pid}/edges`, { source_node_id: source, target_node_id: target }),
  deleteEdge: (eid: string) => fetch(`/api/edges/${eid}`, { method: 'DELETE' }).then(j),
  executeNode: (nid: string) => post(`/api/nodes/${nid}/execute`),
  executeChain: (nid: string) => post(`/api/nodes/${nid}/execute_chain`),
  getNode: (nid: string) => fetch(`/api/nodes/${nid}`).then(j),
  uploadImage: (nid: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch(`/api/nodes/${nid}/upload`, { method: 'POST', body: fd }).then(j)
  },
  agent: (pid: string, message: string, model?: string, research?: boolean,
          history?: { role: string; text: string }[]) =>
    post(`/api/projects/${pid}/agent`, { message, model, research, history }),
  agentModels: () => fetch('/api/agent/models').then(j),
  listAssets: (pid: string) => fetch(`/api/projects/${pid}/assets`).then(j),
  reviewQueue: (pid: string) => fetch(`/api/projects/${pid}/review_queue`).then(j),
  retryInfra: (pid: string) => post(`/api/projects/${pid}/retry_infra`),
  resumeLine: (pid: string) => post(`/api/projects/${pid}/resume_line`),
  patchProject: (pid: string, body: object) =>
    fetch(`/api/projects/${pid}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j),
  qcOverride: (nid: string, verdict: string) =>
    post(`/api/nodes/${nid}/qc_override`, { verdict }),
  listStarred: () => fetch('/api/assets/starred').then(j),
  starAsset: (aid: string, starred: boolean) =>
    fetch(`/api/assets/${aid}/star?starred=${starred}`, { method: 'PATCH' }).then(j),
  deleteAsset: (aid: string) => fetch(`/api/assets/${aid}`, { method: 'DELETE' }).then(j),
  executeStep: (nid: string) => post(`/api/nodes/${nid}/execute_chain?step=true`),
  expandStoryboard: (nid: string, domain = 'general') =>
    post(`/api/nodes/${nid}/expand_storyboard`, { domain }),
  restore: (pid: string, nodes: object[], edges: object[]) =>
    post(`/api/projects/${pid}/restore`, { nodes, edges }),
  authMe: () => fetch('/api/auth/me').then(j),
  authLogin: (body: { username?: string; password?: string; invite_code?: string }) =>
    post('/api/auth/login', body),
  authLogout: () => post('/api/auth/logout'),
  adminInvites: () => fetch('/api/admin/invites').then(j),
  adminCreateInvite: (body: { label: string; daily_video_limit: number; daily_cost_limit_cny: number }) =>
    post('/api/admin/invites', body),
  adminToggleInvite: (code: string) =>
    fetch(`/api/admin/invites/${code}`, { method: 'PATCH' }).then(j),
  projectStats: (pid: string) => fetch(`/api/projects/${pid}/stats`).then(j),
  storyboardFromRef: (nid: string) => post(`/api/nodes/${nid}/storyboard_from_ref`),
}
