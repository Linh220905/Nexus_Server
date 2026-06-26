# Nexus — Custom Server cho ESP32 Voice AI

## Tổng quan
Nền tảng voice AI thời gian thực, kết nối ESP32 với AI models. Pipeline: **ESP32 Audio → WebSocket → STT (Whisper) → Intent Detection → LLM → TTS (Google/Edge) → WebSocket → ESP32**.

- Python 3.11+, FastAPI, SQLite, WebSocket
- Domain: nexus.tanlinh.dev
- Entry point: `python run.py` (uvicorn)

## Cấu trúc thư mục

```
custom_server_xiaozhi/
├── run.py                    # Entry point
├── Dockerfile
├── requirements.txt
├── .env                      # Config (API keys, secrets)
│
├── app/
│   ├── main.py               # FastAPI app, middleware, routers, startup
│   ├── config.py             # Config trung tâm (đọc .env)
│   ├── models.py             # Pydantic models cho WebSocket protocol
│   ├── prompt_store.py       # Tất cả system prompts
│   ├── server_logging.py     # Logging setup
│   │
│   ├── api/                  # REST API endpoints
│   │   ├── routes.py         # Router hierarchy, health, sessions, learning, flashcards
│   │   ├── robot_api.py      # Robot CRUD, OTP claim
│   │   ├── auth.py           # Local auth (register/login)
│   │   ├── auth_google.py    # Google OAuth2, session tokens
│   │   ├── session_utils.py  # Cookie helpers
│   │   ├── otp.py
│   │   ├── orders.py         # Pre-orders từ landing page
│   │   ├── ota.py / ota_activate.py
│   │   ├── traffic.py
│   │   └── admin/            # Admin endpoints
│   │
│   ├── websocket/            # WebSocket xử lý realtime voice
│   │   ├── handler.py        # Main handler: VAD, audio frames, pipeline trigger
│   │   └── session.py        # Session state, audio buffer, VAD algorithm
│   │
│   ├── services/             # AI pipeline services
│   │   ├── pipeline.py       # ConversationPipeline (orchestrator)
│   │   ├── stt.py            # STTService (Groq/OpenAI Whisper)
│   │   ├── llm.py            # LLMService (multi-provider fallback)
│   │   ├── tts.py            # TTSService (Google Cloud TTS + Edge TTS)
│   │   ├── intent.py         # IntentDetectorService (rule + LLM)
│   │   ├── teaching_content.py # TeachingContentService (YAML lessons)
│   │   ├── adaptive_teaching.py # AdaptiveTeachingEngine (lesson plans)
│   │   ├── flashcard_vocab.py # Flashcard vocabulary evaluation
│   │   ├── learning_content.py # Vocab topics, conversation topics, A1 roadmap
│   │   ├── story_engine.py   # NEW: Cốt truyện Nexus Planet, lands, NPCs
│   │   ├── game_profile.py   # NEW: XP, level, badges, quest tracking
│   │   └── teacher_proactive.py # NEW: Proactive teaching, mini-games
│   │
│   ├── database/             # Database layer
│   │   ├── connection.py     # SQLite connection, init_database, tables
│   │   ├── chat_history.py   # Chat session CRUD
│   │   ├── assignments.py    # Parent assignment CRUD
│   │   ├── traffic_log.py    # Traffic logging
│   │   ├── lesson_progress.py # NEW: Teaching progress persistence
│   │   └── game_profile.py   # NEW: Game profile persistence
│   │
│   ├── auth/                 # Authentication
│   │   ├── models.py         # UserRole, Token
│   │   ├── security.py       # bcrypt, JWT, get_current_user
│   │   ├── crud.py           # User CRUD
│   │   └── schemas.py
│   │
│   ├── robots/               # Robot management
│   │   ├── models.py
│   │   └── crud.py           # Robot CRUD, config, OTP
│   │
│   ├── mcp/                  # MCP tools
│   │   ├── __init__.py
│   │   ├── tools.py          # search_vietnamese_music, set_alarm, set_volume
│   │   └── alarm_scheduler.py # Background alarm monitor
│   │
│   └── audio/                # Audio codec
│       └── opus_codec.py     # OpusDecoder (16kHz) + OpusEncoder (24kHz)
│
├── static/admin/index.html   # Admin dashboard
├── data/                     # SQLite DB + vocab seeds
├── models/                   # ONNX TTS models (Piper backup)
├── lessons_data/             # YAML teaching content + A1 roadmap
└── logs/server.log
```

