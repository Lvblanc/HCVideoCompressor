#!/data/data/com.termux/files/usr/bin/env python3
"""
Smart Video Compressor — Termux Edition
h264_mediacodec · HW Accelerated · CRF SW Fallback
HW-Calibrated Quality · Custom Bitrate · Custom FPS
Wakelock · Auto MP4 · Auto-Remux · Post-Encode Size Guard
"""

import os, subprocess, shutil, sys, time, re, json, datetime, contextlib

# ══════════════════════════════════════════════════════════════════════════════
#  BOOTSTRAP
# ══════════════════════════════════════════════════════════════════════════════
def _bootstrap():
    try:
        import rich  # noqa: F401
    except ImportError:
        print("\n  Installing 'rich' …")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "rich"],
            capture_output=True,
        )
        if r.returncode != 0:
            subprocess.run(
                [sys.executable, "-m", "pip", "install",
                 "--quiet", "--break-system-packages", "rich"],
                check=True,
            )
        print("  Done.\n")

_bootstrap()

from rich.console  import Console, Group
from rich.panel    import Panel
from rich.table    import Table
from rich.prompt   import Prompt, Confirm
from rich.text     import Text
from rich.rule     import Rule
from rich.align    import Align
from rich.live     import Live
from rich.padding  import Padding
from rich.markup   import escape
from rich          import box
from rich.progress import Progress, TextColumn, SpinnerColumn

console = Console(highlight=False)

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR    = os.path.join(BASE_DIR, "Compress")
LOG_DIR       = os.path.join(OUTPUT_DIR, "logs")
VIDEO_EXT     = ('.mp4', '.mkv', '.avi', '.mov', '.flv',
                 '.wmv', '.webm', '.ts', '.m4v', '.3gp', '.rm', '.rmvb')
HW_ENCODER    = "h264_mediacodec"
SW_ENCODER    = "libx264"
AUDIO_COPY_BR = 160_000   # copy AAC stream if bitrate ≤ this (bps)
PROBE_TIMEOUT = 25        # ffprobe timeout (seconds)
MIN_SAVINGS   = 10        # % — skip if estimated savings below this
MAX_LOG_LINES = 30        # ffmpeg stderr lines kept on failure

# Common FPS presets offered in the FPS prompt
FPS_PRESETS = [23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0, 120.0]

# ══════════════════════════════════════════════════════════════════════════════
#  COLORS
# ══════════════════════════════════════════════════════════════════════════════
CA  = "cyan"
CW  = "bright_white"
CM  = "#cccccc"
CD  = "#999999"
CF  = "#555555"
COK = "bright_green"
CWN = "#e07b00"
CER = "bright_red"
CIN = "#b57bee"

# ══════════════════════════════════════════════════════════════════════════════
#  FORMAT HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def fmt_size(b: int) -> str:
    if b <= 0:    return "0 B"
    if b < 1<<20: return f"{b/1024:.1f} KB"
    if b < 1<<30: return f"{b/1_048_576:.1f} MB"
    return f"{b/1_073_741_824:.2f} GB"

def fmt_dur(s: float) -> str:
    if s <= 0: return "--:--"
    s = int(s); h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def fmt_br(bps: int) -> str:
    if bps <= 0:         return "—"
    if bps >= 1_000_000: return f"{bps / 1_000_000:.2f} Mbps"
    return f"{bps // 1000} kbps"

def fmt_fps(fps: float) -> str:
    """Format FPS cleanly: 23.976 → '23.98', 30.0 → '30', 59.94 → '59.94'"""
    common = {23.976: "23.976", 29.97: "29.97", 59.94: "59.94"}
    if fps in common:
        return common[fps]
    return f"{fps:.0f}" if fps == int(fps) else f"{fps:.2f}"

def trunc(s: str, n: int) -> str:
    return s[:n] + "…" if len(s) > n else s

def parse_bitrate(raw: str) -> int | None:
    """
    Parse human bitrate string → bps int, or None if invalid.
    Accepts:  '1500k', '1500K', '2.5m', '2.5M', '2500', '2500000'
    """
    raw = raw.strip().lower().replace(" ", "")
    if not raw:
        return None
    try:
        if raw.endswith('m'):
            return int(float(raw[:-1]) * 1_000_000)
        if raw.endswith('k'):
            return int(float(raw[:-1]) * 1_000)
        v = int(float(raw))
        # Heuristic: bare numbers < 100000 are assumed kbps
        return v * 1000 if v < 100_000 else v
    except (ValueError, OverflowError):
        return None

def parse_fps(raw: str) -> float | None:
    """Parse fps string → float, or None if invalid / out of range [1, 240]."""
    raw = raw.strip()
    if not raw:
        return None
    # Allow fraction notation: 30000/1001
    if '/' in raw:
        parts = raw.split('/')
        if len(parts) == 2:
            try:
                return round(int(parts[0]) / int(parts[1]), 3)
            except (ValueError, ZeroDivisionError):
                return None
    try:
        v = float(raw)
        return v if 1.0 <= v <= 240.0 else None
    except ValueError:
        return None

# ══════════════════════════════════════════════════════════════════════════════
#  UI PRIMITIVES
# ══════════════════════════════════════════════════════════════════════════════
def print_header(clear: bool = True):
    if clear:
        console.clear()
    console.print()
    console.print(Panel(
        Align.center(Text.assemble(
            Text("⚡ SMART VIDEO COMPRESSOR  v7\n", style=f"bold {CA}"),
            Text(
                f"{HW_ENCODER}  ·  CRF SW Fallback  ·  "
                f"HW-Calibrated  ·  Custom Bitrate & FPS",
                style=f"italic {CD}",
            ),
        )),
        border_style=CA, box=box.DOUBLE_EDGE, padding=(1, 4),
    ))
    console.print()

def hrule(label: str = "", color: str | None = None):
    c = color or CA
    console.print(Rule(f"[bold {c}]{label}[/]" if label else "", style=CF))

def ok(m):   console.print(f"  [bold {COK}]✔[/]  {m}")
def warn(m): console.print(f"  [bold {CWN}]⚠[/]  [{CWN}]{m}[/]")
def err(m):  console.print(f"  [bold {CER}]✘[/]  [{CER}]{m}[/]")
def info(m): console.print(f"  [{CIN}]·[/]  [{CM}]{m}[/]")
def skp(m):  console.print(f"  [{CF}]○[/]  [{CD}]{m}[/]")
def remu(m): console.print(f"  [{CA}]⇄[/]  [{CM}]{m}[/]")
def blank(): console.print()

