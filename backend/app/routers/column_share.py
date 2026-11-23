"""跨目录受控列共享接口：授权、撤回、核对、封存、领用和双范围查询。

与通用资源路由分开注册，所有写操作返回 {ok, message, ...}；
业务拒绝同样以 200 + ok=False 表达，便于前端按统一契约处理。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.column_share import column_share_service as service

router = APIRouter(prefix="/api/column-share", tags=["受控列共享"])


class GrantPayload(BaseModel):
    source_catalog: str
    target_catalog: str
    table_name: str
    auditor: str
    purpose: str
    granted_columns: list[str] = Field(default_factory=list)
    approver: str
    recipient_group: str


class RevokePayload(BaseModel):
    column: str
    operator: str


class ReconcilePayload(BaseModel):
    requested_columns: list[str] | None = None
    operator: str = ""


class SealPayload(BaseModel):
    signer: str


class ClaimPayload(BaseModel):
    recipient_group: str
    purpose: str


class CoveragePayload(BaseModel):
    reconcile_id: int


@router.get("/catalogs/trace")
def trace_catalogs(table_name: str) -> dict[str, Any]:
    return service.trace_catalogs(table_name)


@router.post("/grants")
def create_grant(payload: GrantPayload) -> dict[str, Any]:
    return service.create_grant(payload.model_dump())


@router.get("/grants")
def list_grants() -> dict[str, Any]:
    items = service.list_grants()
    return {"ok": True, "items": items, "total": len(items)}


@router.get("/grants/{grant_id}/readable-columns")
def readable_columns(grant_id: int) -> dict[str, Any]:
    return service.readable_columns(grant_id)


@router.post("/grants/{grant_id}/revoke")
def revoke_column(grant_id: int, payload: RevokePayload) -> dict[str, Any]:
    return service.revoke_column(grant_id, payload.column, payload.operator)


@router.post("/grants/{grant_id}/reconcile")
def run_reconcile(grant_id: int, payload: ReconcilePayload) -> dict[str, Any]:
    return service.run_reconcile(grant_id, payload.requested_columns, payload.operator)


@router.post("/reconciles/{reconcile_id}/seal")
def seal_report(reconcile_id: int, payload: SealPayload) -> dict[str, Any]:
    return service.seal_report(reconcile_id, payload.signer)


@router.get("/reconciles")
def list_reconciles() -> dict[str, Any]:
    items = service.list_reconciles()
    return {"ok": True, "items": items, "total": len(items)}


@router.post("/reports/{report_id}/claim")
def claim_report(report_id: int, payload: ClaimPayload) -> dict[str, Any]:
    return service.claim_report(report_id, payload.recipient_group, payload.purpose)


@router.get("/reports")
def list_reports() -> dict[str, Any]:
    items = service.list_reports()
    return {"ok": True, "items": items, "total": len(items)}


@router.post("/reports/{report_id}/coverage")
def delivery_coverage(report_id: int, payload: CoveragePayload) -> dict[str, Any]:
    return service.delivery_coverage(report_id, payload.reconcile_id)


@router.get("/approvers/{approver}/scopes")
def approver_scopes(approver: str, table_name: str | None = None) -> dict[str, Any]:
    return service.approver_scopes(approver, table_name)


@router.get("/claims")
def list_claims() -> dict[str, Any]:
    items = service.list_claims()
    return {"ok": True, "items": items, "total": len(items)}
