# HCVideoCompressor

🎬 **Smart, fast, and easy video compressor for Android Termux!**  
Compress and downscale your videos using **MediaCodec hardware acceleration** directly on your device. Perfect for saving storage without losing quality.

---

## ✨ Features

- Automatically compress videos in the same folder as the script.
- Optional downscale to 720p while preserving aspect ratio.
- Choose compression mode: **FAST**, **MEDIUM**, **QUALITY**.
- Hardware-accelerated H.264 encoding (`h264_mediacodec`).
- Outputs saved in a `Compres` folder inside the script directory.
- Fully batch processing friendly.
- Supports `.mp4`, `.mkv`, `.avi`, `.mov`, `.flv`, `.wmv`, `.webm`, `.ts`, `.m4v`.

---

## ⚙️ Requirements

- **Termux**  
- **Python 3** (already included in Termux):  
```bash
pkg install python
