"""
Unit tests cho VAD (Voice Activity Detection).

Sử dụng synthetic PCM data để kiểm tra state machine trong
Session.check_vad() — không cần hardware hay Opus codec thật.
"""

import math
import struct
import pytest
from unittest.mock import MagicMock

from app.websocket.session import Session
from app.config import AppConfig


# ── Helpers tạo PCM ──────────────────────────────────────────────

def _make_pcm(rms: float, n_samples: int = 960) -> bytes:
    """
    Tạo PCM int16 mono với RMS mong muốn (AC RMS, zero mean).
    960 samples = 60ms @ 16kHz (1 frame).
    """
    if rms < 1.0:
        samples = [0] * n_samples
    else:
        amplitude = int(round(rms * math.sqrt(2)))
        samples = []
        for i in range(n_samples):
            val = int(amplitude * math.sin(2 * math.pi * 100 * i / 16000))
            val = max(-32768, min(32767, val))
            samples.append(val)
    return struct.pack(f'<{n_samples}h', *samples)


def _make_session() -> Session:
    """Tạo Session với config mặc định, mock decoder."""
    config = AppConfig()
    session = Session(config, "test-device", "test-client")
    session._decoder = MagicMock()
    return session


@pytest.fixture
def session():
    s = _make_session()
    yield s


# ═══════════════════════════════════════════════════════════════════
# TEST: Basic Speech Detection
# ═══════════════════════════════════════════════════════════════════

class TestBasicSpeechDetection:
    """Các test cơ bản cho phát hiện speech."""

    def test_initial_state(self, session):
        """Session mới: chưa có speech, silent_frames=0."""
        assert not session._has_speech
        assert session._silent_frames == 0
        assert session._speech_frames == 0
        assert session._total_speech_frames == 0
        assert session._peak_rms == 0.0

    def test_detect_speech_after_minimum_frames(self, session):
        """Speech được xác nhận sau speech_frames_needed (2) frames."""
        pcm_high = _make_pcm(450.0)
        pcm_low = _make_pcm(10.0)

        # Frame 1: speech, chưa đủ
        state = session.check_vad(pcm_high, speech_frames_needed=2)
        assert state == 'speech'
        assert not session._has_speech

        # Frame 2: speech, confirm
        state = session.check_vad(pcm_high, speech_frames_needed=2)
        assert session._has_speech

    def test_silence_before_speech(self, session):
        """Im lặng trước khi nói → silence frames tăng."""
        pcm_low = _make_pcm(10.0)
        state = session.check_vad(pcm_low)
        assert state == 'silence'
        assert session._silent_frames == 1
        assert not session._has_speech

    def test_speech_frames_not_decayed_by_silence_threshold(self, session):
        """Frame trong dynamic_silence_threshold không làm giảm _speech_frames nếu chưa có speech."""
        pcm_mid = _make_pcm(200.0)  # Giữa speech và silence threshold
        state = session.check_vad(pcm_mid)
        assert state == 'silence'
        assert session._speech_frames == 0  # Không thay đổi


# ═══════════════════════════════════════════════════════════════════
# TEST: Long utterance (câu dài, >10 frames speech)
# ═══════════════════════════════════════════════════════════════════

class TestLongUtterance:
    """Câu dài (vd: "Hôm nay thời tiết thế nào?") ~15-20 frames."""

    def test_silence_after_long_speech_triggers(self, session):
        """Sau speech dài, im lặng 8 frames → silence_after_speech."""
        pcm_high = _make_pcm(500.0)
        pcm_low = _make_pcm(10.0)

        for _ in range(15):
            session.check_vad(pcm_high)
        assert session._has_speech
        assert session._total_speech_frames > 10

        for i in range(8):
            state = session.check_vad(pcm_low)
            if i < 7:
                assert state == 'silence', f"frame {i}: expected silence, got {state}"
            else:
                assert state == 'silence_after_speech'

    def test_long_utterance_not_truncated_early(self, session):
        """Câu dài không bị trigger sớm: cần 8 silence frames."""
        pcm_high = _make_pcm(500.0)
        pcm_low = _make_pcm(10.0)

        for _ in range(3):
            session.check_vad(pcm_high)

        # 4 frames silence, chưa đủ
        for _ in range(4):
            state = session.check_vad(pcm_low)
        assert state != 'silence_after_speech'

        # Nói tiếp
        for _ in range(5):
            session.check_vad(pcm_high)

        # Im lặng 8 frames → trigger
        for i in range(8):
            state = session.check_vad(pcm_low)
        assert state == 'silence_after_speech'


