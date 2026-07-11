"""
Session state cho mỗi client kết nối.

Mỗi ESP32 kết nối = 1 Session.
Quản lý: audio buffer, chat history, trạng thái.
"""

import uuid
import struct
import math
from app.server_logging import get_logger
from datetime import datetime

from app.config import AppConfig
from app.audio.opus_codec import OpusDecoder
from app.mcp import MCPToolRegistry
from app.services.intent import IntentDetectorService
from app.services.stt import STTService
from app.services.llm import LLMService
from app.services.tts import TTSService
from app.services.pipeline import ConversationPipeline
from app.services.game_profile import GameProfile

logger = get_logger(__name__)


class Session:
    """State cho 1 phiên kết nối client."""

    def __init__(self, config: AppConfig, device_id: str, client_id: str):
        self.session_id = str(uuid.uuid4())
        self.device_id = device_id
        self.client_id = client_id

        # Audio
        self._decoder = OpusDecoder(config.audio_input)
        self._pcm_buffer = bytearray()

        # Services
        stt = STTService(config.stt)
        llm = LLMService(config.llm)
        intent_llm = LLMService(config.intent_llm)
        intent_detector = IntentDetectorService(intent_llm)
        tts = TTSService(config.tts, config.audio_output)
        mcp_tools = MCPToolRegistry()
        self.pipeline = ConversationPipeline(
            stt,
            llm,
            tts,
            intent_detector=intent_detector,
            mcp_tools=mcp_tools,
        )

        # State
        self.chat_history: list[dict] = []
        self.is_speaking = False
        self.is_idling = False
        self.last_idle_at: datetime | None = None
        self.aborted = False

        # Game profile (XP, level, badges)
        self.game_profile = GameProfile()

        # Teaching learning context
        self.learning_context: dict[str, str | None] = {
            "mode": None,
            "topic_id": None,
            "next_index": "0",
            "finished": "0",
            "locked": "0",
            "lock_target_index": "0",
            "attempt_count": "0",
            "seen_words": "",
            "current_lesson_id": "0",
            # Proactive teaching fields
            "module_index": "0",
            "lesson_index": "0",
            "lesson_complete": "0",
            "player_name": "",
            "intro_done": "0",
            "onboarding_state": "",
        }

        self._max_history = config.max_chat_history

        # VAD (Voice Activity Detection)
        self._silent_frames = 0  # Số frames im lặng liên tiếp
        self._has_speech = False  # Đã xác nhận giọng nói chưa
        self._speech_frames = 0  # Số frames có năng lượng cao (đếm để xác nhận)
        self._noise_floor_rms = 30.0  # Nền nhiễu RMS ước lượng (adaptive), khởi tạo mức yên tĩnh
        self._last_speech_threshold = 0.0
        self._last_silence_threshold = 0.0
        self._last_rms_delta = 0.0
        self._rms_history = [30.0] * 50  # Lịch sử RMS để bám nhiễu — pre-fill để percentile hoạt động từ frame 1
        # Adaptive silence: track utterance duration để quyết định silence timeout
        self._total_speech_frames = 0  # Tổng speech frames trong utterance hiện tại
        self._peak_rms = 0.0  # RMS cao nhất trong utterance hiện tại

    @property
    def buffer_size(self) -> int:
        """Kích thước buffer PCM hiện tại (bytes)."""
        return len(self._pcm_buffer)

    def reset_audio_buffer(self) -> None:
        """Xóa buffer audio, chuẩn bị nhận recording mới.
        
        GIỮ LẠI noise_floor_rms để tránh recalibration sai khi user
        đang nói → noise floor nhảy lên cao → speech không detect được.
        """
        self._pcm_buffer = bytearray()
        self._silent_frames = 0
        self._has_speech = False
        self._speech_frames = 0
        # KHÔNG reset _noise_floor_rms — giữ lại từ lượt trước
        self._last_speech_threshold = 0.0
        self._last_silence_threshold = 0.0
        self._last_rms_delta = 0.0
        self._total_speech_frames = 0  # Reset cho utterance mới
        self._peak_rms = 0.0
        self.aborted = False

    def append_audio(self, opus_data: bytes) -> bytes | None:
        """Decode 1 Opus frame, thêm PCM vào buffer, trả về PCM để phân tích."""
        if self.aborted:
            return None
        try:
            pcm = self._decoder.decode(opus_data)
            self._pcm_buffer.extend(pcm)
            return pcm
        except Exception as e:
            logger.error(f"[{self.device_id}] Opus decode error: {e}")
            return None

    def check_vad(
        self,
        pcm: bytes,
        speech_threshold: int = 280,
        silence_threshold: int = 180,
        speech_frames_needed: int = 2,
        silence_frames_needed: int = 8,
    ) -> str:
        """
        Phân tích năng lượng âm thanh, trả về trạng thái.

        Yêu cầu ít nhất `speech_frames_needed` frames có RMS > speech_threshold
        để xác nhận có người nói thật. Sau đó, nếu RMS < silence_threshold
        trong đủ frames liên tiếp (thích ứng theo độ dài câu nói) → trigger STT.

        Sử dụng bộ lọc min-filter (sliding window) kết hợp chống nhiễu
        để tự thích ứng cực nhanh với tạp âm môi trường mà không bị kẹt.

        Silence timeout thích ứng:
          - Utterance ≤ 5 frames speech  → 4 frames silence (câu rất ngắn: "dạ", "có")
          - Utterance ≤ 10 frames speech → 6 frames silence (câu ngắn: "tên gì")
          - Utterance > 10 frames speech → 8 frames silence (câu dài bình thường)

        Returns:
            'speech': Đang nói
            'silence_after_speech': Im lặng sau khi đã nói → trigger STT
            'silence': Im lặng (chưa nói gì)
        """
        rms = self._calc_rms(pcm)

        # 1. Cập nhật lịch sử RMS và tính toán Noise Floor động bằng 3rd-lowest (percentile lọc nhiễu)
        self._rms_history.append(rms)
        if len(self._rms_history) > 50:
            self._rms_history.pop(0)

        # Lấy giá trị nhỏ thứ 3 để triệt tiêu các mẫu dropout / frame lỗi đơn lẻ (RMS = 0)
        sorted_history = sorted(self._rms_history)
        self._noise_floor_rms = max(
            sorted_history[min(2, len(sorted_history) - 1)],
            30.0,  # Không xuống dưới 30 — tránh threshold quá thấp
        )

        # 2. Tính toán ngưỡng speech và silence động thích ứng theo noise floor thực tế
        #    Dùng tỷ lệ SNR (Signal-to-Noise Ratio) thay vì absolute delta
        dynamic_speech_threshold = max(float(speech_threshold), self._noise_floor_rms * 1.35 + 80.0)
        dynamic_silence_threshold = max(float(silence_threshold), self._noise_floor_rms * 1.12 + 30.0)

        rms_delta = rms - self._noise_floor_rms
        self._last_speech_threshold = dynamic_speech_threshold
        self._last_silence_threshold = dynamic_silence_threshold
        self._last_rms_delta = rms_delta

        # 3. State machine cho VAD
        #    Dùng SNR ratio: speech khi RMS > threshold VÀ tỷ lệ tín hiệu/nhiễu >= 40%
        #    (linh hoạt hơn so với delta > 80 cố định)
        snr_ratio = rms / max(self._noise_floor_rms, 1.0)
        if rms > dynamic_speech_threshold and snr_ratio > 1.4:
            self._silent_frames = 0
            self._speech_frames += 1
            self._total_speech_frames += 1
            if rms > self._peak_rms:
                self._peak_rms = rms
            if self._speech_frames >= speech_frames_needed:
                self._has_speech = True
            return 'speech'
        elif rms > dynamic_silence_threshold:
            self._silent_frames = 0
            # Nếu chưa chắc chắn là nói thật, giảm đếm để tránh nhiễu lắt nhắt cộng dồn
            if not self._has_speech and self._speech_frames >= 3:
                self._speech_frames -= 1
            return 'speech' if self._has_speech else 'silence'
        else:
            self._silent_frames += 1
            if not self._has_speech:
                self._speech_frames = 0
            if self._has_speech:
                # Silence timeout thích ứng theo độ dài câu nói
                needed = self._adaptive_silence_needed()
                if self._silent_frames >= needed:
                    return 'silence_after_speech'
            return 'silence'

    def _adaptive_silence_needed(self) -> int:
        """
        Tính số frames silence cần để trigger, dựa trên độ dài câu nói.

        - Utterance ≤ 3 frames speech  → 5 frames (từ rất ngắn: "dạ", "vâng")
        - Utterance ≤ 10 frames speech → 6 frames (câu ngắn: "tên gì", "ở đâu")
        - Utterance > 10 frames speech → 8 frames (câu dài bình thường)
        """
        if self._total_speech_frames <= 3:
            return 5
        elif self._total_speech_frames <= 10:
            return 6
        return 8

    @property
    def has_speech(self) -> bool:
        return self._has_speech

    @staticmethod
    def _calc_rms(pcm: bytes) -> float:
        """Tính AC RMS (trừ DC offset) của PCM int16."""
        if len(pcm) < 2:
            return 0.0
        n_samples = len(pcm) // 2
        samples = struct.unpack(f'<{n_samples}h', pcm[:n_samples * 2])
        if not samples:
            return 0.0
        mean = sum(samples) / n_samples
        sum_sq = sum((s - mean) * (s - mean) for s in samples)
        return math.sqrt(sum_sq / n_samples)

    def take_audio_buffer(self) -> bytes:
        """Lấy toàn bộ PCM buffer và xóa."""
        data = bytes(self._pcm_buffer)
        self._pcm_buffer = bytearray()
        return data

    def save_history(self, user_text: str, assistant_text: str) -> None:
        """Lưu 1 lượt hội thoại vào history."""
        self.chat_history.append({"role": "user", "content": user_text})
        self.chat_history.append({"role": "assistant", "content": assistant_text})
        # Giới hạn kích thước
        if len(self.chat_history) > self._max_history:
            self.chat_history = self.chat_history[-self._max_history :]

    def abort(self) -> None:
        """Đánh dấu abort — dừng phát audio."""
        self.aborted = True
        self.is_speaking = False



_active_sessions: dict[str, Session] = {}


def create_session(config: AppConfig, device_id: str, client_id: str) -> Session:
    """Tạo session mới và lưu vào registry."""
    session = Session(config, device_id, client_id)
    _active_sessions[session.session_id] = session
    logger.info(f"[{device_id}] Session created: {session.session_id}")
    return session


def remove_session(session_id: str) -> None:
    """Xóa session khi client disconnect."""
    removed = _active_sessions.pop(session_id, None)
    if removed:
        logger.info(f"[{removed.device_id}] Session removed: {session_id}")


def get_all_sessions() -> list[Session]:
    """Lấy danh sách tất cả sessions đang active."""
    return list(_active_sessions.values())
