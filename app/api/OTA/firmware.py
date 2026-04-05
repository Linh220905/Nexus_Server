from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import JSONResponse
from app.auth.security import check_admin_role
from pathlib import Path
import shutil
import os
import re

router = APIRouter(prefix="/OTA", tags=["OTA"])

FIRMWARE_DIR = Path("static/firmware")
FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)


def _extract_version_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    version = stem.split("_")[0]
    if re.fullmatch(r"\d+\.\d+\.\d+", version):
        return version
    return ""


def _binary_contains_version(path: Path, version: str) -> bool:
    if not version:
        return False
    marker = version.encode("ascii", errors="ignore")
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            if marker in chunk:
                return True
    return False

@router.post("/upload_firmware", summary="Admin upload firmware file", response_class=JSONResponse)
async def upload_firmware(
    file: UploadFile = File(...),
    current_admin=Depends(check_admin_role)
):
    filename = file.filename or ""
    if not filename.endswith(".bin"):
        raise HTTPException(status_code=400, detail="Chỉ chấp nhận file .bin")

    expected_version = _extract_version_from_filename(filename)
    if not expected_version:
        raise HTTPException(status_code=400, detail="Tên firmware phải bắt đầu bằng version dạng x.y.z")

    dest = FIRMWARE_DIR / filename
    with dest.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    if not _binary_contains_version(dest, expected_version):
        try:
            os.remove(dest)
        except OSError:
            pass
        raise HTTPException(
            status_code=400,
            detail=(
                f"Firmware không khớp version: tên file là {expected_version} nhưng binary không chứa version này. "
                "Hãy build lại đúng PROJECT_VER rồi upload lại."
            ),
        )

    # TODO: Lưu metadata vào database
    return {"success": True, "filename": filename, "url": f"/static/firmware/{filename}"}