# ══════════════════════════════════════════════════════════════════════════════
#  WAKELOCK
# ══════════════════════════════════════════════════════════════════════════════
@contextlib.contextmanager
def wakelock():
    """Acquire Termux wakelock for entire batch. Silently skips if unavailable."""
    in_termux = os.path.exists("/data/data/com.termux")
    has_lock  = bool(shutil.which("termux-wake-lock"))
    if in_termux and not has_lock:
        warn("termux-wake-lock not found.  Install:  [bold]pkg install termux-tools[/]")
    if has_lock:
        subprocess.run(["termux-wake-lock"], capture_output=True)
        info(f"Wakelock [bold {CW}]ON[/]  — screen will stay active.")
    try:
        yield
    finally:
        if has_lock:
            subprocess.run(["termux-wake-unlock"], capture_output=True)
            info(f"Wakelock [bold {CW}]OFF[/]  — released.")

# ══════════════════════════════════════════════════════════════════════════════
#  DEPENDENCY INSTALLER
# ══════════════════════════════════════════════════════════════════════════════
def install_deps():
    missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    if not missing:
        return
    blank()
    warn(f"Missing tools: [bold {CW}]{', '.join(missing)}[/]")
    blank()
    if not Confirm.ask(f"  [{CA}]Install via pkg now?[/]", default=True, console=console):
        err("ffmpeg is required. Exiting."); sys.exit(1)
    blank(); hrule("Installing ffmpeg"); blank()
    with Progress(SpinnerColumn(style=CA),
                  TextColumn(f"[{CD}]{{task.description}}"),
                  console=console, transient=True) as prog:
        task = prog.add_task("pkg install ffmpeg …", total=None)
        proc = subprocess.Popen(
            ["pkg", "install", "-y", "ffmpeg"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True,
        )
        for line in proc.stdout:
            prog.update(task, description=(line.strip() or "…")[:60])
        proc.wait()
    if proc.returncode != 0:
        err("Installation failed.  Run:  [bold]pkg install ffmpeg[/]"); sys.exit(1)
    for tool in missing:
        if not shutil.which(tool):
            err(f"'{tool}' still missing.  Check PATH."); sys.exit(1)
    ok(f"[bold {CW}]ffmpeg[/] ready."); blank()

# ══════════════════════════════════════════════════════════════════════════════
#  PROBE
# ══════════════════════════════════════════════════════════════════════════════
def probe(path: str) -> dict | None:
    """Return video metadata dict, or None on failure."""
    cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json',
           '-show_format', '-show_streams', path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT)
        if r.returncode != 0:
            return None
        d       = json.loads(r.stdout)
        streams = d.get('streams', [])
        vs      = next((s for s in streams if s.get('codec_type') == 'video'), None)
        aus     = next((s for s in streams if s.get('codec_type') == 'audio'), None)
        fmt     = d.get('format', {})
        if not vs:
            return None
        raw_br = int(vs.get('bit_rate') or 0) or int(fmt.get('bit_rate') or 0)
        dur    = float(vs.get('duration') or fmt.get('duration') or 0)
        try:
            n, dv = vs.get('r_frame_rate', '0/1').split('/')
            fps   = round(int(n) / int(dv), 3) if int(dv) else 0
        except Exception:
            fps   = 0
        return dict(
            bitrate     = raw_br,
            duration    = dur,
            width       = int(vs.get('width',  0)),
            height      = int(vs.get('height', 0)),
            codec       = vs.get('codec_name', '?'),
            fps         = fps,
            audio_codec = aus.get('codec_name', '') if aus else '',
            audio_br    = int(aus.get('bit_rate') or 0) if aus else 0,
            has_audio   = aus is not None,
        )
    except Exception as e:
        err(f"ffprobe error: {e}"); return None

# ══════════════════════════════════════════════════════════════════════════════
#  BITRATE ENGINE
# ══════════════════════════════════════════════════════════════════════════════
# Targets calibrated for h264_mediacodec (HW Android encoder).
# HW encoders are ~30% less efficient than libx264, so ceilings are higher.
_BR_TIERS: list[tuple[int, int]] = [
    (320  * 240,    300_000),
    (640  * 360,    650_000),
    (854  * 480,  1_100_000),
    (1280 * 720,  2_500_000),
    (1920 * 1080, 5_000_000),
    (2560 * 1440, 9_000_000),
    (3840 * 2160, 15_000_000),
]

# CRF for libx264 SW fallback (lower = better quality, larger file)
_CRF: dict[str, int] = {"FAST": 20, "MEDIUM": 18, "QUALITY": 16}

# HW ABR multipliers
_MODE_MULT: dict[str, float] = {"FAST": 1.00, "MEDIUM": 1.00, "QUALITY": 0.78}


def _tier_ceiling(pixels: int) -> int:
    """Linear interpolation between resolution tiers — no hard steps."""
    tiers = _BR_TIERS
    if pixels <= tiers[0][0]:
        return tiers[0][1]
    for i in range(1, len(tiers)):
        px_lo, br_lo = tiers[i - 1]
        px_hi, br_hi = tiers[i]
        if pixels <= px_hi:
            t = (pixels - px_lo) / (px_hi - px_lo)
            return int(br_lo + t * (br_hi - br_lo))
    px_ref, br_ref = tiers[-1]
    return int(br_ref * (pixels / px_ref))


def _complexity_boost(orig_br: int, ceiling: int, pixels: int, fps: float) -> float:
    """
    +0–20% boost for high-complexity content (action, sports, animation).
    Based on source bits-per-pixel-per-frame vs our ceiling reference.
    """
    if orig_br <= 0 or ceiling <= 0 or pixels <= 0 or fps <= 0:
        return 1.0
    src_bpp = orig_br / (pixels * fps)
    ref_bpp = ceiling / (pixels * 30.0)
    if ref_bpp <= 0:
        return 1.0
    ratio = src_bpp / ref_bpp
    if ratio <= 1.5:
        return 1.0
    return 1.0 + min((ratio - 1.5) / 5.0, 0.20)


