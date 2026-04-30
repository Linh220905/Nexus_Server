"""
Orders API — public order submission + admin order management.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
from app.database.connection import get_db_connection
from app.server_logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/orders", tags=["orders"])


class OrderCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100)
    phone: str = Field(..., min_length=8, max_length=20)
    email: Optional[str] = Field(None, max_length=100)
    address: Optional[str] = Field(None, max_length=300)
    package: str = Field("starter", pattern="^(starter|pro)$")
    note: Optional[str] = Field(None, max_length=500)


class OrderStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(pending|confirmed|shipped|completed|cancelled)$")


@router.post("")
async def create_order(order: OrderCreate):
    """Public endpoint — khách hàng đặt hàng từ landing page."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO orders (full_name, phone, email, address, package, note)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (order.full_name, order.phone, order.email, order.address, order.package, order.note),
        )
        conn.commit()
        order_id = cursor.lastrowid
    logger.info(f"📦 New order #{order_id}: {order.full_name} — {order.phone} — {order.package}")
    return {"ok": True, "order_id": order_id, "message": "Đặt hàng thành công!"}


@router.get("")
async def list_orders(request: Request, status: Optional[str] = None):
    """Admin endpoint — list all orders."""
    # Simple auth check via cookie
    session_token = request.cookies.get("nexus_session")
    if not session_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        from app.api.auth_google import decode_session_token
        payload = decode_session_token(session_token)
        if payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin only")
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute("SELECT * FROM orders WHERE status = ? ORDER BY created_at DESC", (status,))
        else:
            cursor.execute("SELECT * FROM orders ORDER BY created_at DESC")
        rows = cursor.fetchall()
        orders = [dict(row) for row in rows]
    return {"orders": orders, "total": len(orders)}


@router.patch("/{order_id}")
async def update_order_status(order_id: int, body: OrderStatusUpdate, request: Request):
    """Admin endpoint — update order status."""
    session_token = request.cookies.get("nexus_session")
    if not session_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        from app.api.auth_google import decode_session_token
        payload = decode_session_token(session_token)
        if payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin only")
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (body.status, order_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Order not found")
    return {"ok": True, "message": f"Order #{order_id} updated to {body.status}"}


@router.delete("/{order_id}")
async def delete_order(order_id: int, request: Request):
    """Admin endpoint — delete an order."""
    session_token = request.cookies.get("nexus_session")
    if not session_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        from app.api.auth_google import decode_session_token
        payload = decode_session_token(session_token)
        if payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin only")
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Order not found")
    return {"ok": True, "message": f"Order #{order_id} deleted"}
