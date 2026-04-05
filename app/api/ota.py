import logging
import json
import os
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.database.connection import get_db_connection
from app.robots.crud import get_robot_by_mac, create_robot, generate_otp
from app.robots.models import RobotCreate

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ota"])
OTA_REASSIGN_COOLDOWN_SECONDS = 15 * 60


def _parse_version(version: str) -> list[int]:
    if not version:
        return []
    parts = re.findall(r"\d+", version)
    return [int(p) for p in parts]


def _is_newer_version(current: str, latest: str) -> bool:
    a = _parse_version(current)
    b = _parse_version(latest)
    if not b:
        return False
    if not a:
        return True

    length = max(len(a), len(b))
    a.extend([0] * (length - len(a)))
    b.extend([0] * (length - len(b)))
    return b > a


def _extract_device_version(request_body: dict) -> str:
    if not isinstance(request_body, dict):
        return ""

    app_info = request_body.get("application")
    if isinstance(app_info, dict):
        version = app_info.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()

    # Fallback for clients that may send version at top-level.
    version = request_body.get("firmware_version") or request_body.get("version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return ""


def _update_robot_ota_state(mac: str, **state) -> None:
    if not mac or not state:
        return

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT config FROM robots WHERE mac_address = ?", (mac,))
        row = cursor.fetchone()
        if not row:
            return

        current_config = {}
        raw_config = row["config"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        if raw_config:
            try:
                current_config = json.loads(raw_config)
            except Exception:
                current_config = {}

        ota_state = current_config.get("ota_state")
        if not isinstance(ota_state, dict):
            ota_state = {}

        ota_state.update(state)
        current_config["ota_state"] = ota_state

        cursor.execute(
            """
            UPDATE robots
            SET config = ?, updated_at = CURRENT_TIMESTAMP
            WHERE mac_address = ?
            """,
            (json.dumps(current_config), mac),
        )
        conn.commit()


def _get_robot_ota_state(mac: str) -> dict:
    if not mac:
        return {}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT config FROM robots WHERE mac_address = ?", (mac,))
        row = cursor.fetchone()
        if not row:
            return {}

        raw_config = row["config"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        if not raw_config:
            return {}

        try:
            config = json.loads(raw_config)
        except Exception:
            return {}

        ota_state = config.get("ota_state")
        return ota_state if isinstance(ota_state, dict) else {}


def _was_recently_assigned_same_target(ota_state: dict, firmware_version: str, firmware_file: str) -> bool:
    if not ota_state or not firmware_version or not firmware_file:
        return False

    if ota_state.get("target_version") != firmware_version:
        return False
    if ota_state.get("target_file") != firmware_file:
        return False

    assigned_at = ota_state.get("target_assigned_at")
    if not isinstance(assigned_at, str) or not assigned_at:
        return False

    try:
        assigned_dt = datetime.fromisoformat(assigned_at)
        if assigned_dt.tzinfo is None:
            assigned_dt = assigned_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return False

    age_seconds = (datetime.now(tz=timezone.utc) - assigned_dt).total_seconds()
    return age_seconds < OTA_REASSIGN_COOLDOWN_SECONDS


def _ensure_robot(mac: str):
    robot = get_robot_by_mac(mac)
    if robot is None:
        robot_id = f"nexus-{mac[-8:].replace(':', '').lower()}"
        new_robot = RobotCreate(
            mac_address=mac,
            robot_id=robot_id,
            name=f"Nexus {mac[-5:]}",
        )
        try:
            robot = create_robot(new_robot)
            logger.info("Auto-registered new robot MAC=%s  id=%s", mac, robot_id)
        except ValueError:
            robot = get_robot_by_mac(mac)
    return robot


@router.api_route("/nexus/ota/", methods=["GET", "POST"])
@router.api_route("/nexus/ota", methods=["GET", "POST"])
@router.api_route("/api/nexus/ota/", methods=["GET", "POST"])
@router.api_route("/api/nexus/ota", methods=["GET", "POST"])
@router.api_route("/api/v1/nexus/ota/", methods=["GET", "POST"])
@router.api_route("/api/v1/nexus/ota", methods=["GET", "POST"])
async def ota_bootstrap(request: Request) -> dict:
    host = request.headers.get("host", "127.0.0.1:8000")
    mac = request.headers.get("device-id", "").strip()
    reported_version = request.headers.get("firmware-version", "").strip()

    request_payload = {}
    if request.method == "POST":
        try:
            request_payload = await request.json()
        except Exception:
            request_payload = {}
    if not reported_version:
        reported_version = _extract_device_version(request_payload)

    ws_url = f"ws://{host}"
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)

    # Lấy firmware mới nhất trong static/firmware
    firmware_dir = os.path.join(os.path.dirname(__file__), '../../static/firmware')
    firmware_dir = os.path.abspath(firmware_dir)
    firmware_file = None
    firmware_version = "1.0.0"
    if os.path.isdir(firmware_dir):
        for f in sorted(os.listdir(firmware_dir), reverse=True):
            if f.endswith('.bin'):
                firmware_file = f
                try:
                    firmware_version = f.split('_')[0]
                except Exception:
                    firmware_version = "1.0.0"
                break

    offer_update = bool(firmware_file)
    if reported_version:
        offer_update = offer_update and _is_newer_version(reported_version, firmware_version)

    ota_state = _get_robot_ota_state(mac) if mac else {}
    if offer_update and _was_recently_assigned_same_target(ota_state, firmware_version, firmware_file or ""):
        offer_update = False
        logger.info(
            "Suppress duplicate OTA for %s: target=%s file=%s assigned recently",
            mac or "?",
            firmware_version,
            firmware_file,
        )

    firmware_url = f"http://{host}/static/firmware/{firmware_file}" if (firmware_file and offer_update) else ""
    response_version = firmware_version if offer_update else (reported_version or firmware_version)

    if reported_version:
        logger.info("OTA check from %s reports version=%s, latest=%s, offer_update=%s", mac or "?", reported_version, firmware_version, offer_update)

    response: dict = {
        "websocket": {
            "url": ws_url,
            "token": "",
            "version": 1,
        },
        "server_time": {
            "timestamp": now_ms,
            "timezone_offset": 420,
        },
        "firmware": {
            "version": response_version,
            "url": firmware_url,
            "force": 0,
        },
    }

    if mac:
        robot = _ensure_robot(mac)

        if reported_version:
            _update_robot_ota_state(
                mac,
                reported_version=reported_version,
                last_check_at=datetime.now(tz=timezone.utc).isoformat(),
            )

        if offer_update and firmware_file:
            _update_robot_ota_state(
                mac,
                target_version=firmware_version,
                target_file=firmware_file,
                target_assigned_at=datetime.now(tz=timezone.utc).isoformat(),
            )

        if robot and not robot.owner_username:
            otp = generate_otp(mac, ttl_minutes=10)
            challenge = uuid.uuid4().hex

            logger.info("Device %s chưa có owner → OTP=%s", mac, otp)

            response["activation"] = {
                "code": otp,
                "message": "Nhập mã này trên web để kích hoạt thiết bị",
                "challenge": challenge,
                "timeout_ms": 30000,
            }
        else:
            logger.info("Device %s đã có owner=%s → bỏ qua activation",
                        mac, robot.owner_username if robot else "?")

    return response
