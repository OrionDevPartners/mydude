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

// Auth
export const getBranding = () => request<{ name: string; short_name: string; tagline: string }>('/branding')
export const getMe = () => request<{ authenticated: boolean }>('/me')
export const login = (password: string) =>
  request<{ ok: boolean }>('/login', {
    method: 'POST',
    body: formBody({ password }),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
export const logout = () =>
  request<{ ok: boolean }>('/logout', { method: 'POST' })

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
export interface AuditEntry { provider: string; label: string; action: string; detail: string; created_at: string }
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
