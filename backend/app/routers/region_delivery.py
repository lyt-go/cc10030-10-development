"""区域合作方授权交付专用接口。

与通用资源路由并列：通用 CRUD/动作仍走 resource.py，授权登记、请求、发包、
审校与可领取范围等领域动作在本路由按显式契约暴露。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.region_delivery import RegionDeliveryService

router = APIRouter(prefix="/api/region-delivery", tags=["区域授权交付"])
service = RegionDeliveryService()


class SubzonePayload(BaseModel):
    subzone_code: str
    subzone_name: str
    polygon: list[list[float]] = Field(..., description="多边形顶点 [[纬度, 经度], ...]")
    parent_region: str = ""


class GrantPayload(BaseModel):
    partner_id: str
    subzone_codes: list[str]
    note: str = ""


class BatchPointsPayload(BaseModel):
    batch_key: str
    points: list[dict[str, Any]]
    precision: int = 6


class RequestPayload(BaseModel):
    request_id: str
    partner_id: str
    batch_key: str
    requested_subzones: list[str]
    windows: list[str] = Field(default_factory=list)


class ReviewPayload(BaseModel):
    viewer_id: str
    full_grid: bool = False


class ScopePayload(BaseModel):
    partner_id: str
    windows: list[str]


def _guard(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        status = 404 if isinstance(exc, (KeyError, FileNotFoundError)) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.post("/subzones")
def register_subzone(payload: SubzonePayload) -> dict[str, Any]:
    return _guard(service.register_subzone, payload.subzone_code, payload.subzone_name,
                  payload.polygon, payload.parent_region)


@router.get("/subzones")
def list_subzones() -> list[dict[str, Any]]:
    return service.list_subzones()


@router.post("/authorizations/grant")
def grant_authorization(payload: GrantPayload) -> dict[str, Any]:
    return _guard(service.grant_authorization, payload.partner_id, payload.subzone_codes, payload.note)


@router.post("/authorizations/revoke")
def revoke_authorization(payload: GrantPayload) -> dict[str, Any]:
    return _guard(service.revoke_authorization, payload.partner_id, payload.subzone_codes, payload.note)


@router.get("/authorizations/{party_id}")
def effective_authorization(party_id: str) -> dict[str, Any]:
    return {"party_id": party_id, "authorized_subzones": service.effective_authorization(party_id)}


@router.post("/batch-points")
def write_batch_points(payload: BatchPointsPayload) -> dict[str, Any]:
    path = service.write_batch_points(payload.batch_key, payload.points)
    stats = service.compute_grid_subzone_stats(payload.batch_key, payload.precision)
    return {"points_file": path, "grid_subzone_stats": stats}


@router.get("/grid-subzone-stats/{batch_key}")
def grid_subzone_stats(batch_key: str) -> list[dict[str, Any]]:
    return _guard(service.get_grid_subzone_stats, batch_key)


@router.get("/cross-boundary-contribution/{batch_key}")
def cross_boundary_contribution(batch_key: str) -> list[dict[str, Any]]:
    return _guard(service.verify_cross_boundary_contribution, batch_key)


@router.post("/requests")
def create_request(payload: RequestPayload) -> dict[str, Any]:
    return _guard(service.create_delivery_request, payload.request_id, payload.partner_id,
                  payload.batch_key, payload.requested_subzones, payload.windows)


@router.post("/requests/{request_id}/deliver")
def deliver(request_id: str) -> dict[str, Any]:
    return _guard(service.deliver_request, request_id)


@router.get("/requests/{request_id}/package")
def get_package(request_id: str) -> dict[str, Any]:
    return _guard(service.get_package, request_id)


@router.post("/requests/{request_id}/review")
def review(request_id: str, payload: ReviewPayload) -> dict[str, Any]:
    return _guard(service.review_package, request_id, payload.viewer_id, payload.full_grid)


@router.get("/requests/{request_id}/internal-review")
def internal_review(request_id: str) -> dict[str, Any]:
    return _guard(service.internal_review_package, request_id)


@router.post("/claimable-scope")
def claimable_scope(payload: ScopePayload) -> dict[str, Any]:
    return service.get_claimable_scope(payload.partner_id, payload.windows)


@router.post("/reclaimable-scope")
def reclaimable_scope(payload: ScopePayload) -> dict[str, Any]:
    return service.get_reclaimable_scope(payload.partner_id, payload.windows)


@router.get("/requests/{request_id}/anonymity-check")
def anonymity_check(request_id: str) -> dict[str, Any]:
    return _guard(service.assert_package_is_anonymous, request_id)
