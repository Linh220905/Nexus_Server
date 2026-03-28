"""
Text-to-Speech service using Google Cloud TTS API.

Pipeline: Text → Google Cloud TTS (MP3) → ffmpeg decode (PCM int16 24kHz) → Opus frames.
Streaming per-sentence để giảm giật giữa các câu.

Backup của Piper TTS cũ: tts_piper_backup.py
"""

import asyncio
import base64
import logging
import shutil
import time
from typing import AsyncGenerator

import aiohttp

from app.config import AudioOutputConfig, TTSConfig
from app.audio.opus_codec import OpusEncoder

logger = logging.getLogger(__name__)


class TTSService:
    """Chuyển text thành Opus audio frames dùng Google Cloud TTS API."""

    def __init__(self, tts_cfg: TTSConfig, audio_cfg: AudioOutputConfig):
        self._api_key = tts_cfg.google_tts_api_key
        self._voice_name = tts_cfg.google_tts_voice
        self._language_code = tts_cfg.google_tts_language
        self._speaking_rate = tts_cfg.speed
        self._voice_style = (tts_cfg.voice_style or "normal").strip().lower()

        if not self._api_key or self._api_key == "your-google-tts-api-key-here":
            logger.warning("⚠️  Google TTS API key chưa được cấu hình! Hãy set GOOGLE_TTS_API_KEY trong .env")

        self._target_rate = audio_cfg.sample_rate  # 24000
        self._encoder = OpusEncoder(audio_cfg)
        self._frame_bytes = self._encoder.frame_bytes
        self._frame_duration_s = audio_cfg.frame_duration_ms / 1000.0

        self._tts_url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={self._api_key}"

        logger.info(
            f"Google Cloud TTS initialized — voice={self._voice_name}, "
            f"lang={self._language_code}, rate={self._speaking_rate}, "
            f"style={self._voice_style}"
        )

    @property
    def frame_duration_s(self) -> float:
        """Thời lượng 1 Opus frame (giây)."""
        return self._frame_duration_s

    async def synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        """
        Text → Google Cloud TTS API → MP3 → PCM 24kHz → Opus frames.
        Yield opus frames ngay khi có đủ dữ liệu.
        """
        if not text or not text.strip():
            return

        started_at = time.perf_counter()
        first_frame_at: float | None = None
        total_frames = 0

        try:
            # Detect nếu text là SSML (bắt đầu bằng <speak>)
            text_stripped = text.strip()
            is_ssml = text_stripped.lower().startswith("<speak")

            if is_ssml:
                input_payload = {"ssml": text_stripped}
            else:
                input_payload = {"text": text_stripped}

            # Gọi Google Cloud TTS API
            request_body = {
                "input": input_payload,
                "voice": {
                    "languageCode": self._language_code,
                    "name": self._voice_name,
                },
                "audioConfig": {
                    "audioEncoding": "LINEAR16",
                    "sampleRateHertz": self._target_rate,
                    "speakingRate": self._speaking_rate,
                },
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._tts_url,
                    json=request_body,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(
                            f"Google TTS API error {resp.status}: {error_text}"
                        )
                        return

                    result = await resp.json()
                    audio_content = base64.b64decode(result["audioContent"])

            # Google TTS LINEAR16 trả về WAV (có header 44 bytes)
            # Skip WAV header nếu có
            pcm_data = audio_content
            if pcm_data[:4] == b"RIFF":
                # Tìm data chunk
                idx = pcm_data.find(b"data")
                if idx >= 0:
                    # Skip "data" + 4 bytes size
                    pcm_data = pcm_data[idx + 8:]

            # Encode PCM → Opus frames
            pcm_buffer = bytearray(pcm_data)

            while len(pcm_buffer) >= self._frame_bytes:
                frame_data = bytes(pcm_buffer[: self._frame_bytes])
                pcm_buffer = pcm_buffer[self._frame_bytes:]
                total_frames += 1
                if first_frame_at is None:
                    first_frame_at = time.perf_counter()
                yield self._encoder.encode(frame_data)

            # Pad và encode phần còn lại
            if len(pcm_buffer) > 0:
                pcm_buffer.extend(
                    b"\x00" * (self._frame_bytes - len(pcm_buffer))
                )
                total_frames += 1
                if first_frame_at is None:
                    first_frame_at = time.perf_counter()
                yield self._encoder.encode(bytes(pcm_buffer))

            elapsed = time.perf_counter() - started_at
            first_frame_ms = (
                (first_frame_at - started_at) * 1000.0
                if first_frame_at is not None
                else -1.0
            )
            total_samples = len(pcm_data) / 2.0
            audio_seconds = (
                total_samples / float(self._target_rate)
                if total_samples > 0
                else 0.0
            )
            rtf = (elapsed / audio_seconds) if audio_seconds > 0 else 0.0
            logger.info(
                "TTS timing | chars=%d frames=%d first_frame=%.1fms total=%.3fs "
                "audio=%.3fs rtf=%.2f voice=%s style=%s",
                len(text),
                total_frames,
                first_frame_ms,
                elapsed,
                audio_seconds,
                rtf,
                self._voice_name,
                self._voice_style,
            )

        except asyncio.TimeoutError:
            logger.error("Google TTS API timeout (30s)")
        except Exception as e:
            logger.error(f"Google TTS error: {e}", exc_info=True)

    async def stream_audio_url(self, url: str) -> AsyncGenerator[bytes, None]:
        """Stream audio từ URL (ví dụ preview mp3) -> Opus frames 24kHz mono."""
        if not url:
            return

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("ffmpeg not found, cannot stream audio url")
            return

        is_remote = str(url).lower().startswith(
            ("http://", "https://", "rtsp://", "ftp://")
        )
        if is_remote:
            cmd = [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "3",
                "-i", url,
                "-f", "s16le",
                "-ac", "1",
                "-ar", str(self._target_rate),
                "pipe:1",
            ]
        else:
            cmd = [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-i", url,
                "-f", "s16le",
                "-ac", "1",
                "-ar", str(self._target_rate),
                "pipe:1",
            ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        assert process.stdout is not None
        buffer = bytearray()
        frame_count = 0

        try:
            while True:
                chunk = await process.stdout.read(8192)
                if not chunk:
                    break
                buffer.extend(chunk)

                while len(buffer) >= self._frame_bytes:
                    frame = bytes(buffer[: self._frame_bytes])
                    del buffer[: self._frame_bytes]
                    frame_count += 1
                    yield self._encoder.encode(frame)

            if buffer:
                buffer.extend(b"\x00" * (self._frame_bytes - len(buffer)))
                frame_count += 1
                yield self._encoder.encode(bytes(buffer))

            await process.wait()
            if process.returncode != 0:
                err = b""
                if process.stderr is not None:
                    err = await process.stderr.read()
                logger.warning(
                    "ffmpeg exited with code %s: %s",
                    process.returncode,
                    err.decode("utf-8", errors="ignore"),
                )
            logger.info("Music preview streamed: %s frames", frame_count)
        except asyncio.CancelledError:
            process.kill()
            raise
        except Exception as e:
            logger.error("stream_audio_url error: %s", e, exc_info=True)
            process.kill()
        finally:
            if process.returncode is None:
                process.kill()

    async def stream_full_song_by_query(
        self, query: str
    ) -> AsyncGenerator[bytes, None]:
        """Tìm và phát full audio theo query (ưu tiên YouTube qua yt-dlp)."""
        if not query:
            return

        audio_url = await self._resolve_audio_url_from_youtube(query)
        if not audio_url:
            logger.warning(
                "Cannot resolve full-song url for query: %s", query
            )
            return

        async for frame in self.stream_audio_url(audio_url):
            yield frame

    async def _resolve_audio_url_from_youtube(
        self, query: str
    ) -> str | None:
        """Dùng yt-dlp lấy direct audio URL cho query."""
        ytdlp = shutil.which("yt-dlp")
        if not ytdlp:
            logger.warning(
                "yt-dlp not found, full-song streaming unavailable"
            )
            return None

        search_query = f"ytsearch1:{query} official audio"
        cmd = [
            ytdlp,
            "-f", "bestaudio/best",
            "-g",
            "--no-playlist",
            search_query,
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            logger.warning(
                "yt-dlp failed (%s): %s",
                process.returncode,
                (stderr or b"").decode("utf-8", errors="ignore"),
            )
            return None

        url = (
            (stdout or b"").decode("utf-8", errors="ignore").strip().splitlines()
        )
        return url[0].strip() if url else None