## Pipeline xử lý chi tiết

### 1. WebSocket Audio Flow (`handler.py`)
1. ESP32 gửi Opus frames (16kHz mono 60ms)
2. Decode → PCM → RMS analysis → VAD
3. VAD adaptive: noise floor tracking, speech/silence thresholds
4. Trigger pipeline khi: high-RMS spike → return to baseline, low-RMS timeout, VAD silence_after_speech, max utterance length (15.6s)
5. Idle timeout 60s → goodbye + websocket close
6. Auto-offline after 300s inactivity

### 2. Pipeline (`pipeline.py`)
1. **STT**: PCM → WAV → Whisper API (Groq/OpenAI)
2. **Routing**: Kiểm tra interaction_mode (teaching, roadmap, flashcard, locked learning)
3. **Intent Detection**: Fast (rule-based) → LLM (deep) cho music, alarm, learning, flashcard, assignment
4. **LLM Streaming**: Multi-provider fallback → sentence extraction → TTS pre-fetch queue
5. **TTS**: Sentence → SSML → Google/Edge TTS → PCM → Opus frames → gửi về ESP32
6. **Post-processing**: Lưu chat history, reset state

### 3. VAD Algorithm (`session.py`)
- Adaptive noise floor (EMA)
- Dynamic speech threshold = noise_floor * 1.18 + 120
- Dynamic silence threshold = noise_floor * 1.08 + 60
- Speech confirmed sau 8 frames vượt speech threshold
- silence_after_speech sau 10 frames dưới silence threshold

### 4. Các trigger voice khác (`handler.py`)
- **High-RMS**: RMS > baseline + 3000 trong 5 frames, rồi RMS ≤ baseline + adaptive_margin trong 8 frames
- **Low-RMS**: Sau khi có speech, RMS < dynamic_low_rms_threshold trong 90 frames
- **Max utterance**: 260 frames (~15.6s)
- **Idle timeout**: 1000 frames (~60s) không có speech

## Key Config (`config.py`)
| Module | Provider | Model |
|--------|----------|-------|
| STT | Groq | whisper-large-v3-turbo |
| LLM | Groq → Claude → Gemini (fallback chain) | llama-3.3-70b / claude-sonnet-4-5 / gemini-3-pro |
| TTS | Google Cloud (primary) + Edge TTS (backup) | vi-VN-Neural2-A / en-US-Neural2-F |

## API Endpoints
| Route | Description |
|-------|-------------|
| `WS /` | WebSocket voice |
| `/api/health` | Health check |
| `/api/sessions` | List active sessions |
| `/api/robots/` | Robot CRUD + config + OTP claim |
| `/api/v1/auth/*` | Local auth |
| `/api/auth/*` | Google OAuth2 |
| `/api/chat-history` | Chat history |
| `/api/learning/*` | Learning topics, roadmap, flashcard images |
| `/api/assignments/*` | Parent assignments CRUD |
| `/api/ota/*` | OTA firmware |
| `/api/traffic/*` | Traffic logs |
| `/api/mcp/*` | MCP tools listing |
| `/dashboard/` | Admin panel |
| `/auth/google-login` | Google login |

## WebSocket Protocol
- **Client → Server**: `hello`, `listen` (start/stop/detect), `abort`, `mcp` (tools/list, tools/call)
- **Server → Client**: `hello` (session_id), `stt` (text), `tts` (start/stop/sentence_start + audio frames), `llm` (emotion), `learning` (flashcard data), `mcp` (tool result)
- **Audio**: Opus frames, binary over WebSocket