def calc_target_br(width: int, height: int, orig_br: int,
                   mode: str, fps: float = 30.0,
                   downscale: bool = False) -> int:
    """
    Auto target bitrate (bps). Pipeline:
      1. Pixel-proportional interpolation from _BR_TIERS.
      2. Content complexity boost (+0–20%).
      3. FPS boost for >30fps (+0–80%).  No penalty for <30fps.
      4. Mode multiplier.
      5. Hard cap to orig_br.
      6. Floor 60 kbps.
    """
    w, h   = (1280, 720) if downscale else (width, height)
    pixels = w * h

    ceiling = _tier_ceiling(pixels)
    ceiling = int(ceiling * _complexity_boost(orig_br, ceiling, pixels, max(fps, 1.0)))

    if fps > 30.0:
        fps_factor = min(1.0 + (fps / 30.0 - 1.0) * 0.35, 1.80)
        ceiling    = int(ceiling * fps_factor)

    ceiling = int(ceiling * _MODE_MULT.get(mode, 1.0))

    if orig_br > 0:
        ceiling = min(ceiling, orig_br)

    return max(ceiling, 60_000)


def check_worth(orig_br: int, target_br: int) -> tuple[bool, float]:
    """(should_skip, savings_pct). skip when savings < MIN_SAVINGS."""
    if orig_br <= 0:
        return False, 0.0
    sv = (orig_br - target_br) / orig_br * 100
    return sv < MIN_SAVINGS, max(sv, 0.0)


def est_output_mb(target_br: int, audio_br: int, duration: float) -> float:
    return (target_br + audio_br) * duration / 8 / 1_048_576

# ══════════════════════════════════════════════════════════════════════════════
#  PER-FILE SETTINGS PROMPT  (custom bitrate + custom fps)
# ══════════════════════════════════════════════════════════════════════════════
def ask_per_file_settings(
    filename: str,
    vinfo: dict,
    mode_cfg: dict,
    downscale: bool,
) -> tuple[int | None, float | None]:
    """
    Interactive prompts for custom bitrate and/or custom FPS.

    Returns:
      custom_br   — override bitrate in bps, or None to use auto
      out_fps     — target output FPS, or None to keep source FPS

    Bitrate prompt always shown (user can press Enter for auto).
    FPS prompt shown only when source FPS differs from 30 fps.
    """
    src_fps  = vinfo['fps']
    auto_br  = calc_target_br(
        vinfo['width'], vinfo['height'], vinfo['bitrate'],
        mode_cfg['name'], src_fps, downscale,
    )
    orig_br  = vinfo['bitrate']

    # ── Bitrate prompt ────────────────────────────────────────────────────────
    info(
        f"Auto bitrate:  [bold {CA}]{fmt_br(auto_br)}[/]"
        + (f"  [{CD}](source: {fmt_br(orig_br)})[/]" if orig_br > 0 else "")
    )
    info(
        f"Enter custom or press [bold {CW}]Enter[/] to use auto.  "
        f"[{CD}]Examples: 1500k  2.5m  800  (k=kbps, m=Mbps)[/]"
    )

    custom_br: int | None = None
    while True:
        raw = Prompt.ask(
            f"  [{CA}]Bitrate[/]",
            default="",
            console=console,
            show_default=False,
        ).strip()

        if raw == "":
            info(f"Using auto: [bold {CA}]{fmt_br(auto_br)}[/]")
            break

        parsed = parse_bitrate(raw)
        if parsed is None:
            err(f"Invalid format '{escape(raw)}' — try 1500k, 2.5m, or 800000")
            continue
        if parsed < 60_000:
            err("Bitrate too low (minimum 60 kbps).  Try again.")
            continue
        if orig_br > 0 and parsed > orig_br:
            warn(
                f"[bold]{fmt_br(parsed)}[/] exceeds source bitrate "
                f"[bold]{fmt_br(orig_br)}[/].  "
                f"Clamping to source."
            )
            parsed = orig_br

        custom_br = parsed
        info(
            f"Custom bitrate: [bold {CW}]{fmt_br(custom_br)}[/]"
            + (f"  [{CWN}](overrides auto {fmt_br(auto_br)})[/]"
               if custom_br != auto_br else "")
        )
        break

    # ── FPS prompt (only if source ≠ 30 fps) ─────────────────────────────────
    out_fps: float | None = None
    is_30  = abs(src_fps - 30.0) < 0.1 or abs(src_fps - 29.97) < 0.1

    if not is_30 and src_fps > 0:
        blank()
        info(
            f"Source FPS: [bold {CW}]{fmt_fps(src_fps)}[/]"
            f"  [{CD}](not 30fps)[/]"
        )

        # Build preset menu from FPS_PRESETS, excluding source fps
        presets = [f for f in FPS_PRESETS if abs(f - src_fps) > 0.5]

        # Build compact choice string
        choices_str = "  ".join(
            f"[bold {CW}]{fmt_fps(f)}[/]" for f in presets[:6]
        )
        info(
            f"Convert to:  {choices_str}"
            f"  [bold {CW}]keep[/]  [bold {CW}]custom[/]"
        )
        info(
            f"[{CD}]Reducing FPS (e.g. 60→30) saves extra space.  "
            f"Press Enter to keep {fmt_fps(src_fps)}.[/]"
        )

        while True:
            raw = Prompt.ask(
                f"  [{CA}]Output FPS[/]",
                default="keep",
                console=console,
            ).strip().lower()

            if raw in ("keep", ""):
                info(f"Keeping source FPS: [bold {CW}]{fmt_fps(src_fps)}[/]")
                break

            if raw == "custom":
                raw = Prompt.ask(
                    f"  [{CA}]Enter FPS[/]",
                    console=console,
                ).strip()

            parsed_fps = parse_fps(raw)
            if parsed_fps is None:
                err(f"Invalid FPS '{escape(raw)}' — enter a number between 1 and 240.")
                continue

            # Check if it makes sense
            if abs(parsed_fps - src_fps) < 0.1:
                info(f"Same as source — keeping {fmt_fps(src_fps)}.")
                break

            out_fps = parsed_fps
            space_tag = ""
            if parsed_fps < src_fps:
                reduction = (src_fps - parsed_fps) / src_fps * 100
                space_tag = f"  [{COK}](~{reduction:.0f}% fewer frames → smaller file)[/]"
            elif parsed_fps > src_fps:
                space_tag = f"  [{CWN}](upsampling — larger file, may look unnatural)[/]"

            info(
                f"Output FPS: [bold {CW}]{fmt_fps(out_fps)}[/]"
                f"{space_tag}"
            )
            break

    return custom_br, out_fps

