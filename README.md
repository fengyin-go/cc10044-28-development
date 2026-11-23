# 湖仓一体任务编排与数据治理平台

面向数据湖和数据仓库的ETL任务编排、分区管理、数据血缘和质量治理的工程平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/routers/column_share.py  跨目录受控列共享接口（授权/撤回/核对/封存/领用）
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/column_share.py  受控列共享：授权时快照、无原值差异摘要、封存领用鉴权
│   ├── app/services/base.py     共享服务骨架
│   ├── app/store.py          内存数据仓库与示例数据
│   ├── tests/test_column_share.py  受控列共享 20 条领域规则测试
│   └── scripts/column_share_demo.py  授权→核对→封存→撤回→再核对→领用全链路回放
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8018/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5198/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8018`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| ETL流水线 | `etl_pipeline` | 流水线 | 流水线名称、源端类型、目标端类型 |
| 分区管理 | `partition_manager` | 分区配置 | 配置名称、目标表、分区字段 |
| 数据质量规则 | `data_quality_rule` | 质量规则 | 规则名称、校验维度、目标表 |
| Schema变更管理 | `schema_change` | 变更记录 | 目标表、变更类型、变更内容 |
| 数据血缘 | `lineage_graph` | 血缘关系 | 目标表、来源表列表、转换逻辑 |
| 补数任务 | `backfill_task` | 补数任务 | 目标表、补数时间范围、补数状态 |
| 表归档管理 | `table_archive` | 归档策略 | 策略名称、目标表、归档条件 |
| 聚合表构建器 | `agg_builder` | 聚合配置 | 配置名称、源事实表、聚合维度 |
| 流水线监控 | `pipeline_monitor` | 监控记录 | 流水线名称、运行时间、执行状态 |
| 冷数据恢复 | `cold_restore` | 恢复任务 | 归档来源、恢复目标路径、恢复时间范围 |
| 维度表生成器 | `dimension_builder` | 维度配置 | 配置名称、来源表、维度键 |
| 数据保留策略 | `data_retention` | 保留规则 | 规则名称、目标数据集、保留天数 |
| 转换方案 | `transform_plan` | 转换步骤 | 方案名称、步骤序列、输入表 |
| 数据迁移任务 | `data_migration` | 迁移任务 | 任务名称、源存储、目标存储 |
| 增量合并引擎 | `delta_merge` | 合并任务 | 目标表、增量来源、合并键 |
| 快照生成器 | `snapshot_creator` | 快照任务 | 目标数据集、快照时间点、快照类型 |
| 湖仓审计 | `data_lake_audit` | 审计报告 | 审计范围、审计时间、表总数 |
| SLA策略 | `sla_policy` | SLA规则 | 规则名称、关联流水线、产出截止时间 |
| 清理作业 | `cleanup_job` | 清理作业 | 作业名称、目标路径、清理条件 |
| 目录同步 | `catalog_sync` | 同步任务 | 任务名称、源Catalog、目标Catalog |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 跨目录受控列共享（湖仓审计）

湖仓审计新增列级跨目录共享能力，代码在 `app/services/column_share.py` 与
`app/routers/column_share.py`，前端入口为「受控列共享」导航与湖仓审计页的
「跨目录受控列共享」按钮。

- 授权单按来源目录、目标目录、同名表、审计人、用途、收件组唯一区分，
  来源/目标目录必须沿 `catalog_sync` 同步登记追溯，不接受未登记的跨目录关系。
- 核对在开始时快照授权单**当时可读列**；从未授权的列禁止核对，已撤回的列
  在新核对中不可读、不会被读取。
- 封存摘要与领用交付只含已核验字段范围和每列差异数量（演示数据两列分别为
  8 和 3），不含原值、不含差异行标识，无法反推单行内容。
- 领用按封存时冻结的收件组快照与用途鉴权：封存时未获授权的收件人，即使
  后来拿到新授权也不能领用旧报告。
- `GET /api/column-share/approvers/{审批人}/scopes` 对照同一审批人的两个范围：
  封存报告冻结范围 vs 授权单当前可读列；
  `POST /api/column-share/reports/{id}/coverage` 对照交付覆盖列与新核对实际可读列。

验证：

```bash
cd backend
.venv/bin/python -m unittest discover -s tests     # 20 条领域规则
.venv/bin/python scripts/column_share_demo.py      # 全链路场景回放
```

领域：数据工程
子方向：数仓与数据湖