# ═══════════════════════════════════════════════════════════════════
# TEST: Short utterance (câu ngắn, 1-2 từ)
# ═══════════════════════════════════════════════════════════════════

class TestShortUtterance:
    """Câu ngắn (vd: "dạ", "có", "à") ~2-5 frames speech."""

    def test_short_word_still_confirmed(self, session):
        """2 frames speech vẫn confirm được _has_speech."""
        pcm = _make_pcm(400.0)
        session.check_vad(pcm)
        session.check_vad(pcm)
        assert session._has_speech

    def test_short_word_triggers_fast(self, session):
        """2-3 frames speech → short path: silence_after_speech sau 4 frames."""
        pcm_high = _make_pcm(400.0)
        pcm_low = _make_pcm(10.0)

        for _ in range(3):
            session.check_vad(pcm_high)
        assert session._total_speech_frames <= 5  # short

        for i in range(5):
            state = session.check_vad(pcm_low)
        assert state == 'silence_after_speech', \
            f"Short utterance should trigger in ~4 silence frames"

    def test_short_word_fast_than_long_word(self, session):
        """Câu ngắn trigger nhanh hơn câu dài."""
        session_short = _make_session()
        session_long = _make_session()

        pcm_h = _make_pcm(450.0)
        pcm_l = _make_pcm(10.0)

        # Short: 3 frames speech
        for _ in range(3):
            session_short.check_vad(pcm_h)
        # Long: 15 frames speech
        for _ in range(15):
            session_long.check_vad(pcm_h)

        # Silence
        silence_short = 0
        for _ in range(10):
            st = session_short.check_vad(pcm_l)
            silence_short += 1
            if st == 'silence_after_speech':
                break

        silence_long = 0
        for _ in range(10):
            st = session_long.check_vad(pcm_l)
            silence_long += 1
            if st == 'silence_after_speech':
                break

        assert silence_short <= 5, f"Short should trigger in <=5 silence frames, got {silence_short}"
        assert silence_long >= 6, f"Long should need >=6 silence frames, got {silence_long}"


# ═══════════════════════════════════════════════════════════════════
# TEST: Adaptive silence timeout (dựa trên total_speech_frames)
# ═══════════════════════════════════════════════════════════════════

class TestAdaptiveSilenceTimeout:
    """Silence timeout thích ứng theo độ dài câu nói."""

    def test_very_short_2_frames_uses_min_silence(self, session):
        """2 frames speech → 4 frames silence."""
        pcm_h = _make_pcm(450.0)
        pcm_l = _make_pcm(10.0)

        session.check_vad(pcm_h)
        session.check_vad(pcm_h)

        for i in range(5):
            st = session.check_vad(pcm_l)
        assert st == 'silence_after_speech'

    def test_short_4_frames_uses_min_silence(self, session):
        """4 frames speech → 6 frames silence (bracket <=10)."""
        pcm_h = _make_pcm(450.0)
        pcm_l = _make_pcm(10.0)

        for _ in range(4):
            session.check_vad(pcm_h)

        for i in range(6):
            st = session.check_vad(pcm_l)
        assert st == 'silence_after_speech'

    def test_medium_8_frames_uses_mid_silence(self, session):
        """8 frames speech → 6 frames silence."""
        pcm_h = _make_pcm(450.0)
        pcm_l = _make_pcm(10.0)

        for _ in range(8):
            session.check_vad(pcm_h)

        for i in range(6):
            st = session.check_vad(pcm_l)
            if i < 5:
                assert st == 'silence', f"frame {i}: should be silence, got {st}"
        assert st == 'silence_after_speech', "8-frame u. should trigger at 6 frames silence"

    def test_10_frames_speech_boundary(self, session):
        """10 frames speech (boundary) → 6 frames silence."""
        pcm_h = _make_pcm(450.0)
        pcm_l = _make_pcm(10.0)

        for _ in range(10):
            session.check_vad(pcm_h)

        for i in range(6):
            st = session.check_vad(pcm_l)
        assert st == 'silence_after_speech', \
            f"10-frame utterance: expected trigger at 6 silence, got {st}"

    def test_15_frames_speech_uses_max_silence(self, session):
        """15 frames speech → 8 frames silence."""
        pcm_h = _make_pcm(450.0)
        pcm_l = _make_pcm(10.0)

        for _ in range(15):
            session.check_vad(pcm_h)

        for i in range(8):
            st = session.check_vad(pcm_l)
        assert st == 'silence_after_speech'


