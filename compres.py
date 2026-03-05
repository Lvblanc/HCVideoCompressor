#!/data/data/com.termux/files/usr/bin/env python3
import os
import subprocess
import shutil
import sys
import time
import re
import json

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) # script location folder
OUTPUT_DIR = os.path.join(BASE_DIR, "Compress")
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm', '.ts', '.m4v')

def check_dependencies():
    for tool in ["ffmpeg", "ffprobe"]:
        if not shutil.which(tool):
            print(f"[!] Error: {tool} not found. Install on Termux with pkg install {tool}")
            sys.exit(1)

def get_video_info(filepath):
    cmd = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json', 
        '-show_format', '-show_streams', filepath
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        video_stream = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), None)
        format_info = data.get('format', {})

        if not video_stream:
            return None

        bitrate = int(video_stream.get('bit_rate') or format_info.get('bit_rate') or 0)
        duration = float(video_stream.get('duration') or format_info.get('duration') or 0)
        width = int(video_stream.get('width', 0))
        height = int(video_stream.get('height', 0))

        return {"bitrate": bitrate, "duration": duration, "width": width, "height": height}
    except Exception as e:
        print(f"[!] Error reading video info: {e}")
        return None

def get_smart_bitrate(width, height, orig_bitrate, mode_name, resized):
    eff_w = 1280 if resized else width
    eff_h = 720 if resized else height
    pixels = eff_w * eff_h

    if pixels <= 640 * 360:
        target = 800_000
    elif pixels <= 854 * 480:
        target = 1_200_000
    elif pixels <= 1280 * 720:
        target = 2_200_000
    elif pixels <= 1920 * 1080:
        target = 4_000_000
    else:
        target = 10_000_000

    if mode_name == "FAST": target = int(target * 1.3)
    elif mode_name == "QUALITY": target = int(target * 0.7)

    if orig_bitrate > 0:
        ratio = 0.7 if resized else 0.85
        target = min(target, int(orig_bitrate * ratio))

    return target

def compress_video(input_path, output_path, info, mode_cfg, downscale):
    total_duration = info["duration"]
    target_bitrate = get_smart_bitrate(info["width"], info["height"], info["bitrate"], mode_cfg["name"], downscale)

    cmd = [
        'ffmpeg', '-i', input_path,
        '-c:v', 'h264_mediacodec',
        '-b:v', str(target_bitrate),
        '-maxrate', str(int(target_bitrate * 1.5)),
        '-bufsize', str(int(target_bitrate * 2.0)),
        '-pix_fmt', 'yuv420p',
    ]

    if downscaled:
        cmd += ['-vf', 'scale=-2:720']

    cmd += mode_cfg['extra_params']
    cmd += [
        '-c:a', 'aac', '-b:a', '128k', '-ac', '2',
        '-movflags', '+faststart', '-y', output_path
    ]

    print(f"\n[PROCESS] {os.path.basename(input_path)}")
    if downscale: print("[*] Downscaling to 720p...")

    start_time = time.time()
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    
    try:
        for line in process.stdout:
            time_match = re.search(r"time=(\d+):(\d+):([\d\.]+)", line)
            if time_match and total_duration > 0:
                h, m, s = int(time_match.group(1)), int(time_match.group(2)), float(time_match.group(3))
                curr = h * 3600 + m * 60 + s
                pct = min((curr / total_duration) * 100, 100)
                elap = time. time() - start_time
                eta = (elap / curr) * (total_duration - curr) if curr > 0 else 0
                bar = '█' * int(20 * pct // 100) + '░' * (20 - int(20 * pct // 100))
                sys.stdout.write(f"\r{bar} {pct:>5.1f}% | ETA: {time.strftime('%M:%S', time.gmtime(eta))}")
                sys.stdout.flush()
    except Exception: pass

    process.wait()
    return process.returncode == 0

def main():
    check_dependencies()
    print("\n--- SMART HARDWARE COMPRESSOR (TERMUX) ---")
    print("1. FAST | 2. MEDIUM (Default) | 3. QUALITY")
    select = input("Select Mode (1-3): ") or "2"
    
    modes = {
        "1": {"name": "FAST", "extra_params": ['-profile:v', 'baseline']},
        "2": {"name": "MEDIUM", "extra_params": ['-profile:v', 'main']},
        "3": {"name": "QUALITY", "extra_params": ['-profile:v', 'high']}
    }
    mode_cfg = modes.get(select, modes["2"])

    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
    
    files = [f for f in os.listdir(BASE_DIR) if f.lower().endswith(VIDEO_EXTENSIONS)]
    if not files:
        print("[-] There are no video files in the script folder:", BASE_DIR)
        returns

    for filename in files:
        in_f = os.path.join(BASE_DIR, filename)
        out_f = os.path.join(OUTPUT_DIR, f"{os.path.splitext(filename)[0]}.mp4")

        if os.path.exists(out_f):
            print(f"[-] Skip {filename}, output file already exists.")
            continue

        info = get_video_info(in_f)
        if not info or info["duration"] == 0:
            print(f"[!] Failed to read: {filename}")
            continue

        do_downscale = False
        if info['width'] > 1280 or info['height'] > 720:
            print(f"\n[?] High resolution file '{filename}' ({info['width']}x{info['height']})")
            ask = input("Lower to 720p? (y/n): ").lower()
            if ask == 'y':
                do_downscale = True

        if compress_video(in_f, out_f, info, mode_cfg, do_downscale):
            old_sz, new_sz = os.path.getsize(in_f), os.path.getsize(out_f)
            diff = (old_sz - new_sz) / old_sz * 100
            print(f"\n[V] FINISH: {old_sz/1048576:.1f}MB -> {new_sz/1048576:.1f}MB (-{diff:.1f}%)")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Canceled."); sys.exit(0)