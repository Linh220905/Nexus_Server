"""
Speech-to-Text service.

Mặc định dùng Groq Whisper (nhanh + free + chính xác tiếng Việt).
Đổi provider trong config.py → STTConfig.provider.

Các provider hỗ trợ:
  - "groq": Groq Whisper API (nhanh nhất, free 7000 req/ngày)
  - "openai": OpenAI Whisper API (chính xác, trả phí)

Lấy API key:
  - Groq: https://console.groq.com/keys → set GROQ_API_KEY
  - OpenAI: https://platform.openai.com/api-keys → set OPENAI_API_KEY
"""

import io
import os
import wave
import logging
import tempfile
from typing import Optional

import openai

from app.config import STTConfig

logger = logging.getLogger(__name__)

MIN_PCM_BYTES = 16000

# Các ngôn ngữ được phép — nếu Whisper detect ra ngôn ngữ khác sẽ thử lại
ALLOWED_LANGUAGES = ("vi", "en")


class STTService:
    """Chuyển audio PCM thành text qua Whisper API (Groq/OpenAI).
    
    Hỗ trợ cả tiếng Việt và tiếng Anh:
    - Lần 1: auto-detect (không ép language) + prompt hint tiếng Việt/Anh
    - Nếu detect ra ngôn ngữ không phải vi/en → thử lại với language="vi"
    """

    def __init__(self, cfg: STTConfig):
        self._client = openai.AsyncOpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
        )
        self._model = cfg.model
        self._language = cfg.language  # Rỗng = dual vi/en mode
        logger.info(f"STT provider: {cfg.provider} | model: {cfg.model} | language: {cfg.language or 'auto(vi+en)'}")

    async def transcribe(self, pcm_data: bytes, sample_rate: int = 16000) -> Optional[str]:
        """PCM int16 mono → text. Returns None nếu quá ngắn hoặc lỗi."""
        if len(pcm_data) < MIN_PCM_BYTES:
            logger.debug("Audio quá ngắn, bỏ qua")
            return None

        wav_bytes = _pcm_to_wav(pcm_data, sample_rate)
        return await self._call_api(wav_bytes)

    async def _call_api(self, wav_bytes: bytes) -> Optional[str]:
        """Gửi WAV lên Whisper API.
        
        Nếu language được chỉ định → dùng luôn.
        Nếu rỗng → auto-detect với prompt hint, rồi validate ngôn ngữ.
        """
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(wav_bytes)
                tmp_path = tmp.name

            # --- Trường hợp 1: language được ép cố định ---
            if self._language:
                return await self._transcribe_with_lang(tmp_path, self._language)

            # --- Trường hợp 2: auto-detect (chỉ cho phép vi + en) ---
            # Lần 1: verbose_json để lấy detected language
            result = await self._transcribe_verbose(tmp_path)
            if result is None:
                return None

            text, detected_lang = result
            
            if detected_lang in ALLOWED_LANGUAGES:
                logger.info(f"\033[92m📝 STT [{detected_lang}]: {text}\033[0m")
                return text or None

            # Ngôn ngữ không cho phép → fallback thử lại với "vi"
            logger.warning(f"STT detected '{detected_lang}' (không hỗ trợ), thử lại với vi...")
            fallback = await self._transcribe_with_lang(tmp_path, "vi")
            return fallback

        except Exception as e:
            logger.error(f"STT API error: {e}")
            return None

        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    async def _transcribe_verbose(self, wav_path: str) -> Optional[tuple[str, str]]:
        """Transcribe với response_format=verbose_json để lấy detected language."""
        try:
            with open(wav_path, "rb") as f:
                result = await self._client.audio.transcriptions.create(
                    model=self._model,
                    file=f,
                    response_format="verbose_json",
                    prompt="Transcribe Vietnamese or English speech.",
                )
            text = result.text.strip() if result.text else ""
            detected_lang = getattr(result, "language", "vi") or "vi"
            return text, detected_lang
        except Exception as e:
            logger.error(f"STT verbose API error: {e}")
            return None

    async def _transcribe_with_lang(self, wav_path: str, language: str) -> Optional[str]:
        """Transcribe với language cố định."""
        try:
            with open(wav_path, "rb") as f:
                result = await self._client.audio.transcriptions.create(
                    model=self._model,
                    file=f,
                    language=language,
                )
            text = result.text.strip()
            logger.info(f"\033[92m📝 STT [{language}]: {text}\033[0m")
            return text or None
        except Exception as e:
            logger.error(f"STT API error ({language}): {e}")
            return None


def _pcm_to_wav(pcm_data: bytes, sample_rate: int) -> bytes:
    """Đóng gói PCM int16 mono thành WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()
