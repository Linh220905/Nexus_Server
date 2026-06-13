"""
Traffic monitoring API — admin-only endpoints để xem thống kê traffic và IP đáng ngờ.
"""
from fastapi import APIRouter, Depends, Query
from app.api.auth_google import require_admin
from app.database.traffic_log import (
    get_traffic_summary,
    get_traffic_by_ip,
    get_traffic_by_path,
    get_suspicious_requests,
    get_recent_logs,
    get_hourly_chart,
)

router = APIRouter(prefix="/api/traffic", tags=["traffic"])


@router.get("/summary")
async def traffic_summary(
    hours: int = Query(24, ge=1, le=720),
    session: dict = Depends(require_admin),
):
    """Tổng quan traffic: tổng request, unique IPs, suspicious, errors."""
    return get_traffic_summary(hours)


@router.get("/by-ip")
async def traffic_by_ip(
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(50, ge=1, le=500),
    session: dict = Depends(require_admin),
):
    """Top IP theo số lượng request."""
    return get_traffic_by_ip(hours, limit)


@router.get("/by-path")
async def traffic_by_path(
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(50, ge=1, le=500),
    session: dict = Depends(require_admin),
):
    """Top path được truy cập nhiều nhất."""
    return get_traffic_by_path(hours, limit)


@router.get("/suspicious")
async def suspicious_requests(
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(100, ge=1, le=1000),
    session: dict = Depends(require_admin),
):
    """Danh sách request đáng ngờ: .git, .env, wp-admin, 404..."""
    return get_suspicious_requests(hours, limit)


@router.get("/recent")
async def recent_logs(
    limit: int = Query(100, ge=1, le=1000),
    session: dict = Depends(require_admin),
):
    """N log gần nhất."""
    return get_recent_logs(limit)


@router.get("/hourly")
async def hourly_chart(
    hours: int = Query(24, ge=1, le=720),
    session: dict = Depends(require_admin),
):
    """Thống kê traffic theo giờ cho biểu đồ."""
    return get_hourly_chart(hours)