# ══════════════════════════════════════════════════════════════════════════════
#  COMPRESS LIVE PANEL
# ══════════════════════════════════════════════════════════════════════════════
def _panel(info_rows: list[tuple[str, str]], state: dict,
           batch_n: int, batch_total: int, enc_lbl: str) -> Panel:

    W = 44

    ig = Table.grid(padding=(0, 2))
    ig.add_column(style=CD, justify="right", no_wrap=True, min_width=8)
    ig.add_column(style=CW, min_width=42, no_wrap=False)
    for k, v in info_rows:
        ig.add_row(k, v)
    enc_col = CWN if SW_ENCODER in enc_lbl else COK
    ig.add_row("Encoder", f"[bold {enc_col}]{escape(enc_lbl)}[/]")

    # Overall batch bar
    bf = int(W * batch_n / batch_total)
    bb = Text()
    bb.append("▪" * bf,       style=CIN)
    bb.append("▫" * (W - bf), style=CF)
    bb.append(f"  {batch_n}/{batch_total}", style=f"bold {CM}")

    # Encode progress bar
    pct = state['pct']
    ef  = int(W * pct / 100)
    eb  = Text()
    eb.append("█" * ef,          style=f"bold {CA}")
    eb.append("░" * (W - ef),    style=CF)
    eb.append(f"  {pct:>5.1f}%", style=f"bold {CW}")

    # Stats row
    sp_col  = COK if state['speed'] >= 1.0 else CWN
    fps_txt = f"{state['fps']:.0f}" if state['fps'] > 0 else "—"
    sg = Table.grid(padding=(0, 2))
    for _ in range(5): sg.add_column(no_wrap=True)
    sg.add_row(
        Text.assemble(Text("Elapsed ", style=CD), Text(fmt_dur(state['elapsed']), style=CW)),
        Text.assemble(Text("ETA     ", style=CD), Text(fmt_dur(state['eta']),     style=CW)),
        Text.assemble(Text("Speed   ", style=CD), Text(f"{state['speed']:.2f}x",  style=sp_col)),
        Text.assemble(Text("FPS     ", style=CD), Text(fps_txt,                    style=CW)),
        Text.assemble(Text("Written ", style=CD), Text(f"{state['out_mb']:.1f} MB", style=CW)),
    )

    if   state['retrying']: st = Text(f"  ↻  Retrying with {SW_ENCODER} (CRF)…", style=f"bold {CWN}")
    elif state['done']:     st = Text(  "  ✔  Complete!",                           style=f"bold {COK}")
    elif state['failed']:   st = Text(  "  ✘  Failed.",                             style=f"bold {CER}")
    else:                   st = Text(  "  ⚙  Encoding…",                           style=f"bold {CIN}")

    content = Group(
        Padding(ig, (0, 0, 1, 0)),
        Rule(style=CF),
        Padding(Text("  Overall", style=CD), (1, 0, 0, 0)),
        Padding(bb, (0, 0, 0, 0)),
        Padding(Text("  Encode",  style=CD), (1, 0, 0, 0)),
        Padding(eb, (0, 0, 0, 0)),
        Padding(sg, (1, 0, 1, 0)),
        Rule(style=CF),
        Padding(st, (1, 0, 0, 0)),
    )
    return Panel(
        content,
        title=(f"[bold {CA}]  ⚡ Compressing  "
               f"{escape(f'[{batch_n}/{batch_total}]')}[/]"),
        border_style=CA, box=box.ROUNDED, padding=(0, 2),
    )

# ══════════════════════════════════════════════════════════════════════════════
#  FFMPEG RUNNER
# ══════════════════════════════════════════════════════════════════════════════
def _run_ffmpeg(cmd: list, out_path: str, duration: float,
                state: dict, live: Live, panel_fn) -> tuple[int, list]:
    log  = []
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    t0 = time.time()
    try:
        for line in proc.stdout:
            log.append(line.rstrip())
            if len(log) > MAX_LOG_LINES * 2:
                log = log[-MAX_LOG_LINES:]

            if duration > 0:
                tm = re.search(r"time=(\d+):(\d+):([\d.]+)", line)
                if tm:
                    cur = int(tm[1]) * 3600 + int(tm[2]) * 60 + float(tm[3])
                    el  = time.time() - t0
                    state.update(
                        elapsed = el,
                        pct     = min(cur / duration * 100, 99.9),
                        eta     = (el / cur * (duration - cur)) if cur > 0 else 0,
                        speed   = cur / el if el > 0 else 0,
                    )

            fp = re.search(r"\bfps=\s*([\d.]+)", line)
            if fp:
                state['fps'] = float(fp[1])

            if os.path.exists(out_path):
                state['out_mb'] = os.path.getsize(out_path) / 1_048_576

            live.update(panel_fn())
    except Exception:
        pass

    proc.wait()
    el = time.time() - t0
    state.update(elapsed=el, speed=duration / el if el > 0 else 0)
    return proc.returncode, log[-MAX_LOG_LINES:]

# ══════════════════════════════════════════════════════════════════════════════
#  VIDEO FILTER BUILDER  — combines scale + fps cleanly
# ══════════════════════════════════════════════════════════════════════════════
def _build_vf(downscale: bool, out_fps: float | None) -> list[str]:
    """
    Build -vf argument combining scale and fps filters if both are needed.
    Returns empty list if no filtering required.
    """
    parts = []
    if downscale:
        parts.append("scale=-2:720")
    if out_fps is not None:
        # fps filter with exact fraction for common NTSC rates
        ntsc = {29.97: "30000/1001", 59.94: "60000/1001", 23.976: "24000/1001"}
        fps_str = ntsc.get(out_fps, str(out_fps))
        parts.append(f"fps={fps_str}")
    if not parts:
        return []
    return ["-vf", ",".join(parts)]