## Database Tables
- `users` — auth, roles (admin/user/viewer)
- `robots` — devices, config (JSON blob), OTP
- `chat_sessions` — conversation history
- `assignments` — parent-assigned tasks
- `vocab_topics` / `vocab_words` — vocabulary data
- `orders` — customer pre-orders
- `traffic_logs` — HTTP traffic logging

## Teaching Modes
1. **Teaching Mode** (YAML-based): Adaptive lessons with structured steps (intro → present → repeat → assess → summary)
2. **Roadmap Learning** (A1 roadmap): Sequential lessons from JSON roadmap
3. **Flashcard Vocabulary**: Physical flashcard evaluation with LLM
4. **Conversation Practice**: Scripted dialogues (travel, work, food, etc.)
5. **Topic Vocabulary**: Step-by-step word teaching with image flashcards

## NEW: Proactive Teaching & Gamification (Phase 1-4)

### Story Engine (`story_engine.py`)
- Cốt truyện xuyên suốt: Nexus từ Planet of Languages, viên năng lượng vỡ ra
- 8 vùng đất (lands): Greeting Grove → Family Mountain → Rainbow Falls → Animal Kingdom → Morning Valley → Candy Island → Mirror Land → Nexus HQ
- Mỗi land có NPC, intro, farewell dialogue
- Map từ A1 roadmap modules

### Game Profile (`game_profile.py`)
- **XP**: +10 trả lời đúng, +20 first try, +5 streak bonus (≥3), +50 complete lesson, +100 complete quest
- **Level**: 100 XP/level (max 50). Level 5/10/15/20/25 → +1 Energy Gem
- **Badges**: 🌟 First Words, 🔥 On Fire, ⚡ Fast Learner, 💎 Gem Collector, 🏆 Champion, 🗺️ Explorer, 🦸 Language Hero
- **Streak**: Đúng liên tiếp ≥3 → bonus. Reset khi sai

### Proactive Teacher (`teacher_proactive.py`)
- **Proactive triggers**: Session start → auto open lesson. Pipeline end → auto continue. Idle in teaching → keep engaged
- **Mini-games**: I Spy, Simon Says, Mystery Box, Roleplay Challenge (dùng LLM với `GAME_MASTER_PROMPT`)
- **Evaluation**: LLM-based pronunciation check + XP reward
- **Persistence**: `lesson_progress` + `game_profiles` tables, save/load per robot

### Prompts (`prompt_store.py`)
- `TEACHING_SYSTEM_PROMPT`: Story-driven Game Master persona
- `GAME_MASTER_PROMPT`: Mini-game instructions
- Cả hai đều format JSON `{"language","emotion","text"}`

### Reward System (`handler.py`)
- **SFX**: Tự động generate WAV → Opus frames (xp.wav, levelup.wav, badge.wav, correct.wav)
- **Cache**: Load + encode tại module init (`SFX_CACHE`)
- **Trigger**: Gửi `_send_reward()` → Opus frames qua WebSocket + JSON `type: learning, state: award`
- **Image**: Hiển thị `award_320.png` kèm mỗi reward
- **Flow pipeline**: detect XP/Level/Badge change sau teaching → reward callbacks
- **SFX files**: `static/asset/sfx/*.wav` (gitignored, auto-generate)
- **Reward map**:
  - `correct` → ding + award image mỗi 3 streak
  - `xp` → bright chime
  - `levelup` → ascending tone + award image
  - `badge` → longer chime + award image

## MCP Tools
- `search_vietnamese_music` — Deezer API search
- `set_alarm` — Save to alarms.json, background scheduler
- `set_volume` — Validation only (TODO: ESP32 integration)
- `set_brightness` — Stub (TODO)
- `reboot` — Stub (TODO)

## Task Progress
Hiện đang trong **Phase 1 — Backend Core** của task "Refactor Config Robot":
1. [ ] Migration DB: thêm bảng `robot_configs` trong `connection.py`
2. [ ] Model: thêm `RobotConfigCategory`, `RobotConfigItem` trong `models.py`
3. [ ] CRUD: viết CRUD config items trong `crud.py`
4. [ ] API Routes: thêm endpoints config mới trong `robot_api.py`
5. [ ] WebSocket: push config update event trong `handler.py`
