"""受控列共享领域规则测试：授权区分度、授权时快照、无原值、封存与领用鉴权。"""
from __future__ import annotations

import json
import unittest

from app.main import app
from app.services.column_share import column_share_service as svc
from app.store import store

TABLE = "dws_sales_daily"
APPROVER = "王敏"
AUDITOR = "陈审"
QUALITY_GROUP = "质量组"
OTHER_GROUP = "营销分析组"
PURPOSE = "月度跨目录对账"

SHARE_TABLES = (
    svc.GRANT_TABLE,
    svc.RECONCILE_TABLE,
    svc.REPORT_TABLE,
    svc.CLAIM_TABLE,
)


class ColumnShareTestBase(unittest.TestCase):
    def setUp(self) -> None:
        for table in SHARE_TABLES:
            store.rows(table).clear()
        self.trace = svc.trace_catalogs(TABLE)
        self.assertTrue(self.trace["ok"])

    def _create_grant(self, columns=None, auditor=AUDITOR, purpose=PURPOSE,
                      recipient=QUALITY_GROUP, approver=APPROVER):
        return svc.create_grant({
            "source_catalog": self.trace["source_catalog"],
            "target_catalog": self.trace["target_catalog"],
            "table_name": TABLE,
            "auditor": auditor,
            "purpose": purpose,
            "granted_columns": ["settle_amount", "risk_level"] if columns is None else columns,
            "approver": approver,
            "recipient_group": recipient,
        })


class TraceAndGrantTests(ColumnShareTestBase):
    def test_trace_follows_catalog_sync_registration(self) -> None:
        self.assertEqual(self.trace["source_catalog"], "hive_metastore_prod")
        self.assertEqual(self.trace["target_catalog"], "aws_glue_catalog")
        self.assertTrue(self.trace["registrations"])

    def test_trace_unknown_table_fails(self) -> None:
        result = svc.trace_catalogs("not_registered_table_xyz")
        self.assertFalse(result["ok"])

    def test_grant_rejects_catalog_mismatch(self) -> None:
        result = svc.create_grant({
            "source_catalog": "iceberg_catalog",
            "target_catalog": "unity_catalog_main",
            "table_name": TABLE,
            "auditor": AUDITOR, "purpose": PURPOSE,
            "granted_columns": ["settle_amount"],
            "approver": APPROVER, "recipient_group": QUALITY_GROUP,
        })
        self.assertFalse(result["ok"])

    def test_grant_rejects_unknown_and_duplicate_and_empty(self) -> None:
        bad_column = self._create_grant(columns=["settle_amount", "ghost_col"])
        self.assertFalse(bad_column["ok"])
        empty = self._create_grant(columns=[])
        self.assertFalse(empty["ok"])
        first = self._create_grant()
        self.assertTrue(first["ok"])
        duplicate = self._create_grant()
        self.assertFalse(duplicate["ok"])

    def test_grant_identity_distinguishes_auditor_purpose_and_columns(self) -> None:
        # 同表同目录但审计人/用途不同，应视为不同授权单而非重复。
        first = self._create_grant(auditor=AUDITOR, purpose=PURPOSE)
        second = self._create_grant(auditor="李审", purpose=PURPOSE)
        third = self._create_grant(auditor=AUDITOR, purpose="季度合规复核",
                                   columns=["settle_amount"])
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(third["ok"])
        self.assertEqual(len(svc.list_grants()), 3)
        # 同名表的授权单完整保留来源目录、审计人、用途和列四个维度。
        grant = first["grant"]
        self.assertEqual((grant["来源目录"], grant["审计人"], grant["用途"], grant["授权列"]),
                         ("hive_metastore_prod", AUDITOR, PURPOSE,
                          ["settle_amount", "risk_level"]))