# ══════════════════════════════════════════════════════════════════════════════
#  COMPRESS  — HW ABR → SW CRF fallback; always .mp4 output
# ══════════════════════════════════════════════════════════════════════════════
def compress(
    in_path: str, out_path: str, vinfo: dict, mode_cfg: dict,
    downscale: bool, custom_br: int | None, out_fps: float | None,
    batch_n: int, batch_total: int,
) -> tuple[bool, list, str]:

    fname    = os.path.basename(in_path)
    auto_br  = calc_target_br(
        vinfo['width'], vinfo['height'], vinfo['bitrate'],
        mode_cfg['name'], vinfo['fps'], downscale,
    )
    # Use custom bitrate if provided, capped to source
    if custom_br is not None:
        target_br = custom_br
        if vinfo['bitrate'] > 0:
            target_br = min(target_br, vinfo['bitrate'])
    else:
        target_br = auto_br

    crf      = mode_cfg['crf']
    dur      = vinfo['duration']
    res_in   = f"{vinfo['width']}x{vinfo['height']}"
    res_out  = "1280x720" if downscale else res_in
    src_mb   = os.path.getsize(in_path) / 1_048_576
    audio_br = (min(vinfo['audio_br'] if vinfo['audio_br'] > 0 else 128_000, 192_000)
                if vinfo['has_audio'] else 0)
    est_mb   = est_output_mb(target_br, audio_br, dur)
    savings  = ((vinfo['bitrate'] - target_br) / vinfo['bitrate'] * 100
                if vinfo['bitrate'] > 0 else 0)

    # Audio params
    if (vinfo['has_audio']
            and vinfo['audio_codec'] == 'aac'
            and 0 < vinfo['audio_br'] <= AUDIO_COPY_BR):
        audio_p   = ['-c:a', 'copy']
        audio_lbl = "copy (AAC)"
    elif vinfo['has_audio']:
        audio_p   = ['-c:a', 'aac', '-b:a', '128k', '-ac', '2']
        audio_lbl = "AAC 128k"
    else:
        audio_p   = ['-an']
        audio_lbl = "no audio"

    sv_col = COK if savings >= MIN_SAVINGS else CWN

    # Bitrate label: show [custom] tag if overridden
    br_label = (
        f"[bold {CW}]{fmt_br(target_br)}[/]  [{CIN}][custom][/]"
        if custom_br is not None
        else f"[bold {CA}]{fmt_br(target_br)}[/]"
    )

    # FPS label
    src_fps_lbl = fmt_fps(vinfo['fps'])
    if out_fps is not None:
        fps_lbl = (
            f"[{CM}]{src_fps_lbl}[/]  [{CF}]→[/]  "
            f"[bold {CW}]{fmt_fps(out_fps)}[/]  [{CIN}][custom][/]"
        )
    else:
        fps_lbl = f"[{CM}]{src_fps_lbl}[/]  [{CD}](keep source)[/]"

    info_rows: list[tuple[str, str]] = [
        ("File",    f"[bold {CW}]{escape(trunc(fname, 44))}[/]"),
        ("Source",  f"[{CM}]{src_mb:.1f} MB  ·  {fmt_dur(dur)}  ·  "
                    f"{escape(vinfo['codec'])}[/]"),
        ("Res",     f"[{CM}]{res_in}[/]"
                    + (f"  [{CWN}]→ {res_out}[/]" if downscale else "")),
        ("Bitrate", f"[{CM}]{fmt_br(vinfo['bitrate'])}[/]"
                    f"  [{CF}]→[/]  "
                    f"{br_label}"
                    f"  [{sv_col}]({savings:.0f}% reduction)[/]"),
        ("FPS",     fps_lbl),
        ("SW CRF",  f"[{CM}]CRF {crf} if HW fails[/]"),
        ("Est out", f"[{CM}]~{est_mb:.1f} MB[/]"),
        ("Audio",   f"[{CM}]{audio_lbl}[/]"),
        ("Mode",    f"[bold {CA}]{mode_cfg['name']}[/]"),
    ]

    vf = _build_vf(downscale, out_fps)

    def build_cmd_hw() -> list:
        c = ['ffmpeg', '-i', in_path, '-map', '0:v:0']
        if vinfo['has_audio']:
            c += ['-map', '0:a?']
        c += ['-c:v', HW_ENCODER,
              '-b:v', str(target_br),
              '-maxrate', str(int(target_br * 1.5)),
              '-bufsize', str(int(target_br * 2.5)),
              '-pix_fmt', 'yuv420p',
              '-profile:v', mode_cfg['hw_profile']]
        c += vf
        c += audio_p
        c += ['-movflags', '+faststart', '-y', out_path]
        return c

    def build_cmd_sw() -> list:
        c = ['ffmpeg', '-i', in_path, '-map', '0:v:0']
        if vinfo['has_audio']:
            c += ['-map', '0:a?']
        c += ['-c:v', SW_ENCODER,
              '-crf', str(crf),
              '-preset', mode_cfg['sw_preset'],
              '-profile:v', mode_cfg['sw_profile'],
              '-maxrate', str(int(target_br * 1.5)),
              '-bufsize', str(int(target_br * 3.0)),
              '-pix_fmt', 'yuv420p']
        c += vf
        c += audio_p
        c += ['-movflags', '+faststart', '-y', out_path]
        return c

    state: dict = dict(pct=0.0, elapsed=0.0, eta=0.0, speed=0.0,
                       out_mb=0.0, fps=0.0,
                       done=False, failed=False, retrying=False)
    enc_lbl = HW_ENCODER

    def pf() -> Panel:
        return _panel(info_rows, state, batch_n, batch_total, enc_lbl)

    with Live(pf(), console=console, refresh_per_second=10, transient=False) as live:
        rc, log = _run_ffmpeg(build_cmd_hw(), out_path, dur, state, live, pf)

        if rc != 0:
            if os.path.exists(out_path): os.remove(out_path)
            state.update(pct=0.0, elapsed=0.0, eta=0.0, speed=0.0,
                         out_mb=0.0, fps=0.0, retrying=True)
            enc_lbl = f"{SW_ENCODER} CRF{crf} (fallback)"
            live.update(pf()); time.sleep(0.5)
            state['retrying'] = False
            rc, log = _run_ffmpeg(build_cmd_sw(), out_path, dur, state, live, pf)

        if rc == 0:
            state.update(pct=100.0, eta=0.0, done=True)
            if os.path.exists(out_path):
                state['out_mb'] = os.path.getsize(out_path) / 1_048_576
        else:
            state['failed'] = True
        live.update(pf())

    return rc == 0, log, enc_lbl

# ══════════════════════════════════════════════════════════════════════════════
#  REMUX — lossless container → MP4, no re-encode
# ══════════════════════════════════════════════════════════════════════════════
def remux_to_mp4(in_path: str, out_path: str) -> tuple[bool, list]:
    cmd = ['ffmpeg', '-i', in_path,
           '-map', '0', '-c', 'copy',
           '-movflags', '+faststart',
           '-y', out_path]
    log  = []
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    for line in proc.stdout:
        log.append(line.rstrip())
    proc.wait()
    return proc.returncode == 0, log[-MAX_LOG_LINES:]

# ══════════════════════════════════════════════════════════════════════════════
#  ERROR LOG
# ══════════════════════════════════════════════════════════════════════════════
def save_log(filename: str, log_lines: list) -> str:
    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe  = re.sub(r'[^\w\-.]', '_', os.path.splitext(filename)[0])
    path  = os.path.join(LOG_DIR, f"err_{safe}_{stamp}.log")
    with open(path, "w") as f:
        f.write(f"File : {filename}\nTime : {stamp}\n\n")
        f.write("\n".join(log_lines))
    return path

