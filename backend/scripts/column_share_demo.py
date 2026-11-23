"""受控列共享场景回放：目录方授权 → 核对 → 审签封存 → 撤回一列 → 再核对与领用。

运行（在 backend/ 下）：
    .venv/bin/python scripts/column_share_demo.py
脚本只使用内存示例数据，不依赖外部服务；输出全程不含任何原始列值。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.column_share import column_share_service as svc  # noqa: E402

TABLE = "dws_sales_daily"
APPROVER = "王敏"          # 目录方审批人
AUDITOR = "陈审"           # 获准审计人
QUALITY_GROUP = "质量组"   # 获准收件人
OTHER_GROUP = "营销分析组"  # 从未在该用途获授权的收件人
PURPOSE = "月度跨目录对账"


def show(title: str, result: dict) -> None:
    print(f"\n===== {title} =====")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    # 1. 湖仓审计沿目录同步登记找来源和目标。
    trace = svc.trace_catalogs(TABLE)
    show("1. 沿目录同步登记追溯来源/目标目录", trace)
    assert trace["ok"]

    # 2. 目录方给质量组开放两列做对账。
    grant = svc.create_grant({
        "source_catalog": trace["source_catalog"],
        "target_catalog": trace["target_catalog"],
        "table_name": TABLE,
        "auditor": AUDITOR,
        "purpose": PURPOSE,
        "granted_columns": ["settle_amount", "risk_level"],
        "approver": APPROVER,
        "recipient_group": QUALITY_GROUP,
    })
    show("2. 登记列共享授权单（两列、质量组、对账用途）", grant)
    assert grant["ok"]
    grant_id = grant["grant"]["id"]

    # 未授权列不能进入核对范围。
    denied = svc.run_reconcile(grant_id, ["promotion_id"], operator=AUDITOR)
    show("2b. 尝试核对从未授权的 promotion_id 列（应拒绝）", denied)
    assert not denied["ok"]

    # 3. 新的核对只能读取当时授权的列；差异数分别为 8 和 3，且无原值。
    first = svc.run_reconcile(grant_id, operator=AUDITOR)
    show("3. 第一次核对：仅读授权列，差异数 8 与 3，无原值", first)
    assert first["ok"]
    assert first["reconcile"]["列差异摘要"] == [
        {"column": "settle_amount", "status": "已核验", "diff_count": 8},
        {"column": "risk_level", "status": "已核验", "diff_count": 3},
    ]
    reconcile_id = first["reconcile"]["id"]

    # 4. 审计人审签并封存无原值摘要。
    sealed = svc.seal_report(reconcile_id, signer=AUDITOR)
    show("4. 审签封存无原值差异摘要", sealed)
    assert sealed["ok"]
    report_id = sealed["report"]["id"]

    # 5. 目录方撤回贡献为 3 的那一列（risk_level）。
    revoked = svc.revoke_column(grant_id, "risk_level", operator=APPROVER)
    show("5. 目录方撤回 risk_level 列", revoked)
    assert revoked["ok"]
    assert revoked["grant"]["可读列"] == ["settle_amount"]

    # 6. 同一审批人的两个范围：封存报告范围 vs 当前还能读的列。
    scopes = svc.approver_scopes(APPROVER, TABLE)
    show("6. 审批人视角的双范围（封存范围 / 当前可读范围）", scopes)
    assert scopes["ok"]
    assert scopes["封存报告范围"][0]["封存时已核验列"] == ["settle_amount", "risk_level"]
    assert scopes["当前可读范围"][0]["当前可读列"] == ["settle_amount"]

    # 7. 质量组按原用途领用报告；无权限收件人不能领用旧报告。
    claim_ok = svc.claim_report(report_id, QUALITY_GROUP, PURPOSE)
    show("7. 质量组按原用途领用封存摘要", claim_ok)
    assert claim_ok["ok"]
    claim_bad = svc.claim_report(report_id, OTHER_GROUP, PURPOSE)
    show("7b. 从未授权的营销分析组尝试领用旧报告（应拒绝）", claim_bad)
    assert not claim_bad["ok"]
    claim_bad_purpose = svc.claim_report(report_id, QUALITY_GROUP, "临时营销分析")
    show("7c. 质量组改用其他用途领用（应拒绝）", claim_bad_purpose)
    assert not claim_bad_purpose["ok"]

    # 8. 质量组再发起一次核对：新核对实际能读的列可查。
    second = svc.run_reconcile(grant_id, operator=AUDITOR)
    show("8. 撤回后再次核对：risk_level 不可读，仅 settle_amount 出差异", second)
    assert second["ok"]
    assert second["reconcile"]["本次已核验列"] == ["settle_amount"]
    assert second["reconcile"]["本次不可读列"] == ["risk_level"]
    second_id = second["reconcile"]["id"]

    # 9. 查清本次交付覆盖哪些列和差异数，以及新核对实际读取的列。
    coverage = svc.delivery_coverage(report_id, second_id)
    show("9. 交付覆盖与新核对可读列总览", coverage)
    assert coverage["ok"]
    assert coverage["交付覆盖列"] == ["settle_amount", "risk_level"]
    assert coverage["交付列差异数"] == {"settle_amount": 8, "risk_level": 3}
    assert coverage["新核对实际已核验列"] == ["settle_amount"]
    assert coverage["新核对不可读列"] == ["risk_level"]

    # 10. 全链路安全检查：任何落库记录都不含原始列值或差异行标识。
    blob = json.dumps({
        "grants": svc.list_grants(),
        "reconciles": svc.list_reconciles(),
        "reports": svc.list_reports(),
        "claims": svc.list_claims(),
    }, ensure_ascii=False)
    forbidden = ["source_value", "target_value", "row_id", "diff_rows", "sample"]
    assert not any(word in blob for word in forbidden), "差异摘要中混入了原值或行标识"
    assert all(r["仅含差异计数"] for r in svc.list_reconciles())
    assert all(r["仅含差异计数"] for r in svc.list_reports())
    print("\n场景回放全部断言通过：授权受控、撤回生效、封存无原值、领用按冻结快照鉴权。")


if __name__ == "__main__":
    main()
