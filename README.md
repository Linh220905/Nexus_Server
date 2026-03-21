
# custom_server_nexus  
### Custom AI Server for Nexus ESP32 Client

> A real-time voice interaction backend designed for Nexus ESP32 devices.  
> Supports low-latency streaming STT → LLM → TTS pipeline.

---

## Overview


`custom_server_nexus` là AI backend server dùng để giao tiếp với client ESP32 (nexus).

Server thực hiện:

- Nhận audio stream từ ESP32 qua WebSocket
- Chuyển giọng nói → văn bản (Speech-to-Text)
- Xử lý hội thoại bằng LLM (streaming response)
- Chuyển văn bản → giọng nói (Text-to-Speech)
- Encode Opus và stream trả về ESP32 theo thời gian thực

Hệ thống được thiết kế tối ưu cho:

- Low latency (1–2 giây phản hồi)
- Streaming pipeline
- Dễ thay thế STT / LLM / TTS provider
- Tương thích với Nexus firmware

---

## 🏗 Architecture Overview

<p align="center">
  <img src="flow.png" width="900"/>
</p>

### Processing Flow

1. ESP32 gửi audio (Opus/PCM) qua WebSocket  
2. Server decode và gom PCM buffer  
3. STT chuyển audio → text  
4. Intent detection (fast path nếu cần)  
5. LLM sinh phản hồi (streaming theo câu)  
6. TTS tổng hợp từng câu  
7. Encode Opus và stream trả audio về ESP32  

---

## Requirements

- Python 3.10+
- Các gói trong `requirements.txt`
- `ffmpeg` (nếu TTS trả MP3 cần convert → PCM)
- `libopus` (nếu dùng `opuslib`)

---

## Quickstart

```bash
cd custom_server_nexus
pip install -r requirements.txt
python run.py