# ══════════════════════════════════════════════════════════════════════════════
#  PRE-FLIGHT
# ══════════════════════════════════════════════════════════════════════════════
def probe_all(files: list) -> list:
    results = []
    with Progress(SpinnerColumn(style=CA),
                  TextColumn(f"[{CD}]Probing: {{task.description}}"),
                  console=console, transient=True) as prog:
        task = prog.add_task("", total=None)
        for fname in files:
            prog.update(task, description=trunc(fname, 52))
            results.append((fname, probe(os.path.join(BASE_DIR, fname))))
    return results


def print_preflight(probed: list, mode_name: str) -> tuple[list, list, list]:
    """
    Render analysis table. Returns (processable, to_remux, truly_skip).
    """
    ft = Table(
        box=box.SIMPLE_HEAVY, border_style=CF, show_header=True,
        header_style=f"bold {CM}", pad_edge=True, padding=(0, 1),
    )
    ft.add_column("#",       style=CD, width=3,  justify="right")
    ft.add_column("File",    style=CW, min_width=22)
    ft.add_column("Size",    style=CM, width=8,  justify="right")
    ft.add_column("Dur",     style=CM, width=8,  justify="right")
    ft.add_column("Res",     style=CA, width=11, justify="center")
    ft.add_column("FPS",     style=CM, width=7,  justify="right")
    ft.add_column("Orig BR", style=CM, width=10, justify="right")
    ft.add_column("Target",  style=CA, width=10, justify="right")
    ft.add_column("Save",    style=CW, width=7,  justify="right")
    ft.add_column("Action",  style=CD, width=10)

    processable, to_remux, truly_skip = [], [], []

    for i, (fname, v) in enumerate(probed, 1):
        short  = trunc(fname, 22)
        fpath  = os.path.join(BASE_DIR, fname)
        sz     = fmt_size(os.path.getsize(fpath))
        is_mp4 = fname.lower().endswith('.mp4')

        if not v:
            ft.add_row(str(i), f"[{CER}]{short}[/]", sz,
                       "—","—","—","—","—","—", f"[{CER}]error[/]")
            continue

        tgt       = calc_target_br(v['width'], v['height'],
                                   v['bitrate'], mode_name, v['fps'])
        skip_, sv = check_worth(v['bitrate'], tgt)
        sv_txt    = f"-{sv:.0f}%" if v['bitrate'] > 0 else "?"
        sv_col    = COK if not skip_ else CD
        dim       = skip_
        dc        = CD if dim else CW
        fps_txt   = fmt_fps(v['fps']) if v['fps'] > 0 else "?"

        # Highlight non-30fps in FPS column
        fps_col = (CWN if (v['fps'] > 0 and abs(v['fps'] - 30.0) > 0.5
                           and abs(v['fps'] - 29.97) > 0.5)
                   else (CD if dim else CM))

        if skip_ and not is_mp4:
            action = f"[{CA}]remux[/]"
        elif skip_:
            action = f"[{CF}]skip[/]"
        else:
            action = f"[{COK}]compress[/]"

        ft.add_row(
            str(i),
            f"[{dc}]{escape(short)}[/]",
            f"[{CD if dim else CM}]{sz}[/]",
            f"[{CD if dim else CM}]{fmt_dur(v['duration'])}[/]",
            f"[{CD if dim else CA}]{v['width']}x{v['height']}[/]",
            f"[{fps_col}]{fps_txt}[/]",
            f"[{CD if dim else CM}]{fmt_br(v['bitrate'])}[/]",
            f"[{CD if dim else CA}]{fmt_br(tgt)}[/]",
            f"[bold {sv_col}]{sv_txt}[/]",
            action,
        )

        if skip_:
            (truly_skip if is_mp4 else to_remux).append((fname, v, sv))
        else:
            processable.append((fname, v))

    console.print(Padding(ft, (0, 2)))
    return processable, to_remux, truly_skip

# ══════════════════════════════════════════════════════════════════════════════
#  FILE SELECTOR
# ══════════════════════════════════════════════════════════════════════════════
def select_files(processable: list) -> list:
    blank()
    info(
        f"[bold {CW}]a[/] = all"
        f"   [bold {CW}]1,3,5[/] = specific by number"
        f"   [bold {CW}]q[/] = quit"
    )
    blank()
    while True:
        sel = Prompt.ask(
            f"  [bold {CA}]Select files[/]", default="a", console=console,
        ).strip().lower()
        if sel == "q":
            warn("Aborted."); blank(); sys.exit(0)
        if sel == "a":
            return processable
        try:
            chosen = []
            for x in sel.split(","):
                idx = int(x.strip())
                if not 1 <= idx <= len(processable):
                    raise ValueError(f"out of range: {idx}")
                chosen.append(processable[idx - 1])
            if chosen:
                return chosen
        except ValueError as e:
            err(f"Invalid selection ({e}) — try again.")

