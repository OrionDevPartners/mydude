// Thin API client — all requests go through /api/* proxied to the FastAPI backend.
// Credentials (session cookie) are included automatically by the browser.

const BASE = '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Accept': 'application/json', ...(options?.headers || {}) },
    ...options,
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try { detail = (await res.json()).detail || detail } catch {}
    throw new ApiError(res.status, detail)
  }
  return res.json()
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

function formBody(data: Record<string, string | number | boolean | undefined | null>): URLSearchParams {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(data)) {
    if (v !== undefined && v !== null) p.set(k, String(v))
  }
  return p
}

// Fetch a binary endpoint (e.g. synthesized audio). On error the backend still
// returns a JSON {detail}; surface it as an ApiError. On success, return an
// object URL the caller is responsible for revoking.
async function requestBlob(path: string, options?: RequestInit): Promise<string> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    ...options,
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try { detail = (await res.json()).detail || detail } catch {}
    throw new ApiError(res.status, detail)
  }
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}

// Auth
export const getBranding = () => request<{ name: string; short_name: string; tagline: string }>('/branding')
export const getMe = () =>
  request<{ authenticated: boolean; username: string | null; is_admin: boolean; dev_bypass: boolean }>('/me')
export const login = (username: string, password: string) =>
  request<{ ok: boolean; username: string; is_admin: boolean }>('/login', {
    method: 'POST',
    body: formBody({ username, password }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const logout = () =>
  request<{ ok: boolean }>('/logout', { method: 'POST' })

// User management (admin only)
export interface AppUser {
  id: number; username: string; email: string; is_active: boolean; is_admin: boolean;
  created_at: string | null; last_login_at: string | null;
}
export const getUsers = () => request<{ users: AppUser[] }>('/users')
export const createUser = (data: { username: string; password: string; email?: string; is_admin?: boolean }) =>
  request<{ ok: boolean; user: AppUser }>('/users', {
    method: 'POST',
    body: formBody({ username: data.username, password: data.password, email: data.email || '', is_admin: data.is_admin ? '1' : '' }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const toggleUser = (id: number) =>
  request<{ ok: boolean; is_active: boolean }>(`/users/${id}/toggle`, { method: 'POST' })
export const resetUserPassword = (id: number, password: string) =>
  request<{ ok: boolean }>(`/users/${id}/password`, {
    method: 'POST',
    body: formBody({ password }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const deleteUser = (id: number) =>
  request<{ ok: boolean }>(`/users/${id}/delete`, { method: 'POST' })

// Dashboard / Tasks
export const getDashboard = () => request<DashboardData>('/dashboard')
export const runTask = (prompt: string) =>
  request<{ ok: boolean; task_id: number }>('/tasks/run', {
    method: 'POST',
    body: formBody({ prompt }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const getTaskHistory = (page = 1) => request<TaskHistoryData>(`/tasks/history?page=${page}`)
export const getTask = (id: number) => request<Task>(`/tasks/${id}`)

// Keys
export const getKeys = (params?: { q?: string; category?: string; reveal?: number }) => {
  const p = new URLSearchParams()
  if (params?.q) p.set('q', params.q)
  if (params?.category) p.set('category', params.category)
  if (params?.reveal) p.set('reveal', String(params.reveal))
  return request<KeysData>(`/keys?${p}`)
}
export const addKey = (data: AddKeyPayload) =>
  request<{ ok: boolean; msg: string }>('/keys', {
    method: 'POST',
    body: formBody(data as unknown as Record<string, string>),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const rotateKey = (id: number, api_key: string) =>
  request<{ ok: boolean; msg: string }>(`/keys/${id}/rotate`, {
    method: 'POST',
    body: formBody({ api_key }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const toggleKey = (id: number) =>
  request<{ ok: boolean; is_active: boolean }>(`/keys/${id}/toggle`, { method: 'POST' })
export const deleteKey = (id: number) =>
  request<{ ok: boolean; msg: string }>(`/keys/${id}/delete`, { method: 'POST' })
export const getKeyAudit = () => request<{ entries: AuditEntry[] }>('/keys/audit')

// Services
export const getDirectory = () => request<DirectoryData>('/directory')
export const getConnected = () => request<ConnectedData>('/connected')

// Governance
export const getGovernance = () => request<GovernanceData>('/governance')
export const ackAlert = (id: number) =>
  request<{ ok: boolean }>(`/governance/alerts/${id}/ack`, { method: 'POST' })
export const getProvenance = (params?: { q?: string; page?: number }) => {
  const p = new URLSearchParams()
  if (params?.q) p.set('q', params.q)
  if (params?.page) p.set('page', String(params.page))
  return request<ProvenanceData>(`/provenance?${p}`)
}
export const getMemory = (params?: { q?: string; layer?: string }) => {
  const p = new URLSearchParams()
  if (params?.q) p.set('q', params.q)
  if (params?.layer) p.set('layer', params.layer)
  return request<MemoryData>(`/memory?${p}`)
}
export const getSystem = () => request<SystemData>('/system')

// Local AI Models
export const getLocalModels = () => request<LocalModelsData>('/local-models')
export const addLocalModel = (model_id: string, provider: string) =>
  request<{ ok: boolean; entry: { model_id: string; provider: string } }>('/local-models/registry/add', {
    method: 'POST',
    body: formBody({ model_id, provider }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const updateLocalModel = (
  model_id: string,
  provider: string,
  new_model_id: string,
  new_provider: string,
  details: Record<string, string>,
) =>
  request<{ ok: boolean; entry: Record<string, unknown> }>('/local-models/registry/update', {
    method: 'POST',
    body: formBody({ model_id, provider, new_model_id, new_provider, details: JSON.stringify(details) }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const removeLocalModel = (model_id: string, provider: string) =>
  request<{ ok: boolean }>('/local-models/registry/remove', {
    method: 'POST',
    body: formBody({ model_id, provider }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })

// Capabilities
export const getCapabilities = () => request<CapabilitiesData>('/capabilities')
export const toggleCapability = (capability: string, enabled: boolean) =>
  request<{ ok: boolean; enabled: boolean }>('/capabilities/toggle', {
    method: 'POST',
    body: formBody({ capability, enabled: enabled ? '1' : '0' }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const testBrowser = (url: string) =>
  request<TestResult>('/capabilities/test/browser', {
    method: 'POST',
    body: formBody({ url }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const testSsh = (command: string) =>
  request<TestResult>('/capabilities/test/ssh', {
    method: 'POST',
    body: formBody({ command }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const testCode = () =>
  request<TestResult>('/capabilities/test/code', { method: 'POST' })
export const testHistory = (browser: string) =>
  request<TestResult>('/capabilities/test/history', {
    method: 'POST',
    body: formBody({ browser }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const testReceipts = () =>
  request<TestResult>('/capabilities/test/receipts', { method: 'POST' })
export const saveEmailConfig = (data: Record<string, string>) =>
  request<{ ok: boolean; msg: string }>('/capabilities/email-config', {
    method: 'POST',
    body: formBody(data),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const saveSshConfig = (data: Record<string, string>) =>
  request<{ ok: boolean; msg: string }>('/capabilities/ssh-config', {
    method: 'POST',
    body: formBody(data),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })

// Subscriptions
export const getSubscriptions = () => request<SubscriptionsData>('/subscriptions')
export const discoverSubscriptions = (browser: string) =>
  request<DiscoverResult>('/subscriptions/discover', {
    method: 'POST',
    body: formBody({ browser }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const discoverEmailSubscriptions = () =>
  request<DiscoverResult>('/subscriptions/discover/email', { method: 'POST' })
export const addSubscription = (data: Record<string, string>) =>
  request<{ ok: boolean; msg: string }>('/subscriptions/add', {
    method: 'POST',
    body: formBody(data),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const setSubStatus = (id: number, status: string) =>
  request<{ ok: boolean }>(`/subscriptions/${id}/status`, {
    method: 'POST',
    body: formBody({ status }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const setSubCredentials = (id: number, data: Record<string, string>) =>
  request<{ ok: boolean }>(`/subscriptions/${id}/credentials`, {
    method: 'POST',
    body: formBody(data),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const deleteSub = (id: number) =>
  request<{ ok: boolean }>(`/subscriptions/${id}/delete`, { method: 'POST' })
export const openSub = (id: number) =>
  request<SubActionResult>(`/subscriptions/${id}/open`, { method: 'POST' })
export const cancelRequest = (id: number) =>
  request<SubActionResult>(`/subscriptions/${id}/cancel/request`, { method: 'POST' })
export const cancelConfirm = (id: number, confirm: string) =>
  request<SubActionResult>(`/subscriptions/${id}/cancel/confirm`, {
    method: 'POST',
    body: formBody({ confirm }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })

// Finance
export const getFinance = () => request<FinanceData>('/finance')
export const getFinanceTransactions = (params?: { status?: string; project_id?: number | string; limit?: number }) => {
  const p = new URLSearchParams()
  if (params?.status) p.set('status', params.status)
  if (params?.project_id) p.set('project_id', String(params.project_id))
  if (params?.limit) p.set('limit', String(params.limit))
  return request<{ transactions: FinanceTxn[] }>(`/finance/transactions?${p}`)
}
export const syncFinance = () =>
  request<FinanceSyncReport>('/finance/sync', { method: 'POST' })
export const setFinanceAutosync = (enabled: boolean) =>
  request<{ ok: boolean; autosync_enabled: boolean }>('/finance/autosync', {
    method: 'POST',
    body: formBody({ enabled: enabled ? '1' : '0' }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const createFinanceProject = (data: Record<string, string>) =>
  request<{ ok: boolean; id: number; code: string }>('/finance/projects', {
    method: 'POST',
    body: formBody(data),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const addFinanceBudget = (projectId: number, data: Record<string, string>) =>
  request<{ ok: boolean; id: number }>(`/finance/projects/${projectId}/budget`, {
    method: 'POST',
    body: formBody(data),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const attributeTransaction = (txnId: number, projectId: number | string) =>
  request<{ ok: boolean; project_id: number | null }>(`/finance/transactions/${txnId}/attribute`, {
    method: 'POST',
    body: formBody({ project_id: projectId }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const createFinanceRule = (data: Record<string, string>) =>
  request<{ ok: boolean; id: number }>('/finance/rules', {
    method: 'POST',
    body: formBody(data),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const setVendorDefaultProject = (vendorId: number, projectId: number | string) =>
  request<{ ok: boolean; default_project_id: number | null }>(`/finance/vendors/${vendorId}/default-project`, {
    method: 'POST',
    body: formBody({ project_id: projectId }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const requestFinanceWrite = (data: Record<string, string>) =>
  request<{ ok: boolean; write: FinanceWrite }>('/finance/writes/request', {
    method: 'POST',
    body: formBody(data),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const confirmFinanceWrite = (id: number, confirm: string) =>
  request<{ ok: boolean; write?: FinanceWrite; message?: string }>(`/finance/writes/${id}/confirm`, {
    method: 'POST',
    body: formBody({ confirm }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const rejectFinanceWrite = (id: number) =>
  request<{ ok: boolean; write: FinanceWrite }>(`/finance/writes/${id}/reject`, { method: 'POST' })

// Coach (PA / secretary + life-coach + mood)
export const getCoach = () => request<CoachData>('/coach')
export const getCoachSignals = (params?: { signal_type?: string; limit?: number }) => {
  const p = new URLSearchParams()
  if (params?.signal_type) p.set('signal_type', params.signal_type)
  if (params?.limit) p.set('limit', String(params.limit))
  return request<{ signals: MoodSignal[] }>(`/coach/signals?${p}`)
}
export const ingestCoachText = (data: { text: string; prefer?: string; project_id?: string; event_ref?: string }) =>
  request<{ ok: boolean; signal: MoodSignal }>('/coach/ingest', {
    method: 'POST',
    body: formBody(data as unknown as Record<string, string>),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })

export const ingestCoachAudio = (file: Blob, opts?: { filename?: string; project_id?: string; event_ref?: string }) => {
  const fd = new FormData()
  fd.append('file', file, opts?.filename || 'recording.webm')
  if (opts?.project_id) fd.append('project_id', opts.project_id)
  if (opts?.event_ref) fd.append('event_ref', opts.event_ref)
  return request<{ ok: boolean; signal: MoodSignal }>('/coach/ingest-audio', {
    method: 'POST',
    body: fd,
  })
}

export const computeCoachBehavior = () =>
  request<{ ok: boolean; written: unknown[]; skipped: { signal: string; reason: string }[] }>('/coach/behavior/compute', { method: 'POST' })
export const askCoach = (question: string) =>
  request<CoachAskResult>('/coach/ask', {
    method: 'POST',
    body: formBody({ question }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const reflectCoach = () =>
  request<{ ok: boolean; status: string; insights: CoachInsight[]; message?: string }>('/coach/reflect', { method: 'POST' })
export const setCoachAutoreflect = (enabled: boolean) =>
  request<{ ok: boolean; autoreflect_enabled: boolean }>('/coach/autoreflect', {
    method: 'POST',
    body: formBody({ enabled: enabled ? 'true' : 'false' }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const setCoachStrictPrivate = (enabled: boolean) =>
  request<{ ok: boolean; strict_private: boolean }>('/coach/strict-private', {
    method: 'POST',
    body: formBody({ enabled: enabled ? 'true' : 'false' }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const setInsightOutcome = (id: number, status: string, outcome?: string) =>
  request<{ ok: boolean; insight: CoachInsight }>(`/coach/insights/${id}/outcome`, {
    method: 'POST',
    body: formBody({ status, outcome: outcome || '' }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const requestCoachAction = (data: Record<string, string>) =>
  request<{ ok: boolean; action: SecretaryAction }>('/coach/actions/request', {
    method: 'POST',
    body: formBody(data),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const confirmCoachAction = (id: number, confirm: string) =>
  request<{ ok: boolean; action?: SecretaryAction; message?: string }>(`/coach/actions/${id}/confirm`, {
    method: 'POST',
    body: formBody({ confirm }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const rejectCoachAction = (id: number) =>
  request<{ ok: boolean; action: SecretaryAction }>(`/coach/actions/${id}/reject`, { method: 'POST' })
export const purgeCoach = (confirm: string, ids?: string) =>
  request<{ ok: boolean; deleted_signals?: number; forgotten_memories?: number; message?: string }>('/coach/purge', {
    method: 'POST',
    body: formBody({ confirm, ids: ids || '' }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })

// Avatar (humanistic avatar layer — persona/voice + external GPU avatar bridge)
export const getAvatar = () => request<AvatarData>('/avatar')
export const getAvatarVoices = () => request<{ voices: AvatarVoice[] }>('/avatar/voices')
export const previewAvatarVoice = (text: string, voice_id: string) =>
  requestBlob('/avatar/voice/preview', {
    method: 'POST',
    body: formBody({ text, voice_id }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const createAvatarProfile = (data: Record<string, string | boolean>) =>
  request<{ ok: boolean; profile: AvatarProfile }>('/avatar/profiles', {
    method: 'POST',
    body: formBody(data as Record<string, string>),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })

// Fleet
export const getFleetStatus = () => request<FleetStatus>('/fleet/status')
export const listBots = () => request<{ bots: FleetBot[] }>('/fleet/bots')
export const createBot = (data: Record<string, string | undefined>) =>
  request<{ ok: boolean; bot: FleetBot }>('/fleet/bots', {
    method: 'POST',
    body: formBody(data as Record<string, string>),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const startBot = (id: number, goal?: string) =>
  request<{ ok: boolean; msg: string; bot_id: number }>(`/fleet/bots/${id}/start`, {
    method: 'POST',
    body: formBody({ goal: goal || '' }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const stopBot = (id: number) =>
  request<{ ok: boolean; msg: string }>(`/fleet/bots/${id}/stop`, { method: 'POST' })
export const deleteBot = (id: number) =>
  request<{ ok: boolean; msg: string }>(`/fleet/bots/${id}/delete`, { method: 'POST' })
export const listTeams = () => request<{ teams: FleetTeam[] }>('/fleet/teams')
export const createTeam = (data: Record<string, string>) =>
  request<{ ok: boolean; team: FleetTeam }>('/fleet/teams', {
    method: 'POST',
    body: formBody(data),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const updateAvatarProfile = (id: number, data: Record<string, string | boolean>) =>
  request<{ ok: boolean; profile: AvatarProfile }>(`/avatar/profiles/${id}`, {
    method: 'PATCH',
    body: formBody(data as Record<string, string>),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const deleteAvatarProfile = (id: number) =>
  request<{ ok: boolean; id: number }>(`/avatar/profiles/${id}`, { method: 'DELETE' })
export const getAvatarSessions = (status?: string) =>
  request<{ sessions: AvatarSession[] }>(
    `/avatar/sessions${status ? `?status=${encodeURIComponent(status)}` : ''}`)
export const startAvatarSession = (profile_id: number) =>
  request<AvatarSessionStartResult>('/avatar/session/start', {
    method: 'POST',
    body: formBody({ profile_id }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const recordAvatarConsent = (id: number, granted: boolean, detail?: string) =>
  request<{ ok: boolean; session: AvatarSession }>(`/avatar/session/${id}/consent`, {
    method: 'POST',
    body: formBody({ granted, detail: detail || '' }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const endAvatarSession = (id: number) =>
  request<{ ok: boolean; session: AvatarSession }>(`/avatar/session/${id}/end`, { method: 'POST' })

export const startTeam = (id: number) =>
  request<{ ok: boolean; msg: string; team_id: number }>(`/fleet/teams/${id}/start`, { method: 'POST' })
export const stopTeam = (id: number) =>
  request<{ ok: boolean; msg: string }>(`/fleet/teams/${id}/stop`, { method: 'POST' })
export const deleteTeam = (id: number) =>
  request<{ ok: boolean; msg: string }>(`/fleet/teams/${id}/delete`, { method: 'POST' })
export const scaleTeam = (id: number, targetCount: number, goalTemplate?: string) =>
  request<{ ok: boolean; msg: string; spawned: number; errors: string[]; current_count: number }>(
    `/fleet/teams/${id}/scale`,
    {
      method: 'POST',
      body: formBody({ target_count: String(targetCount), goal_template: goalTemplate || '' }),
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    }
  )
export const listProvisioning = () => request<{ jobs: ProvisioningJob[]; resources: ProvisionedResource[] }>('/fleet/provision')
export const planProvision = (data: { resource_type: string; config: string; bot_id?: string; team_id?: string }) =>
  request<{ ok: boolean; job_id: number; resource_id: number; plan_output: string; status: string }>('/fleet/provision/plan', {
    method: 'POST',
    body: formBody(data as Record<string, string>),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const approveProvision = (jobId: number) =>
  request<{ ok: boolean; job_id: number; resource_id: string; output: string; status: string }>(`/fleet/provision/${jobId}/approve`, { method: 'POST' })

// ---- Types ----
export interface Task {
  id: number
  prompt: string
  status: string
  result: string | null
  parsed: Record<string, unknown> | null
  scores: Record<string, unknown> | null
  execution_time_ms: number | null
  created_at: string
}
export interface DashboardData { recent_tasks: Task[]; has_keys: boolean }
export interface TaskHistoryData { tasks: Task[]; page: number; total_pages: number; total: number }
export interface ApiKey {
  id: number; provider: string; name: string; label: string; category: string;
  masked_key: string; revealed: string | null; env_var: string; is_active: boolean;
  notes: string; expires_at: string | null; rotation_days: number | null;
  last_used_at: string | null; created_at: string;
}
export interface ServiceEntry { slug: string; name: string; category: string; env_var?: string; key_url?: string; signup_url?: string; steps?: string[]; saved: boolean }
export interface KeysData { keys: ApiKey[]; catalog: ServiceEntry[]; categories: string[]; used_categories: string[]; reminders: { level: string; text: string }[]; total_count: number }
export interface AddKeyPayload { provider: string; label?: string; api_key: string; category?: string; env_var?: string; notes?: string; expires_at?: string; rotation_days?: string }
export interface AuditEntry { provider: string; label: string; action: string; actor: string | null; detail: string; created_at: string }
export interface DirectoryData { grouped: { category: string; services: ServiceEntry[] }[] }
export interface ConnectedData { rows: ConnectedRow[]; proxy_available: boolean; connected_count: number; total_count: number }
export interface ConnectedRow { name: string; category: string; connector: string; connected: boolean; created_at: string | null; description?: string }
export interface GovernanceData { alerts: Alert[]; open_alerts: number; ledger: LedgerEntry[]; metrics: MetricRow[]; total_metrics: number; cloud_shift_active: boolean; exec_locus_dist: unknown[] }
export interface Alert { id: number; rule: string; severity: string; detail: string; acknowledged: boolean; created_at: string }
export interface LedgerEntry { id: number; agent_role: string; provider: string; score: number; detail: string; created_at: string }
export interface MetricRow { provider: string; calls: number; avg_latency: number; success_rate: number; avg_rating: number | null }
export interface ProvenanceData { records: ProvenanceRecord[]; q: string; page: number; total_pages: number; total: number }
export interface ProvenanceRecord { id: number; claim_text: string; origin_role: string; origin_provider: string; confidence: number; verified: boolean; created_at: string }
export interface MemoryData { layers: MemoryLayer[]; layer_types: string[]; q: string; layer: string; total: number }
export interface MemoryLayer { id: number; layer_type: string; topic: string; summary: string; content: string; created_at: string }
export interface SystemData { results: Record<string, unknown>; error: string | null }
export interface LocalModelGuidance {
  install_url: string | null; models_url: string | null; install_note: string | null;
  install_cmd: string; serve_cmd: string; pull_cmd: string;
}
export interface LocalProvider {
  key: string; label: string; blurb: string; base_url: string; reachable: boolean;
  default_model: string; loaded_models: string[] | null; list_error: string | null;
  registry_models: Record<string, unknown>[]; guidance: LocalModelGuidance | null;
  model_env: string; concurrency: number;
}
export interface LocalModelsData {
  providers: LocalProvider[]; reachable_count: number; total_count: number;
  registry: Record<string, unknown>[]; registry_path: string; registry_exists: boolean;
}
export interface CapabilitiesData { browser_enabled: boolean; ssh_enabled: boolean; email_enabled: boolean; browser_backends: unknown[]; ssh: SshStatus; email: EmailStatus; browser_domains: string[]; ssh_commands: string[]; audit: CapabilityAudit[] }
export interface SshStatus { configured: boolean; host: string; user: string; port: number; auth: string; host_verified: boolean }
export interface EmailStatus { configured: boolean; host: string; user: string; port: number; mailbox: string; ssl: boolean }
export interface CapabilityAudit { capability: string; target: string; backend: string; status: string; detail: string; source: string; created_at: string }
export interface TestResult { allowed: boolean; reason: string; output: string | null; screenshot?: string | null }
export interface SubscriptionsData { subscriptions: Subscription[]; audit: SubAuditEntry[]; catalog: unknown[] }
export interface Subscription { id: number; name: string; domain: string; login_url: string; account_url: string; login_username: string; has_credential: boolean; status: string; est_cost: string; currency: string; source: string; notes: string; last_checked_at: string | null; created_at: string }
export interface SubAuditEntry { subscription: string; action: string; status: string; detail: string; created_at: string }
export interface SubActionResult { kind: string; ok: boolean | null; message: string; screenshot?: string | null; pending?: boolean; sub_id: number }
export interface DiscoverResult { kind: string; ok: boolean; message: string }
// Finance
export interface FinanceProviderStatus { provider: string; connected: boolean; source: string | null; detail: string }
export interface FinanceProviders { quickbooks: FinanceProviderStatus; plaid: FinanceProviderStatus }
export interface FinanceBudgetRow {
  project_id: number; code: string; name: string; llc: string | null;
  budget_total: number; actual_total: number; variance: number; pct_used: number | null;
  txn_count: number; largest_txn: number; flags: string[];
}
export interface FinanceBudgetData { projects: FinanceBudgetRow[]; unattributed: { count: number; total: number } }
export interface FinanceProjectLite { id: number; code: string; name: string; llc: string | null; active: boolean }
export interface FinanceRule { id: number; match_text: string; project_id: number; note: string | null }
export interface FinanceVendorLite { id: number; name: string; source: string; default_project_id: number | null }
export interface FinanceRun {
  id: number; source: string; trigger: string; status: string;
  transactions_ingested: number; entities_ingested: number; removed_count: number;
  attributed_count: number; error: string | null; started_at: string | null; finished_at: string | null;
}
export interface FinanceAuditEntry { id: number; action: string; status: string; detail: string | null; created_at: string | null }
export interface FinanceWrite {
  id: number; kind: string; target_external_id: string | null; summary: string | null;
  status: string; result_detail: string | null; requested_at: string | null; confirmed_at: string | null;
}
export interface FinanceTxn {
  id: number; source: string; external_id: string; date: string | null; amount: number;
  currency: string; name: string | null; memo: string | null; category_raw: string | null;
  pending: boolean; vendor: string | null; vendor_id: number | null;
  project_code: string | null; project_id: number | null;
  attribution_status: string; attribution_confidence: number; attribution_method: string | null;
}
export interface FinanceData {
  providers: FinanceProviders; budget: FinanceBudgetData; projects: FinanceProjectLite[];
  rules: FinanceRule[]; vendors: FinanceVendorLite[]; recent_runs: FinanceRun[];
  audit: FinanceAuditEntry[]; writes: FinanceWrite[]; txn_count: number; autosync_enabled: boolean;
}
export interface FinanceSyncReport {
  ok: boolean; trigger: string; error: string | null;
  plaid: Record<string, unknown>; quickbooks: Record<string, unknown>;
}

// Coach
export interface MoodProviderConn { provider: string; connected: boolean; source: string | null; detail: string; sunset?: string }
export interface MoodProviderStatus { active: string; hume?: MoodProviderConn; [key: string]: unknown }
export interface DeliveryChannel { channel: string; configured: boolean; provider: string | null; detail: string }
export interface DeliveryStatus { email: DeliveryChannel; sms: DeliveryChannel; calendar: DeliveryChannel }
export interface MoodSignal {
  id: number; signal_type: string; source: string; observed_at: string | null;
  valence: number | null; arousal: number | null; score: number | null;
  label: string | null; summary: string | null; metrics: Record<string, unknown> | null;
  project_id: number | null; event_ref: string | null; memory_id: string | null;
  private: boolean; created_at: string | null;
}
export interface CoachCitation { ref: string; memory_id: string | null; content?: string; category?: string | null; signal_id?: number }
export interface CoachInsight {
  id: number; kind: string; title: string; detail: string | null; severity: string;
  micro_action: string | null; citations: CoachCitation[] | null; confidence: number | null;
  status: string; outcome: string | null; source: string; created_at: string | null; updated_at: string | null;
}
export interface SecretaryAction {
  id: number; kind: string; channel: string | null; recipient: string | null;
  subject: string | null; body: string | null; payload: Record<string, unknown> | null;
  summary: string | null; status: string; provider: string | null;
  result_detail: string | null; requested_at: string | null; confirmed_at: string | null;
}
export interface CoachAuditEntry { id: number; action: string; status: string; source: string | null; detail: string | null; created_at: string | null }
export interface CoachAskResult {
  ok: boolean; status: string; answer: string | null; citations: CoachCitation[];
  message?: string; strict_private?: boolean;
  compliance_scores?: unknown; hallucination_risks?: unknown;
}
export interface CoachData {
  mood_provider: MoodProviderStatus; delivery: DeliveryStatus;
  recent_signals: MoodSignal[]; insights: CoachInsight[];
  actions: SecretaryAction[]; pending_actions: SecretaryAction[];
  audit: CoachAuditEntry[]; autoreflect_enabled: boolean; strict_private: boolean;
}

// Avatar
export interface AvatarVoiceStatus { provider: string; connected: boolean; source: string | null; detail: string }
export interface AvatarBackend { configured: boolean; source: string | null; detail: string }
export interface AvatarStatus { configured: boolean; providers: Record<string, AvatarBackend>; detail: string }
export interface AvatarVoice { voice_id: string; name: string | null; category: string | null; preview_url: string | null }
export interface AvatarProfile {
  id: number; name: string; persona: string | null; bot_id: number | null;
  voice_provider: string | null; voice_id: string | null;
  avatar_provider: string | null; avatar_config: Record<string, unknown> | null;
  disclosure_required: boolean; consent_required: boolean; active: boolean;
  created_at: string | null; updated_at: string | null;
}
export interface AvatarSession {
  id: number; avatar_profile_id: number; mode: string | null; status: string;
  provider: string | null; disclosure_shown: boolean; consent_status: string;
  consent_detail: string | null; result_detail: string | null;
  started_at: string | null; ended_at: string | null; created_at: string | null;
  connection?: Record<string, unknown> | null;
}
export interface AvatarAuditEntry { action: string; status: string; detail: string | null; created_at: string | null }
export interface AvatarData {
  voice: AvatarVoiceStatus; avatar: AvatarStatus; profiles: AvatarProfile[];
  sessions: AvatarSession[]; disclosure: string; consent_prompt: string;
  audit: AvatarAuditEntry[];
}
export interface AvatarSessionStartResult {
  ok: boolean; session: AvatarSession; disclosure: string | null; consent_prompt: string | null;
}

// Fleet
export interface FleetBot {
  id: number; name: string; description: string | null; team_id: number | null; spawned_by_id: number | null;
  identity_schema: Record<string, string>; prompt_cards: string[]; goal: string | null; protocols: string[];
  allowed_caps: string[]; lifecycle: string; last_run_at: string | null; last_task_run_id: number | null;
  created_at: string; updated_at: string;
}
export interface FleetTeam {
  id: number; name: string; description: string | null; spawn_cap: number; status: string;
  memory_namespace: string | null; member_count: number; created_at: string; updated_at: string;
}
export interface ProvisioningJob {
  id: number; bot_id: number | null; team_id: number | null; resource_id: number | null; status: string;
  requested_config: Record<string, unknown>; plan_summary: string | null; apply_summary: string | null;
  error: string | null; planned_at: string | null; approved_at: string | null; applied_at: string | null;
  created_at: string;
}
export interface ProvisionedResource {
  id: number; bot_id: number | null; team_id: number | null; resource_type: string; provider: string;
  name: string | null; resource_id: string | null; status: string; plan_output: string | null;
  apply_output: string | null; config_json: Record<string, unknown>; approved_at: string | null;
  provisioned_at: string | null; created_at: string;
}
export interface FleetStatus {
  total_bots: number; total_teams: number; total_resources: number; jobs_awaiting_approval: number;
  bot_lifecycle: Record<string, number>; team_status: Record<string, number>; resource_status: Record<string, number>;
}

// Prompt Engine (self-evolving prompts)
export interface PromptProgramSummary {
  id: number; name: string; signature_name: string; description: string | null;
  current_version_id: number | null; live_version_no: number | null;
  live_score?: number | null;
  version_count?: number; usable_trace_count: number; updated_at?: string | null;
}
export interface PromptScoreBreakdown {
  n: number; score: number | null; format_fraction: number | null;
  compliance_score: number | null; hallucination_risk: number | null;
  missing_sections: string[];
}
export interface PromptVersionRow {
  id: number; version_no: number; status: string; ever_live: boolean;
  optimizer: string | null; score: number | null; base_score: number | null;
  delta: number | null;
  breakdown?: PromptScoreBreakdown | null;
  base_breakdown?: PromptScoreBreakdown | null;
  instructions: string; provenance: Record<string, unknown>;
  created_at: string | null; promoted_at: string | null;
}
export interface PromptRunCandidate { version_id: number; optimizer: string | null; score: number | null }
export interface PromptRunRow {
  id: number; program_id: number; status: string; optimizer: string | null;
  trainset_size: number | null; best_score: number | null; base_score: number | null;
  error: string | null; candidates: PromptRunCandidate[];
  started_by: string | null; created_at: string | null; completed_at: string | null;
}
export interface PromptProgramDetail {
  program: PromptProgramSummary; versions: PromptVersionRow[]; runs: PromptRunRow[];
}

export const listPrompts = () =>
  request<{ programs: PromptProgramSummary[]; min_traces: number }>('/prompts')
export const getPromptDetail = (name: string) =>
  request<PromptProgramDetail>(`/prompts/${encodeURIComponent(name)}`)
export const optimizePrompt = (name: string) =>
  request<{ ok: boolean; run_id: number }>(`/prompts/${encodeURIComponent(name)}/optimize`, { method: 'POST' })
export const getPromptRun = (runId: number) =>
  request<PromptRunRow>(`/prompts/runs/${runId}`)
export const promptVersionPromote = (versionId: number) =>
  request<{ ok: boolean; proposal_id: string; proposal_db_id: number | null; message: string }>(
    `/prompts/versions/${versionId}/promote`, { method: 'POST' })
export const promptVersionRollback = (versionId: number) =>
  request<{ ok: boolean }>(`/prompts/versions/${versionId}/rollback`, { method: 'POST' })

// Evolution Loop (edge-truth / thesis self-evolution)
export interface EvolutionIteration {
  id: number; iteration_no: number; sandbox_label: string;
  test_results: Record<string, unknown>; compliance_score: number | null;
  hallucination_risk: number | null; composite_score: number | null;
  all_tests_passed: boolean; outcome: string; error: string | null;
  created_at: string | null;
}
export interface EvolutionThesis {
  id: number; component_id: number; branch_cell: string;
  thesis: Record<string, unknown>; rationale: string | null; status: string;
  test_score: number | null; base_score: number | null;
  governance_proposal_id: string | null; governance_proposal_db_id: number | null;
  requires_human_gate: boolean; trial_iteration_count: number;
  stalled_at: string | null; cycle_index: number;
  selection_votes: Record<string, unknown>;
  iterations: EvolutionIteration[];
  created_at: string | null; updated_at: string | null;
}
export interface CognitionComponent {
  id: number; name: string; component_type: string; description: string | null;
  truth_json: Record<string, unknown>; truth_version_id: number | null;
  loop_state: string; loop_enabled: boolean; cycle_count: number;
  last_cycle_at: string | null; active_thesis: EvolutionThesis | null;
  total_theses: number; promoted_theses: number;
  thread_alive: boolean;
  created_at: string | null; updated_at: string | null;
}
export interface EvolutionCycleLog {
  id: number; cycle_index: number; outcome: string; thesis_id: number | null;
  next_selection: Record<string, unknown>; detail: string | null; created_at: string | null;
}
export interface ComponentDetail {
  component: CognitionComponent;
  theses: EvolutionThesis[];
  cycle_logs: EvolutionCycleLog[];
}

export const listEvolutionComponents = () =>
  request<{ components: CognitionComponent[] }>('/evolution/components')
export const getEvolutionComponent = (id: number) =>
  request<ComponentDetail>(`/evolution/components/${id}`)
export interface EvolutionComponentStatus {
  id: number; loop_state: string; thread_alive: boolean;
  cycle_count: number; last_cycle_at: string | null;
  total_theses: number; promoted_theses: number;
  active_thesis_id: number | null; active_thesis_status: string | null;
  active_thesis_iterations: number; latest_cycle_log_id: number | null;
}
export const getEvolutionComponentStatus = (id: number) =>
  request<EvolutionComponentStatus>(`/evolution/components/${id}/status`)
export const startEvolutionLoop = (id: number) =>
  request<{ ok: boolean; started: boolean; already_running: boolean }>(
    `/evolution/components/${id}/start`, { method: 'POST' })
export const stopEvolutionLoop = (id: number) =>
  request<{ ok: boolean; stopped: boolean }>(
    `/evolution/components/${id}/stop`, { method: 'POST' })
export const listEvolutionTheses = (params?: { component_id?: number; status?: string }) => {
  const p = new URLSearchParams()
  if (params?.component_id !== undefined) p.set('component_id', String(params.component_id))
  if (params?.status) p.set('status', params.status)
  const qs = p.toString()
  return request<{ theses: EvolutionThesis[]; total: number }>(`/evolution/theses${qs ? '?' + qs : ''}`)
}
export const getEvolutionThesis = (id: number) =>
  request<EvolutionThesis>(`/evolution/theses/${id}`)
export const seedEvolutionThesis = (
  componentId: number,
  body: { branch_cell: string; thesis: Record<string, unknown>; rationale?: string; requires_human_gate?: boolean }
) =>
  request<{ ok: boolean; thesis_id: number }>(
    `/evolution/components/${componentId}/thesis`,
    { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }
  )
export const triggerEvolutionTrial = (componentId: number) =>
  request<{ ok: boolean; outcome: string }>(
    `/evolution/components/${componentId}/trial`, { method: 'POST' })
export const getEvolutionLoopStatus = () =>
  request<{ components: CognitionComponent[] }>('/evolution/loop/status')