class ReconcileTests(ColumnShareTestBase):
    def test_reconcile_only_reads_authorized_columns_with_counts_8_and_3(self) -> None:
        grant = self._create_grant()["grant"]
        result = svc.run_reconcile(grant["id"], operator=AUDITOR)
        self.assertTrue(result["ok"])
        reconcile = result["reconcile"]
        self.assertEqual(reconcile["本次已核验列"], ["settle_amount", "risk_level"])
        self.assertEqual(reconcile["列差异摘要"], [
            {"column": "settle_amount", "status": "已核验", "diff_count": 8},
            {"column": "risk_level", "status": "已核验", "diff_count": 3},
        ])
        self.assertEqual(reconcile["总差异数"], 11)

    def test_never_authorized_column_cannot_be_read(self) -> None:
        grant = self._create_grant()["grant"]
        result = svc.run_reconcile(grant["id"], ["promotion_id"], operator=AUDITOR)
        self.assertFalse(result["ok"])
        self.assertEqual(svc.list_reconciles(), [])

    def test_revoked_column_unreadable_in_new_reconcile(self) -> None:
        grant = self._create_grant()["grant"]
        first = svc.run_reconcile(grant["id"], operator=AUDITOR)["reconcile"]
        self.assertEqual(first["总差异数"], 11)

        revoke = svc.revoke_column(grant["id"], "risk_level", APPROVER)
        self.assertTrue(revoke["ok"])
        self.assertEqual(svc.readable_columns(grant["id"])["当前可读列"], ["settle_amount"])

        second = svc.run_reconcile(grant["id"], operator=AUDITOR)["reconcile"]
        self.assertEqual(second["本次已核验列"], ["settle_amount"])
        self.assertEqual(second["本次不可读列"], ["risk_level"])
        risk = next(s for s in second["列差异摘要"] if s["column"] == "risk_level")
        self.assertIsNone(risk["diff_count"])
        self.assertEqual(second["总差异数"], 8)

    def test_reconcile_can_request_subset_of_granted_columns(self) -> None:
        grant = self._create_grant()["grant"]
        result = svc.run_reconcile(grant["id"], ["risk_level"], operator=AUDITOR)
        self.assertTrue(result["ok"])
        reconcile = result["reconcile"]
        self.assertEqual(reconcile["请求列"], ["risk_level"])
        self.assertEqual(reconcile["本次已核验列"], ["risk_level"])
        self.assertEqual(reconcile["总差异数"], 3)
        # 快照仍保留授权单当时全部可读列，便于重查时核对实际授权范围。
        self.assertEqual(reconcile["核对时可读列"], ["settle_amount", "risk_level"])

    def test_no_raw_values_are_persisted(self) -> None:
        grant = self._create_grant()["grant"]
        svc.run_reconcile(grant["id"], operator=AUDITOR)
        blob = json.dumps(svc.list_reconciles(), ensure_ascii=False)
        for token in ("source_value", "target_value", "row_id", "diff_rows", "sample"):
            self.assertNotIn(token, blob)
        self.assertTrue(all(row["仅含差异计数"] for row in svc.list_reconciles()))


class SealAndClaimTests(ColumnShareTestBase):
    def _sealed_report(self):
        grant = self._create_grant()["grant"]
        reconcile = svc.run_reconcile(grant["id"], operator=AUDITOR)["reconcile"]
        report = svc.seal_report(reconcile["id"], signer=AUDITOR)["report"]
        return grant, reconcile, report

    def test_seal_freezes_authorized_snapshot_without_values(self) -> None:
        _, _, report = self._sealed_report()
        self.assertEqual(report["已核验字段范围"], ["settle_amount", "risk_level"])
        self.assertEqual(report["列差异数"], {"settle_amount": 8, "risk_level": 3})
        self.assertTrue(report["仅含差异计数"])

    def test_double_seal_rejected(self) -> None:
        _, reconcile, _ = self._sealed_report()
        again = svc.seal_report(reconcile["id"], signer=AUDITOR)
        self.assertFalse(again["ok"])

    def test_authorized_recipient_claims_for_original_purpose(self) -> None:
        _, _, report = self._sealed_report()
        result = svc.claim_report(report["id"], QUALITY_GROUP, PURPOSE)
        self.assertTrue(result["ok"])
        self.assertEqual(result["delivery"]["已核验字段范围"],
                         ["settle_amount", "risk_level"])
        self.assertEqual(result["delivery"]["列差异数"],
                         {"settle_amount": 8, "risk_level": 3})
        self.assertTrue(result["delivery"]["仅含差异计数"])

    def test_recipient_without_prior_permission_cannot_claim_old_report(self) -> None:
        _, _, report = self._sealed_report()
        # 即使授权单后来仍有效，从未在该用途获准的收件人也不能领用旧报告。
        result = svc.claim_report(report["id"], OTHER_GROUP, PURPOSE)
        self.assertFalse(result["ok"])
        self.assertEqual(svc.list_claims(), [])

    def test_wrong_purpose_claim_rejected(self) -> None:
        _, _, report = self._sealed_report()
        result = svc.claim_report(report["id"], QUALITY_GROUP, "其他用途")
        self.assertFalse(result["ok"])

    def test_later_authorized_recipient_still_cannot_claim_old_report(self) -> None:
        # 旧报告封存后，另一收件组即使后来拿到同表的新授权，也不能领用封存前的报告。
        _, _, report = self._sealed_report()
        later = self._create_grant(recipient=OTHER_GROUP, purpose=PURPOSE,
                                   columns=["settle_amount"])
        self.assertTrue(later["ok"])
        result = svc.claim_report(report["id"], OTHER_GROUP, PURPOSE)
        self.assertFalse(result["ok"])
        self.assertEqual(svc.list_claims(), [])

    def test_revocation_does_not_change_sealed_report_snapshot(self) -> None:
        grant, _, report = self._sealed_report()
        svc.revoke_column(grant["id"], "risk_level", APPROVER)
        # 封存报告里冻结的历史范围仍包含被撤回的列与差异数 3。
        self.assertEqual(report["封存时授权列快照"], ["settle_amount", "risk_level"])
        self.assertEqual(report["列差异数"]["risk_level"], 3)
        claim = svc.claim_report(report["id"], QUALITY_GROUP, PURPOSE)
        self.assertTrue(claim["ok"])
        self.assertEqual(claim["delivery"]["列差异数"]["risk_level"], 3)