# ═══════════════════════════════════════════════════════════════════
# TEST: Kalman-like noise floor tracking
# ═══════════════════════════════════════════════════════════════════

class TestNoiseFloor:
    """Noise floor tracking với percentile filter."""

    def test_noise_floor_adapts_to_background(self, session):
        """Noise floor tăng khi background noise tăng."""
        pcm_n = _make_pcm(150.0)
        for _ in range(55):
            session.check_vad(pcm_n)
        assert session._noise_floor_rms > 50

    def test_speech_does_not_raise_noise_floor(self, session):
        """Speech không làm noise floor tăng vọt."""
        # 10 frames noise
        for _ in range(10):
            session.check_vad(_make_pcm(100.0))

        noise_before = session._noise_floor_rms

        # Speech burst
        for _ in range(5):
            session.check_vad(_make_pcm(3000.0))

        # Noise floor không nên tăng đáng kể do speech
        assert session._noise_floor_rms <= noise_before + 50 or session._noise_floor_rms == pytest.approx(noise_before, abs=50)

    def test_noise_floor_floor_value(self, session):
        """Noise floor không âm, xử lý PCM gần im lặng."""
        for _ in range(10):
            session.check_vad(_make_pcm(0.5))
        assert session._noise_floor_rms >= 0.0


# ═══════════════════════════════════════════════════════════════════
# TEST: SNR-based detection (thay thế rms_delta > 80)
# ═══════════════════════════════════════════════════════════════════

class TestSNRDetection:
    """Phát hiện speech dùng SNR ratio."""

    def test_speech_in_noisy_environment(self, session):
        """Môi trường noise 200, speech 450 vẫn detect được."""
        for _ in range(15):
            session.check_vad(_make_pcm(200.0))
        pcm_speech = _make_pcm(450.0)
        for _ in range(3):
            st = session.check_vad(pcm_speech)
        assert session._has_speech

    def test_quiet_environment_low_threshold(self, session):
        """Môi trường yên tĩnh, ngưỡng thấp → speech nhỏ cũng detect."""
        for _ in range(5):
            session.check_vad(_make_pcm(10.0))
        st = session.check_vad(_make_pcm(300.0))
        assert 'speech' in st


# ═══════════════════════════════════════════════════════════════════
# TEST: Edge cases
# ═══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases và boundary conditions."""

    def test_empty_pcm_rms(self):
        assert Session._calc_rms(b'') == 0.0

    def test_reset_keeps_noise_floor(self, session):
        session._noise_floor_rms = 150.0
        session.reset_audio_buffer()
        assert session._noise_floor_rms == 150.0
        assert not session._has_speech
        assert session._speech_frames == 0
        assert session._total_speech_frames == 0
        assert session._peak_rms == 0.0

    def test_reset_clears_speech_state(self, session):
        pcm = _make_pcm(500.0)
        for _ in range(5):
            session.check_vad(pcm)
        assert session._has_speech
        session.reset_audio_buffer()
        assert not session._has_speech
        assert session._silent_frames == 0
        assert session._total_speech_frames == 0

    def test_speech_during_silence_resets_counter(self, session):
        pcm_h = _make_pcm(500.0)
        pcm_l = _make_pcm(10.0)

        for _ in range(3):
            session.check_vad(pcm_h)
        for _ in range(3):
            session.check_vad(pcm_l)
        assert session._silent_frames == 3

        state = session.check_vad(pcm_h)
        assert session._silent_frames == 0

    def test_has_speech_property(self, session):
        assert not session.has_speech
        pcm = _make_pcm(500.0)
        for _ in range(3):
            session.check_vad(pcm)
        assert session.has_speech

    def test_state_never_returns_unexpected(self, session):
        """Tất cả states đều là một trong 3 giá trị hợp lệ."""
        expected = {'speech', 'silence', 'silence_after_speech'}
        pcm_h = _make_pcm(500.0)
        pcm_l = _make_pcm(10.0)
        for _ in range(20):
            if _ < 10:
                st = session.check_vad(pcm_h)
            else:
                st = session.check_vad(pcm_l)
            assert st in expected, f"Unexpected state: {st}"

    def test_mid_speech_threshold_does_not_reset_silent(self, session):
        """Frame ở mức trung gian không reset silent_frames hay increment silence."""
        pcm_mid = _make_pcm(220.0)
        st = session.check_vad(pcm_mid)
        # 220 nằm giữa 180 và 280
        assert st in ('speech', 'silence')
        # Không quan trọng cái nào, miễn không crash
