"""跨目录受控列共享领域服务。

数据链路位置：数据审计层与元数据同步层之间。目录方（源 Catalog）通过授权单
把同名表的指定列共享给读取目录方，审计人只能在授权当时有效的列范围内发起
核对；核对结果只沉淀「无法反推单行内容」的差异数量，全程不落任何原始值。

领域不变量：
1. 授权单按「来源目录 + 目标目录 + 同名表 + 审计人 + 用途 + 收件组」唯一区分，
   列级授权可单独撤回，撤回不影响封存报告里冻结的历史范围。
2. 每次核对在开始时快照当时可读列；撤回列在之后的核对中不可读、不会被读取。
3. 封存摘要只保存已核验字段范围与每列差异计数，不含原值、不含差异行标识。
4. 领用按封存时冻结的授权快照鉴权：从未在该用途上获准的收件人不能领用旧报告。
5. 来源/目标目录沿 catalog_sync 同步登记追溯，不接受未登记的跨目录关系。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.store import store

# 演示用受控数据：仅核对引擎内部读取，任何接口输出与落库记录都不得带出原值。
# 100 行对齐主键，settle_amount 有 8 处差异、risk_level 有 3 处差异；
# promotion_id 未授权，用于证明未授权列不会被读取。
_TABLE_NAME = "dws_sales_daily"
_COMPARED_ROWS = 100
_SETTLE_DIFF_POSITIONS = (3, 17, 28, 41, 55, 63, 79, 92)   # 共 8 处
_RISK_DIFF_POSITIONS = (12, 47, 88)                        # 共 3 处
_FIXTURE_COLUMNS = ("settle_amount", "risk_level", "promotion_id")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ColumnShareService:
    """受控列共享、授权范围内核对、封存与领用的全部领域逻辑。"""

    GRANT_TABLE = "column_share_grant"
    RECONCILE_TABLE = "column_share_reconcile"
    REPORT_TABLE = "column_share_report"
    CLAIM_TABLE = "column_share_claim"

    # ------------------------------------------------------------------ 基础
    def _next_id(self, table: str) -> int:
        return max((int(row.get("id", 0)) for row in store.rows(table)), default=0) + 1

    @staticmethod
    def _fail(message: str, **extra: Any) -> dict[str, Any]:
        return {"ok": False, "message": message, **extra}

    def _find(self, table: str, entry_id: int) -> dict[str, Any] | None:
        return store.find(table, entry_id)

    # ---------------------------------------------------- 受控数据（内部）
    def _fixture_columns(self) -> list[str]:
        return list(_FIXTURE_COLUMNS)

    def _row_count(self) -> int:
        return _COMPARED_ROWS

    def _column_diff_count(self, column: str) -> int:
        """返回某列源/目标两侧的差异行数；只回计数，不回任何原值或行号。"""
        if column == "settle_amount":
            return len(_SETTLE_DIFF_POSITIONS)
        if column == "risk_level":
            return len(_RISK_DIFF_POSITIONS)
        if column == "promotion_id":
            return 0
        # 未登记列不允许读取，调用方应先做授权校验。
        raise KeyError(column)

    # ------------------------------------------------ 目录同步追溯
    def trace_catalogs(self, table_name: str) -> dict[str, Any]:
        """沿目录同步登记查找同名表的来源目录与目标目录。"""
        registrations: list[dict[str, Any]] = []
        for task in store.rows("catalog_sync"):
            scope = str(task.get("同步范围", ""))
            scope_parts = [piece.strip() for piece in scope.split(",") if piece.strip()]
            if scope == "全部":
                continue  # 全量同步不具名，不能为任意表的跨目录关系背书
            hit = any(
                part.rstrip("*") in table_name if part.endswith("*") else part == table_name
                for part in scope_parts
            )
            if hit:
                registrations.append(task)
        if not registrations:
            return self._fail(
                f"表 {table_name} 没有对应的目录同步登记，无法确定来源目录和目标目录",
                registrations=[],
            )
        normalized = [{
            "catalog_sync_id": task.get("id"),
            "source_catalog": task.get("源Catalog"),
            "target_catalog": task.get("目标Catalog"),
            "sync_scope": str(task.get("同步范围", "")),
            "sync_status": task.get("同步状态") or task.get("status"),
        } for task in registrations]
        return {
            "ok": True,
            "table_name": table_name,
            "registrations": normalized,
            "source_catalog": normalized[0]["source_catalog"],
            "target_catalog": normalized[0]["target_catalog"],
        }

    # ------------------------------------------------ 授权单
    def create_grant(self, payload: dict[str, Any]) -> dict[str, Any]:
        """目录方登记一份列共享授权单。"""
        required = ["source_catalog", "target_catalog", "table_name", "auditor",
                    "purpose", "granted_columns", "approver", "recipient_group"]
        missing = [name for name in required if not str(payload.get(name) or "").strip()]
        if missing:
            return self._fail(f"缺少必填字段：{'、'.join(missing)}")
        columns = [c.strip() for c in payload["granted_columns"] if str(c).strip()]
        if not columns:
            return self._fail("授权列不能为空")
        if len(columns) != len(set(columns)):
            return self._fail("授权列存在重复项")

        unknown = [c for c in columns if c not in self._fixture_columns()]
        if unknown:
            return self._fail(f"列 {('、'.join(unknown))} 不在受控表可读字段内")

        trace = self.trace_catalogs(payload["table_name"])
        if not trace["ok"]:
            return trace
        if payload["source_catalog"] != trace["source_catalog"] \
                or payload["target_catalog"] != trace["target_catalog"]:
            return self._fail(
                "来源/目标目录与目录同步登记不一致，授权单必须沿同步登记建立",
                expected_source=trace["source_catalog"],
                expected_target=trace["target_catalog"],
            )

        duplicate = next((g for g in store.rows(self.GRANT_TABLE)
                          if g["来源目录"] == payload["source_catalog"]
                          and g["目标目录"] == payload["target_catalog"]
                          and g["同名表"] == payload["table_name"]
                          and g["审计人"] == payload["auditor"]
                          and g["用途"] == payload["purpose"]
                          and g["收件组"] == payload["recipient_group"]
                          and g["status"] == "生效中"), None)
        if duplicate is not None:
            return self._fail(
                "同一来源目录/目标目录/同名表/审计人/用途/收件组已存在生效授权单",
                grant_id=duplicate["id"])

        grant_id = self._next_id(self.GRANT_TABLE)
        entry = {
            "id": grant_id,
            "status": "生效中",
            "pending": False,
            "abnormal": False,
            "授权编号": f"CSG-{grant_id:04d}",
            "来源目录": payload["source_catalog"],
            "目标目录": payload["target_catalog"],
            "同名表": payload["table_name"],
            "审计人": payload["auditor"],
            "用途": payload["purpose"],
            "授权列": columns,
            "可读列": list(columns),
            "已撤回列": [],
            "收件组": payload["recipient_group"],
            "审批人": payload["approver"],
            "授权时间": _now(),
            "撤回记录": [],
        }
        store.rows(self.GRANT_TABLE).append(entry)
        return {"ok": True, "message": "列共享授权单已登记", "grant": entry}

    def revoke_column(self, grant_id: int, column: str, operator: str) -> dict[str, Any]:
        """目录方撤回某一列的共享；已封存报告冻结的历史范围不受影响。"""
        grant = self._find(self.GRANT_TABLE, grant_id)
        if grant is None:
            return self._fail(f"授权单 {grant_id} 不存在")
        column = column.strip()
        if column not in grant["授权列"]:
            return self._fail(f"列 {column} 不在授权单范围内")
        if column in grant["已撤回列"]:
            return self._fail(f"列 {column} 已撤回，无需重复操作")
        grant["已撤回列"].append(column)
        grant["可读列"] = [c for c in grant["授权列"] if c not in grant["已撤回列"]]
        grant["撤回记录"].append({
            "column": column, "revoked_by": operator, "revoked_at": _now(),
        })
        if not grant["可读列"]:
            grant["status"] = "已全部撤回"
            grant["abnormal"] = True
        return {"ok": True, "message": f"列 {column} 已撤回，新核对将无法读取该列", "grant": grant}

    def readable_columns(self, grant_id: int) -> dict[str, Any]:
        """查询授权单当前还能实际读取的列。"""
        grant = self._find(self.GRANT_TABLE, grant_id)
        if grant is None:
            return self._fail(f"授权单 {grant_id} 不存在")
        return {
            "ok": True,
            "grant_id": grant_id,
            "授权列": list(grant["授权列"]),
            "已撤回列": list(grant["已撤回列"]),
            "当前可读列": list(grant["可读列"]),
        }

    # ------------------------------------------------ 核对
    def run_reconcile(self, grant_id: int, requested_columns: list[str] | None = None,
                      operator: str = "") -> dict[str, Any]:
        """在授权当时有效的列范围内核对源/目标同名表，产出无原值的差异摘要。"""
        grant = self._find(self.GRANT_TABLE, grant_id)
        if grant is None:
            return self._fail(f"授权单 {grant_id} 不存在")

        requested = [c.strip() for c in (requested_columns or grant["授权列"]) if str(c).strip()]
        out_of_grant = [c for c in requested if c not in grant["授权列"]]
        if out_of_grant:
            return self._fail(f"列 {'、'.join(out_of_grant)} 从未授权，禁止核对",
                              granted_columns=list(grant["授权列"]))

        # 核对开始时快照当时授权可读列：撤回列本次不可读、不会被读取。
        readable_at_run = list(grant["可读列"])
        readable_now = [c for c in requested if c in readable_at_run]
        revoked_in_scope = [c for c in requested if c in grant["已撤回列"]]

        column_summaries: list[dict[str, Any]] = []
        for column in readable_now:
            column_summaries.append({
                "column": column,
                "status": "已核验",
                "diff_count": self._column_diff_count(column),
            })
        for column in revoked_in_scope:
            column_summaries.append({
                "column": column,
                "status": "授权已撤回-本次不可读",
                "diff_count": None,
            })

        reconcile_id = self._next_id(self.RECONCILE_TABLE)
        entry = {
            "id": reconcile_id,
            "status": "已核对",
            "pending": False,
            "abnormal": any(s["diff_count"] for s in column_summaries),
            "核对编号": f"RCN-{reconcile_id:04d}",
            "授权编号": grant["授权编号"],
            "来源目录": grant["来源目录"],
            "目标目录": grant["目标目录"],
            "同名表": grant["同名表"],
            "审计人": operator or grant["审计人"],
            "用途": grant["用途"],
            "请求列": requested,
            "核对时可读列": readable_at_run,
            "本次已核验列": readable_now,
            "本次不可读列": revoked_in_scope,
            "比较行数": self._row_count(),
            "列差异摘要": column_summaries,
            "总差异数": sum(s["diff_count"] or 0 for s in column_summaries),
            "仅含差异计数": True,
            "核对时间": _now(),
        }
        store.rows(self.RECONCILE_TABLE).append(entry)
        return {
            "ok": True,
            "message": "核对完成，仅生成无原值的列级差异数量",
            "reconcile": entry,
        }

    # ------------------------------------------------ 封存
    def seal_report(self, reconcile_id: int, signer: str) -> dict[str, Any]:
        """审计人审签并封存无原值差异摘要，冻结当时授权范围与收件人快照。"""
        reconcile = self._find(self.RECONCILE_TABLE, reconcile_id)
        if reconcile is None:
            return self._fail(f"核对记录 {reconcile_id} 不存在")
        existing = next((r for r in store.rows(self.REPORT_TABLE)
                         if r["来源核对编号"] == reconcile["核对编号"]), None)
        if existing is not None:
            return self._fail("该核对记录已封存，禁止重复封存", report_id=existing["id"])

        grant = next((g for g in store.rows(self.GRANT_TABLE)
                      if g["授权编号"] == reconcile["授权编号"]), None)
        if grant is None:
            return self._fail("授权单缺失，无法冻结授权快照")

        verified = [dict(s) for s in reconcile["列差异摘要"] if s["status"] == "已核验"]
        report_id = self._next_id(self.REPORT_TABLE)
        entry = {
            "id": report_id,
            "status": "已封存",
            "pending": False,
            "abnormal": False,
            "报告编号": f"ADR-{report_id:04d}",
            "来源核对编号": reconcile["核对编号"],
            "授权编号": grant["授权编号"],
            "来源目录": grant["来源目录"],
            "目标目录": grant["目标目录"],
            "同名表": grant["同名表"],
            "用途": grant["用途"],
            "已核验字段范围": [s["column"] for s in verified],
            "列差异数": {s["column"]: s["diff_count"] for s in verified},
            "总差异数": sum(s["diff_count"] or 0 for s in verified),
            "比较行数": reconcile["比较行数"],
            "封存时授权列快照": list(grant["授权列"]),
            "封存时可读列快照": list(grant["可读列"]),
            "封存时已撤回列快照": list(grant["已撤回列"]),
            "授权收件组快照": grant["收件组"],
            "审计人": grant["审计人"],
            "审签人": signer,
            "封存时间": _now(),
            "仅含差异计数": True,
        }
        store.rows(self.REPORT_TABLE).append(entry)
        return {"ok": True, "message": "差异摘要已审签封存", "report": entry}

    # ------------------------------------------------ 领用
    def claim_report(self, report_id: int, recipient_group: str, purpose: str) -> dict[str, Any]:
        """获准收件人按原用途领用封存摘要；按封存时冻结的授权快照鉴权。"""
        report = self._find(self.REPORT_TABLE, report_id)
        if report is None:
            return self._fail(f"封存报告 {report_id} 不存在")
        if purpose != report["用途"]:
            return self._fail("用途与封存登记不一致，禁止领用")
        if recipient_group != report["授权收件组快照"]:
            # 关键不变量：以前没有权限的收件人不能领用旧报告（即使后来拿到别的授权）。
            return self._fail(
                "该收件人在报告封存时未获授权，不能领用旧报告",
                sealed_authorized_group=report["授权收件组快照"],
            )

        claim_id = self._next_id(self.CLAIM_TABLE)
        entry = {
            "id": claim_id,
            "status": "已领用",
            "pending": False,
            "abnormal": False,
            "领用编号": f"CLM-{claim_id:04d}",
            "报告编号": report["报告编号"],
            "收件组": recipient_group,
            "用途": purpose,
            "领用时间": _now(),
        }
        store.rows(self.CLAIM_TABLE).append(entry)
        # 交付内容仅含已核验字段范围与差异数量，结构上不含原值。
        delivery = {
            "报告编号": report["报告编号"],
            "同名表": report["同名表"],
            "来源目录": report["来源目录"],
            "目标目录": report["目标目录"],
            "用途": report["用途"],
            "已核验字段范围": list(report["已核验字段范围"]),
            "列差异数": dict(report["列差异数"]),
            "总差异数": report["总差异数"],
            "比较行数": report["比较行数"],
            "仅含差异计数": True,
        }
        return {"ok": True, "message": "领用成功，交付无原值差异摘要", "claim": entry,
                "delivery": delivery}

    # ------------------------------------------------ 双范围视图
    def approver_scopes(self, approver: str, table_name: str | None = None) -> dict[str, Any]:
        """同一审批人面对的两个范围：封存报告冻结范围 vs 授权单当前可读范围。"""
        grants = [g for g in store.rows(self.GRANT_TABLE)
                  if g["审批人"] == approver and (table_name is None or g["同名表"] == table_name)]
        if not grants:
            return self._fail(f"审批人 {approver} 名下没有相关授权单")

        sealed_scopes: list[dict[str, Any]] = []
        for report in store.rows(self.REPORT_TABLE):
            if report["审签人"] != approver and not any(
                    g["授权编号"] == report["授权编号"] and g["审批人"] == approver
                    for g in grants):
                continue
            sealed_scopes.append({
                "报告编号": report["报告编号"],
                "授权编号": report["授权编号"],
                "同名表": report["同名表"],
                "封存时已核验列": list(report["已核验字段范围"]),
                "封存时授权列快照": list(report["封存时授权列快照"]),
                "封存时可读列快照": list(report["封存时可读列快照"]),
                "封存时已撤回列快照": list(report["封存时已撤回列快照"]),
                "列差异数": dict(report["列差异数"]),
            })

        current_scopes = [{
            "授权编号": g["授权编号"],
            "同名表": g["同名表"],
            "授权列": list(g["授权列"]),
            "当前可读列": list(g["可读列"]),
            "已撤回列": list(g["已撤回列"]),
        } for g in grants]

        return {
            "ok": True,
            "approver": approver,
            "封存报告范围": sealed_scopes,
            "当前可读范围": current_scopes,
        }

    def delivery_coverage(self, report_id: int, reconcile_id: int) -> dict[str, Any]:
        """质量组视角：本次交付覆盖哪些列和差异数，以及新核对实际能读哪些列。"""
        report = self._find(self.REPORT_TABLE, report_id)
        if report is None:
            return self._fail(f"封存报告 {report_id} 不存在")
        reconcile = self._find(self.RECONCILE_TABLE, reconcile_id)
        if reconcile is None:
            return self._fail(f"核对记录 {reconcile_id} 不存在")
        return {
            "ok": True,
            "封存报告": report["报告编号"],
            "交付覆盖列": list(report["已核验字段范围"]),
            "交付列差异数": dict(report["列差异数"]),
            "交付总差异数": report["总差异数"],
            "新核对": reconcile["核对编号"],
            "新核对请求列": list(reconcile["请求列"]),
            "新核对时可读列": list(reconcile["核对时可读列"]),
            "新核对实际已核验列": list(reconcile["本次已核验列"]),
            "新核对不可读列": list(reconcile["本次不可读列"]),
            "新核对列差异数": {
                s["column"]: s["diff_count"]
                for s in reconcile["列差异摘要"] if s["status"] == "已核验"
            },
            "仅含差异计数": True,
        }

    # ------------------------------------------------ 查询
    def list_grants(self) -> list[dict[str, Any]]:
        return list(store.rows(self.GRANT_TABLE))

    def list_reconciles(self) -> list[dict[str, Any]]:
        return list(store.rows(self.RECONCILE_TABLE))

    def list_reports(self) -> list[dict[str, Any]]:
        return list(store.rows(self.REPORT_TABLE))

    def list_claims(self) -> list[dict[str, Any]]:
        return list(store.rows(self.CLAIM_TABLE))


column_share_service = ColumnShareService()