class ScopeAndCoverageTests(ColumnShareTestBase):
    def test_approver_sees_sealed_and_current_scopes(self) -> None:
        grant = self._create_grant()["grant"]
        reconcile = svc.run_reconcile(grant["id"], operator=AUDITOR)["reconcile"]
        svc.seal_report(reconcile["id"], signer=AUDITOR)
        svc.revoke_column(grant["id"], "risk_level", APPROVER)

        scopes = svc.approver_scopes(APPROVER, TABLE)
        self.assertTrue(scopes["ok"])
        sealed = scopes["封存报告范围"][0]
        current = scopes["当前可读范围"][0]
        self.assertEqual(sealed["封存时已核验列"], ["settle_amount", "risk_level"])
        self.assertEqual(sealed["列差异数"], {"settle_amount": 8, "risk_level": 3})
        self.assertEqual(current["当前可读列"], ["settle_amount"])
        self.assertEqual(current["已撤回列"], ["risk_level"])

    def test_delivery_coverage_and_new_reconcile_readable_columns(self) -> None:
        grant = self._create_grant()["grant"]
        first = svc.run_reconcile(grant["id"], operator=AUDITOR)["reconcile"]
        report = svc.seal_report(first["id"], signer=AUDITOR)["report"]
        svc.revoke_column(grant["id"], "risk_level", APPROVER)
        second = svc.run_reconcile(grant["id"], operator=AUDITOR)["reconcile"]

        coverage = svc.delivery_coverage(report["id"], second["id"])
        self.assertTrue(coverage["ok"])
        self.assertEqual(coverage["交付覆盖列"], ["settle_amount", "risk_level"])
        self.assertEqual(coverage["交付列差异数"], {"settle_amount": 8, "risk_level": 3})
        self.assertEqual(coverage["交付总差异数"], 11)
        self.assertEqual(coverage["新核对实际已核验列"], ["settle_amount"])
        self.assertEqual(coverage["新核对不可读列"], ["risk_level"])
        self.assertEqual(coverage["新核对列差异数"], {"settle_amount": 8})


class RouterRegistrationTests(unittest.TestCase):
    def test_column_share_routes_registered_before_resource_router(self) -> None:
        # 新版 Starlette 把 include_router 折叠为 _IncludedRouter，OpenAPI 会摊平全部路径。
        paths = set(app.openapi()["paths"].keys())
        for expected in (
            "/api/column-share/grants",
            "/api/column-share/grants/{grant_id}/revoke",
            "/api/column-share/grants/{grant_id}/reconcile",
            "/api/column-share/reconciles/{reconcile_id}/seal",
            "/api/column-share/reports/{report_id}/claim",
            "/api/column-share/reports/{report_id}/coverage",
            "/api/column-share/approvers/{approver}/scopes",
            "/api/column-share/catalogs/trace",
        ):
            self.assertIn(expected, paths)
        # 固定路径必须先于动态资源路由注册，避免被 /api/{module} 抢占。
        from app.routers import ROUTERS
        self.assertEqual(ROUTERS[0].router.prefix, "/api/column-share")


if __name__ == "__main__":
    unittest.main()
