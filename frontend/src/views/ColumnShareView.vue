<template>
  <section class="page column-share" data-module="column_share">
    <header class="page-head">
      <div>
        <h2>跨目录受控列共享</h2>
        <p class="page-desc">
          湖仓审计跨目录列级共享：授权单区分来源目录、审计人、用途和列；核对只读当时授权列，
          交付仅含已核验字段范围与无法反推单行内容的差异数量，全程不含原值。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn ghost" to="/data_lake_audit">返回湖仓审计</RouterLink>
      </div>
    </header>

    <p v-if="message" :class="['status-banner', messageOk ? 'ok' : 'err']" data-testid="status-banner">
      {{ message }}
    </p>

    <!-- 步骤1：沿目录同步登记追溯来源/目标 -->
    <article class="panel" data-testid="panel-trace">
      <h3>① 沿目录同步登记追溯来源/目标目录</h3>
      <form class="inline-form" @submit.prevent="onTrace">
        <label><span>同名表</span>
          <input v-model="traceTable" data-testid="trace-table" required />
        </label>
        <button class="btn primary" data-testid="trace-submit" type="submit">追溯目录</button>
      </form>
      <table v-if="traceResult?.ok" class="data-table" data-testid="trace-result">
        <thead>
          <tr><th>同步登记</th><th>来源目录</th><th>目标目录</th><th>同步范围</th><th>同步状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="reg in traceResult.registrations" :key="String(reg.catalog_sync_id)">
            <td>#{{ reg.catalog_sync_id }}</td>
            <td>{{ reg.source_catalog }}</td>
            <td>{{ reg.target_catalog }}</td>
            <td>{{ reg.sync_scope }}</td>
            <td>{{ reg.sync_status }}</td>
          </tr>
        </tbody>
      </table>
    </article>

    <!-- 步骤2：登记列共享授权单 -->
    <article class="panel" data-testid="panel-grant">
      <h3>② 目录方登记列共享授权单</h3>
      <form class="grid-form" @submit.prevent="onCreateGrant">
        <label><span>来源目录</span><input v-model="grantForm.source_catalog" data-testid="grant-source" required /></label>
        <label><span>目标目录</span><input v-model="grantForm.target_catalog" data-testid="grant-target" required /></label>
        <label><span>同名表</span><input v-model="grantForm.table_name" data-testid="grant-table" required /></label>
        <label><span>审计人</span><input v-model="grantForm.auditor" data-testid="grant-auditor" required /></label>
        <label><span>用途</span><input v-model="grantForm.purpose" data-testid="grant-purpose" required /></label>
        <label><span>收件组</span><input v-model="grantForm.recipient_group" data-testid="grant-recipient" required /></label>
        <label><span>审批人</span><input v-model="grantForm.approver" data-testid="grant-approver" required /></label>
        <label><span>授权列（逗号分隔）</span>
          <input v-model="grantColumnsText" data-testid="grant-columns" required />
        </label>
        <button class="btn primary" data-testid="grant-submit" type="submit">登记授权单</button>
      </form>

      <table class="data-table" data-testid="grant-table-list">
        <thead>
          <tr>
            <th>授权编号</th><th>来源/目标目录</th><th>同名表</th><th>审计人</th><th>用途</th>
            <th>授权列</th><th>当前可读列</th><th>已撤回列</th><th>收件组</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="grant in grants" :key="grant.id" :data-testid="`grant-row-${grant.id}`">
            <td>{{ grant.授权编号 }}</td>
            <td>{{ grant.来源目录 }} → {{ grant.目标目录 }}</td>
            <td>{{ grant.同名表 }}</td>
            <td>{{ grant.审计人 }}</td>
            <td>{{ grant.用途 }}</td>
            <td>{{ grant.授权列.join(', ') }}</td>
            <td data-testid="readable-cols">{{ grant.可读列.join(', ') || '—' }}</td>
            <td>{{ grant.已撤回列.join(', ') || '—' }}</td>
            <td>{{ grant.收件组 }}</td>
            <td class="row-actions">
              <button class="link" data-testid="btn-reconcile" type="button" @click="onReconcile(grant.id)">
                发起核对
              </button>
              <button
                v-for="col in grant.可读列"
                :key="col"
                class="link danger"
                type="button"
                :data-testid="`btn-revoke-${grant.id}-${col}`"
                @click="onRevoke(grant.id, col)"
              >
                撤回{{ col }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </article>

    <!-- 步骤3：核对记录（无原值差异摘要） -->
    <article class="panel" data-testid="panel-reconcile">
      <h3>③ 授权范围内核对（只存差异数量，无原值）</h3>
      <table class="data-table" data-testid="reconcile-table">
        <thead>
          <tr>
            <th>核对编号</th><th>同名表</th><th>核对时可读列</th><th>已核验列</th><th>不可读列</th>
            <th>列差异摘要</th><th>总差异数</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in reconciles" :key="row.id" :data-testid="`reconcile-row-${row.id}`">
            <td>{{ row.核对编号 }}</td>
            <td>{{ row.同名表 }}</td>
            <td>{{ row.核对时可读列.join(', ') }}</td>
            <td data-testid="verified-cols">{{ row.本次已核验列.join(', ') || '—' }}</td>
            <td>{{ row.本次不可读列.join(', ') || '—' }}</td>
            <td>
              <span v-for="d in row.列差异摘要" :key="d.column" class="diff-pill">
                {{ d.column }}:
                <template v-if="d.diff_count === null">不可读</template>
                <template v-else>{{ d.diff_count }}</template>
              </span>
            </td>
            <td data-testid="total-diff">{{ row.总差异数 }}</td>
            <td class="row-actions">
              <button class="link" data-testid="btn-seal" type="button" @click="onSeal(row.id)">
                审签封存
              </button>
            </td>
          </tr>
          <tr v-if="!reconciles.length">
            <td colspan="8" class="empty-state">暂无核对记录</td>
          </tr>
        </tbody>
      </table>
    </article>

    <!-- 步骤4：封存报告与领用 -->
    <article class="panel" data-testid="panel-report">
      <h3>④ 封存差异摘要与获准领用</h3>
      <table class="data-table" data-testid="report-table">
        <thead>
          <tr>
            <th>报告编号</th><th>同名表</th><th>用途</th><th>已核验字段范围</th><th>列差异数</th>
            <th>封存收件组快照</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="report in reports" :key="report.id" :data-testid="`report-row-${report.id}`">
            <td>{{ report.报告编号 }}</td>
            <td>{{ report.同名表 }}</td>
            <td>{{ report.用途 }}</td>
            <td data-testid="report-scope">{{ report.已核验字段范围.join(', ') }}</td>
            <td data-testid="report-diffs">{{ JSON.stringify(report.列差异数) }}</td>
            <td>{{ report.授权收件组快照 }}</td>
            <td class="row-actions">
              <button class="link" data-testid="btn-claim" type="button" @click="onClaim(report.id)">
                质量组领用
              </button>
              <button class="link" data-testid="btn-claim-denied" type="button" @click="onClaimDenied(report.id)">
                未授权组领用（验证拒绝）
              </button>
              <button class="link" data-testid="btn-coverage" type="button" @click="onCoverage(report.id)">
                交付覆盖核对
              </button>
            </td>
          </tr>
          <tr v-if="!reports.length">
            <td colspan="7" class="empty-state">暂无封存报告</td>
          </tr>
        </tbody>
      </table>
      <pre v-if="delivery" class="json-box" data-testid="delivery-box">{{ JSON.stringify(delivery, null, 2) }}</pre>
    </article>

    <!-- 步骤5：审批人双范围 -->
    <article class="panel" data-testid="panel-scopes">
      <h3>⑤ 同一审批人的两个范围：封存报告 vs 当前可读列</h3>
      <form class="inline-form" @submit.prevent="onScopes">
        <label><span>审批人</span><input v-model="scopeApprover" data-testid="scope-approver" required /></label>
        <button class="btn primary" data-testid="scope-submit" type="submit">查询双范围</button>
      </form>
      <pre v-if="scopes" class="json-box" data-testid="scopes-box">{{ JSON.stringify(scopes, null, 2) }}</pre>
    </article>

    <footer class="page-foot">
      <span>授权 {{ grants.length }} 份 · 核对 {{ reconciles.length }} 次 · 封存报告 {{ reports.length }} 份</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import {
  approverScopes,
  claimReport,
  createGrant,
  deliveryCoverage,
  listGrants,
  listReconciles,
  listReports,
  revokeColumn,
  runReconcile,
  sealReport,
  traceCatalogs,
  type GrantRow,
  type ReconcileRow,
  type ReportRow,
} from '@/api/column-share'

const DEFAULT_TABLE = 'dws_sales_daily'
const traceTable = ref(DEFAULT_TABLE)
const traceResult = ref<Awaited<ReturnType<typeof traceCatalogs>> | null>(null)

const grantForm = reactive({
  source_catalog: 'hive_metastore_prod',
  target_catalog: 'aws_glue_catalog',
  table_name: DEFAULT_TABLE,
  auditor: '陈审',
  purpose: '月度跨目录对账',
  recipient_group: '质量组',
  approver: '王敏',
})
const grantColumnsText = ref('settle_amount, risk_level')

const grants = ref<GrantRow[]>([])
const reconciles = ref<ReconcileRow[]>([])
const reports = ref<ReportRow[]>([])
const delivery = ref<unknown>(null)
const scopes = ref<unknown>(null)
const scopeApprover = ref('王敏')
const message = ref('')
const messageOk = ref(true)

function notify(ok: boolean, text: string) {
  messageOk.value = ok
  message.value = text
}

async function reloadAll() {
  const [grantRes, reconcileRes, reportRes] = await Promise.all([
    listGrants(),
    listReconciles(),
    listReports(),
  ])
  grants.value = grantRes.items
  reconciles.value = reconcileRes.items
  reports.value = reportRes.items
}

async function onTrace() {
  traceResult.value = await traceCatalogs(traceTable.value)
  notify(traceResult.value.ok, traceResult.value.message || '目录追溯完成')
}

async function onCreateGrant() {
  const granted_columns = grantColumnsText.value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
  const result = await createGrant({ ...grantForm, granted_columns })
  notify(result.ok, result.message)
  if (result.ok) await reloadAll()
}

async function onRevoke(grantId: number, column: string) {
  const result = await revokeColumn(grantId, column, grantForm.approver)
  notify(result.ok, result.message)
  if (result.ok) await reloadAll()
}

async function onReconcile(grantId: number) {
  const result = await runReconcile(grantId, null, grantForm.auditor)
  notify(result.ok, result.message)
  if (result.ok) await reloadAll()
}

async function onSeal(reconcileId: number) {
  const result = await sealReport(reconcileId, grantForm.auditor)
  notify(result.ok, result.message)
  if (result.ok) await reloadAll()
}

async function onClaim(reportId: number) {
  const result = await claimReport(reportId, '质量组', grantForm.purpose)
  notify(result.ok, result.message)
  if (result.ok) delivery.value = result.delivery
}

async function onClaimDenied(reportId: number) {
  const result = await claimReport(reportId, '营销分析组', grantForm.purpose)
  notify(result.ok, result.message)
}

async function onCoverage(reportId: number) {
  const latest = reconciles.value.at(-1)
  if (!latest) {
    notify(false, '还没有核对记录，无法比对交付覆盖')
    return
  }
  delivery.value = await deliveryCoverage(reportId, latest.id)
  notify(true, '已生成交付覆盖与新核对可读列对照')
}

async function onScopes() {
  const result = await approverScopes(scopeApprover.value, DEFAULT_TABLE)
  notify(result.ok, result.message)
  if (result.ok) scopes.value = result
}

onMounted(async () => {
  await Promise.all([onTrace(), reloadAll()])
})
</script>

<style scoped>
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 16px;
}
.panel h3 { margin: 0 0 12px; font-size: 15px; }
.inline-form, .grid-form { display: flex; flex-wrap: wrap; gap: 10px 14px; align-items: flex-end; }
.grid-form label, .inline-form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #475569; }
.grid-form input, .inline-form input { min-width: 180px; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.diff-pill {
  display: inline-block;
  margin: 2px 6px 2px 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: #eef2ff;
  border: 1px solid #c7d2fe;
  font-size: 12px;
}
.link.danger { color: #b91c1c; }
.json-box {
  margin-top: 12px;
  padding: 12px;
  background: #0f172a;
  color: #e2e8f0;
  border-radius: 8px;
  font-size: 12px;
  overflow: auto;
}
.status-banner { padding: 8px 12px; border-radius: 8px; font-size: 13px; }
.status-banner.ok { background: #ecfdf5; border: 1px solid #6ee7b7; color: #047857; }
.status-banner.err { background: #fef2f2; border: 1px solid #fca5a5; color: #b91c1c; }
</style>
