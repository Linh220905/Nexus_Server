"""
Cấu hình tập trung cho toàn bộ server.

Đọc từ biến môi trường, có giá trị mặc định cho dev.
Sửa file này để đổi provider STT/LLM/TTS.
"""

import os
from pathlib import Path
from pydantic import BaseModel
from .prompt_store import SYSTEM_PROMPT


_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class AudioInputConfig(BaseModel):
    """Audio ESP32 gửi lên: Opus 16kHz mono 60ms."""
    sample_rate: int = 16000
    channels: int = 1
    frame_duration_ms: int = 60

    @property
    def frame_size(self) -> int:
        """Số samples trong 1 frame: 16000 * 60 / 1000 = 960."""
        return self.sample_rate * self.frame_duration_ms // 1000


class AudioOutputConfig(BaseModel):
    """Audio server gửi về ESP32: Opus 24kHz mono 60ms."""
    sample_rate: int = 24000
    channels: int = 1
    frame_duration_ms: int = 60
    opus_bitrate: int = int(os.environ.get("AUDIO_OUTPUT_OPUS_BITRATE", "48000"))

    @property
    def frame_size(self) -> int:
        """Số samples trong 1 frame: 24000 * 60 / 1000 = 1440."""
        return self.sample_rate * self.frame_duration_ms // 1000


class LLMProviderConfig(BaseModel):
    """Config cho 1 LLM provider."""
    name: str = ""  
    api_key: str = ""
    base_url: str = ""
    model: str = ""


class LLMConfig(BaseModel):
    """LLM config với fallback — thử lần lượt từng provider."""
    providers: list[LLMProviderConfig] = []
    max_tokens: int = 500
    temperature: float = 0.7
    system_prompt: str = SYSTEM_PROMPT

    @classmethod
    def from_env(
        cls,
        *,
        providers_env: str = "LLM_PROVIDERS",
        default_api_key_env: str = "OPENAI_API_KEY",
        default_base_url_env: str = "OPENAI_BASE_URL",
        default_model_env: str = "OPENAI_LLM_MODEL",
    ) -> "LLMConfig":
        providers = []
        raw = os.environ.get(providers_env, "")
        if raw:
            for entry in raw.split(";"):
                entry = entry.strip()
                if not entry:
                    continue
                parts = entry.split("|")
                if len(parts) >= 3:
                    providers.append(LLMProviderConfig(
                        name=parts[0].strip(),
                        base_url=parts[1].strip(),
                        model=parts[2].strip(),
                        api_key=parts[3].strip() if len(parts) > 3 else os.environ.get(default_api_key_env, ""),
                    ))

        if not providers:
            providers.append(LLMProviderConfig(
                name="default",
                api_key=os.environ.get(default_api_key_env, os.environ.get("OPENAI_API_KEY", "")),
                base_url=os.environ.get(default_base_url_env, os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8045/v1")),
                model=os.environ.get(default_model_env, os.environ.get("OPENAI_LLM_MODEL", "claude-sonnet-4-5")),
            ))
        return cls(
            providers=providers,
            max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "500")),
            temperature=float(os.environ.get("LLM_TEMPERATURE", "0.7")),
        )


class OpenAIConfig(BaseModel):
    """Legacy — chỉ còn dùng cho STT nếu cần."""
    api_key: str = os.environ.get("OPENAI_API_KEY", "")
    base_url: str = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8045/v1")
    stt_model: str = "whisper-1"


class STTConfig(BaseModel):
    """STT config — mặc định dùng Groq Whisper."""
    provider: str = "groq" 
    api_key: str = os.environ.get("GROQ_API_KEY", "")
    base_url: str = "https://api.groq.com/openai/v1"
    model: str = "whisper-large-v3-turbo" 

    language: str = ""  # Rỗng = auto-detect (hỗ trợ cả tiếng Việt + tiếng Anh)



