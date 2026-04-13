from app.server_logging import get_logger
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - graceful fallback when pillow is missing
    Image = None
    ImageDraw = None
    ImageFont = None

from app.models import HealthResponse, SessionInfo
from app.websocket.session import get_all_sessions
from app.database.chat_history import get_chat_sessions_for_user
from .auth import router as auth_router
from .robot_api import router as robot_router
from .otp import router as otp_router
from .ota_activate import router as ota_activate_router
from .auth_google import router as auth_google_router, require_viewer
from .OTA.firmware import router as ota_firmware_router
from .admin import firmware_router as admin_firmware_router
from app.services.learning_content import get_learning_payload
from app.database.assignments import (
    create_assignment_for_user,
    delete_assignment_for_user,
    list_assignments_for_user,
    update_assignment_for_user,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["API"])
v1_router = APIRouter(prefix="/api/v1", tags=["API v1"])


def _pick_flashcard_font(size: int, bold: bool = False):
    if ImageFont is None:
        return None

    candidates = []
    try:
        import PIL

        pil_font_dir = Path(PIL.__file__).resolve().parent / "fonts"
        if bold:
            candidates.append(str(pil_font_dir / "DejaVuSans-Bold.ttf"))
        candidates.append(str(pil_font_dir / "DejaVuSans.ttf"))
    except Exception:
        pass

    if bold:
        candidates.extend(
            [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            ]
        )
    candidates.extend(
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    )

    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    sessions = get_all_sessions()
    return HealthResponse(active_sessions=len(sessions))


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions():
    return [
        SessionInfo(
            session_id=s.session_id,
            device_id=s.device_id,
            client_id=s.client_id,
            is_speaking=s.is_speaking,
            history_length=len(s.chat_history),
        )
        for s in get_all_sessions()
    ]


@router.get("/sessions/{session_id}/history")
async def get_history(session_id: str):
    for s in get_all_sessions():
        if s.session_id == session_id:
            return {"session_id": session_id, "history": s.chat_history}
    return {"error": "Session not found"}


v1_router.include_router(auth_router)
router.include_router(auth_google_router)
router.include_router(robot_router)
router.include_router(otp_router)
router.include_router(ota_activate_router)

router.include_router(ota_firmware_router)
router.include_router(admin_firmware_router)


@router.get("/chat-history")
async def chat_history(session: dict = Depends(require_viewer)):
    """Lấy lịch sử chat của tất cả robot thuộc user hiện tại (chỉ dành cho user/viewer)."""
    email = session.get("email", "")
    sessions = get_chat_sessions_for_user(email)
    return {"ok": True, "sessions": sessions}


@router.get("/learning/topics")
async def learning_topics(session: dict = Depends(require_viewer)):
    _ = session
    return get_learning_payload()


@router.get("/assignments")
async def list_assignments(session: dict = Depends(require_viewer)):
    email = session.get("email", "")
    return {"ok": True, "items": list_assignments_for_user(email)}


@router.post("/assignments")
async def create_assignment(payload: dict, session: dict = Depends(require_viewer)):
    email = session.get("email", "")
    try:
        item = create_assignment_for_user(email, payload)
    except ValueError as e:
        return {"ok": False, "detail": str(e)}
    return {"ok": True, "item": item}


@router.put("/assignments/{assignment_id}")
async def update_assignment(assignment_id: int, payload: dict, session: dict = Depends(require_viewer)):
    email = session.get("email", "")
    try:
        item = update_assignment_for_user(email, assignment_id, payload)
    except ValueError as e:
        return {"ok": False, "detail": str(e)}
    return {"ok": True, "item": item}


@router.delete("/assignments/{assignment_id}")
async def delete_assignment(assignment_id: int, session: dict = Depends(require_viewer)):
    email = session.get("email", "")
    delete_assignment_for_user(email, assignment_id)
    return {"ok": True}


