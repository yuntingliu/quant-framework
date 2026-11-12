import { getApiBase } from "./api"

export type Service = "models" | "search" | "rqdata"
export const services: Service[] = ["models", "search", "rqdata"]
export type Selection = { kind: "profile" | "environment" | "none"; id?: string }
export interface Profile {
  id: string; name: string; base_url?: string; model?: string; provider?: string
  model_profile_id?: string | null; user?: string; host?: string
  has_api_key?: boolean; has_brave_api_key?: boolean; has_password?: boolean; configured?: boolean
}
export interface Settings {
  profiles: Profile[]; active_id: string | null; mode?: string; revision?: string
  configured?: boolean; source?: string; environment_configured?: boolean; selection?: Selection
  provider?: string; model_profile_id?: string | null
}
export interface CredentialPolicy { service: Service; id: string; action: "retain" | "clear" | "replace" }
export interface Preview {
  kind: "import" | "rollback"; backend: "json" | "sqlite"; can_apply: boolean
  revision: string | null; receipt: string | null; expires?: number
  changes: Record<Service, { added: string[]; changed: string[]; unchanged: string[]; removed: string[]; selection_before: Selection; selection_after: Selection }>
  configuration: Record<Service, { profiles: Profile[]; selection: Selection }>
  credential_options: { service: Service; id: string; retain_eligible: boolean }[]
  missing_credentials: { service: Service; id: string; code: string }[]
  credential_actions: CredentialPolicy[]; required_consents: string[]; migrations: string[]
  diagnostics: { code: string; message: string }[]
  selection_explanations: Record<Service, { kind: string; message: string }>
}
export interface Operation {
  operation: string; state: string; kind: "import" | "rollback"; revision: string | null
  current_revision?: string | null; rollback_available?: boolean
}
const paths: Record<Service, string> = { models: "/model-providers", search: "/search-providers", rqdata: "/data-source-profiles" }
const codes = new Set(["STALE_PREVIEW", "RECEIPT_EXPIRED", "RECEIPT_USED", "HISTORY_CAPACITY", "APPLICATION_UNCERTAIN", "UNSUPPORTED_JSON_APPLY", "MISSING_CONSENT", "ROLLBACK_CONFLICT", "LOCAL_AUTHORIZATION_REQUIRED", "RETENTION_NOT_ELIGIBLE", "INVALID_CONFIGURATION", "REVISION_CONFLICT", "CREDENTIAL_REQUIRED", "INVALID_REQUEST", "UNAVAILABLE", "BUSY"])
export class ConfigurationError extends Error {
  readonly code: string
  constructor(code: string) { super("Configuration request could not be completed."); this.name = "ConfigurationError"; this.code = code }
}
export function errorCode(error: unknown): string { return error instanceof ConfigurationError ? error.code : "UNAVAILABLE" }

// The same origin/Basic authentication rules as the application client apply.
// No raw error body, thrown network exception, or private request is surfaced.
async function request<T>(path: string, body?: unknown, method = "POST", transfer = false): Promise<T> {
  const base = getApiBase()
  if (transfer) {
    let target: URL
    try { target = new URL(base, window.location.href) }
    catch { throw new ConfigurationError("LOCAL_AUTHORIZATION_REQUIRED") }
    if (!["http:", "https:"].includes(target.protocol) || !["localhost", "127.0.0.1", "[::1]"].includes(target.hostname)
      || target.username || target.password || target.search || target.hash) throw new ConfigurationError("LOCAL_AUTHORIZATION_REQUIRED")
  }
  let response: Response
  try {
    response = await fetch(`${base}${path}`, {
      method: body === undefined ? "GET" : method, credentials: "same-origin", cache: "no-store", redirect: "error",
      signal: AbortSignal.timeout(45_000),
      headers: { ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...(transfer ? { "x-alphalab-configuration-consent": "local-configuration-transfer" } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
  } catch { throw new ConfigurationError("UNAVAILABLE") }
  if (!response.ok) {
    const value = await response.json().catch(() => null)
    const code = typeof value?.detail?.code === "string" && codes.has(value.detail.code) ? value.detail.code
      : response.status === 409 ? "REVISION_CONFLICT" : response.status === 401 || response.status === 403 ? "LOCAL_AUTHORIZATION_REQUIRED"
        : response.status === 422 ? "INVALID_CONFIGURATION" : "UNAVAILABLE"
    throw new ConfigurationError(code)
  }
  try { return (transfer && path.endsWith("/export") ? await response.text() : await response.json()) as T }
  catch { throw new ConfigurationError("UNAVAILABLE") }
}
export const configurationApi = {
  read: (service: Service) => request<Settings>(paths[service]),
  save: (service: Service, body: unknown) => request<Settings>(paths[service], body, "PUT"),
  select: (service: Service, body: unknown) => request<Settings>(paths[service] + "/selection", body, "PUT"),
  test: (service: Service, body: unknown) => request<{ ok?: boolean; connected?: boolean }>(service === "rqdata" ? "/data-sync/connection-test" : paths[service] + "/test", service === "rqdata" ? { template_id: "rq.a_share_research" } : body),
  export: () => request<string>("/configuration-transfer/export", undefined, "GET", true),
  preview: (body: unknown) => request<Preview>("/configuration-transfer/preview", body, "POST", true),
  rollbackPreview: (body: unknown) => request<Preview>("/configuration-transfer/rollback/preview", body, "POST", true),
  apply: (body: unknown, rollback: boolean) => request<Operation>(`/configuration-transfer/${rollback ? "rollback/" : ""}apply`, body, "POST", true),
  operation: (operation: string) => request<Operation>("/configuration-transfer/operation", { operation }, "POST", true),
}