class TTSConfig(BaseModel):
    provider: str = os.environ.get("TTS_PROVIDER", "google")

    # === Google Cloud TTS (primary) ===
    google_tts_api_key: str = os.environ.get("GOOGLE_TTS_API_KEY", "")
    google_tts_voice: str = os.environ.get("GOOGLE_TTS_VOICE", "vi-VN-Neural2-A")
    google_tts_language: str = os.environ.get("GOOGLE_TTS_LANGUAGE", "vi-VN")
    google_tts_voice_vi: str = os.environ.get("GOOGLE_TTS_VOICE_VI", "vi-VN-Neural2-A")
    google_tts_voice_en: str = os.environ.get("GOOGLE_TTS_VOICE_EN", "en-US-Neural2-F")
    google_tts_language_vi: str = os.environ.get("GOOGLE_TTS_LANGUAGE_VI", "vi-VN")
    google_tts_language_en: str = os.environ.get("GOOGLE_TTS_LANGUAGE_EN", "en-US")

    # === Microsoft Edge TTS (backup / optional) ===
    edge_tts_voice_vi: str = os.environ.get("EDGE_TTS_VOICE_VI", "vi-VN-HoaiMyNeural")
    edge_tts_voice_en: str = os.environ.get("EDGE_TTS_VOICE_EN", "en-US-JennyNeural")
    edge_tts_rate_vi: str = os.environ.get("EDGE_TTS_RATE_VI", "+0%")
    edge_tts_rate_en: str = os.environ.get("EDGE_TTS_RATE_EN", "+0%")
    edge_tts_pitch_vi: str = os.environ.get("EDGE_TTS_PITCH_VI", "+0Hz")
    edge_tts_pitch_en: str = os.environ.get("EDGE_TTS_PITCH_EN", "+0Hz")

    # Ràng buộc ngôn ngữ mặc định cho robot: vi | en | auto
    language: str = os.environ.get("TTS_LANGUAGE", "auto")

    speed: float = float(os.environ.get("TTS_SPEED", "1.0"))
    voice_style: str = os.environ.get("TTS_VOICE_STYLE", "normal")
    volume_gain_db: float = float(os.environ.get("TTS_VOLUME_GAIN_DB", "6.0"))
    post_gain_db: float = float(os.environ.get("TTS_POST_GAIN_DB", "8.0"))
    target_rms: float = float(os.environ.get("TTS_TARGET_RMS", "9500"))
    max_peak: int = int(os.environ.get("TTS_MAX_PEAK", "30000"))
    max_boost_db: float = float(os.environ.get("TTS_MAX_BOOST_DB", "18.0"))
    compressor_threshold: float = float(os.environ.get("TTS_COMPRESSOR_THRESHOLD", "0.70"))
    compressor_ratio: float = float(os.environ.get("TTS_COMPRESSOR_RATIO", "3.0"))
    post_makeup_db: float = float(os.environ.get("TTS_POST_MAKEUP_DB", "10.0"))
    softclip_drive: float = float(os.environ.get("TTS_SOFTCLIP_DRIVE", "1.6"))
    enable_post_loudness: bool = os.environ.get("TTS_ENABLE_POST_LOUDNESS", "true").strip().lower() in {"1", "true", "yes", "on"}
    log_audio_stats: bool = os.environ.get("TTS_LOG_AUDIO_STATS", "false").strip().lower() in {"1", "true", "yes", "on"}

    # === Piper TTS backup config (không dùng nữa, giữ lại để tham khảo) ===
    audio_profile: str = os.environ.get("TTS_AUDIO_PROFILE", "small-bluetooth-speaker-class-device")

    # model_path: str = os.environ.get("TTS_MODEL_PATH", "models/vi_VN-vais1000-medium.onnx")
    # speaker_id: int | None = int(os.environ["TTS_SPEAKER_ID"]) if os.environ.get("TTS_SPEAKER_ID") else None


class AppConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    audio_input: AudioInputConfig = AudioInputConfig()
    audio_output: AudioOutputConfig = AudioOutputConfig()
    openai: OpenAIConfig = OpenAIConfig()
    llm: LLMConfig = LLMConfig()
    intent_llm: LLMConfig = LLMConfig()
    stt: STTConfig = STTConfig()
    tts: TTSConfig = TTSConfig()
    max_chat_history: int = 20


_intent_provider_env = os.environ.get("INTENT_LLM_PROVIDERS", "").strip()
if _intent_provider_env:
    _intent_llm_cfg = LLMConfig.from_env(
        providers_env="INTENT_LLM_PROVIDERS",
        default_api_key_env="INTENT_LLM_API_KEY",
        default_base_url_env="INTENT_LLM_BASE_URL",
        default_model_env="INTENT_LLM_MODEL",
    )
else:
    # Mặc định dùng cùng provider chain với LLM chính để tránh rơi về endpoint local không tương thích.
    _intent_llm_cfg = LLMConfig.from_env()

config = AppConfig(
    llm=LLMConfig.from_env(),
    intent_llm=_intent_llm_cfg,
)
