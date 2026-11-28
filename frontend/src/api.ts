const BASE_PATH = import.meta.env.BASE_URL.replace(/\/$/, '')
const request = (path: string, init?: RequestInit) => window.fetch(`${BASE_PATH}${path}`, init)

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
  request(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  }).then(j)

const post = jsonReq('POST')
const patch = jsonReq('PATCH')
const put = jsonReq('PUT')

const uploadWithProgress = (path: string, body: FormData, onProgress?: (percent: number) => void) =>
  new Promise<any>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${BASE_PATH}${path}`)
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) onProgress?.(Math.min(100, Math.round(event.loaded / event.total * 100)))
    }
    xhr.onerror = () => reject(new Error('网络连接中断，请检查网络后重试'))
    xhr.onabort = () => reject(new Error('上传已取消'))
    xhr.onload = () => {
      let data: any = {}
      try { data = xhr.responseText ? JSON.parse(xhr.responseText) : {} } catch { data = {} }
      if (xhr.status === 401) window.dispatchEvent(new CustomEvent('jojo-auth-required'))
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(data?.detail ? String(data.detail) : `HTTP ${xhr.status}`))
        return
      }
      resolve(data)
    }
    xhr.send(body)
  })

export const api = {
  listProjects: () => request('/api/projects').then(j),
  createProject: (title: string) => post('/api/projects', { title }),
  importYuke: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request('/api/projects/import/yuke', { method: 'POST', body: fd }).then(j)
  },
  importTeachingVideo: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request('/api/projects/import/teaching-video', { method: 'POST', body: fd }).then(j)
  },
  confirmTeachingVideo: (pid: string, body: object) =>
    post(`/api/projects/${pid}/teaching-video/confirm`, body),
  produceTeachingVideo: (pid: string, body: object) =>
    post(`/api/projects/${pid}/teaching-video/produce`, body),
  listTeachingVideoCandidates: (pid: string) =>
    request(`/api/projects/${pid}/teaching-video/candidates`).then(j),
  trainingLibrary: () => request('/api/training/library').then(j),
  submitTrainingReview: (body: object) => post('/api/training/reviews', body),
  getDocument: (name: 'project' | 'guide') => request(`/api/docs/${name}`).then(j),
  documentDownload: (name: 'project' | 'guide') => `${BASE_PATH}/api/docs/${name}/download`,
  getGraph: (pid: string) => request(`/api/projects/${pid}/graph`).then(j),
  createNode: (pid: string, node: object) => post(`/api/projects/${pid}/nodes`, node),
  updateNode: (nid: string, body: object) => patch(`/api/nodes/${nid}`, body),
  deleteNode: (nid: string) => request(`/api/nodes/${nid}`, { method: 'DELETE' }).then(j),
  createEdge: (pid: string, source: string, target: string) =>
    post(`/api/projects/${pid}/edges`, { source_node_id: source, target_node_id: target }),
  deleteEdge: (eid: string) => request(`/api/edges/${eid}`, { method: 'DELETE' }).then(j),
  executeNode: (nid: string) => post(`/api/nodes/${nid}/execute`),
  executeChain: (nid: string) => post(`/api/nodes/${nid}/execute_chain`),
  getNode: (nid: string) => request(`/api/nodes/${nid}`).then(j),
  uploadImage: (nid: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request(`/api/nodes/${nid}/upload`, { method: 'POST', body: fd }).then(j)
  },
  agent: (pid: string, message: string, model?: string, research?: boolean,
          history?: { role: string; text: string }[]) =>
    post(`/api/projects/${pid}/agent`, { message, model, research, history }),
  agentModels: () => request('/api/agent/models').then(j),
  listAssets: (pid: string) => request(`/api/projects/${pid}/assets`).then(j),
  reviewQueue: (pid: string) => request(`/api/projects/${pid}/review_queue`).then(j),
  retryInfra: (pid: string) => post(`/api/projects/${pid}/retry_infra`),
  resumeLine: (pid: string) => post(`/api/projects/${pid}/resume_line`),
  patchProject: (pid: string, body: object) =>
    request(`/api/projects/${pid}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j),
  qcOverride: (nid: string, verdict: string, failType = '', reason = '') =>
    post(`/api/nodes/${nid}/qc_override`, { verdict, fail_type: failType, reason }),
  listStarred: () => request('/api/assets/starred').then(j),
  starAsset: (aid: string, starred: boolean) =>
    request(`/api/assets/${aid}/star?starred=${starred}`, { method: 'PATCH' }).then(j),
  deleteAsset: (aid: string) => request(`/api/assets/${aid}`, { method: 'DELETE' }).then(j),
  patchAsset: (aid: string, body: { folder?: string; rights?: string; library?: boolean; subject_id?: string }) =>
    patch(`/api/assets/${aid}`, body),
  listAssetFolders: () => request('/api/assets/folders').then(j),
  listLibrary: (params: { folder?: string; kind?: string; q?: string; starred?: boolean }) => {
    const qs = new URLSearchParams()
    if (params.folder) qs.set('folder', params.folder)
    if (params.kind) qs.set('kind', params.kind)
    if (params.q) qs.set('q', params.q)
    if (params.starred) qs.set('starred', 'true')
    return request(`/api/assets/library?${qs}`).then(j)
  },
  nodeVersions: (nid: string) => request(`/api/nodes/${nid}/versions`).then(j),
  useNodeVersion: (nid: string, aid: string) =>
    post(`/api/nodes/${nid}/use_version/${aid}`),
  uploadToLibrary: (file: File, meta: { folder: string; rights: string; note: string; communityId?: string; contributionId?: string }) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('folder', meta.folder)
    fd.append('rights', meta.rights)
    fd.append('note', meta.note)
    if (meta.communityId) fd.append('community_id', meta.communityId)
    if (meta.contributionId) fd.append('contribution_id', meta.contributionId)
    return request('/api/assets/upload', { method: 'POST', body: fd }).then(j)
  },
  uploadFolderToLibrary: (files: File[], meta: { folder: string; rights: string; note: string; communityId?: string; onProgress?: (percent: number) => void }) => {
    const fd = new FormData()
    files.forEach(file => {
      fd.append('files', file)
      fd.append('paths', file.webkitRelativePath || file.name)
    })
    fd.append('folder', meta.folder)
    fd.append('rights', meta.rights)
    fd.append('note', meta.note)
    if (meta.communityId) fd.append('community_id', meta.communityId)
    return uploadWithProgress('/api/assets/upload_folder', fd, meta.onProgress)
  },
  listIngestionBatches: () => request('/api/assets/ingestion-batches').then(j),
  assetText: (assetId: string): Promise<{ text: string; format: 'markdown' | 'text'; truncated: boolean; parser: string }> =>
    request(`/api/assets/${assetId}/text`).then(j),
  reviewIngestionBatch: (batchId: string, action: string, comment: string) =>
    post(`/api/assets/ingestion-batches/${batchId}/review`, { action, comment }),
  moveIngestionBatch: (batchId: string, folder: string) =>
    post(`/api/assets/ingestion-batches/${batchId}/move`, { folder }),
  listCommunities: () => request('/api/communities').then(j),
  createCommunity: (body: object) => post('/api/communities', body),
  communityDetail: (id: string) => request(`/api/communities/${id}`).then(j),
  joinCommunity: (id: string) => post(`/api/communities/${id}/join`),
  createContribution: (id: string, body: object) => post(`/api/communities/${id}/contributions`, body),
  contributionDetail: (id: string, contributionId: string) =>
    request(`/api/communities/${id}/contributions/${contributionId}`).then(j),
  addContributionComment: (id: string, contributionId: string, body: object) =>
    post(`/api/communities/${id}/contributions/${contributionId}/comments`, body),
  uploadCommentScreenshot: (id: string, contributionId: string, file: File) => {
    const fd = new FormData(); fd.append('file', file)
    return request(`/api/communities/${id}/contributions/${contributionId}/screenshots`, { method: 'POST', body: fd }).then(j)
  },
  analyzeCommentScreenshot: (screenshotId: string, note: string) =>
    post(`/api/communities/screenshots/${screenshotId}/analyze`, { note }),
  reviewContribution: (id: string, contributionId: string, body: object) =>
    post(`/api/communities/${id}/contributions/${contributionId}/review`, body),
  reviseContribution: (id: string, contributionId: string, body: object) =>
    post(`/api/communities/${id}/contributions/${contributionId}/revisions`, body),
  bindContribution: (id: string, contributionId: string, body: object) =>
    post(`/api/communities/${id}/contributions/${contributionId}/bind`, body),
  createCommunityTask: (id: string, body: object) => post(`/api/communities/${id}/tasks`, body),
  communityTaskAction: (id: string, taskId: string, action: string) =>
    post(`/api/communities/${id}/tasks/${taskId}/${action}`),
  publishCommunityVersion: (id: string, body: object) => post(`/api/communities/${id}/versions`, body),
  setCommunityMemberRole: (id: string, memberId: string, role: string) =>
    post(`/api/communities/${id}/members/${memberId}/${role}`),
  readCommunityNotifications: (id: string) => post(`/api/communities/${id}/notifications/read`),
  inspectRepository: (body: { source_url: string; branch?: string }) => post('/api/repositories/inspect', body),
  importRepository: (body: { source_url: string; branch?: string; community_id: string; paths: string[] }) =>
    post('/api/repositories/import', body),
  repositoryFiles: (id: string) => request(`/api/repositories/${id}/files`).then(j),
  repositoryFile: (id: string, path: string) =>
    request(`/api/repositories/${id}/file?path=${encodeURIComponent(path)}`).then(j),
  importCommunityBatch: (id: string, batchId: string) => post(`/api/communities/${id}/import-batch`, { batch_id: batchId }),
  suggestFolder: (text: string): Promise<{ folder: string; score: number; matches: { ref_id: string; text: string; score: number }[] }> =>
    post('/api/assets/suggest_folder', { text }),
  createAssetFolder: (path: string) => post('/api/assets/folders', { path }),
  executeStep: (nid: string) => post(`/api/nodes/${nid}/execute_chain?step=true`),
  expandStoryboard: (nid: string, domain = 'general') =>
    post(`/api/nodes/${nid}/expand_storyboard`, { domain }),
  restore: (pid: string, nodes: object[], edges: object[]) =>
    post(`/api/projects/${pid}/restore`, { nodes, edges }),
  authMe: () => request('/api/auth/me').then(j),
  authLogin: (body: { username?: string; password?: string; invite_code?: string }) =>
    post('/api/auth/login', body),
  authLogout: () => post('/api/auth/logout'),
  adminInvites: () => request('/api/admin/invites').then(j),
  adminCreateInvite: (body: { label: string; daily_video_limit: number; daily_cost_limit_cny: number }) =>
    post('/api/admin/invites', body),
  adminToggleInvite: (code: string) =>
    request(`/api/admin/invites/${code}`, { method: 'PATCH' }).then(j),
  providerSettings: () => request('/api/admin/provider-settings').then(j),
  saveProviderSettings: (body: object) => put('/api/admin/provider-settings', body),
  providerCatalog: (provider: 'moma' | 'ark') => request(`/api/admin/provider-settings/catalog/${provider}`).then(j),
  projectStats: (pid: string) => request(`/api/projects/${pid}/stats`).then(j),
  storyboardFromRef: (nid: string) => post(`/api/nodes/${nid}/storyboard_from_ref`),
  scriptFromRef: (nid: string) => post(`/api/nodes/${nid}/script_from_ref`),
}