# ══════════════════════════════════════════════════════════════════════════════
#  OUTPUT PATH — always .mp4, auto-rename on collision
# ══════════════════════════════════════════════════════════════════════════════
def safe_out(filename: str) -> str:
    base = os.path.splitext(filename)[0]
    path = os.path.join(OUTPUT_DIR, f"{base}.mp4")
    if not os.path.exists(path): return path
    n = 1
    while True:
        path = os.path.join(OUTPUT_DIR, f"{base}_{n}.mp4")
        if not os.path.exists(path): return path
        n += 1

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    print_header()
    install_deps()

    # ── Mode selection ────────────────────────────────────────────────────────
    hrule("Compression Mode")
    blank()

    MODES = [
        {
            "key": "1", "name": "FAST",    "icon": "🚀",
            "col": CWN, "tag": "",
            "desc": "Fastest encode, good visual quality.\n"
                    "CRF 20 / ABR ×1.00 — fast job, good result.",
            "crf": 20, "sw_preset": "fast",   "sw_profile": "baseline",
            "hw_profile": "baseline",
            "stat_crf": "CRF 20", "stat_save": "~30-50%", "stat_sp": "Fastest",
        },
        {
            "key": "2", "name": "MEDIUM",  "icon": "⚖",
            "col": CA,  "tag": "  ★ Default",
            "desc": "Best balance of quality and file size.\n"
                    "CRF 18 / ABR ×1.00 — recommended for most videos.",
            "crf": 18, "sw_preset": "medium", "sw_profile": "main",
            "hw_profile": "main",
            "stat_crf": "CRF 18", "stat_save": "~35-55%", "stat_sp": "Balanced",
        },
        {
            "key": "3", "name": "QUALITY", "icon": "💎",
            "col": COK, "tag": "",
            "desc": "Maximum space savings, slower encode.\n"
                    "CRF 16 / ABR ×0.78 — smallest file, excellent quality.",
            "crf": 16, "sw_preset": "slow",   "sw_profile": "high",
            "hw_profile": "high",
            "stat_crf": "CRF 16", "stat_save": "~40-60%", "stat_sp": "Slowest",
        },
    ]

    for m in MODES:
        sg = Table.grid(padding=(0, 4))
        for _ in range(6): sg.add_column()
        sg.add_row(
            Text("SW CRF",  style=CD), Text(m['stat_crf'],  style=f"bold {m['col']}"),
            Text("Est save", style=CD), Text(m['stat_save'], style=f"bold {m['col']}"),
            Text("Speed",   style=CD), Text(m['stat_sp'],   style=f"bold {m['col']}"),
        )
        body = Group(
            Text.assemble(
                Text(f" {m['icon']}  ", style=m['col']),
                Text(m['name'],         style=f"bold {CW}"),
                Text(m['tag'],          style=f"bold {m['col']}"),
            ),
            Padding(Text(m['desc'], style=CM), (0, 0, 0, 4)),
            Padding(sg,                        (0, 0, 0, 4)),
        )
        console.print(Padding(
            Panel(body,
                  title=f"[bold {m['col']}] {m['key']} [/]",
                  title_align="left", border_style=m['col'],
                  box=box.ROUNDED, padding=(0, 2)),
            (0, 2, 0, 2),
        ))

    blank()
    sel = Prompt.ask(
        f"  [bold {CA}]Select mode[/]",
        choices=["1", "2", "3"], default="2", console=console,
    )
    cm       = next(m for m in MODES if m["key"] == sel)
    mode_cfg = {
        "name":       cm["name"],
        "crf":        cm["crf"],
        "sw_preset":  cm["sw_preset"],
        "sw_profile": cm["sw_profile"],
        "hw_profile": cm["hw_profile"],
    }
    ok(
        f"[bold {CW}]{cm['icon']}  {cm['name']}[/]"
        f"  [{CD}]CRF {cm['crf']}  ·  "
        f"preset {cm['sw_preset']}  ·  "
        f"est save {cm['stat_save']}[/]"
    )
    blank()

    # ── Scan & probe ──────────────────────────────────────────────────────────
    hrule("Pre-flight Analysis")
    blank()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_files = sorted(
        f for f in os.listdir(BASE_DIR) if f.lower().endswith(VIDEO_EXT)
    )
    if not all_files:
        warn(f"No video files found in [{CM}]{escape(BASE_DIR)}[/]")
        info(f"Supported: {', '.join(VIDEO_EXT)}")
        blank(); sys.exit(0)

    info(f"Probing [bold {CW}]{len(all_files)}[/] file(s) …")
    blank()
    probed = probe_all(all_files)
    processable, to_remux, truly_skip = print_preflight(probed, mode_cfg["name"])
    blank()

    if truly_skip:
        skp(
            f"{len(truly_skip)} file(s) already optimal MP4 — no action: "
            + ", ".join(f"[{CW}]{escape(f)}[/]" for f, _, _ in truly_skip)
        )
        blank()
    if to_remux:
        remu(
            f"{len(to_remux)} non-MP4 file(s) already optimal — "
            f"will remux to MP4 (lossless): "
            + ", ".join(f"[{CW}]{escape(f)}[/]" for f, _, _ in to_remux)
        )
        blank()

    if not processable and not to_remux:
        info("All files are already optimal MP4.  Nothing to do.")
        blank(); sys.exit(0)

    # ── File selection ─────────────────────────────────────────────────────────
    to_compress: list = []
    if processable:
        info(
            f"[bold {CW}]{len(processable)}[/] file(s) eligible for compression"
            f"  [{CD}]→ {escape(OUTPUT_DIR)}[/]"
        )
        to_compress = select_files(processable)
        blank()

    total_jobs = len(to_compress) + len(to_remux)
    if total_jobs == 0:
        info("Nothing selected."); blank(); sys.exit(0)

    if not Confirm.ask(
        f"  [bold {CA}]Start?  "
        f"({len(to_compress)} compress"
        f"{f'  +  {len(to_remux)} remux' if to_remux else ''})[/]",
        default=True, console=console,
    ):
        warn("Aborted."); blank(); sys.exit(0)

    # ── Clear terminal ─────────────────────────────────────────────────────────
    console.clear()
    print_header(clear=False)
    hrule(f"Processing  {total_jobs} file(s)  ·  Mode: {mode_cfg['name']}", CA)
    blank()

    results: list = []

    with wakelock():
        blank()

        # ── Phase 1: Compress ─────────────────────────────────────────────────
        for batch_n, (filename, vinfo) in enumerate(to_compress, 1):
            in_f  = os.path.join(BASE_DIR, filename)
            out_f = safe_out(filename)

            hrule(f"File {batch_n}/{len(to_compress)}  ·  {escape(trunc(filename, 40))}", CD)
            blank()

            # Downscale prompt
            do_ds = False
            if vinfo['width'] > 1280 or vinfo['height'] > 720:
                warn(f"High resolution: [bold {CW}]{vinfo['width']}x{vinfo['height']}[/]")
                do_ds = Confirm.ask(
                    f"  [{CA}]Downscale to 720p?[/]",
                    default=False, console=console,
                )
                if do_ds:
                    tgt720    = calc_target_br(vinfo['width'], vinfo['height'],
                                               vinfo['bitrate'], mode_cfg['name'],
                                               vinfo['fps'], True)
                    skip_, sv = check_worth(vinfo['bitrate'], tgt720)
                    if skip_:
                        skp(
                            f"[{CW}]{escape(filename)}[/] — "
                            f"720p target too close to source ({sv:.0f}% savings). Skipping."
                        )
                        results.append((filename, "skipped-optimal", 0, 0, "—"))
                        blank(); continue
                blank()

            # ── Custom bitrate + FPS prompt ───────────────────────────────────
            hrule("Settings", CD)
            blank()
            custom_br, out_fps = ask_per_file_settings(filename, vinfo, mode_cfg, do_ds)
            blank()

            success, log_lines, enc_used = compress(
                in_f, out_f, vinfo, mode_cfg,
                do_ds, custom_br, out_fps,
                batch_n, len(to_compress),
            )
            blank()

            if success and os.path.exists(out_f):
                old_sz = os.path.getsize(in_f)
                new_sz = os.path.getsize(out_f)
                saved  = (old_sz - new_sz) / old_sz * 100 if old_sz else 0

                # Post-encode guard: discard if not meaningfully smaller
                if new_sz >= old_sz * (1.0 - MIN_SAVINGS / 100):
                    os.remove(out_f)
                    skp(
                        f"[{CW}]{escape(filename)}[/] — "
                        f"output not smaller ({saved:.1f}% < {MIN_SAVINGS}%). Source kept."
                    )
                    results.append((filename, "skipped-optimal", 0, 0, enc_used))
                else:
                    tags = []
                    if SW_ENCODER in enc_used:
                        tags.append(f"[{CWN}]↺ SW CRF{mode_cfg['crf']}[/]")
                    if custom_br is not None:
                        tags.append(f"[{CIN}]custom BR[/]")
                    if out_fps is not None:
                        tags.append(f"[{CIN}]{fmt_fps(out_fps)} fps[/]")
                    tag_str = "  " + "  ".join(tags) if tags else ""
                    ok(
                        f"[bold {CW}]{escape(filename)}[/]"
                        f"  [{CM}]{fmt_size(old_sz)}[/]  →  "
                        f"[bold {COK}]{fmt_size(new_sz)}[/]"
                        f"  [bold {CA}]-{saved:.1f}%[/]{tag_str}"
                    )
                    results.append((filename, "ok", old_sz, new_sz, enc_used))
            else:
                lp = save_log(filename, log_lines)
                err(f"Failed: [bold {CW}]{escape(filename)}[/]")
                if log_lines:
                    blank()
                    console.print(Padding(Panel(
                        "\n".join(log_lines[-10:]),
                        title=f"[bold {CER}] ffmpeg output [/]",
                        border_style=CER, box=box.ROUNDED, padding=(0, 1),
                    ), (0, 2)))
                blank()
                info(f"Error log → [{CM}]{escape(lp)}[/]")
                results.append((filename, "failed", 0, 0, enc_used))
            blank()

        # ── Phase 2: Remux non-MP4 optimal files ──────────────────────────────
        if to_remux:
            hrule("Remuxing to MP4", CD); blank()

        for filename, vinfo, _sv in to_remux:
            in_f  = os.path.join(BASE_DIR, filename)
            out_f = safe_out(filename)
            remu(f"[bold {CW}]{escape(filename)}[/]  [{CD}]→ MP4[/]")
            with Progress(SpinnerColumn(style=CA),
                          TextColumn(f"[{CD}]{{task.description}}"),
                          console=console, transient=True) as prog:
                task    = prog.add_task("Remuxing …", total=None)
                success, log_lines = remux_to_mp4(in_f, out_f)
                prog.update(task, description="Done." if success else "Failed.")

            if success and os.path.exists(out_f):
                old_sz = os.path.getsize(in_f)
                new_sz = os.path.getsize(out_f)
                ok(
                    f"[bold {CW}]{escape(filename)}[/]"
                    f"  [{CM}]{fmt_size(old_sz)}[/]  →  "
                    f"[{CM}]{fmt_size(new_sz)}[/]"
                    f"  [{CD}](remux, no re-encode)[/]"
                )
                results.append((filename, "remuxed", old_sz, new_sz, "remux"))
            else:
                lp = save_log(filename, log_lines)
                err(f"Remux failed: [bold {CW}]{escape(filename)}[/]")
                info(f"Error log → [{CM}]{escape(lp)}[/]")
                results.append((filename, "failed", 0, 0, "remux"))
            blank()

    # Append truly-skipped
    for fname, _, _ in truly_skip:
        results.append((fname, "skipped", 0, 0, "—"))

    # ── Summary ───────────────────────────────────────────────────────────────
    hrule("Summary"); blank()

    st = Table(
        box=box.SIMPLE_HEAVY, border_style=CF, show_header=True,
        header_style=f"bold {CM}", pad_edge=True, padding=(0, 2),
    )
    st.add_column("File",     style=CW, min_width=24)
    st.add_column("Status",   justify="center", width=14)
    st.add_column("Original", justify="right",  style=CM, width=10)
    st.add_column("Output",   justify="right",  width=10)
    st.add_column("Saved",    justify="right",  width=8)
    st.add_column("Method",   style=CD, width=12)

    total_old = total_new = 0
    for fname, status, old, new, enc in results:
        short  = trunc(fname, 24)
        method = (enc.replace(HW_ENCODER, "HW")
                     .replace(" (fallback)", "↺")
                     .replace(SW_ENCODER, "SW"))
        match status:
            case "ok":
                sv = (old - new) / old * 100 if old else 0
                st.add_row(
                    short, f"[bold {COK}]✔  done[/]",
                    fmt_size(old), f"[bold {COK}]{fmt_size(new)}[/]",
                    f"[bold {CA}]-{sv:.1f}%[/]", method,
                )
                total_old += old; total_new += new
            case "remuxed":
                st.add_row(short, f"[{CA}]⇄  remuxed[/]",
                           fmt_size(old), f"[{CM}]{fmt_size(new)}[/]",
                           f"[{CD}]—[/]", "remux")
            case "skipped" | "skipped-optimal":
                st.add_row(short, f"[{CF}]○  optimal[/]", "—","—","—","—")
            case _:
                st.add_row(short, f"[bold {CER}]✘  failed[/]", "—","—","—", method)

    console.print(Padding(st, (0, 2)))

    if total_old > 0:
        ts    = (total_old - total_new) / total_old * 100
        freed = total_old - total_new
        blank()
        console.print(Padding(Panel(
            Text.assemble(
                Text("Total  ",           style=CD),
                Text(fmt_size(total_old),  style=CM),
                Text("  →  ",             style=CF),
                Text(fmt_size(total_new),  style=f"bold {COK}"),
                Text("    Saved  ",        style=CD),
                Text(f"-{ts:.1f}%",        style=f"bold {CA}"),
                Text("    Freed  ",        style=CD),
                Text(fmt_size(freed),      style=f"bold {CW}"),
            ),
            border_style=CA, box=box.ROUNDED, padding=(0, 4),
        ), (0, 2)))
    blank()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        blank(); warn("Canceled."); blank(); sys.exit(0)
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
