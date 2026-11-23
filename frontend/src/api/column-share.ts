/** 跨目录受控列共享接口封装：授权、撤回、核对、封存、领用与范围查询。 */
import { fetchJson, request } from './client'

const BASE = '/api/column-share'

export type ColumnDiff = {
  column: string
  status: string
  diff_count: number | null
}

export type ReconcileRow = {
  id: number
  核对编号: string
  授权编号: string
  同名表: string
  审计人: string
  用途: string
  请求列: string[]
  核对时可读列: string[]
  本次已核验列: string[]
  本次不可读列: string[]
  比较行数: number
  列差异摘要: ColumnDiff[]
  总差异数: number
  仅含差异计数: boolean
  核对时间: string
}

export type ReportRow = {
  id: number
  报告编号: string
  来源核对编号: string
  授权编号: string
  同名表: string
  用途: string
  已核验字段范围: string[]
  列差异数: Record<string, number>
  总差异数: number
  封存时授权列快照: string[]
  封存时可读列快照: string[]
  封存时已撤回列快照: string[]
  授权收件组快照: string
  审计人: string
  审签人: string
  封存时间: string
  仅含差异计数: boolean
}

export type GrantRow = {
  id: number
  status: string
  授权编号: string
  来源目录: string
  目标目录: string
  同名表: string
  审计人: string
  用途: string
  授权列: string[]
  可读列: string[]
  已撤回列: string[]
  收件组: string
  审批人: string
  授权时间: string
  撤回记录: { column: string; revoked_by: string; revoked_at: string }[]
}

export type ShareResult<T = Record<string, unknown>> = { ok: boolean; message: string } & T

async function post<T>(path: string, body: unknown): Promise<ShareResult<T>> {
  const response = await request(`${BASE}${path}`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return (await response.json()) as ShareResult<T>
}

export type TraceRegistration = {
  catalog_sync_id: number
  source_catalog: string
  target_catalog: string
  sync_scope: string
  sync_status: string
}

export function traceCatalogs(tableName: string) {
  return fetchJson<ShareResult<{
    registrations: TraceRegistration[]
    source_catalog: string
    target_catalog: string
  }>>(`${BASE}/catalogs/trace?table_name=${encodeURIComponent(tableName)}`)
}

export function listGrants() {
  return fetchJson<{ ok: boolean; items: GrantRow[]; total: number }>(`${BASE}/grants`)
}

export function listReconciles() {
  return fetchJson<{ ok: boolean; items: ReconcileRow[]; total: number }>(`${BASE}/reconciles`)
}

export function listReports() {
  return fetchJson<{ ok: boolean; items: ReportRow[]; total: number }>(`${BASE}/reports`)
}

export function createGrant(payload: {
  source_catalog: string
  target_catalog: string
  table_name: string
  auditor: string
  purpose: string
  granted_columns: string[]
  approver: string
  recipient_group: string
}) {
  return post<{ grant: GrantRow }>('/grants', payload)
}

export function revokeColumn(grantId: number, column: string, operator: string) {
  return post<{ grant: GrantRow }>(`/grants/${grantId}/revoke`, { column, operator })
}

export function readableColumns(grantId: number) {
  return fetchJson<ShareResult>(`${BASE}/grants/${grantId}/readable-columns`)
}

export function runReconcile(grantId: number, requestedColumns: string[] | null, operator: string) {
  return post<{ reconcile: ReconcileRow }>(`/grants/${grantId}/reconcile`, {
    requested_columns: requestedColumns,
    operator,
  })
}

export function sealReport(reconcileId: number, signer: string) {
  return post<{ report: ReportRow }>(`/reconciles/${reconcileId}/seal`, { signer })
}

export function claimReport(reportId: number, recipientGroup: string, purpose: string) {
  return post<{ delivery: Record<string, unknown>; claim: Record<string, unknown> }>(
    `/reports/${reportId}/claim`,
    { recipient_group: recipientGroup, purpose },
  )
}

export function deliveryCoverage(reportId: number, reconcileId: number) {
  return post<Record<string, unknown>>(`/reports/${reportId}/coverage`, { reconcile_id: reconcileId })
}

export function approverScopes(approver: string, tableName?: string) {
  const query = tableName ? `?table_name=${encodeURIComponent(tableName)}` : ''
  return fetchJson<ShareResult>(
    `${BASE}/approvers/${encodeURIComponent(approver)}/scopes${query}`,
  )
}