@router.get("/learning/flashcard")
async def learning_flashcard(
        topic_id: str = Query("general"),
        word: str = Query("Word"),
        meaning: str = Query("Nghia"),
        w: int = Query(320, ge=120, le=800),
        h: int = Query(240, ge=120, le=600),
        q: int = Query(38, ge=20, le=85),
        fmt: str = Query("png"),
):
    safe_word = (word or "Word")[:40]
    safe_meaning = (meaning or "Nghia")[:60]

    if Image is not None and ImageDraw is not None:
        img = Image.new("RGB", (w, h), "#121826")
        draw = ImageDraw.Draw(img)
        panel_margin = max(10, w // 24)
        draw.rounded_rectangle(
            (panel_margin, panel_margin, w - panel_margin, h - panel_margin),
            radius=max(10, w // 28),
            fill="#f8fafc",
        )

        meaning_font = _pick_flashcard_font(max(24, h // 7), bold=True)

        def _center_x(text: str, font_obj) -> int:
            bbox = draw.textbbox((0, 0), text, font=font_obj)
            tw = max(1, bbox[2] - bbox[0])
            return int(max(panel_margin * 2, (w - tw) // 2))

        word_text = safe_word.upper()

        def _fit_font(text: str, prefer: int, min_size: int, bold: bool = True):
            size = prefer
            max_w = w - panel_margin * 4
            while size >= min_size:
                f = _pick_flashcard_font(size, bold=bold)
                bbox = draw.textbbox((0, 0), text, font=f)
                tw = max(1, bbox[2] - bbox[0])
                if tw <= max_w:
                    return f
                size -= 2
            return _pick_flashcard_font(min_size, bold=bold)

        word_font = _fit_font(word_text, prefer=max(60, (h * 2) // 5), min_size=max(34, h // 6), bold=True)
        meaning_font = _fit_font(safe_meaning, prefer=max(38, h // 4), min_size=max(24, h // 10), bold=True)

        y_word = max(panel_margin * 3, h // 2 - h // 5)
        y_meaning = min(h - panel_margin * 4, y_word + h // 3 - panel_margin)

        draw.text(
            (_center_x(word_text, word_font), y_word),
            word_text,
            fill="#000000",
            font=word_font,
            stroke_width=max(2, h // 120),
            stroke_fill="#000000",
        )
        draw.text(
            (_center_x(safe_meaning, meaning_font), y_meaning),
            safe_meaning,
            fill="#000000",
            font=meaning_font,
            stroke_width=max(1, h // 160),
            stroke_fill="#000000",
        )

        output = BytesIO()
        if (fmt or "jpg").lower() == "png":
            img.save(output, format="PNG", optimize=False)
            return Response(content=output.getvalue(), media_type="image/png")

        img.save(output, format="JPEG", quality=q, optimize=True, progressive=False)
        return Response(content=output.getvalue(), media_type="image/jpeg")

    # Fallback when Pillow is unavailable
    svg = f"""
<svg xmlns='http://www.w3.org/2000/svg' width='800' height='480' viewBox='0 0 800 480'>
    <defs>
        <linearGradient id='bg' x1='0' y1='0' x2='1' y2='1'>
            <stop offset='0%' stop-color='#1f2937'/>
            <stop offset='100%' stop-color='#111827'/>
        </linearGradient>
    </defs>
    <rect width='800' height='480' fill='url(#bg)'/>
    <rect x='36' y='36' width='728' height='408' rx='24' fill='#f9fafb' opacity='0.98'/>
    <text x='72' y='230' font-size='72' fill='#111827' font-family='Arial, sans-serif' font-weight='700'>{safe_word}</text>
    <text x='72' y='325' font-size='44' fill='#111827' font-family='Arial, sans-serif' font-weight='700'>{safe_meaning}</text>
</svg>
""".strip()
    return Response(content=svg, media_type="image/svg+xml")


@router.post("/mcp/tools")
async def list_mcp_tools():
    return {
        "tools": [
            {"name": "set_volume", "description": "Điều chỉnh âm lượng"},
            {"name": "set_brightness", "description": "Điều chỉnh độ sáng"},
            {"name": "reboot", "description": "Khởi động lại thiết bị"},
        ]
    }


@router.post("/mcp/call/{tool_name}")
async def call_mcp_tool(tool_name: str, params: dict = {}):
    logger.info(f"MCP call: {tool_name} params={params}")
    return {
        "tool": tool_name,
        "status": "not_implemented",
        "message": "MCP tool calling chưa được implement.",
    }
