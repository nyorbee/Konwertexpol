#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Right-click file converter for Windows (Explorer context menu).
Besides the standard library it only uses the Python modules pikepdf, Pillow and pdf2docx (PDF compression, PDF -> Word),
installed into tools\\python together with the other tools (the tools folder can be moved to a computer without internet).
External tools (FFmpeg, ImageMagick, libjxl, Pandoc, Poppler, Ghostscript, qpdf) live in the tools\\ folder next to this file and are downloaded / updated
by tools_manager.py. Edge/Chrome is used to turn text documents into PDF.

By default every conversion is LOSSLESS (LOSSLESS = True):
    * video/audio: when the codecs fit the container the streams are copied (remux: no loss, no re-encoding),
        otherwise the file is re-encoded at a quality indistinguishable from the original that plays everywhere
        (H.264 crf 18 + yuv420p, VP9 crf 16, AAC 256k); true crf 0 / lossless only when ticked in the window;
    * images: PNG/TIFF/BMP unchanged, lossless WebP, lossless JXL (JPEG -> JXL without loss, reversible);
    * lossy only through "Compress to size" or a resolution change;
  * scanned PDFs: OCR (Tesseract + ocrmypdf) adds a searchable text layer without touching the page images,
    and PDF -> text / Word recognises the text of scans automatically.
"""
import os
import re
import sys
import json
import glob
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tools_manager  # noqa: E402

CREATE_NO_WINDOW = 0x08000000

LOSSLESS = True        # False = old "reasonable quality" mode (crf 23, JPG q90 etc.)
PDF_DPI_FALLBACK = 150            # PDF -> image when the PDF has no images to read the dpi from
PDF_DPI_MIN, PDF_DPI_MAX = 100, 300   # allowed range for the dpi read from embedded images
OCR_PRESETS = ["eng", "pol+eng", "deu+eng", "fra+eng", "spa+eng", "ita+eng", "por+eng", "nld+eng", "rus+eng",
               "ukr+eng", "ces+eng", "tur+eng", "chi_sim+eng", "jpn+eng"]
USE_GPU = False                   # optional (window checkbox): H.264 high-quality encodes use the graphics card
VIDEO_TRUE_LOSSLESS = False       # True = crf 0 / VP9 lossless (huge files that play almost nowhere)
OCR_LANG = ""                     # Tesseract language(s), e.g. "pol+eng"; "" = Windows UI language + English
OCR_MIN_CHARS_PER_PAGE = 20       # fewer characters per page on average = a scan without a text layer


# ----------------------------------------------------------------------------
# Cancelling: one flag + a registry of running processes (ffmpeg, Ghostscript, qpdf...)
# ----------------------------------------------------------------------------
class Cancelled(BaseException):
    """The user pressed "Cancel" or closed the window while work was running. (A BaseException so that no
    "except Exception" in the conversion code can swallow the cancellation.)"""


CANCEL = threading.Event()
_CTX = threading.local()           # per worker thread: .idx = number of the file being converted (parallel mode)
_ASK_LOCK = threading.Lock()       # only one question window (tools / password / audio tracks) at a time
_PROCS = set()
_PROCS_LOCK = threading.Lock()


def check_cancel():
    if CANCEL.is_set():
        raise Cancelled("Cancelled by the user.")


def _kill_tree(p):
    """Kills a process together with its child processes (e.g. a headless browser)."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True,
                           creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass
    else:                                       # POSIX: the whole process group (spawn starts a new session)
        try:
            os.killpg(p.pid, 9)
        except Exception:
            pass
    try:
        p.kill()
    except Exception:
        pass


def spawn(cmd, **kw):
    """Popen that can be interrupted via cancel_all()."""
    if os.name == "nt":
        kw.setdefault("creationflags", CREATE_NO_WINDOW)
    else:
        kw.setdefault("start_new_session", True)
    p = subprocess.Popen([str(c) for c in cmd], **kw)
    with _PROCS_LOCK:
        _PROCS.add(p)
    if CANCEL.is_set():                 # cancelled right before the process started
        _kill_tree(p)
    return p


def release(p):
    with _PROCS_LOCK:
        _PROCS.discard(p)


def cancel_all():
    """Called from the window: sets the flag and stops every running tool."""
    CANCEL.set()
    with _PROCS_LOCK:
        procs = list(_PROCS)
    for p in procs:
        _kill_tree(p)


def prun(cmd, **kw):
    """subprocess.run with captured output that can be cancelled."""
    p = spawn(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    try:
        out, err = p.communicate()
    finally:
        release(p)
    check_cancel()
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


tools_manager.CANCEL_CHECK = CANCEL.is_set     # tool downloads react to "Cancel" too

# ----------------------------------------------------------------------------
# Formats
# ----------------------------------------------------------------------------
ALIASES = {"jpeg": "jpg", "tif": "tiff", "htm": "html", "markdown": "md",
           "aif": "aiff", "mpeg": "mpg", "heif": "heic", "mts": "ts"}

VIDEO_IN = {"mp4", "mkv", "webm", "avi", "mov", "wmv", "flv", "mpg", "m4v", "3gp", "ts"}
AUDIO_IN = {"mp3", "wav", "flac", "aac", "m4a", "ogg", "opus", "wma", "aiff", "ac3", "amr"}
IMAGE_IN = {"jpg", "png", "webp", "avif", "jxl", "bmp", "tiff", "gif", "ico", "heic"}
TEXT_IN = {"txt", "md", "html", "docx", "odt", "rtf", "epub", "tex"}
PDF_IN = {"pdf"}

VIDEO_OUT = ["mp4", "mkv", "webm", "avi", "mov", "wmv", "flv", "mpg", "3gp"]
AUDIO_OUT = ["mp3", "wav", "flac", "aac", "m4a", "ogg", "opus", "wma", "aiff", "ac3"]
IMAGE_OUT = ["jpg", "png", "webp", "avif", "jxl", "bmp", "tiff", "gif", "ico"]
TEXT_OUT = ["txt", "md", "html", "docx", "odt", "rtf", "epub", "tex", "pdf"]

TARGETS = {
    "video": VIDEO_OUT + ["gif"] + AUDIO_OUT,   # video -> video / gif / audio only
    "audio": AUDIO_OUT,
    "image": IMAGE_OUT + ["pdf"],
    "text": TEXT_OUT,
    "pdf": list(TEXT_OUT) + ["jpg", "png", "webp", "avif", "jxl", "bmp", "tiff", "gif"],
}

# ready-made "Resize to" menu entries (register.py) and hints in the GUI
VIDEO_RES_PRESETS = ["2160p", "1440p", "1080p", "720p", "480p", "360p", "240p"]
IMAGE_RES_PRESETS = ["3840x2160", "2560x1440", "1920x1080", "1280x720", "800x600", "50%", "25%"]


def norm(ext):
    e = ext.lower().lstrip(".")
    return ALIASES.get(e, e)


def ext_of(p):
    return norm(Path(p).suffix)


def category(p):
    e = ext_of(p)
    if e in VIDEO_IN:
        return "video"
    if e in AUDIO_IN:
        return "audio"
    if e in IMAGE_IN:
        return "image"
    if e in TEXT_IN:
        return "text"
    if e in PDF_IN:
        return "pdf"
    return None


# ----------------------------------------------------------------------------
# External tools: the tools\ folder first, then PATH and typical install locations
# ----------------------------------------------------------------------------
EXTRA = {
    "ffmpeg": [r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg-*\bin\ffmpeg.exe",
               r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"],
    "ffprobe": [r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg-*\bin\ffprobe.exe",
                r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffprobe.exe"],
    "magick": [r"%ProgramFiles%\ImageMagick-*\magick.exe"],
    "pandoc": [r"%LOCALAPPDATA%\Pandoc\pandoc.exe", r"%ProgramFiles%\Pandoc\pandoc.exe"],
    "pdftotext": [r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\oschwartz10612.Poppler*\poppler-*\Library\bin\pdftotext.exe",
                  r"%LOCALAPPDATA%\Microsoft\WinGet\Links\pdftotext.exe"],
    "pdftoppm": [r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\oschwartz10612.Poppler*\poppler-*\Library\bin\pdftoppm.exe",
                 r"%LOCALAPPDATA%\Microsoft\WinGet\Links\pdftoppm.exe"],
    "gswin64c": [r"%ProgramFiles%\gs\gs*\bin\gswin64c.exe"],
    "gswin32c": [r"%ProgramFiles(x86)%\gs\gs*\bin\gswin32c.exe"],
    "msedge": [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
               r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"],
    "chrome": [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
               r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"],
    "tesseract": [r"%ProgramFiles%\Tesseract-OCR\tesseract.exe"],
    "cjxl": [r"%ProgramFiles%\libjxl\cjxl.exe"],
    "djxl": [r"%ProgramFiles%\libjxl\djxl.exe"],
}
ALT = {"gs": ["gswin64c", "gswin32c", "gs"], "browser": ["msedge", "chrome"]}
HINT = {
    "browser": "Microsoft Edge or Google Chrome (only needed for document -> PDF)",
}


def refresh_path():
    """Explorer has a stale PATH after a fresh install - read it from the registry."""
    try:
        import winreg
    except ImportError:
        return
    parts = []
    for root, sub in ((winreg.HKEY_LOCAL_MACHINE,
                       r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(root, sub) as k:
                v, _ = winreg.QueryValueEx(k, "Path")
                parts.append(os.path.expandvars(v))
        except OSError:
            pass
    os.environ["PATH"] = os.pathsep.join(parts + [os.environ.get("PATH", "")])


def tool(name):
    p = tools_manager.local_path(name)           # 1. folder tools\
    if p:
        return p
    for n in ALT.get(name, [name]):              # 2. PATH / old install locations (fallback)
        p = shutil.which(n)
        if p:
            return p
        for pat in EXTRA.get(n, []):
            m = glob.glob(os.path.expandvars(pat))
            if m:
                return m[0]
    p = tools_manager.system_exe(name)           # 3. a normal installation (registry, Program Files, scoop...)
    if p:
        return p
    hint = HINT.get(name, "will be downloaded automatically into the tools folder (check your internet connection)")
    raise RuntimeError(f"Tool '{name}' not found: {hint}")


def have(name):
    try:
        tool(name)
        return True
    except RuntimeError:
        return False


def needed_tools(cat, s, d):
    """Tools needed to convert category cat, from extension s to d."""
    if cat in ("video", "audio"):
        return ["ffmpeg", "ffprobe"]
    jxl = (["cjxl"] if d == "jxl" else []) + (["djxl"] if s == "jxl" else [])
    if cat == "image":
        return ["magick"] + jxl
    if cat == "pdf":
        if d in IMAGE_OUT:
            return ["pdftoppm", "pdfinfo", "pdfimages", "magick"] + jxl
        if d == "pdf":
            return ["qpdf", "gs", "pdfimages", "pdftoppm", "pdfinfo", "pdftocairo", "pikepdf"]
        if d == "docx":                    # page layout, columns and tables; fallback pdftotext + Pandoc
            return ["pdf2docx", "pdftotext", "pdfinfo"]
        return ["pdftotext", "pdfinfo"] + ([] if d == "txt" else ["pandoc"])
    if cat == "text":
        return [] if d == "pdf" and s == "html" else ["pandoc"]
    return []


def run(cmd):
    p = prun(cmd, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "unknown error").strip()[-700:])
    return p


# ----------------------------------------------------------------------------
# Resolution: "1080p", "1920x1080", "1920x1080!", "1280" (max width), "x720" (max height), "50%"
# ----------------------------------------------------------------------------
def parse_res(text):
    """Returns a spec or None (original resolution). Raises ValueError on bad input."""
    t = (text or "").strip().lower().replace("×", "x").replace(" ", "")
    if t in ("", "original", "orig"):
        return None
    err = ValueError("Resolution: use e.g. 1080p, 1920x1080, 1920x1080! (exact), "
                     "1280 (max width), x720 (max height) or 50%.")
    m = re.fullmatch(r"(\d+)p", t)
    if m and int(m[1]) > 0:
        return ("short", int(m[1]))
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)%", t)
    if m and float(m[1].replace(",", ".")) > 0:
        return ("pct", float(m[1].replace(",", ".")))
    m = re.fullmatch(r"(\d*)x(\d*)(!?)", t)
    if m and (m[1] or m[2]):
        w, h = int(m[1] or 0), int(m[2] or 0)
        if not (w or h) or (m[3] and not (w and h)):
            raise err
        return ("box", w, h, bool(m[3]))
    m = re.fullmatch(r"(\d+)", t)
    if m and int(m[1]) > 0:
        return ("box", int(m[1]), 0, False)
    raise err


def res_label(spec):
    """Short text for the output file name."""
    if not spec:
        return ""
    if spec[0] == "short":
        return f"{spec[1]}p"
    if spec[0] == "pct":
        return f"{spec[1]:g}pct"
    return f"{spec[1] or ''}x{spec[2] or ''}"


def res_tag(src, cat, spec):
    """What goes into the output file name: the size the result REALLY has (e.g. 2560x1440), so the name never says
    something the file is not. Falls back to the plain label when the size cannot be determined."""
    try:
        if cat == "image":
            w, h = img_dims(src)
        elif cat == "video":
            w, h = video_wh(first_stream(probe(str(src)), "video"))
        else:
            return res_label(spec)
        nd = compute_dims(w, h, spec, even=(cat == "video")) or (w, h)
        return f"{nd[0]}x{nd[1]}" if nd[0] and nd[1] else res_label(spec)
    except Exception:
        return res_label(spec)


def compute_dims(w, h, spec, even=False):
    """New size (w, h), or None when the file already has exactly that size. The chosen resolution is applied as asked -
    smaller OR larger (plain Lanczos resampling, no AI: enlarging adds pixels, not detail).
    '1080p' = shorter side 1080 (landscape 1920x1080, portrait 1080x1920).
    'WxH' = fits in the box keeping the aspect ratio (an exclamation mark forces the exact size)."""
    if not spec or not w or not h:
        return None
    kind = spec[0]
    if kind == "pct":
        f = spec[1] / 100
    elif kind == "short":
        f = spec[1] / min(w, h)
    else:
        bw, bh, exact = spec[1], spec[2], spec[3]
        if exact:                                 # ignores the aspect ratio; a missing dimension = the original one
            nw, nh = bw or w, bh or h
            f = None
        else:
            f = min(bw / w if bw else float("inf"), bh / h if bh else float("inf"))
    if kind != "box" or f is not None:
        nw, nh = max(1, round(w * f)), max(1, round(h * f))
    if even:
        nw, nh = max(2, nw - nw % 2), max(2, nh - nh % 2)
    return None if (nw, nh) == (w, h) else (nw, nh)


# ----------------------------------------------------------------------------
# ffprobe
# ----------------------------------------------------------------------------
def probe(src):
    try:
        p = run([tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", src])
        return json.loads(p.stdout)
    except Exception:
        return {}


def first_stream(info, kind):
    for s in info.get("streams", []):
        if s.get("codec_type") == kind and not (s.get("disposition") or {}).get("attached_pic"):
            return s
    return {}


def probe_duration(info):
    try:
        d = float(info.get("format", {}).get("duration"))
        return d if d > 0 else None
    except (TypeError, ValueError):
        return None


def video_wh(vs):
    """Frame size AFTER applying the rotation from the metadata (phones)."""
    try:
        w, h = int(vs.get("width", 0)), int(vs.get("height", 0))
    except (TypeError, ValueError):
        return 0, 0
    rot = 0
    for sd in vs.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = sd["rotation"]
    if not rot:
        rot = (vs.get("tags") or {}).get("rotate", 0)
    try:
        if abs(int(float(rot))) % 180 == 90:
            w, h = h, w
    except (TypeError, ValueError):
        pass
    return w, h


def duration(src):
    d = probe_duration(probe(src))
    if not d:
        raise RuntimeError("Cannot determine the file duration.")
    return d


def safe_duration(src):
    try:
        return duration(src)
    except Exception:
        return None


# ----------------------------------------------------------------------------
# Audio / video
# ----------------------------------------------------------------------------
# v: video codec, a: audio codec (old mode), q: quality (old mode / no limit),
# hq: quality after a resolution change, two: two-pass encoding (compress to size)
VIDEO_ENC = {
    "mp4":  dict(v=["-c:v", "libx264", "-pix_fmt", "yuv420p"], a=["-c:a", "aac"],
                 q=["-crf", "23"], hq=["-crf", "18"], two=True, extra=["-movflags", "+faststart"]),
    "mov":  dict(v=["-c:v", "libx264", "-pix_fmt", "yuv420p"], a=["-c:a", "aac"],
                 q=["-crf", "23"], hq=["-crf", "18"], two=True, extra=["-movflags", "+faststart"]),
    "mkv":  dict(v=["-c:v", "libx264", "-pix_fmt", "yuv420p"], a=["-c:a", "aac"],
                 q=["-crf", "23"], hq=["-crf", "18"], two=True, extra=[]),
    "3gp":  dict(v=["-c:v", "libx264", "-pix_fmt", "yuv420p"], a=["-c:a", "aac"],
                 q=["-crf", "26"], hq=["-crf", "20"], two=True, extra=[]),
    "webm": dict(v=["-c:v", "libvpx-vp9"], a=["-c:a", "libopus"],
                 q=["-crf", "32", "-b:v", "0"], hq=["-crf", "28", "-b:v", "0"], two=True, extra=[]),
    "avi":  dict(v=["-c:v", "mpeg4"], a=["-c:a", "libmp3lame"],
                 q=["-q:v", "4"], hq=["-q:v", "2"], two=False, extra=[]),
    "wmv":  dict(v=["-c:v", "wmv2"], a=["-c:a", "wmav2"],
                 q=["-q:v", "4"], hq=["-q:v", "2"], two=False, extra=[]),
    "flv":  dict(v=["-c:v", "flv"], a=["-c:a", "libmp3lame", "-ar", "44100"],
                 q=["-q:v", "4"], hq=["-q:v", "2"], two=False, extra=[]),
    "mpg":  dict(v=["-c:v", "mpeg2video"], a=["-c:a", "mp2"],
                 q=["-q:v", "4"], hq=["-q:v", "2"], two=False, extra=[]),
}

# Codecs the container accepts without re-encoding (None = practically everything)
COPY_V = {
    "mp4": {"h264", "hevc", "mpeg4", "av1", "vp9"},
    "mov": {"h264", "hevc", "mpeg4", "prores", "mjpeg"},
    "mkv": None,
    "webm": {"vp8", "vp9", "av1"},
    "avi": {"h264", "mpeg4", "mpeg2video", "mjpeg", "msmpeg4v3", "ffv1", "huffyuv"},
    "wmv": {"wmv1", "wmv2", "wmv3", "vc1"},
    "flv": {"flv1", "h264", "vp6f"},
    "mpg": {"mpeg1video", "mpeg2video"},
    "3gp": {"h264", "mpeg4", "h263"},
}
COPY_A = {
    "mp4": {"aac", "mp3", "ac3", "eac3", "alac", "opus"},
    "mov": {"aac", "mp3", "ac3", "eac3", "alac", "pcm_s16le", "pcm_s24le", "pcm_s16be", "pcm_s24be"},
    "mkv": None,
    "webm": {"opus", "vorbis"},
    "avi": {"mp3", "mp2", "ac3", "pcm_s16le", "pcm_s24le", "pcm_u8"},
    "wmv": {"wmav1", "wmav2", "wmapro"},
    "flv": {"mp3", "aac"},
    "mpg": {"mp2", "mp3", "ac3"},
    "3gp": {"aac", "amr_nb", "amr_wb"},
}

# Lossless video encoding per container: (arguments, truly lossless)
V_LOSSLESS = {
    "mp4": (["-c:v", "libx264", "-crf", "0", "-preset", "medium"], True),
    "mov": (["-c:v", "libx264", "-crf", "0", "-preset", "medium"], True),
    "mkv": (["-c:v", "libx264", "-crf", "0", "-preset", "medium"], True),
    "3gp": (["-c:v", "libx264", "-crf", "0", "-preset", "medium"], True),
    "webm": (["-c:v", "libvpx-vp9", "-lossless", "1", "-row-mt", "1"], True),
    "avi": (["-c:v", "ffv1", "-level", "3"], True),
    "wmv": (["-c:v", "wmv2", "-q:v", "1"], False),
    "flv": (["-c:v", "flv", "-q:v", "1"], False),
    "mpg": (["-c:v", "mpeg2video", "-q:v", "1"], False),
}
# Default encoding when the streams cannot be copied into the container: quality practically indistinguishable
# from the original, and an ordinary file (plays on TVs, phones and in browsers).
# (arguments, truly lossless) - same shape as V_LOSSLESS so the code can use both the same way
V_GOOD = {
    "mp4": (["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"], False),
    "mov": (["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"], False),
    "mkv": (["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"], False),
    "3gp": (["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"], False),
    "webm": (["-c:v", "libvpx-vp9", "-crf", "16", "-b:v", "0", "-row-mt", "1", "-pix_fmt", "yuv420p"], False),
    "avi": (["-c:v", "mpeg4", "-q:v", "2", "-pix_fmt", "yuv420p"], False),
    "wmv": (["-c:v", "wmv2", "-q:v", "1"], False),
    "flv": (["-c:v", "flv", "-q:v", "1"], False),
    "mpg": (["-c:v", "mpeg2video", "-q:v", "1"], False),
}
# Audio in mp4/mov when copying is impossible: AAC 256k instead of ALAC (ALAC does not play on many TVs and browsers)
A_GOOD = {"mp4": ["-c:a", "aac", "-b:a", "256k"], "mov": ["-c:a", "aac", "-b:a", "256k"]}
# Lossless audio inside a video container (when copying is impossible); None = by bit depth (PCM)
A_LOSSLESS = {
    "mp4": (["-c:a", "alac"], True),
    "mov": (["-c:a", "alac"], True),
    "mkv": (["-c:a", "flac"], True),
    "avi": (None, True),
    "3gp": (["-c:a", "aac", "-b:a", "320k"], False),
    "webm": (["-c:a", "libopus", "-b:a", "256k"], False),
    "wmv": (["-c:a", "wmav2", "-b:a", "320k"], False),
    "flv": (["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "44100"], False),
    "mpg": (["-c:a", "mp2", "-b:a", "384k"], False),
}

# a: codec, q: quality (old mode), best: highest quality (default), lossy: whether the bitrate can be controlled
AUDIO_ENC = {
    "mp3":  dict(a=["-c:a", "libmp3lame"], q=["-q:a", "2"], best=["-b:a", "320k"], lossy=True, maxk=320),
    "aac":  dict(a=["-c:a", "aac"], q=["-b:a", "192k"], best=["-b:a", "320k"], lossy=True, maxk=512),
    "m4a":  dict(a=["-c:a", "aac"], q=["-b:a", "192k"], best=["-b:a", "320k"], lossy=True, maxk=512),
    "ogg":  dict(a=["-c:a", "libvorbis"], q=["-q:a", "5"], best=["-q:a", "10"], lossy=True, maxk=500),
    "opus": dict(a=["-c:a", "libopus"], q=["-b:a", "128k"], best=["-b:a", "256k"], lossy=True, maxk=256),
    "wma":  dict(a=["-c:a", "wmav2"], q=["-b:a", "192k"], best=["-b:a", "320k"], lossy=True, maxk=384),
    "ac3":  dict(a=["-c:a", "ac3"], q=["-b:a", "192k"], best=["-b:a", "640k"], lossy=True, maxk=640),
    "wav":  dict(a=["-c:a", "pcm_s16le"], q=[], best=[], lossy=False),
    "flac": dict(a=["-c:a", "flac"], q=[], best=["-compression_level", "8"], lossy=False),
    "aiff": dict(a=["-c:a", "pcm_s16be"], q=[], best=[], lossy=False),
}
# Audio codecs that can go into a given format without re-encoding
AUDIO_COPY = {
    "mp3": {"mp3"}, "aac": {"aac"}, "m4a": {"aac", "alac"}, "ogg": {"vorbis", "opus"},
    "opus": {"opus"}, "wma": {"wmav1", "wmav2"}, "ac3": {"ac3"}, "flac": {"flac"},
    "wav": {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_u8", "pcm_f32le"},
    "aiff": {"pcm_s16be", "pcm_s24be", "pcm_s32be"},
}
LOSSLESS_CODECS = {"flac", "alac", "wavpack", "ape", "tta", "truehd", "mlp"}


def is_lossless_codec(codec):
    return bool(codec) and (codec in LOSSLESS_CODECS or codec.startswith("pcm_"))


def pcm_codec(astream, endian):
    """PCM codec with the same bit depth as the source (16/24/32 bit, float)."""
    codec = astream.get("codec_name") or ""
    if codec.startswith("pcm_f"):
        return "pcm_f32" + endian
    if not is_lossless_codec(codec):
        return "pcm_s16" + endian
    bits = 0
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        try:
            bits = max(bits, int(astream.get(key) or 0))
        except (TypeError, ValueError):
            pass
    if not bits:
        fmt = astream.get("sample_fmt", "")
        bits = 32 if fmt.startswith("s32") else 16
    n = 16 if bits <= 16 else 24 if bits <= 24 else 32
    return f"pcm_s{n}{endian}"


PROGRESS = lambda frac: None  # the GUI plugs a function in here; frac: 0..1 or None (unknown progress)
STEP = lambda msg: None       # an entry in the window log ("   • ...")
DETAIL = lambda msg: None     # short description of the current step for the status bar


def step(msg):
    """What the program is doing now: a log entry + the status bar."""
    STEP(msg)
    DETAIL(msg)


def mb(n):
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{n / 1024:.0f} KB"


def run_ff(cmd, dur=None, start=0.0, span=1.0):
    """Runs ffmpeg and reports progress as it goes (via -progress)."""
    cmd = [str(c) for c in cmd]
    cmd = [cmd[0], "-hide_banner", "-nostats", "-progress", "pipe:1", *cmd[1:]]
    p = spawn(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
              encoding="utf-8", errors="replace")
    err = []
    t = threading.Thread(target=lambda: err.extend(p.stderr.readlines()), daemon=True)
    t.start()
    last = -1.0
    try:
        for line in p.stdout:
            if CANCEL.is_set():
                _kill_tree(p)
                break
            if line.startswith(("out_time_us=", "out_time_ms=")) and dur:
                try:
                    sec = int(line.split("=")[1]) / 1_000_000
                except ValueError:
                    continue
                frac = min(1.0, start + span * sec / dur)
                if frac - last >= 0.005:
                    last = frac
                    PROGRESS(frac)
        p.wait()
        t.join(timeout=2)
    finally:
        release(p)
    check_cancel()
    if p.returncode != 0:
        raise RuntimeError("".join(err).strip()[-700:] or "ffmpeg exited with an error")


COVER_FORMATS = ("mp3", "m4a", "flac", "ogg", "opus")      # target formats that can carry a cover picture


def conv_audio(src, dst, target, res=None):
    """Audio / video -> audio. The cover picture (embedded thumbnail, e.g. from yt-dlp, or a frame of the video) is
    put into the result where the format allows it; if the format refuses it, the conversion is repeated without."""
    ctx = {"cover": False}
    try:
        return _conv_audio(src, dst, target, True, ctx)
    except RuntimeError as e:
        if not ctx["cover"]:
            raise
        STEP(f"(the cover picture could not be embedded in .{ext_of(dst)}: {str(e)[:80]} — converting without it)")
        try:
            Path(dst).unlink()
        except OSError:
            pass
        return _conv_audio(src, dst, target, False, ctx)


def _conv_audio(src, dst, target, allow_cover, ctx):
    d = ext_of(dst)
    enc = AUDIO_ENC[d]
    info = probe(src)
    dur = probe_duration(info)

    # Does the source already contain a cover (e.g. a thumbnail from yt-dlp stored as an attached_pic stream)?
    cover_stream_index = None
    for s in info.get("streams", []):
        if s.get("codec_type") == "video" and (s.get("disposition") or {}).get("attached_pic"):
            cover_stream_index = s.get("index")
            break

    tmp_dir = Path(tempfile.mkdtemp())
    cover_path = tmp_dir / "cover.jpg"
    has_cover = False

    try:
        try:
            if allow_cover and d in COVER_FORMATS:
                if cover_stream_index is not None:
                    # take the ready-made picture embedded in the source file
                    prun([tool("ffmpeg"), "-y", "-i", src, "-map", f"0:{cover_stream_index}", "-vframes", "1", str(cover_path)])
                    has_cover = cover_path.exists() and cover_path.stat().st_size > 0
                elif first_stream(info, "video"):
                    # no embedded cover: use a representative frame, taken 10% into the video
                    seek_time = min(max((dur or 30) * 0.1, 3.0), 60.0)
                    p = prun([tool("ffmpeg"), "-y", "-ss", str(seek_time), "-i", src, "-vframes", "1", "-q:v", "2", str(cover_path)])
                    if p.returncode != 0 or not cover_path.exists() or cover_path.stat().st_size == 0:
                        prun([tool("ffmpeg"), "-y", "-i", src, "-vframes", "1", "-q:v", "2", str(cover_path)])
                    has_cover = cover_path.exists() and cover_path.stat().st_size > 0
        except Exception:
            has_cover = False
        ctx["cover"] = has_cover

        if has_cover:
            base = [tool("ffmpeg"), "-y", "-i", src, "-i", str(cover_path), "-map", "0:a:0", "-map", "1:v:0",
                    "-c:v", "mjpeg", "-disposition:v:0", "attached_pic", "-id3v2_version", "3", "-map_metadata", "0"]
            step("Embedding the cover picture")
        else:
            base = [tool("ffmpeg"), "-y", "-i", src, "-vn", "-map_metadata", "0"]

        if not target:
            if not LOSSLESS:
                run_ff(base + enc["a"] + enc["q"] + [dst], dur)
                return ""
            a = first_stream(info, "audio")
            codec = a.get("codec_name")
            note = ""
            if codec in AUDIO_COPY[d]:                     # same codec -> no re-encoding
                args = ["-c:a", "copy"]
                step(f"Copying the audio stream ({codec}) without re-encoding")
            elif d == "m4a" and is_lossless_codec(codec):  # FLAC/WAV -> M4A without loss
                args = ["-c:a", "alac"]
                step("Lossless ALAC encoding")
            elif d == "wav":
                args = ["-c:a", pcm_codec(a, "le")]
                step(f"Decoding to PCM ({args[1]})")
            elif d == "aiff":
                args = ["-c:a", pcm_codec(a, "be")]
                step(f"Decoding to PCM ({args[1]})")
            else:
                args = enc["a"] + enc["best"]
                step(f"Encoding audio to .{d} (max quality)")
                if enc["lossy"]:
                    note = f".{d} is a lossy format - maximum quality was used"
            run_ff(base + args + [dst], dur)
            return note

        if not enc["lossy"]:
            raise RuntimeError("A lossless format (wav/flac/aiff) does not support compression to a "
                               "given size. Choose mp3, aac, ogg or opus.")
        if not dur:
            raise RuntimeError("Cannot determine the file duration.")

        kbps = int(target * 8 * 0.97 / dur / 1000)
        a_src = first_stream(info, "audio")
        try:
            src_k = int(float(a_src.get("bit_rate") or info.get("format", {}).get("bit_rate") or 0) / 1000)
        except (TypeError, ValueError):
            src_k = 0
        if src_k and not is_lossless_codec(a_src.get("codec_name")) and kbps > src_k:
            kbps = src_k
            step(f"The limit allows more than the source has - staying at {src_k} kb/s")
        else:
            step(f"Plan: limit {mb(target)} at {dur:.0f} s = about {kbps} kb/s")

        for n in range(5):
            k = max(8, min(kbps, enc["maxk"]))
            cmd = base + enc["a"] + ["-b:a", f"{k}k"]
            if d == "mp3":
                cmd = base + enc["a"] + ["-abr", "1", "-b:a", f"{k}k"]
                if k < 32:
                    cmd += ["-ar", "22050" if k >= 16 else "16000"]
            step(f"Attempt {n + 1}: {k} kb/s")
            run_ff(cmd + [dst], dur)
            size = Path(dst).stat().st_size
            STEP(f"Attempt {n + 1}: {k} kb/s → {mb(size)} (limit {mb(target)})")
            if size <= target or k <= 8:
                return ""
            kbps = min(k - 1, int(k * target / size * 0.98))
        return ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _audio_args_for_video(d, a, enc):
    """Audio inside a video file: copy if the container accepts it; otherwise lossless encoding."""
    if not a:
        return [], ""
    codec = a.get("codec_name")
    if not LOSSLESS:
        return enc["a"] + ["-b:a", "128k"], ""
    allowed = COPY_A[d]
    if allowed is None or codec in allowed:
        return ["-c:a", "copy"], ""
    if not VIDEO_TRUE_LOSSLESS and d in A_GOOD:
        return A_GOOD[d], f"audio ({codec or '?'}) re-encoded to AAC 256k — .{d} will not take it unchanged"
    args, truly = A_LOSSLESS[d]
    if args is None:
        args = ["-c:a", pcm_codec(a, "le")]
    return args, ("" if truly else f"audio in .{d} can only be lossy")


# Subtitle handling per container: "copy" = any subtitle codec as it is; otherwise text subtitles are converted
# to the given codec; containers not listed here cannot hold subtitles at all.
SUB_TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
SUB_CONTAINER = {"mkv": "copy", "mp4": "mov_text", "mov": "mov_text", "3gp": "mov_text", "webm": "webvtt"}


def _index_args(args, k):
    """"-c:a x -b:a y" -> "-c:a:k x -b:a:k y" (options for the k-th output audio stream)."""
    return [f"{a}:{k}" if a in ("-c:a", "-b:a", "-ar", "-ac") else a for a in args]


def _track_args(d, info, enc):
    """-map / codec arguments that keep the first video track, ALL audio tracks and every subtitle track the
    container can hold (plus fonts attached to .mkv files). Returns (maps, audio_args, subtitle_args, notes)."""
    streams = info.get("streams", [])
    maps, aargs, sargs, notes = ["-map", "0:v:0"], [], [], []
    auds = [x for x in streams if x.get("codec_type") == "audio"]
    for k, a in enumerate(auds):
        maps += ["-map", f"0:{a['index']}"]
        args, note = _audio_args_for_video(d, a, enc)
        aargs += _index_args(args, k)
        if note and note not in notes:
            notes.append(note)
    if len(auds) > 1:
        notes.append(f"{len(auds)} audio tracks kept")
    smaps, sargs, snotes = _subtitle_args(d, streams)
    return maps + smaps, aargs, sargs, notes + snotes


def _subtitle_args(d, streams):
    """-map / codec arguments for the subtitle tracks (and mkv attachments) the container .d can hold.
    Returns (maps, subtitle_args, notes)."""
    maps, sargs, notes = [], [], []
    kind = SUB_CONTAINER.get(d)
    kept = skipped = 0
    converted = False
    for x in (x for x in streams if x.get("codec_type") == "subtitle"):
        codec = x.get("codec_name")
        if kind is None or (kind != "copy" and codec not in SUB_TEXT_CODECS):
            skipped += 1
            continue
        maps += ["-map", f"0:{x['index']}"]
        if kind == "copy" or codec == kind:
            sargs += [f"-c:s:{kept}", "copy"]
        else:
            sargs += [f"-c:s:{kept}", kind]
            converted = True
        kept += 1
    if kept:
        notes.append(f"{kept} subtitle track(s) kept" + (f" (converted to {kind}, styling may be simplified)" if converted else ""))
    if skipped:
        notes.append(f"{skipped} subtitle track(s) dropped: " +
                     (f".{d} cannot hold subtitles" if kind is None
                      else f".{d} cannot hold image-based subtitles (use .mkv to keep them)"))
    if d == "mkv" and any(x.get("codec_type") == "attachment" for x in streams):
        maps += ["-map", "0:t"]                       # fonts etc. needed by styled subtitles
    return maps, sargs, notes


ASK_AUDIO = None      # the window plugs in function(file_name, [(stream_index, label), ...]) -> indexes to keep / None


def _audio_label(k, a):
    tags = a.get("tags") or {}
    parts = [f"#{k + 1}", (tags.get("language") or "und").lower(), a.get("codec_name") or "?"]
    if a.get("channels"):
        parts.append(f"{a['channels']} ch")
    try:
        parts.append(f"{int(a.get('bit_rate')) // 1000} kb/s")
    except (TypeError, ValueError):
        pass
    if tags.get("title"):
        parts.append(f"“{tags['title']}”")
    return "  ".join(parts)


def _pick_audio_tracks(src, auds):
    """Compress-to-size mode: with several audio tracks the window asks which ones to keep (every kept track is
    re-encoded and takes a share of the size limit). Without a window / question all tracks are kept."""
    if len(auds) < 2 or ASK_AUDIO is None:
        return auds
    keep = ASK_AUDIO(Path(src).name, [(a["index"], _audio_label(k, a)) for k, a in enumerate(auds)])
    if keep is None:
        return auds
    return [a for a in auds if a["index"] in keep]


# ----------------------------------------------------------------------------
# Optional GPU encoding (NVENC / Quick Sync / AMF) - only for the "high quality" H.264 encodes, never for a size limit
# ----------------------------------------------------------------------------
_GPU = {"done": False, "enc": None}
_GPU_LOCK = threading.Lock()


def gpu_encoder():
    """Name of a working hardware H.264 encoder (h264_nvenc / h264_qsv / h264_amf) or None. Tested once with a tiny
    real encode, because ffmpeg lists encoders even when the computer has no matching graphics card."""
    with _GPU_LOCK:
        if not _GPU["done"]:
            _GPU["done"] = True
            try:
                listed = prun([tool("ffmpeg"), "-hide_banner", "-encoders"], text=True,
                              encoding="utf-8", errors="replace").stdout or ""
                for name in ("h264_nvenc", "h264_qsv", "h264_amf"):
                    if name not in listed:
                        continue
                    r = prun([tool("ffmpeg"), "-hide_banner", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=5:duration=1",
                              "-pix_fmt", "yuv420p", "-c:v", name, "-f", "null", "-"], text=True)
                    if r.returncode == 0:
                        _GPU["enc"] = name
                        break
            except Cancelled:
                raise
            except Exception:
                pass
        return _GPU["enc"]


def hw_decode_args():
    """Decode on the graphics card too (falls back to the processor by itself when a format is not supported)."""
    return ["-hwaccel", "auto"] if USE_GPU and gpu_encoder() else []


def gpu_video_args(vargs):
    """vargs with libx264 + crf (+ preset / pix_fmt) -> the same quality on the graphics card, when the option is on
    and a working encoder exists. Anything else is returned unchanged."""
    if not USE_GPU or "libx264" not in vargs or "-crf" not in vargs:
        return vargs
    enc = gpu_encoder()
    if not enc:
        STEP("GPU option is on, but no working hardware H.264 encoder was found - using the processor (libx264)")
        return vargs
    STEP(f"Encoding on the graphics card ({enc})")
    crf = vargs[vargs.index("-crf") + 1]
    rest, skip = [], 0
    for k, a in enumerate(vargs):
        if skip:
            skip -= 1
        elif a in ("-c:v", "-crf", "-preset", "-pix_fmt"):
            skip = 1
        else:
            rest.append(a)
    if enc == "h264_nvenc":
        new = ["-c:v", enc, "-preset", "p5", "-rc", "vbr", "-cq", crf, "-b:v", "0", "-pix_fmt", "yuv420p"]
    elif enc == "h264_qsv":
        new = ["-c:v", enc, "-preset", "slow", "-global_quality", crf, "-pix_fmt", "nv12"]
    else:
        new = ["-c:v", enc, "-quality", "quality", "-rc", "cqp", "-qp_i", crf, "-qp_p", crf, "-qp_b", crf,
               "-pix_fmt", "yuv420p"]
    return rest + new


def conv_video(src, dst, target, res=None):
    d = ext_of(dst)
    enc = VIDEO_ENC[d]
    ff = tool("ffmpeg")
    info = probe(src)
    vs = first_stream(info, "video")
    w, h = video_wh(vs)
    dims = compute_dims(w, h, res, even=True)
    scale = f"scale={dims[0]}:{dims[1]}:flags=lanczos" if dims else None
    fix_even = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    dur = probe_duration(info)

    if target:                                    # compress to size (lossy by definition)
        streams = info.get("streams", [])
        auds = [x for x in streams if x.get("codec_type") == "audio"]
        keep = _pick_audio_tracks(src, auds)      # several audio tracks: the window asks which to keep
        maps = ["-map", "0:v:0"]
        for a in keep:
            maps += ["-map", f"0:{a['index']}"]
        smaps, sargs, tnotes = _subtitle_args(d, streams)      # subtitles are tiny - always kept when possible
        maps += smaps
        if len(keep) < len(auds):
            tnotes.append(f"{len(auds) - len(keep)} audio track(s) removed at your request")
        if len(keep) > 1:
            tnotes.append(f"{len(keep)} audio tracks kept and compressed")
        tnote = "; ".join(tnotes)
        vf = ["-vf", scale or fix_even]
        dur = duration(src)
        total_bps = target * 8 * 0.95 / dur
        n_a = len(keep)
        abps = 128_000 if total_bps > 300_000 else 48_000          # per audio track
        if n_a > 1:                                                # several tracks: at most ~35% of the budget
            abps = max(min(abps, int(total_bps * 0.35 / n_a)), 24_000)
        vbps = max(int(total_bps - abps * n_a), 30_000)
        ab = str(abps)
        try:
            src_v = int(vs.get("bit_rate") or 0)
        except (TypeError, ValueError):
            src_v = 0
        if src_v and vbps > src_v:
            vbps = src_v                  # a bitrate higher than the source's would not improve anything
        step(f"Plan: limit {mb(target)} at {dur:.0f} s → video {vbps // 1000} kb/s"
             + (f" + audio {abps // 1000} kb/s" + (f" × {n_a} tracks" if n_a > 1 else "") if n_a else " (no audio)"))

        def single_pass(vbps):
            run_ff([ff, "-y", "-i", src, *maps, *vf, *enc["v"], "-b:v", str(vbps),
                    "-maxrate", str(int(vbps * 1.5)), "-bufsize", str(vbps * 2),
                    *enc["a"], "-b:a", ab, *sargs, *enc["extra"], dst], dur)

        def encode(vbps):
            vb = str(vbps)
            if not enc["two"]:
                single_pass(vbps)
                return
            DETAIL("pass 1/2 (analysis)")
            tmp = Path(tempfile.mkdtemp())
            log = str(tmp / "pass")
            try:
                try:
                    run_ff([ff, "-y", "-i", src, "-map", "0:v:0", *vf, *enc["v"], "-b:v", vb,
                            "-pass", "1", "-passlogfile", log, "-an", "-f", "null", "NUL"], dur, 0.0, 0.5)
                    DETAIL("pass 2/2 (encoding)")
                    run_ff([ff, "-y", "-i", src, *maps, *vf, *enc["v"], "-b:v", vb, "-pass", "2",
                            "-passlogfile", log, *enc["a"], "-b:a", ab, *sargs, *enc["extra"], dst], dur, 0.5, 0.5)
                except RuntimeError:
                    # very short clips can break the encoder's two-pass statistics - a single pass still works
                    STEP("(two-pass encoding failed on this clip — using a single pass instead)")
                    single_pass(vbps)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        for n in range(1, 4):
            step(f"Attempt {n}/3: video bitrate {vbps // 1000} kb/s" + (", 2 passes" if enc["two"] else ""))
            encode(vbps)
            size = Path(dst).stat().st_size
            STEP(f"Attempt {n}/3 → {mb(size)} (limit {mb(target)})")
            if size <= target or vbps <= 20_000:
                return tnote
            vbps = max(int(vbps * target / size * 0.96), 20_000)  # too big -> lower bitrate, once more
        return tnote

    maps, aargs, sargs, notes = _track_args(d, info, enc)      # all audio tracks + subtitles where possible
    vcodec = vs.get("codec_name")

    if res and not dims:
        notes.append("the video already has exactly this resolution")
    if dims:                                      # resolution change: re-encode in high quality
        step(f"Changing resolution {w}x{h} → {dims[0]}x{dims[1]} and encoding (high quality)")
        vargs = ["-vf", scale, *gpu_video_args([*enc["v"], *enc["hq"]])]
    elif not LOSSLESS:
        step("Encoding video")
        vargs = ["-vf", fix_even, *enc["v"], *enc["q"]]
    elif COPY_V[d] is None or vcodec in COPY_V[d]:   # remux: no loss, practically instant
        vargs = ["-c:v", "copy"]
        step(f"Copying the streams without re-encoding (remux, {vcodec or 'video'})")
        if vcodec == "hevc" and d in ("mp4", "mov"):
            vargs += ["-tag:v", "hvc1"]
        if vcodec == "h264" and d == "avi":
            vargs += ["-bsf:v", "h264_mp4toannexb"]     # AVI needs Annex B H.264 (MP4/MKV store it differently)
    elif VIDEO_TRUE_LOSSLESS:                        # deliberate choice in the window: truly lossless
        lv, truly = V_LOSSLESS[d]
        step(f"Encoding video {'losslessly' if truly else 'at max quality'} "
             f"({lv[1]}) — codec {vcodec or '?'} does not fit .{d}, this may take a while")
        vargs = ["-vf", fix_even, *lv]
        if truly:
            notes.append("lossless encoding: the file can be huge and will not play on many devices")
        else:
            notes.append(f"video in .{d} can only be lossy (max quality used)")
    else:                                            # default: high quality, plays everywhere
        lv, _ = V_GOOD[d]
        step(f"Encoding video in high quality ({lv[1]}) — codec {vcodec or '?'} does not fit .{d}, "
             f"so the streams cannot be copied; this may take a while")
        vargs = ["-vf", fix_even, *gpu_video_args(lv)]
        notes.append(f"video re-encoded ({vcodec or '?'} → {lv[1]}): quality practically indistinguishable "
                     f"from the original, and the file plays everywhere")
    hw = hw_decode_args() if "copy" not in vargs else []
    try:
        run_ff([ff, "-y", *hw, "-i", src, *maps, *vargs, *aargs, *sargs, *enc["extra"], dst], dur)
    except RuntimeError:
        if not hw:
            raise
        STEP("(hardware decoding failed on this file - decoding on the processor instead)")
        run_ff([ff, "-y", "-i", src, *maps, *vargs, *aargs, *sargs, *enc["extra"], dst], dur)
    return "; ".join(notes)


def conv_video_to_gif(src, dst, target, res=None, clip=None):
    """clip: (start_s, length_s or None) - only that part of the video becomes the GIF."""
    ff = tool("ffmpeg")
    info = probe(src)
    dur = probe_duration(info)
    cut = []
    if clip:
        cut = ["-ss", f"{clip[0]:.3f}"] + (["-t", f"{clip[1]:.3f}"] if clip[1] else [])
        dur = clip[1] or ((dur - clip[0]) if dur else None)
    w, h = video_wh(first_stream(info, "video"))
    dims = compute_dims(w, h, res, even=True)
    base_w = dims[0] if dims else 480

    def make(wd, ht=-1):
        step(f"Generating a {wd}px GIF (colour palette + frames)")
        run_ff([ff, "-y", *cut, "-i", src, "-an", "-vf",
                f"fps=12,scale={wd}:{ht}:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
                "-loop", "0", dst], dur)
        return Path(dst).stat().st_size

    size = make(base_w, dims[1] if dims else -1)
    wd = base_w
    if target:
        # GIF size grows with width to the power of ~1.5-2: we start at 1.6 and after the second measurement
        # the exponent comes from the measured points
        pexp, prev = 1.6, None
        for _ in range(4):
            STEP(f"GIF {wd}px → {mb(size)} (limit {mb(target)})")
            if size <= target:
                break
            if prev and prev[0] != wd and prev[1] != size:
                pexp = min(2.5, max(0.8, math.log(prev[1] / size) / math.log(prev[0] / wd)))
            nw = int(wd * (target * 0.93 / size) ** (1 / pexp)) // 2 * 2
            if nw >= wd:
                nw = (wd - 2) // 2 * 2
            if nw < 16:
                raise RuntimeError("Could not fit the GIF into the given size (try a shorter clip).")
            prev = (wd, size)
            wd = nw
            size = make(wd)
        if size > target:
            raise RuntimeError("Could not fit the GIF into the given size (try a shorter clip).")
    return "GIF has at most 256 colours and 12 fps"


def conv_av(src, dst, target, res=None):
    d = ext_of(dst)
    if d in AUDIO_ENC:
        return conv_audio(src, dst, target, res)
    if d == "gif":
        return conv_video_to_gif(src, dst, target, res)
    return conv_video(src, dst, target, res)


# ----------------------------------------------------------------------------
# Joining several videos into one
# ----------------------------------------------------------------------------
def _join_sig(info):
    v, a = first_stream(info, "video"), first_stream(info, "audio")
    n_a = sum(1 for x in info.get("streams", []) if x.get("codec_type") == "audio")
    return (v.get("codec_name"), v.get("width"), v.get("height"), v.get("pix_fmt"), v.get("r_frame_rate"),
            a.get("codec_name"), a.get("sample_rate"), a.get("channels"), n_a)


def join_videos(files, dst):
    """Joins the videos (in the given order) into dst. Same codec / size / frame rate / audio everywhere -> the
    streams are copied (no quality loss, instant). Otherwise everything is brought to the first video's size and
    frame rate and encoded once in high quality. Returns a note."""
    ff = tool("ffmpeg")
    dst = Path(dst)
    d = ext_of(dst)
    infos = [probe(str(f)) for f in files]
    for f, i in zip(files, infos):
        if not first_stream(i, "video"):
            raise RuntimeError(f"{Path(f).name} has no video track.")
    durs = [probe_duration(i) or 0 for i in infos]
    total = sum(durs) or None
    sigs = [_join_sig(i) for i in infos]
    codec_v, codec_a = sigs[0][0], sigs[0][5]
    cv, ca = COPY_V.get(d, "?"), COPY_A.get(d, "?")
    fits = (cv is None or (isinstance(cv, set) and codec_v in cv)) and \
           (codec_a is None or ca is None or (isinstance(ca, set) and codec_a in ca))
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    notes = []
    if any(x.get("codec_type") == "subtitle" for i in infos for x in i.get("streams", [])):
        notes.append("subtitles are not carried over")
    try:
        if all(x == sigs[0] for x in sigs) and fits:
            step(f"All {len(files)} videos match - joining without re-encoding")
            work = Path(tempfile.mkdtemp())
            try:
                lst = work / "list.txt"
                lst.write_text("".join("file '%s'\n" % Path(f).resolve().as_posix().replace("'", "'\\''") for f in files),
                               encoding="utf-8")
                tag = ["-tag:v", "hvc1"] if codec_v == "hevc" and d in ("mp4", "mov") else []
                enc = VIDEO_ENC.get(d, {"extra": []})
                run_ff([ff, "-y", "-f", "concat", "-safe", "0", "-i", lst, "-map", "0:v:0", "-map", "0:a?",
                        "-c", "copy", *tag, *enc["extra"], tmp], total)
            finally:
                shutil.rmtree(work, ignore_errors=True)
            notes.insert(0, "streams copied - no quality loss")
        else:
            if d not in VIDEO_ENC:
                raise RuntimeError(f"These videos differ, and .{d} cannot be written with re-encoding. "
                                   f"Join them into .mp4 or .mkv (rename the first file's format).")
            enc = VIDEO_ENC[d]
            v0 = first_stream(infos[0], "video")
            W, H = video_wh(v0)
            W, H = (W or 1920) // 2 * 2, (H or 1080) // 2 * 2
            try:
                num, den = (v0.get("r_frame_rate") or "30/1").split("/")
                fps = min(60.0, max(1.0, float(num) / float(den or 1)))
            except (ValueError, ZeroDivisionError):
                fps = 30.0
            audio = all(first_stream(i, "audio") for i in infos)
            step(f"The videos differ - joining at {W}x{H}, {fps:.3g} fps with one high-quality encode")
            cmd, parts = [ff, "-y"], []
            for k, f in enumerate(files):
                cmd += ["-i", str(f)]
                parts.append(f"[{k}:v:0]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                             f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:.6g},format=yuv420p[v{k}]")
                if audio:
                    parts.append(f"[{k}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{k}]")
            ins = "".join(f"[v{k}]" + (f"[a{k}]" if audio else "") for k in range(len(files)))
            parts.append(f"{ins}concat=n={len(files)}:v=1:a={1 if audio else 0}[v]" + ("[a]" if audio else ""))
            lv, _ = V_GOOD[d]
            vargs = gpu_video_args(lv)
            aargs = (A_GOOD.get(d) or enc["a"] + ["-b:a", "256k"]) if audio else ["-an"]
            run_ff(cmd + ["-filter_complex", ";".join(parts), "-map", "[v]"] + (["-map", "[a]"] if audio else [])
                   + vargs + aargs + enc["extra"] + [tmp], total)
            notes.insert(0, "videos were different - everything was re-encoded once in high quality")
            if not audio:
                notes.append("some videos have no audio, so the result has none")
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return "; ".join(notes)


# ----------------------------------------------------------------------------
# Trimming (cut a clip out of a video / audio file)
# ----------------------------------------------------------------------------
def parse_time(text, what="time"):
    """'45', '0:45', '1:02:03', '0:45.5' (also with a comma) -> seconds (float). Empty -> None."""
    t = (text or "").strip().replace(",", ".")
    if not t:
        return None
    parts = t.split(":")
    if len(parts) > 3 or not all(re.fullmatch(r"\d+(?:\.\d+)?", x or "x") for x in parts):
        raise ValueError(f"{what}: use seconds (45), minutes:seconds (0:45) or hours:minutes:seconds (1:02:03).")
    sec = 0.0
    for x in parts:
        sec = sec * 60 + float(x)
    return sec


def fmt_time(sec):
    """Seconds -> text safe for file names: 0m30s, 1h02m03s."""
    sec = int(round(sec))
    h, m, s = sec // 3600, sec // 60 % 60, sec % 60
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def conv_trim(src, dst, start, end, exact):
    """Cuts [start, end] (end None = to the end) out of a video/audio file, keeping the container.
    exact=False: streams are copied - no quality loss, instant, but the clip starts at the nearest keyframe BEFORE
    `start` (can be a few seconds early). exact=True (video): re-encodes in high quality for a frame-accurate cut."""
    info = probe(src)
    total = probe_duration(info)
    d = ext_of(dst)
    if total and start >= total:
        raise RuntimeError(f"The start ({fmt_time(start)}) is after the end of the file ({fmt_time(total)}).")
    if end is None or (total and end > total):
        end = total
    if end is not None and end <= start:
        raise RuntimeError("The end of the clip must be later than its start.")
    length = (end - start) if end else None
    ff = tool("ffmpeg")
    head = [ff, "-y", *(hw_decode_args() if exact else []), "-ss", f"{start:.3f}", "-i", src] + \
        (["-t", f"{length:.3f}"] if length else [])
    notes = []
    if first_stream(info, "video") and category(src) == "video":
        enc = VIDEO_ENC.get(d)
        if enc:
            maps, aargs, sargs, tn = _track_args(d, info, enc)
        else:
            maps, aargs, sargs, tn = ["-map", "0:v:0", "-map", "0:a?"], [], [], []
        if exact and enc:
            step("Cutting frame-accurately (the clip is re-encoded in high quality)")
            lv, _ = V_GOOD[d]
            vargs = ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", *gpu_video_args(lv)]
            notes += tn + ["frame-accurate cut; the video of the clip was re-encoded in high quality"]
            cmd = head + maps + vargs + aargs + sargs + enc["extra"] + ["-avoid_negative_ts", "make_zero", dst]
        else:
            step("Cutting without re-encoding (streams are copied)")
            vc = first_stream(info, "video").get("codec_name")
            tag = ["-tag:v", "hvc1"] if vc == "hevc" and d in ("mp4", "mov") else []
            cmd = head + maps + ["-c", "copy", *tag, "-avoid_negative_ts", "make_zero", dst]
            notes.append("no re-encoding; the clip may start up to a few seconds early (at the previous keyframe)"
                         if not exact else "exact cut is not available for this format - streams were copied")
        try:
            run_ff(cmd, length)
        except RuntimeError:
            if "-hwaccel" not in cmd:
                raise
            STEP("(hardware decoding failed on this file - decoding on the processor instead)")
            run_ff([c for k, c in enumerate(cmd) if c != "-hwaccel" and not (k and cmd[k - 1] == "-hwaccel")], length)
    else:                                         # audio: copying is precise enough, cover art is kept when possible
        step("Cutting the audio without re-encoding")
        try:
            run_ff(head + ["-map", "0:a:0", "-map", "0:v?", "-c", "copy", "-map_metadata", "0", dst], length)
        except RuntimeError:
            if Path(dst).exists():
                Path(dst).unlink()
            run_ff(head + ["-map", "0:a:0", "-c", "copy", "-map_metadata", "0", dst], length)
    return "; ".join(notes)


# ----------------------------------------------------------------------------
# Images (ImageMagick; JXL via cjxl/djxl - with lossless JPEG transcoding)
# ----------------------------------------------------------------------------
LOSSY_IMG = {"jpg", "webp", "avif", "jxl"}
DEFAULT_Q = {"jpg": 90, "webp": 90, "avif": 65, "jxl": 90}     # only when LOSSLESS = False


import math  # noqa: E402


def fit_param(measure, lo, hi, goal, guess, maxit=6, tol=0.88):
    """The largest integer parameter x in [lo, hi] for which measure(x) <= goal (size grows with x).
    Instead of bisection: start from a computed estimate and interpolate on a log(size) scale - usually
    2-4 measurements instead of 7-12. Returns (x, size) of the best result that fits, or None."""
    pts = {}
    best = over = None                       # best: largest x that fits; over: smallest x that does not
    x = int(min(max(round(guess), lo), hi))
    for _ in range(maxit):
        if x not in pts:
            pts[x] = measure(x)
        sz = pts[x]
        if sz <= goal:
            if best is None or x > best[0]:
                best = (x, sz)
            if sz >= tol * goal or x >= hi:
                break
        elif over is None or x < over[0]:
            over = (x, sz)
        if over and over[0] <= lo:
            break
        if len(pts) >= 2 and max(pts.values()) < 1.01 * min(pts.values()):
            break                            # the parameter does not affect the size - no point measuring further
        if best and over:
            if over[0] - best[0] <= 1:
                break
            a, b = best, over
            t = (math.log(goal * 0.97) - math.log(a[1])) / (math.log(b[1]) - math.log(a[1]))
            w = b[0] - a[0]
            nx = a[0] + t * w
            nx = int(round(min(max(nx, a[0] + 0.2 * w), b[0] - 0.2 * w)))   # guard against slow convergence
            nx = min(max(nx, a[0] + 1), b[0] - 1)
        else:
            ref = best or over
            others = [(k, v) for k, v in pts.items() if k != ref[0]]
            if others:                       # slope from two measurements (log of size per parameter unit)
                k2, v2 = min(others, key=lambda kv: abs(kv[0] - ref[0]))
                slope = (math.log(ref[1]) - math.log(v2)) / (ref[0] - k2) if ref[0] != k2 else 0
            else:
                slope = 0
            if not others:
                slope = 0.03                 # typically: size doubles every ~22 quality units (ln2/22)
            if slope > 1e-4:
                nx = ref[0] + (math.log(goal * 0.95) - math.log(ref[1])) / slope
                nx = min(max(nx, ref[0] - 30), ref[0] + 30)      # do not jump too far on a single measurement
            else:
                nx = ref[0] + (hi - ref[0]) * 0.6 if best else ref[0] - (ref[0] - lo) * 0.6
            nx = int(round(min(max(nx, lo), hi)))
            if best and nx <= best[0]:
                nx = min(hi, best[0] + 1)
            if over and not best and nx >= over[0]:
                nx = max(lo, over[0] - 1)
        if nx in pts and pts[nx] is not None and nx == x:
            break
        x = nx
    if best is None and lo not in pts:       # nothing fit within the measurement budget - check the lower bound
        sz = measure(lo)
        if sz <= goal:
            best = (lo, sz)
    return best


def img_dims(src):
    try:
        p = run([tool("magick"), "identify", "-format", "%w %h", f"{src}[0]"])
        w, h = p.stdout.split()[:2]
        return int(w), int(h)
    except Exception:
        return 0, 0


def _magick(src, dst, q=None, scale=None, size=None):
    """q=None -> lossless / maximum quality mode; q=number -> lossy with that quality.
    scale: percent (used when compressing to size), size: (w, h) exactly."""
    d = ext_of(dst)
    cmd = [tool("magick"), str(src) if d == "gif" else f"{src}[0]"]
    if d != "gif":
        cmd += ["-auto-orient"]                       # photos with an EXIF rotation flag must come out the right way up
    if size:
        cmd += ["-filter", "Lanczos", "-resize", f"{size[0]}x{size[1]}!"]
    if scale and scale < 100:
        cmd += ["-resize", f"{scale}%"]
    if d == "jpg":
        cmd += ["-background", "white", "-alpha", "remove", "-alpha", "off"]
    if d == "ico":
        cmd += ["-resize", "256x256>"]
    if q is None and not LOSSLESS and d in DEFAULT_Q:
        q = DEFAULT_Q[d]
    if d == "jpg":
        cmd += ["-sampling-factor", "4:4:4", "-quality", str(100 if q is None else q)]
    elif d == "webp":
        if q is None:
            cmd += ["-define", "webp:lossless=true", "-define", "webp:method=6", "-quality", "100"]
        else:
            cmd += ["-quality", str(q)]
    elif d in ("avif", "jxl"):
        cmd += ["-quality", str(100 if q is None else q)]
    elif d in ("tiff", "pdf"):
        cmd += ["-compress", "zip"]                   # Flate = lossless
    run(cmd + [dst])


def _jxl_path(src, dst, q, scale, size):
    """JXL path via libjxl: JPEG->JXL is a reversible lossless transcode, the rest: -d 0 (lossless)."""
    s, d = ext_of(src), ext_of(dst)
    tmp = Path(tempfile.mkdtemp())
    try:
        cur = Path(src)
        if s == "jxl":
            cur = tmp / "a.png"
            step("Decoding JPEG XL (djxl)")
            run([tool("djxl"), src, cur])
            if d == "png" and not scale and not size:
                shutil.copy(cur, dst)
                return
        if d == "jxl":
            direct = (not scale and not size and ext_of(cur) in ("jpg", "png", "gif"))
            if not direct:
                p2 = tmp / "b.png"
                _magick(cur, p2, None, scale, size)
                cur = p2
            cmd = [tool("cjxl"), cur, dst, "-e", "7"]
            if q is None:
                if ext_of(cur) != "jpg":
                    cmd += ["-d", "0"]                # lossless; for JPEG the default is a reversible transcode
            else:
                cmd += ["-q", q] + (["--lossless_jpeg=0"] if ext_of(cur) == "jpg" else [])
            step("Encoding JPEG XL (cjxl)" + (" — lossless JPEG transcode" if direct and ext_of(cur) == "jpg" and q is None
                                              else " — lossless" if q is None else f" — quality {q}"))
            run(cmd)
        else:
            _magick(cur, dst, q, scale, size)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def img_once(src, dst, q=None, scale=None, size=None):
    s, d = ext_of(src), ext_of(dst)
    jxl_ok = ((d != "jxl" or have("cjxl")) and (s != "jxl" or have("djxl"))) and "jxl" in (s, d)
    if jxl_ok:
        try:
            _jxl_path(src, dst, q, scale, size)
            return
        except RuntimeError:
            if not (have("magick")):
                raise
    _magick(src, dst, q, scale, size)             # ImageMagick has its own JXL support (fallback)


def _exif_orientation(data):
    """EXIF orientation (1 = normal), or 1 when missing."""
    i = data.find(b"Exif\x00\x00", 0, 65536)
    if i < 0:
        return 1
    t = i + 6
    bo = data[t:t + 2]
    e = "little" if bo == b"II" else "big" if bo == b"MM" else None
    if not e:
        return 1
    try:
        p = t + int.from_bytes(data[t + 4:t + 8], e)
        n = int.from_bytes(data[p:p + 2], e)
        p += 2
        for _ in range(min(n, 64)):
            if int.from_bytes(data[p:p + 2], e) == 0x0112:
                return int.from_bytes(data[p + 8:p + 10], e) or 1
            p += 12
    except Exception:
        pass
    return 1


def _jfif_dpi(data):
    if data[6:11] == b"JFIF\x00" and data[13] == 1:
        x = int.from_bytes(data[14:16], "big")
        if 30 <= x <= 2400:
            return x
    return 150


def _image_to_pdf(src, dst, target, size):
    """Image -> PDF. A JPEG without a size change is embedded as the original (no loss, the file does not bloat);
    with a size limit we pick the JPEG quality first."""
    s = ext_of(src)
    tmp = Path(tempfile.mkdtemp())
    try:
        jpg, dpi = None, 150
        if s in ("jpg", "jpeg") and not size:
            data = Path(src).read_bytes()
            try:
                _jpeg_info(data)
                if _exif_orientation(data) == 1:
                    jpg, dpi = Path(src), _jfif_dpi(data)
            except RuntimeError:
                jpg = None
        if target:
            if jpg is None or jpg.stat().st_size > target - 4096:
                step("Image to PDF with a size limit: choosing the JPEG quality")
                tj = tmp / "t.jpg"
                conv_image(str(src), str(tj), max(target - 4096, 1024), (None if not size else None))
                jpg = tj
                dpi = 150
            else:
                step("The JPEG fits within the limit — embedding the original unchanged")
        if jpg is None:
            _magick(src, dst, None, None, size)       # other formats: lossless (Flate)
            return ""
        step("Embedding the JPEG in the PDF" + (" (original, no re-encoding)" if jpg == Path(src) else ""))
        _pdf_from_jpegs([str(jpg)], dpi, dst)
        return "" if jpg == Path(src) or not target else "image saved as a JPEG of a chosen quality"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def conv_image(src, dst, target, res=None):
    d = ext_of(dst)
    size = None
    if res:
        w, h = img_dims(src)
        if not (w and h):
            raise RuntimeError("Cannot read the image dimensions (needed for the resize).")
        size = compute_dims(w, h, res)
    if d == "pdf":
        return _image_to_pdf(src, dst, target, size)
    if not target:
        step(f"Converting the image to .{d}" + (f" ({size[0]}x{size[1]})" if size else "")
             + (" — lossless" if LOSSLESS else ""))
        img_once(src, dst, None, None, size)
        if res and not size:
            return "the image already has exactly this resolution — nothing was resized"
        if d in ("jpg", "avif") and LOSSLESS and not size:
            return f".{d} has no fully lossless mode - used max quality (q100)" + \
                   (", 4:4:4" if d == "jpg" else "")
        return ""

    # --- compress to size: calculations first, then 2-4 measurements instead of blind attempts ---
    sw, sh = size if size else img_dims(src)
    if not (sw and sh):
        sw, sh = img_dims(src)
    srcsz = Path(src).stat().st_size
    if ext_of(src) == d and not size and srcsz <= target:
        shutil.copy(src, dst)
        step(f"The file is already {mb(srcsz)} (limit {mb(target)}) — nothing to do")
        return ""
    pixels = max(sw * sh, 1)
    bpp = target * 8 / pixels                     # how many bits per pixel we may spend
    PROGRESS(0.0)
    tmp = Path(tempfile.mkdtemp())
    cnt = [0]
    goal = target * 0.98

    def measure(q, sc):
        cnt[0] += 1
        out = tmp / f"t_{q}_{sc}.{d}"
        desc = ("lossless" if q is None else f"quality {q}") + (f", scale {sc}%" if sc else "")
        step(f"Measurement {cnt[0]}: {desc}")
        img_once(src, out, q, sc if sc and sc < 100 else None, size)
        sz = out.stat().st_size
        STEP(f"Measurement {cnt[0]}: {desc} → {mb(sz)} (limit {mb(target)})")
        PROGRESS(min(0.95, cnt[0] / 5))
        return sz

    def done(q, sc):
        shutil.copy(tmp / f"t_{q}_{sc}.{d}", dst)

    try:
        if d in LOSSY_IMG:
            # approximate quality <-> bits/pixel relation (photos): jpg q50 ~ 1 bpp, every doubling of bpp ~ +22 q
            eff = {"jpg": 1.0, "webp": 0.7, "jxl": 0.6, "avif": 0.4}.get(d, 1.0)
            qg = min(max(50 + 22 * math.log2(max(bpp, 0.01) / eff), 8), 92)
            step(f"Plan: the {mb(target)} limit is about {bpp:.2f} bits per pixel ({sw}x{sh}) → starting at quality ~{qg:.0f}")
            if bpp >= 6 and measure(None, None) <= goal:
                done(None, None)                 # the limit is so loose that even lossless fits
                return ""
            res = fit_param(lambda q: measure(q, None), 1, 99, goal, qg)
            if res:
                done(res[0], None)
                return ""
            # even quality 1 is too big -> fixed quality 45 and reduce the resolution
            q0 = 45
            s0 = measure(q0, None)
            sg = 100 * math.sqrt(goal / s0)
            res = fit_param(lambda sc: measure(q0, sc), 2, 100, goal, sg)
            if res:
                done(q0, res[0])
                return "resolution was reduced as well to fit the limit"
            raise RuntimeError("The image cannot be made this small.")
        # lossless formats (png, bmp, tiff, gif, ico): the only lever is the resolution
        s0 = measure(None, None)
        if s0 <= goal:
            done(None, None)
            return ""
        sg = 100 * math.sqrt(goal / s0)                  # size ~ number of pixels ~ scale^2
        step(f"Plan: .{d} is lossless, so I reduce the resolution — starting at a scale of ~{sg:.0f}%")
        res = fit_param(lambda sc: measure(None, sc), 2, 99, goal, sg)
        if not res:
            raise RuntimeError("The image cannot be made this small.")
        done(None, res[0])
        return f".{d} is lossless - the resolution was reduced to {res[0]}%"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------
# Text / documents (Pandoc + LibreOffice)
# ----------------------------------------------------------------------------
def text_to_pdf(src, dst):
    """Document -> HTML (Pandoc) -> PDF (the built-in Edge/Chrome in headless mode)."""
    tmp = Path(tempfile.mkdtemp())
    try:
        s = ext_of(src)
        if s == "html":
            page = Path(src)
        else:
            page = tmp / "page.html"
            cmd = [tool("pandoc"), src, "-s", "--embed-resources",
                   "--metadata", f"pagetitle={Path(src).stem}", "-o", page]
            if s == "txt":
                cmd += ["-f", "markdown"]
            run(cmd)
        out = tmp / "out.pdf"
        run([tool("browser"), "--headless", "--disable-gpu", "--no-pdf-header-footer",
             f"--user-data-dir={tmp / 'profile'}", f"--print-to-pdf={out}",
             page.resolve().as_uri()])
        for _ in range(60):  # the browser can be asynchronous
            check_cancel()
            if out.exists() and out.stat().st_size > 0:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("The browser did not create the PDF file.")
        time.sleep(0.2)
        shutil.move(str(out), dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def conv_text(src, dst, target):
    if target:
        raise RuntimeError("Compress to size does not apply to text documents.")
    s, d = ext_of(src), ext_of(dst)
    if d == "pdf":
        text_to_pdf(src, dst)
        return
    cmd = [tool("pandoc"), src, "-o", dst]
    if s == "txt":
        cmd += ["-f", "markdown"]
    if d == "txt":
        cmd += ["-t", "plain", "--wrap=none"]
    if d == "md":
        cmd += ["--wrap=none"]
    if d in ("html", "tex"):
        cmd += ["-s", "--metadata", f"pagetitle={Path(src).stem}"]
    run(cmd)


# ----------------------------------------------------------------------------
# OCR: Tesseract through ocrmypdf (a separate process, so "Cancel" stops it at once)
# ----------------------------------------------------------------------------
OCR_ERRORS = {
    2: "The input is not a valid PDF.",
    3: "A required program is missing (Tesseract or Ghostscript).",
    6: "The PDF already contains a text layer.",
    8: "The PDF is encrypted / password-protected — remove the password first.",
    9: "Invalid OCR settings (check the language).",
}


def _ocr_langs():
    """Validated OCR language string like 'pol+eng' (the window's setting, or the Windows UI language + English)."""
    try:
        return "+".join(tools_manager.check_ocr_langs(OCR_LANG or tools_manager.default_ocr_lang()))
    except ValueError as e:
        raise RuntimeError(str(e))


def _tools_say(msg, frac=None):
    DETAIL(msg)


def _ensure_ocr_tools(langs):
    """Makes sure Tesseract, Ghostscript, the ocrmypdf module and the language data are in the tools folder."""
    tools_manager.use_python_path()
    names = []
    if not tools_manager.pymods_installed():
        names.append("ocrmypdf")
    names += [n for n in ("tesseract", "gs", "pdftotext", "pdfinfo") if not have(n)]
    if names:
        step("Preparing OCR: downloading the missing components (Tesseract, Python modules) into the tools folder")
        _, errs = tools_manager.ensure(names, _tools_say)
        check_cancel()
        if errs:
            raise RuntimeError("OCR needs components that are missing: " + "; ".join(errs))
        tools_manager.use_python_path()
    if not tools_manager.module_available("ocrmypdf") or not have("tesseract") or not have("gs"):
        raise RuntimeError("OCR is not available (Tesseract / Ghostscript / ocrmypdf missing).")
    try:
        got = tools_manager.ensure_tessdata(langs, _tools_say, tool("tesseract"))
    except RuntimeError as e:
        raise RuntimeError(str(e))
    except Exception as e:
        raise RuntimeError(f"Could not download the OCR language data ({e}).")
    if got:
        STEP("(" + "; ".join(got) + ")")


def _ocr_env():
    paths = []
    for n in ("tesseract", "gs", "qpdf"):
        try:
            paths.append(str(Path(tool(n)).parent))
        except RuntimeError:
            pass
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=os.pathsep.join(tools_manager.python_paths()),
               TESSDATA_PREFIX=str(tools_manager.TESSDATA))
    env["PATH"] = os.pathsep.join(paths + [env.get("PATH", "")])
    return env


def run_ocr(src, dst, langs, text_out=None, force=False):
    """Runs ocrmypdf. dst=None: only the recognised text (text_out) is wanted. force=True re-renders every page
    (pure scans); otherwise pages that already have text are left alone."""
    cmd = [tools_manager.python_exe(), "-m", "ocrmypdf", "-l", langs,
           "--force-ocr" if force else "--skip-text",
           "--output-type", "none" if dst is None else "pdf"]
    if text_out:
        cmd += ["--sidecar", str(text_out)]
    cmd += [str(src), "-" if dst is None else str(dst)]
    PROGRESS(None)
    p = spawn(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
              errors="replace", env=_ocr_env())
    tail = []
    try:
        for line in p.stdout:
            if CANCEL.is_set():
                _kill_tree(p)
                break
            line = line.strip()
            if not line:
                continue
            tail = (tail + [line])[-10:]
            if line.startswith("Parsing"):
                DETAIL("building the text layer")
            elif line.startswith("Postprocessing"):
                DETAIL("finishing the PDF")
        p.wait()
    finally:
        release(p)
    check_cancel()
    if p.returncode != 0:
        detail = "\n".join(tail)[-500:]
        raise RuntimeError((OCR_ERRORS.get(p.returncode) or "OCR failed.") + (f"\n{detail}" if detail else ""))


def _pdf_text_chars(path, last_page=None):
    """Number of non-blank characters in the text layer (first last_page pages)."""
    cmd = [tool("pdftotext"), "-enc", "UTF-8"] + (["-l", str(last_page)] if last_page else []) + [str(path), "-"]
    out = prun(cmd).stdout or b""
    return len("".join(out.decode("utf-8", "replace").split()))


def pdf_looks_scanned(src):
    """True when the PDF has (almost) no text layer - typically a scan."""
    n = pdf_pages_count(src)
    sample = max(1, min(n, 10))
    return _pdf_text_chars(src, sample) / sample < OCR_MIN_CHARS_PER_PAGE


def conv_pdf_ocr(src, dst):
    """PDF -> the same PDF with a searchable / selectable text layer (OCR). Page images are not changed;
    pages that already contain text are left alone."""
    langs = _ocr_langs()
    _ensure_ocr_tools(langs)
    n = pdf_pages_count(src)
    step(f"OCR: recognising text on {n} page(s), language(s): {langs} — this can take a while")
    run_ocr(src, dst, langs)
    if _pdf_text_chars(dst) < 5:
        return (f"WARNING: no text was recognised ({langs}) — check the language, or the pages may contain no text. "
                f"Page images unchanged")
    return (f"text layer added (OCR, {langs}): the text is now searchable and copyable; page images unchanged "
            f"(lossless optimisation only); pages that already had text were left as they are. "
            f"Check important text for recognition errors")


def conv_image_ocr(src, dst, res=None):
    """Image (scan, photo of a document) -> searchable PDF: image -> PDF first, then OCR."""
    tmp = Path(tempfile.mkdtemp())
    try:
        page = tmp / "page.pdf"
        note = conv_image(str(src), str(page), None, res)
        ocr_note = conv_pdf_ocr(str(page), str(dst))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return "; ".join(x for x in (note, ocr_note) if x)


def _pdf_text_via_ocr(src, dst, d):
    """Scanned PDF -> text / document: OCR first, then the text goes to TXT or through Pandoc."""
    langs = _ocr_langs()
    try:
        _ensure_ocr_tools(langs)
    except RuntimeError as e:
        raise RuntimeError(f"This PDF is a scan (it has no text layer), and OCR is not available: {e}")
    n = pdf_pages_count(src)
    step(f"No text layer found — this looks like a scanned PDF. Running OCR on {n} page(s), language(s): {langs}")
    tmp = Path(tempfile.mkdtemp())
    try:
        side = tmp / "ocr.txt"
        run_ocr(src, None, langs, text_out=side, force=True)
        text = side.read_text(encoding="utf-8", errors="replace").replace("\f", "\n\n") if side.exists() else ""
        if not text.strip():
            raise RuntimeError(f"OCR found no text on the pages ({langs}) — check the OCR language.")
        if d == "txt":
            Path(dst).write_text(text, encoding="utf-8")
        else:
            side.write_text(text, encoding="utf-8")
            conv_text(str(side), dst, None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return (f"scanned PDF: text recognised by OCR ({langs}); page layout, images and tables are not kept — "
            f"check the result for recognition errors")


def conv_pdf_docx(src, dst):
    """PDF -> DOCX keeping the page layout, columns, tables and images (pdf2docx). Runs in a separate process,
    so "Cancel" stops it at once. Returns a note for the log."""
    tools_manager.use_python_path()
    if not tools_manager.module_available("pdf2docx"):
        step("Downloading Python modules (pdf2docx) into the tools\\python folder")
        _, errs = tools_manager.ensure(["pdf2docx"], _tools_say)
        for e in errs:
            STEP(f"({e})")
        tools_manager.use_python_path()
    if not tools_manager.module_available("pdf2docx"):
        raise RuntimeError("The pdf2docx module is missing (installation failed).")
    code = ("import sys, logging\n"
            "logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stderr)\n"
            "from pdf2docx import Converter\n"
            "cv = Converter(sys.argv[1])\n"
            "try:\n    cv.convert(sys.argv[2])\nfinally:\n    cv.close()\n")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=os.pathsep.join(tools_manager.python_paths()))
    step("Converting with layout preserved (pdf2docx) — analysing pages")
    rdir = Path(tempfile.mkdtemp())
    runner = rdir / "run_pdf2docx.py"
    runner.write_text(code, encoding="utf-8")
    p = spawn([tools_manager.python_exe(), str(runner), str(src), str(dst)], stdout=subprocess.PIPE,
              stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env)
    tail, phase = [], 0
    try:
        for line in p.stdout:
            if CANCEL.is_set():
                _kill_tree(p)
                break
            line = line.rstrip()
            tail = (tail + [line])[-8:]
            m = re.search(r"\[(\d)/4\]", line)
            if m:
                phase = {"3": 1, "4": 2}.get(m.group(1), 0)
                DETAIL({1: "analysing pages", 2: "building the Word document"}.get(phase, "preparing"))
                continue
            m = re.match(r"\((\d+)/(\d+)\) Page", line)
            if m and phase:
                PROGRESS(min(1.0, ((phase - 1) + int(m.group(1)) / max(int(m.group(2)), 1)) / 2))
        p.wait()
    finally:
        release(p)
        shutil.rmtree(rdir, ignore_errors=True)
    check_cancel()
    if p.returncode != 0 or not Path(dst).exists():
        raise RuntimeError("\n".join(l for l in tail if l)[-500:] or "pdf2docx exited with an error")
    return "page layout, columns and tables preserved (scans without a text layer end up in Word as images)"


def conv_pdf_text(src, dst, target):
    """PDF -> text. TXT: pdftotext (column layout roughly preserved). DOCX: pdf2docx (keeps the layout);
    if that fails - pdftotext + Pandoc. Other formats: pdftotext + Pandoc. Returns a note for the log."""
    if target:
        raise RuntimeError("To compress a PDF choose PDF or an image as the output format.")
    d = ext_of(dst)
    if pdf_looks_scanned(src):                     # a scan has no text to extract - recognise it first
        return _pdf_text_via_ocr(src, dst, d)
    if d == "txt":
        run([tool("pdftotext"), "-layout", src, dst])
        return "TXT does not keep the page layout or tables"
    note = ""
    if d == "docx":
        try:
            return conv_pdf_docx(src, dst)
        except Exception as e:                     # Cancelled (BaseException) passes straight through
            if Path(dst).exists():
                try:
                    Path(dst).unlink()
                except OSError:
                    pass
            STEP(f"(pdf2docx could not do it: {str(e)[:160]} — trying a simpler method)")
            _, errs = tools_manager.ensure(["pdftotext", "pandoc"], lambda m, f=None: None)
            for er in errs:
                STEP(f"({er})")
            note = "page layout NOT preserved (pdf2docx failed, pdftotext + Pandoc was used)"
    tmp = Path(tempfile.mkdtemp())
    try:
        txt = tmp / "x.txt"
        run([tool("pdftotext"), src, txt])
        conv_text(str(txt), dst, None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return note or "text format — page layout, columns and tables are not preserved"


class GsGrew(RuntimeError):
    pass


def run_gs(cmd, label, out_file=None, abort_above=None):
    """Ghostscript with progress tracking: percentages are computed from the 'Page N' output.
    When out_file and abort_above are given, it aborts if the result grows beyond abort_above bytes."""
    cmd = [str(c) for c in cmd]
    p = spawn(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
              encoding="utf-8", errors="replace")
    try:
        return _gs_loop(p, label, out_file, abort_above)
    finally:
        release(p)


def _gs_loop(p, label, out_file, abort_above):
    pages, tail = 0, []
    for line in p.stdout:
        if CANCEL.is_set():
            _kill_tree(p)
            break
        line = line.rstrip()
        tail = (tail + [line])[-12:]
        m = re.match(r"Processing pages \d+ through (\d+)", line)
        if m:
            pages = int(m.group(1))
            DETAIL(f"{label}: {pages} pages")
            continue
        m = re.match(r"Page (\d+)", line)
        if m and pages:
            PROGRESS(min(1.0, int(m.group(1)) / pages))
            DETAIL(f"{label}: page {m.group(1)}/{pages}")
            if out_file and abort_above:
                try:
                    if Path(out_file).stat().st_size > abort_above:
                        p.kill()
                        p.wait()
                        raise GsGrew(f"the result grows beyond {abort_above / 1048576:.0f} MB — aborted")
                except OSError:
                    pass
    p.wait()
    check_cancel()
    if p.returncode != 0:
        raise RuntimeError("\n".join(tail[-6:]) or "Ghostscript exited with an error")


def pdf_scan(src):
    """Quick analysis of the PDF structure without external tools: how many bytes are taken by images, fonts,
    page content (text/vector), attachments and the rest. Reads only the stream dictionaries (a few seconds
    even for files of hundreds of MB). Returns a dict with bytes + 'pages' (guessed from /Type /Page)."""
    import mmap
    c = dict(images=0, fonts=0, content=0, content_raw=0, attach=0, other=0, pages=0)
    with open(src, "rb") as f:
        try:
            data = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError):
            data = f.read()
        c["pages"] = len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", data))
        pos = 0
        rx = re.compile(rb">>\s*stream\r?\n")
        while True:
            m = rx.search(data, pos)
            if not m:
                break
            start = m.end()
            end = data.find(b"endstream", start)
            if end < 0:
                break
            n = end - start
            pos = end + 9
            b0 = data.rfind(b" obj", max(0, m.start() - 4000), m.start())
            d = data[b0 if b0 >= 0 else max(0, m.start() - 600): m.start() + 2]
            if re.search(rb"/Subtype\s*/Image", d):
                c["images"] += n
            elif re.search(rb"/Type\s*/EmbeddedFile", d):
                c["attach"] += n
            elif re.search(rb"/Length1|/Subtype\s*/(?:Type1C|CIDFontType0C|OpenType)", d):
                c["fonts"] += n
            elif re.search(rb"/Type\s*/(?:ObjStm|XRef|Metadata)|/Alternate", d):
                c["other"] += n
            elif b"/Filter" in d:
                c["content"] += n
            else:
                c["content_raw"] += n         # uncompressed stream - Ghostscript will compress it
        try:
            data.close()
        except AttributeError:
            pass
    return c


def pdf_images(src):
    """pdfimages -list: unique images [(size_B, ppi, is_jpeg)] - masks included."""
    out = run([tool("pdfimages"), "-list", src]).stdout.splitlines()[2:]
    seen, imgs = set(), []
    for line in out:
        t = line.split()
        if len(t) < 16 or t[2] not in ("image", "mask", "stencil", "smask"):
            continue
        try:
            oid = (t[-6], t[-5])
            if oid in seen:                      # the same image used on many pages
                continue
            seen.add(oid)
            ppi = max(float(t[-4]), float(t[-3]))
            m = re.fullmatch(r"([\d.]+)([BKMG]?)", t[-2])
            size = float(m.group(1)) * {"": 1, "B": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[m.group(2)]
            imgs.append((size, max(ppi, 1.0), t[8] in ("jpeg", "jpx")))
        except (ValueError, AttributeError):
            continue
    return imgs



def pdf_diag(src, pages):
    """Breakdown of the file by stream kind (qpdf --json): what really weighs. Returns a list of log lines."""
    import json
    try:
        p = prun([tool("qpdf"), "--json=2", "--json-stream-data=none", str(src)])
        j = json.loads(p.stdout.decode("utf-8", "replace"))
        objs = j["qpdf"][1]
    except Cancelled:
        raise
    except Exception as e:
        return [f"(diagnostics unavailable: {str(e)[:80]})"]
    agg, top, enc = {}, [], bool(j.get("encrypt", {}).get("encrypted"))
    for k, v in objs.items():
        st = v.get("stream") if isinstance(v, dict) else None
        if not st:
            continue
        d = st.get("dict", {})
        n = int(d.get("/Length", 0)) if isinstance(d.get("/Length", 0), (int, float)) else 0
        kind = d.get("/Subtype") or d.get("/Type") or ("/Length1" in d and "Font" or "content stream")
        flt = d.get("/Filter", "none")
        flt = "+".join(flt) if isinstance(flt, list) else flt
        key = f"{kind} [{str(flt).replace('/', '')}]"
        a = agg.setdefault(key, [0, 0])
        a[0] += 1
        a[1] += n
        top.append((n, k[4:], key))
    out = []
    for key, (c, b) in sorted(agg.items(), key=lambda x: -x[1][1])[:8]:
        out.append(f"{b / 1048576:7.2f} MB  x{c:<5} {str(key).replace('/', '')}")
    top.sort(reverse=True)
    out.append("largest: " + "; ".join(f"{n / 1048576:.2f} MB {k.split()[0]} {t.replace('/', '')}" for n, k, t in top[:3]))
    if pages:
        out.append(f"on average {Path(src).stat().st_size / pages / 1024:.0f} KB per page ({pages} pages)" + ("; file is encrypted" if enc else ""))
    return out


def pdf_page_count(src, scan):
    try:
        out = run([tool("pdfinfo"), src]).stdout
        m = re.search(r"Pages:\s+(\d+)", out)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return scan.get("pages") or 0


PDF_Q_NORMAL, PDF_Q_STRONG = 0.5, 1.0       # Ghostscript JPEG QFactor (higher = stronger compression)
PDF_MIN_DPI = 30


def _gs_image_args(dpi, q, thr=1.0):
    """Forces re-encoding of images (JPEG) and downsampling to the dpi - also when an image has less than 1.5x as much."""
    opts = [
        "-dCompatibilityLevel=1.6", "-sColorConversionStrategy=LeaveColorUnchanged",
        "-dEmbedAllFonts=true", "-dSubsetFonts=true", "-dCompressFonts=true", "-dDetectDuplicateImages=true",
        "-dDownsampleColorImages=true", "-dColorImageDownsampleType=/Bicubic",
        f"-dColorImageResolution={dpi}", f"-dColorImageDownsampleThreshold={thr}",
        "-dDownsampleGrayImages=true", "-dGrayImageDownsampleType=/Bicubic",
        f"-dGrayImageResolution={dpi}", f"-dGrayImageDownsampleThreshold={thr}",
        "-dDownsampleMonoImages=true", f"-dMonoImageResolution={min(600, dpi * 2)}",
        "-dMonoImageDownsampleThreshold=1.0",
        "-dAutoFilterColorImages=false", "-dColorImageFilter=/DCTEncode",
        "-dAutoFilterGrayImages=false", "-dGrayImageFilter=/DCTEncode"]
    ps = (f"<< /ColorImageDict << /QFactor {q} /Blend 1 /HSamples [2 1 1 2] /VSamples [2 1 1 2] >> "
          f"/GrayImageDict << /QFactor {q} /Blend 1 /HSamples [1 1 1 1] /VSamples [1 1 1 1] >> >> "
          f"setdistillerparams")
    return opts, ps


# --- writing a PDF from ready-made JPEGs (no re-encoding, result size = sum of the JPEGs + a few KB) ---
def _jpeg_info(data):
    """(width, height, number of components) from the JPEG header."""
    i, n = 2, len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        mk = data[i + 1]
        if mk in (0xD8, 0x01) or 0xD0 <= mk <= 0xD7 or mk == 0xFF:
            i += 1 if mk == 0xFF else 2
            continue
        seg = int.from_bytes(data[i + 2:i + 4], "big")
        if mk in (0xC0, 0xC1, 0xC2):
            return (int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big"),
                    data[i + 9])
        i += 2 + seg
    raise RuntimeError("Cannot read the JPEG header.")


def _pdf_from_jpegs(files, dpi, dst):
    objs = []                                           # object no. (i+1) = objs[i] (bytes)
    kids = []
    for k, jf in enumerate(files):
        data = Path(jf).read_bytes()
        w, h, nc = _jpeg_info(data)
        cs = {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}.get(nc, "/DeviceRGB")
        dec = b" /Decode [1 0 1 0 1 0 1 0]" if nc == 4 else b""
        pw, ph = w * 72.0 / dpi, h * 72.0 / dpi
        base = 3 + k * 3                                # page object number
        kids.append(f"{base} 0 R")
        content = f"q {pw:.2f} 0 0 {ph:.2f} 0 0 cm /Im0 Do Q".encode()
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.2f} {ph:.2f}] "
                     f"/Resources << /XObject << /Im0 {base + 2} 0 R >> >> /Contents {base + 1} 0 R >>").encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        objs.append((f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace {cs} "
                     f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} ").encode() + dec
                    + b">>\nstream\n" + data + b"\nendstream")
    head = [b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Count {len(kids)} /Kids [{' '.join(kids)}] >>".encode()]
    allobjs = head + objs
    with open(dst, "wb") as f:
        f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offs = []
        for i, body in enumerate(allobjs, 1):
            offs.append(f.tell())
            f.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
        xr = f.tell()
        f.write(f"xref\n0 {len(allobjs) + 1}\n".encode() + b"0000000000 65535 f \n")
        for o in offs:
            f.write(f"{o:010d} 00000 n \n".encode())
        f.write(f"trailer\n<< /Size {len(allobjs) + 1} /Root 1 0 R >>\nstartxref\n{xr}\n%%EOF\n".encode())


# Keys that do not affect how the page looks but can weigh megabytes: private application data
# (Illustrator/InDesign/Photoshop put their own copies of the artwork here), XMP metadata, thumbnails.
PDF_DISCARD_KEYS = ("/PieceInfo", "/Metadata", "/Thumb")


def _need_pikepdf():
    """pikepdf (and Pillow) live in tools\\python together with the other tools; missing = download them there."""
    tools_manager.use_python_path()
    try:
        import pikepdf
        return pikepdf
    except ImportError:
        pass
    step("Downloading Python modules (pikepdf, Pillow, pdf2docx) into the tools\\python folder")
    msgs, errs = tools_manager.ensure(["pikepdf"], lambda m, f=None: None)
    for e in errs:
        STEP(f"({e})")
    tools_manager.use_python_path()
    try:
        import pikepdf
        return pikepdf
    except ImportError:
        return None


def _strip_private(src, out):
    """Lossless as far as appearance goes: removes private application data, XMP and thumbnails from ALL objects and
    merges identical streams (images, forms, fonts repeated many times). Returns the size or None."""
    pikepdf = _need_pikepdf()
    if pikepdf is None:
        return None
    import hashlib
    try:
        pdf = pikepdf.open(str(src))
        for obj in pdf.objects:
            d = None
            if isinstance(obj, pikepdf.Stream):
                d = obj.stream_dict
            elif isinstance(obj, pikepdf.Dictionary):
                d = obj
            if d is not None:
                for k in PDF_DISCARD_KEYS:
                    if k in d:
                        del d[k]
        # deduplicate identical streams (same dictionary + same bytes)
        canon, remap = {}, {}
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") in ("/Image", "/Form") or \
                    (isinstance(obj, pikepdf.Stream) and "/Length1" in obj.stream_dict):
                try:
                    key = hashlib.sha1(obj.read_raw_bytes() + repr(sorted(
                        (k, str(v)) for k, v in obj.stream_dict.items() if k != "/Length")).encode()).hexdigest()
                except Exception:
                    continue
                if key in canon:
                    remap[obj.objgen] = canon[key]
                else:
                    canon[key] = obj
        if remap:
            def fix(c, depth=0):
                if depth > 6:
                    return
                if isinstance(c, pikepdf.Dictionary) or (isinstance(c, pikepdf.Stream)):
                    items = list((c.stream_dict if isinstance(c, pikepdf.Stream) else c).items())
                    tgt = c.stream_dict if isinstance(c, pikepdf.Stream) else c
                    for k, v in items:
                        if isinstance(v, (pikepdf.Stream,)) and v.is_indirect and v.objgen in remap:
                            tgt[k] = remap[v.objgen]
                        elif isinstance(v, (pikepdf.Dictionary, pikepdf.Array)) and not v.is_indirect:
                            fix(v, depth + 1)
                elif isinstance(c, pikepdf.Array):
                    for i in range(len(c)):
                        v = c[i]
                        if isinstance(v, pikepdf.Stream) and v.is_indirect and v.objgen in remap:
                            c[i] = remap[v.objgen]
                        elif isinstance(v, (pikepdf.Dictionary, pikepdf.Array)) and not v.is_indirect:
                            fix(v, depth + 1)
            for obj in list(pdf.objects):
                if isinstance(obj, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)):
                    fix(obj)
        pdf.save(str(out), object_stream_mode=pikepdf.ObjectStreamMode.generate,
                 compress_streams=True, recompress_flate=True)
        pdf.close()
        return Path(out).stat().st_size
    except Exception as e:
        STEP(f"(cleaning failed: {str(e)[:100]})")
        return None


# --- lowering the quality of vector artwork (Form XObject) - text on the page stays text ---
ART_LEVELS = [(200, 85), (150, 80), (110, 75), (80, 70), (60, 60), (45, 50), (30, 40)]   # (dpi, JPEG quality)
ART_MIN_FORM = 15 * 1024        # we leave smaller artwork alone - the gain would be negligible


def _form_candidates(pikepdf, pdf):
    """Artwork (Form XObjects) with a BBox: [(objgen, bytes_in_file, has_text, (llx,lly,urx,ury))], biggest first.
    has_text also covers nested artwork (text in a form inside a form)."""
    forms = {o.objgen: o for o in pdf.objects
             if isinstance(o, pikepdf.Stream) and o.get("/Subtype") == "/Form"}
    memo = {}

    def has_text(og, depth=0):
        if og in memo:
            return memo[og]
        memo[og] = False                                   # guard against loops
        o = forms[og]
        try:
            txt = re.search(rb"\bBT\b", o.read_bytes()) is not None
        except Exception:
            txt = True
        if not txt and depth < 8:
            try:
                xo = o.get("/Resources", {}).get("/XObject", {})
                for k in list(xo.keys()):
                    ch = xo[k]
                    if getattr(ch, "objgen", (0, 0)) in forms and has_text(ch.objgen, depth + 1):
                        txt = True
                        break
            except Exception:
                txt = True
        memo[og] = txt
        return txt

    out = []
    for og, obj in forms.items():
        if "/BBox" not in obj:
            continue
        try:
            raw = len(obj.read_raw_bytes())
        except Exception:
            continue
        if raw < ART_MIN_FORM:
            continue
        try:
            bb = [float(x) for x in obj["/BBox"]]
            m = [float(x) for x in obj["/Matrix"]] if "/Matrix" in obj else [1, 0, 0, 1, 0, 0]
            pts = [(bb[0], bb[1]), (bb[2], bb[1]), (bb[0], bb[3]), (bb[2], bb[3])]
            xs = [m[0] * x + m[2] * y + m[4] for x, y in pts]
            ys = [m[1] * x + m[3] * y + m[5] for x, y in pts]
            box = (min(xs), min(ys), max(xs), max(ys))
            if box[2] - box[0] < 1 or box[3] - box[1] < 1:
                continue
        except Exception:
            continue
        out.append((og, raw, has_text(og), box))
    out.sort(key=lambda c: -c[1])
    return out


def _render_form(pikepdf, pdf, obj, box, dpi, q, tmp):
    """Renders one piece of artwork to a JPEG (+ transparency mask if any). Returns (rgb, mask|None, w, h)."""
    from PIL import Image
    llx, lly, urx, ury = box
    w, h = urx - llx, ury - lly
    px = w * h * (dpi / 72.0) ** 2
    if px > 36e6:                                   # memory protection for huge artwork
        dpi = max(20, dpi * (36e6 / px) ** 0.5)
    new = pikepdf.new()
    fx = new.copy_foreign(obj)
    page = new.add_blank_page(page_size=(max(w, 1), max(h, 1)))
    page.MediaBox = [llx, lly, urx, ury]
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(F=fx))
    page.Contents = new.make_stream(b"/F Do")
    fp = tmp / "f.pdf"
    new.save(str(fp))
    new.close()
    base = tmp / "f"
    for old in (tmp / "f.png",):
        if old.exists():
            old.unlink()
    p = prun([tool("pdftocairo"), "-png", "-transp", "-singlefile", "-r", f"{dpi:.1f}", str(fp), str(base)])
    png = tmp / "f.png"
    if p.returncode != 0 or not png.exists():
        raise RuntimeError("rendering the artwork failed")
    import io
    im = Image.open(png).convert("RGBA")
    a = im.getchannel("A")
    opaque = a.getextrema()[0] >= 250
    if opaque:
        rgb = im.convert("RGB")
    else:
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        rgb = Image.alpha_composite(bg, im).convert("RGB")
    b1 = io.BytesIO()
    rgb.save(b1, "JPEG", quality=q, optimize=True)
    mask = None
    if not opaque:
        b2 = io.BytesIO()
        a.save(b2, "JPEG", quality=max(40, q - 10), optimize=True)
        mask = b2.getvalue()
    return b1.getvalue(), mask, im.size[0], im.size[1]


def _replace_form(pikepdf, pdf, obj, box, rgb, mask, wpx, hpx):
    llx, lly, urx, ury = box
    img = pdf.make_stream(rgb)
    img.Type, img.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    img.Width, img.Height = wpx, hpx
    img.ColorSpace, img.BitsPerComponent = pikepdf.Name.DeviceRGB, 8
    img.Filter = pikepdf.Name.DCTDecode
    if mask:
        m = pdf.make_stream(mask)
        m.Type, m.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
        m.Width, m.Height = wpx, hpx
        m.ColorSpace, m.BitsPerComponent = pikepdf.Name.DeviceGray, 8
        m.Filter = pikepdf.Name.DCTDecode
        img.SMask = m
    sd = obj.stream_dict
    for k in ("/Matrix", "/PieceInfo", "/Metadata", "/OPI", "/Ref"):
        if k in sd:
            del sd[k]
    sd["/BBox"] = pikepdf.Array([llx, lly, urx, ury])
    sd["/Resources"] = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im=img))
    obj.write(f"q {urx - llx:.4f} 0 0 {ury - lly:.4f} {llx:.4f} {lly:.4f} cm /Im Do Q".encode())


def _art_quality(dpi):
    return 80 if dpi >= 120 else 72 if dpi >= 70 else 60


def _forms_ratio(pikepdf, pdf, cands, tmp):
    """Sample: how much the JPEG of the largest artwork weighs at 60 dpi compared to its vector weight (for estimates)."""
    objs = {o.objgen: o for o in pdf.objects if isinstance(o, pikepdf.Stream)}
    samp = [c for c in cands if not c[2]][:3] or cands[:3]
    sj = sr = 0
    for og, raw, _t, box in samp:
        try:
            rgb, mask, _w, _h = _render_form(pikepdf, pdf, objs[og], box, 60, 75, tmp)
            sj += len(rgb) + (len(mask) if mask else 0)
            sr += raw
        except Exception:
            pass
    return max(sj / sr, 0.005) if sr else None


def _flatten_pass(pikepdf, src, out, cands, dpi, tmp):
    """All artwork for which an image at this dpi pays off (gain >= 10%), replaced with images at that dpi.
    Artwork with text inside only from 100 dpi. Returns the number of artwork items changed."""
    q = _art_quality(dpi)
    elig = [c for c in cands if dpi >= 100 or not c[2]]
    pdf = pikepdf.open(str(src))
    objs = {o.objgen: o for o in pdf.objects if isinstance(o, pikepdf.Stream)}
    done = 0
    for i, (og, raw, _t, box) in enumerate(elig):
        try:
            rgb, mask, w, h = _render_form(pikepdf, pdf, objs[og], box, dpi, q, tmp)
        except Exception:
            continue
        if raw - len(rgb) - (len(mask) if mask else 0) < raw * 0.1:
            continue
        _replace_form(pikepdf, pdf, objs[og], box, rgb, mask, w, h)
        done += 1
        PROGRESS(min(0.95, (i + 1) / max(len(elig), 1)))
        DETAIL(f"vector artwork {i + 1}/{len(elig)} → {dpi} dpi")
    pdf.save(str(out), object_stream_mode=pikepdf.ObjectStreamMode.generate,
             compress_streams=True, recompress_flate=True)
    pdf.close()
    return done


def _qpdf_optimize(src, out):
    """Lossless repacking of the PDF structure (qpdf): Flate streams to level 9, objects into object
    streams. Text, vectors and images stay bit for bit, and the result is never larger than the source."""
    try:
        q = tool("qpdf")
    except RuntimeError:
        return None
    p = prun([q, "--object-streams=generate", "--compress-streams=y", "--recompress-flate",
              "--compression-level=9", str(src), str(out)])
    if p.returncode in (0, 3) and Path(out).exists():       # 3 = warnings, the file was created
        return Path(out).stat().st_size
    return None


# --- our own image recompression (pikepdf + Pillow): independent of the Ghostscript version -------------------------
FIT_Q_MIN, FIT_Q_PREF, FIT_Q_PREF_FLAT = 35, 72, 88      # lowest JPEG quality / quality kept before pixels are reduced
FIT_SCALES = [1.0, 0.85, 0.7, 0.55, 0.42, 0.32, 0.25, 0.18]
FIT_SAMPLE = 6                                           # with more images the search runs on a sample of this size


def _cs_components(pk, cs):
    """Number of colour components of a plain device / ICC / Cal colour space; 0 = anything else (Indexed, Lab...)."""
    try:
        t = cs._type_code
        if t == getattr(pk.ObjectType, "name_", None):
            return {"/DeviceRGB": 3, "/DeviceGray": 1, "/DeviceCMYK": 4}.get(str(cs), 0)
        if t == pk.ObjectType.array:
            kind = str(cs[0])
            if kind == "/ICCBased":
                return int(cs[1].get("/N", 0))
            if kind == "/CalRGB":
                return 3
            if kind == "/CalGray":
                return 1
    except Exception:
        pass
    return 0


def _is_flat(pil):
    """True for screenshots / drawings / scanned text with very few colours: JPEG at low quality looks bad on them
    (ringing around edges), so fewer pixels at a higher quality is the better trade."""
    try:
        from PIL import Image
        t = pil.resize((256, 256), Image.NEAREST)
        if t.mode == "L":
            h = sorted(t.histogram(), reverse=True)[:4]
            return sum(h) >= 0.7 * 65536
        colors = t.getcolors(maxcolors=65536)
        return colors is not None and len(colors) <= 4000
    except Exception:
        return False


def _fit_images(pk, src, out, target):
    """Re-encodes the big images of the PDF as JPEG so that the file lands just under `target` bytes with the HIGHEST
    quality that fits (pixels are only reduced when quality alone is not enough). Text and vector graphics are not
    touched. With many images the quality is found on a sample and applied in one parallel pass (with progress).
    Returns (size, note), or None when there is nothing suitable to recompress."""
    import io
    from concurrent.futures import ThreadPoolExecutor
    from PIL import Image, ImageFile
    Image.MAX_IMAGE_PIXELS = None
    ImageFile.MAXBLOCK = 128 * 1024 * 1024            # big JPEGs with optimize=True otherwise fail ("Suspension not allowed here")
    orig_size = Path(src).stat().st_size
    workers = max(1, min(4, os.cpu_count() or 2))

    # ---- 1. find the images that can be recompressed --------------------------------------------------------
    pdf = pk.open(str(src))
    items = []                                        # dicts: gen, old, w, h, mode, pil (cache or None), flat
    seen, smasks = set(), set()
    for obj in pdf.objects:                           # soft masks (transparency) are kept exactly as they are
        if isinstance(obj, pk.Stream) and obj.get("/Subtype") == pk.Name.Image and "/SMask" in obj:
            try:
                smasks.add(obj["/SMask"].objgen)
            except Exception:
                pass
    objs = [o for o in pdf.objects if isinstance(o, pk.Stream) and o.get("/Subtype") == pk.Name.Image]
    for k, obj in enumerate(objs):
        check_cancel()
        if obj.objgen in seen or obj.objgen in smasks:
            continue
        seen.add(obj.objgen)
        filt = str(obj.get("/Filter", ""))
        if (obj.get("/ImageMask") or "/Mask" in obj or "/SMaskInData" in obj
                or any(x in filt for x in ("JBIG2", "CCITT", "JPX"))):
            continue
        old_len = len(obj.read_raw_bytes())
        w, h = int(obj.get("/Width", 0)), int(obj.get("/Height", 0))
        if old_len < 24 * 1024 or w * h < 40_000 or w * h > 120_000_000:
            continue
        # what kind of image is it - decided from the PDF's own description, WITHOUT decoding the pixels
        cs, bpc, dec = obj.get("/ColorSpace"), int(obj.get("/BitsPerComponent", 8)), obj.get("/Decode")
        comps = _cs_components(pk, cs) if cs is not None else 0
        indexed = False
        if comps == 0 and cs is not None:
            try:
                indexed = cs._type_code == pk.ObjectType.array and str(cs[0]) == "/Indexed" and bpc <= 8
            except Exception:
                indexed = False
        if indexed:
            mode = "RGB"
        elif bpc == 8 and comps in (1, 3, 4):
            mode = {1: "L", 3: "RGB", 4: "CMYK"}[comps]
        else:
            continue                                  # 1-bit, 16-bit, Lab, Separation...: left exactly as it is
        if mode == "CMYK":
            inverted = dec is not None and [float(x) for x in dec] == [1, 0, 1, 0, 1, 0, 1, 0]
            if ("DCTDecode" in filt and not inverted) or ("DCTDecode" not in filt and dec is not None):
                continue                              # only Adobe-style (inverted) CMYK JPEGs and plain raw CMYK
        elif dec is not None:
            continue                                  # inverted / remapped images: left as they are
        items.append({"gen": obj.objgen, "old": old_len, "w": w, "h": h, "mode": mode, "pil": None, "flat": False})
    pdf.close()
    if not items:
        return None
    old_total = sum(i["old"] for i in items)
    fixed = orig_size - old_total                     # everything that is not one of these images
    budget = target * 0.985 - fixed
    if budget <= 0:
        budget = 0.01 * target
    pdf_r = pk.open(str(src))

    def decode(it):
        """Pixels of an image (None when it cannot be decoded - such an image is then simply left alone)."""
        if it["pil"] is not None:
            return it["pil"]
        try:
            pil = pk.PdfImage(pdf_r.get_object(it["gen"])).as_pil_image()
            pil.load()
        except Exception:
            return None
        if pil.mode in ("P", "RGBA"):
            return pil.convert("RGB")
        return pil.convert("L") if pil.mode == "LA" else pil

    def encode(pil, it, s, q):
        if s < 0.999:
            pil = pil.resize((max(1, round(it["w"] * s)), max(1, round(it["h"] * s))), Image.LANCZOS)
        buf = io.BytesIO()
        # quality 98+ also keeps full colour resolution (4:4:4); below that 4:2:0 is visually the same and much smaller
        pil.save(buf, "JPEG", quality=int(q), optimize=True, subsampling=0 if q >= 98 else 2)
        return buf.getvalue(), pil.size

    # ---- 2. search (on a sample when there are many images) ------------------------------------------------
    if len(items) > FIT_SAMPLE:
        by_size = sorted(range(len(items)), key=lambda i: -items[i]["old"])
        pick = set(by_size[:FIT_SAMPLE // 2])
        step_ = max(1, len(items) // (FIT_SAMPLE - len(pick)))
        pick |= set(range(0, len(items), step_))
        sample = [items[i] for i in sorted(pick)][:FIT_SAMPLE]
    else:
        sample = items
    DETAIL("analysing the images")
    for it in list(sample):                           # the sample is decoded once and kept (and checked for "flat" art)
        check_cancel()
        pil = decode(it)
        if pil is None or pil.mode != it["mode"]:
            items.remove(it)
            if it in sample:
                sample.remove(it)
            continue
        it["pil"], it["flat"] = pil, _is_flat(pil)
    if not items or not sample:
        pdf_r.close()
        return None
    old_total = sum(i["old"] for i in items)
    fixed = orig_size - old_total
    budget = max(target * 0.985 - fixed, 0.01 * target)
    sample_old = sum(i["old"] for i in sample)
    q_pref = FIT_Q_PREF_FLAT if sum(i["old"] for i in sample if i["flat"]) > 0.5 * sample_old else FIT_Q_PREF
    n_meas = [0]
    PROGRESS(None)

    def estimate(s, q, ex):
        n_meas[0] += 1
        DETAIL(f"measurement {n_meas[0]}: quality {q}" + (f", {int(s * 100)}% size" if s < 1 else "")
               + (f" (sample of {len(sample)} images)" if sample is not items else ""))
        sizes = list(ex.map(lambda it: min(len(encode(it["pil"], it, s, q)[0]), it["old"]), sample))
        return sum(sizes) / sample_old * old_total        # the sample's compression ratio applied to everything

    def search(bud, ex):
        chosen = None
        for k, s in enumerate(FIT_SCALES):
            last = k == len(FIT_SCALES) - 1
            if estimate(s, 100, ex) <= bud:
                q = 100
            elif estimate(s, FIT_Q_MIN, ex) > bud:
                if last:
                    chosen = (s, FIT_Q_MIN)
                continue                               # even the lowest quality is too big - fewer pixels
            else:
                lo, hi = FIT_Q_MIN, 100
                while hi - lo > 1:
                    check_cancel()
                    mid = (lo + hi) // 2
                    if estimate(s, mid, ex) <= bud:
                        lo = mid
                    else:
                        hi = mid
                q = lo
            if q >= q_pref or last:
                return (s, q)
            if chosen is None or q > chosen[1]:
                chosen = (s, q)
        return chosen

    with ThreadPoolExecutor(workers) as ex:
        s, q = search(budget, ex)
        STEP(f"→ quality {q}" + (f", pixels reduced to {int(s * 100)}%" if s < 1 else "")
             + f" ({n_meas[0]} measurements" + (f" on a sample of {len(sample)} of {len(items)} images)" if sample is not items else ")"))

        # ---- 3. the real pass: every image, in parallel, with progress ------------------------------------
        size = prev = None
        replaced = 0
        for attempt in range(3):                      # the real file can differ a little from the estimate
            pdf = pk.open(str(src))
            replaced, pending, done = 0, [], 0

            def apply(it, fut):
                nonlocal replaced
                blob, (nw, nh) = fut.result()
                if len(blob) >= it["old"] * 0.97:
                    return                             # not smaller than what is there - keep the original stream
                obj = pdf.get_object(it["gen"])
                want = {"RGB": 3, "L": 1, "CMYK": 4}[it["mode"]]
                if _cs_components(pk, obj.get("/ColorSpace")) != want:
                    obj["/ColorSpace"] = {3: pk.Name.DeviceRGB, 1: pk.Name.DeviceGray, 4: pk.Name.DeviceCMYK}[want]
                obj.write(blob, filter=pk.Name.DCTDecode)
                if "/DecodeParms" in obj:
                    del obj["/DecodeParms"]
                if want == 4:                          # Pillow writes CMYK JPEGs Adobe-style (inverted)
                    obj["/Decode"] = pk.Array([1, 0, 1, 0, 1, 0, 1, 0])
                obj["/Width"], obj["/Height"], obj["/BitsPerComponent"] = nw, nh, 8
                replaced += 1

            for it in items:
                check_cancel()
                pil = decode(it)                      # pikepdf stays on this thread
                if pil is not None:
                    pending.append((it, ex.submit(encode, pil, it, s, q)))
                if len(pending) >= workers * 2:
                    apply(*pending.pop(0))
                    done += 1
                    PROGRESS(done / len(items))
                    DETAIL(f"recompressing the images ({done}/{len(items)})")
            while pending:
                apply(*pending.pop(0))
                done += 1
                PROGRESS(done / len(items))
                DETAIL(f"recompressing the images ({done}/{len(items)})")
            pdf.save(str(out), compress_streams=True, object_stream_mode=pk.ObjectStreamMode.generate,
                     recompress_flate=False)
            pdf.close()
            size = Path(out).stat().st_size
            if size <= target or (prev is not None and size >= prev * 0.99):
                break
            prev = size
            real_img = max(1.0, size - fixed)          # the estimate was too optimistic - search again with the facts
            budget = budget * (budget / real_img) * 0.99
            STEP(f"(the real file is {mb(size)}, a bit over the limit — searching again)")
            s, q = search(budget, ex)
    pdf_r.close()
    if not replaced:
        return None
    return size, (f"{replaced} image(s) re-encoded as JPEG (quality {q}"
                  + (f", pixels reduced to {int(s * 100)}%" if s < 1 else "")
                  + "); text and vector graphics unchanged")


def conv_pdf_compress(src, dst, target):
    """PDF compression to a given size, keeping the text (pages are NOT turned into images
    unless you switch that option on yourself). Order - from least to most invasive, each step
    only when the calculations say it makes sense:
    1. qpdf: lossless structure optimisation (vectors, text, fonts - nothing is lost),
    2. Ghostscript: shrinking images to a DPI computed from the limit (only when images decide the weight),
       a result worse than step 1 is discarded,
    3. optionally rendering pages to JPEG (DPI and quality from measurements)."""
    orig = Path(src).stat().st_size
    if orig <= target:
        shutil.copy(src, dst)
        step(f"The file is already {mb(orig)} (limit {mb(target)}) — nothing to do")
        return ""
    step("PDF analysis: file composition and images")
    sc = pdf_scan(src)
    try:
        imgs = pdf_images(src)
    except Exception:
        imgs = []
    pages = pdf_page_count(src, sc)
    img_total = sum(i[0] for i in imgs) or sc["images"]
    img_bytes = max(sc["images"], img_total)
    STEP(f"Composition: images {mb(sc['images'])}, fonts {mb(sc['fonts'])}, page content "
         f"{mb(sc['content'] + sc['content_raw'])} (of which uncompressed {mb(sc['content_raw'])}), "
         f"rest {mb(sc['other'] + sc['attach'])}" + (f"; pages: {pages}" if pages else ""))
    for ln in pdf_diag(src, pages):
        STEP("   " + ln)
    tmp = Path(tempfile.mkdtemp())
    cur = [Path(src), orig]                    # the best result so far that keeps the text
    try:
        # --- 0. removing data that is not visible on the page ---
        step("Step 0: removing hidden application data, XMP metadata, thumbnails and duplicates (appearance unchanged)")
        sp = tmp / "stripped.pdf"
        ss = _strip_private(src, sp)
        if ss is None:
            STEP("(skipped)")
        else:
            STEP(f"→ {mb(ss)} (limit {mb(target)})" + ("" if ss <= target else " — too big"))
            if ss < cur[1]:
                cur = [sp, ss]
                src = str(sp)                  # the following steps work on the cleaned file
            if ss <= target:
                qo0 = _qpdf_optimize(sp, tmp / "s_q.pdf")
                best0 = (tmp / "s_q.pdf", qo0) if qo0 and qo0 < ss else (sp, ss)
                shutil.copy(best0[0], dst)
                return "hidden data and duplicates removed — appearance and text unchanged"
        # --- 1. lossless ---
        step("Step 1: lossless structure optimisation (qpdf) — text and images unchanged")
        qo = tmp / "q.pdf"
        qs = _qpdf_optimize(src, qo)
        if qs is None:
            STEP("(qpdf unavailable — skipping this step)")
        else:
            STEP(f"→ {mb(qs)} (limit {mb(target)})" + ("" if qs <= target else " — too big"))
            if qs < cur[1]:
                cur = [qo, qs]
            if qs <= target:
                shutil.copy(qo, dst)
                return "lossless — text and images unchanged"

        # --- 2. nothing more can be done losslessly: SHARED quality ---
        # Images and vector artwork go down to the same resolution (dpi), chosen so that the file fits
        # the limit at the highest possible quality. Real text (fonts) stays unchanged - it costs
        # pennies. Nothing is turned into full-page bitmaps.
        floor = cur[1]
        step(f"Smallest size without quality loss: {mb(floor)}. The {mb(target)} limit requires lowering the quality "
             f"of images and vector artwork equally (text stays text)")
        pk = _need_pikepdf()
        cands, r60 = [], None
        probe = tmp / "probe"
        probe.mkdir()
        if pk:
            try:
                pdf0 = pk.open(str(cur[0]))
                cands = _form_candidates(pk, pdf0)
                r60 = _forms_ratio(pk, pdf0, cands, probe) if cands else None
                pdf0.close()
            except Exception as e:
                STEP(f"(vector artwork analysis failed: {str(e)[:100]})")
                cands = []
        if not r60:
            cands = []
        # --- 2a. images: our own JPEG recompression - lands just under the limit at the highest quality that fits,
        #         and does not depend on Ghostscript at all ---
        if pk and img_bytes > 0.03 * cur[1]:
            step("Recompressing the images (JPEG) to fit the limit at the highest possible quality")
            fit_out = tmp / "fit.pdf"
            try:
                fit = _fit_images(pk, cur[0], fit_out, target)
            except Exception as e:
                STEP(f"(own image recompression failed: {str(e)[:120]})")
                fit = None
            if fit:
                fsize, fnote = fit
                STEP(f"→ {mb(fsize)} (limit {mb(target)})" + ("" if fsize <= target else " — too big"))
                if fsize <= target:
                    shutil.copy(fit_out, dst)
                    return fnote
                if not cands:                          # nothing else that could still be shrunk
                    shutil.copy(fit_out, dst)
                    return (f"limit {mb(target)} not reachable: {mb(fsize)} is the smallest that keeps the text and "
                            f"layout ({fnote})")
                if fsize < cur[1]:
                    cur = [fit_out, fsize]
        if not imgs and sc["images"]:
            imgs = [(float(sc["images"]), 300.0, True)]
        gs = None
        if imgs and img_bytes > 0.03 * cur[1]:
            try:
                gs = tool("gs")
            except RuntimeError:
                STEP("(Ghostscript unavailable — images stay unchanged)")
        if cands:
            STEP(f"Vector artwork to shrink: {len(cands)} item(s), total {mb(sum(c[1] for c in cands))}")
        if gs:
            STEP(f"Images to shrink: {len(imgs)} item(s), total {mb(img_bytes)}")
        goal = target * 0.97
        d_hi = float(max(30.0, min(300.0, max([i[1] for i in imgs] or [200.0]) if gs else 200.0)))
        base_noimg = max(cur[1] - img_bytes, 0.0)

        def est_total(d):
            e_img = (sum(sz * min(1.0, d / ppi) ** 2 * (0.5 if jp else 0.15) for sz, ppi, jp in imgs)
                     if gs else img_bytes)
            gain = 0.0
            if cands:
                ratio = min(1.0, r60 * (d / 60.0) ** 1.8)
                gain = sum(c[1] * (1 - ratio) for c in cands if d >= 100 or not c[2])
            return base_noimg + e_img - gain

        if not gs and not cands:
            d0 = None
        else:
            d0 = 30.0
            for cand in range(int(d_hi), 29, -5):
                if est_total(cand) <= goal:
                    d0 = float(cand)
                    break

        gs_works = [True]

        def run_at(d, n):
            """One pass at resolution d: vector artwork first (pikepdf), then images (Ghostscript)."""
            st = tmp / f"w{n}"
            st.mkdir()
            work, n_flat, gs_used = cur[0], 0, False
            if cands:
                oa = st / "a.pdf"
                n_flat = _flatten_pass(pk, work, oa, cands, d, st)
                if n_flat:
                    work = oa
            if gs and gs_works[0]:
                ob = st / "b.pdf"
                opts, ps = _gs_image_args(d, PDF_Q_NORMAL if d >= 60 else PDF_Q_STRONG, 1.1)
                PROGRESS(0.0)
                try:
                    run_gs([gs, "-sDEVICE=pdfwrite", "-dNOPAUSE", "-dBATCH", "-sstdout=%stderr", *opts,
                            f"-sOutputFile={ob}", "-c", ps, "-f", str(work)], f"images → {d} dpi",
                           out_file=ob, abort_above=int(Path(work).stat().st_size * 1.05) + 1048576)
                    if ob.stat().st_size < Path(work).stat().st_size * 0.98:
                        work, gs_used = ob, True
                    else:
                        STEP("(Ghostscript did not shrink the images at this setting)")
                except Exception as e:
                    gs_works[0] = False
                    STEP(f"(Ghostscript is out: {str(e)[:100]})")
            sz = Path(work).stat().st_size
            qf = _qpdf_optimize(work, st / "f.pdf")
            if qf is not None and qf < sz:
                work, sz = st / "f.pdf", qf
            return work, sz, n_flat, gs_used

        best_fit = best_any = None
        fit = fail = None                              # (dpi, size): the highest that fit / the lowest that did not
        tried = set()
        d = d0
        for n in range(1, 6):
            if d is None:
                break
            di = int(round(max(30.0, min(d_hi, d))))
            if di in tried:
                break
            tried.add(di)
            step(f"Pass {n}: images and vector artwork → {di} dpi")
            work, size, n_flat, gs_used = run_at(di, n)
            STEP(f"→ {mb(size)} (limit {mb(target)}), vector artwork: {n_flat}, images: "
                 f"{'shrunk' if gs_used else 'unchanged'}" + ("" if size <= target else " — too big"))
            if not n_flat and not gs_used:
                if di <= 30:
                    break
                d = max(30.0, di * 0.6)                # this setting changed nothing - try a lower resolution
                continue
            res = (size, n_flat, gs_used, di, work)
            if best_any is None or size < best_any[0]:
                best_any = res
            if size <= target:
                if fit is None or di > fit[0]:
                    fit = (di, size)
                    best_fit = res
                if size >= 0.90 * target or di >= d_hi:
                    break
            else:
                if fail is None or di < fail[0]:
                    fail = (di, size)
                if di <= 30:
                    break
            if fit and fail:
                if fail[0] - fit[0] <= 3:
                    break
                frac = math.log(goal / fit[1]) / math.log(fail[1] / fit[1]) if fail[1] > fit[1] else 0.5
                d = fit[0] * (fail[0] / fit[0]) ** min(0.9, max(0.1, frac))
            elif fit:
                d = min(d_hi, fit[0] * min(1.7, max(1.05, (goal / size) ** 0.5)))
            else:
                d = max(30.0, fail[0] * max(0.5, min(0.95, (goal / size) ** 0.5)))
        pick = best_fit or best_any
        if pick and pick[0] < cur[1]:
            size, n_flat, gs_used, di, work = pick
            shutil.copy(work, dst)
            what = []
            if gs_used:
                what.append("images")
            if n_flat:
                what.append(f"{n_flat} vector artwork item(s)")
            what = " and ".join(what)
            if size <= target:
                return (f"the smallest size without quality loss was {mb(floor)}; to reach the limit the quality of "
                        f"({what}) was lowered to {di} dpi, text unchanged")
            return (f"limit {mb(target)} not reachable: the smallest possible is {mb(size)} ({what} lowered to {di} dpi, "
                    f"text unchanged); lossless was {mb(floor)}")
        if cur[1] < orig:
            shutil.copy(cur[0], dst)
            return (f"limit {mb(target)} not reachable: there are no images or artwork whose quality could be "
                    f"lowered; this is the least that can be done without damaging the text ({mb(cur[1])})")
        raise RuntimeError(f"This PDF cannot be made smaller (below {mb(orig)}): it contains no images or large "
                           f"vector artwork that could be shrunk.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _merge_many(pdfs, out, work, batch=60):
    """pdfunite takes every file on the command line (limited to ~32k characters on Windows), so many files are
    merged in batches and the partial results are merged again."""
    pdfs = list(pdfs)
    level = 0
    while len(pdfs) > batch:
        nxt = []
        for k in range(0, len(pdfs), batch):
            check_cancel()
            part = work / f"part{level}_{k // batch:04d}.pdf"
            merge_pdfs(pdfs[k:k + batch], part) if len(pdfs[k:k + batch]) > 1 else shutil.copy(pdfs[k], part)
            nxt.append(part)
        pdfs, level = nxt, level + 1
    merge_pdfs(pdfs, out)


def conv_pdf_lossless(src, dst):
    """PDF -> the smallest possible PDF WITHOUT quality loss: removes what you cannot see (hidden application data,
    thumbnails, XMP metadata), merges identical objects and packs the data better (qpdf). Images, text and vectors
    stay bit for bit. When nothing can be gained - an error with an explanation (no file is created)."""
    orig = Path(src).stat().st_size
    tmp = Path(tempfile.mkdtemp())
    try:
        best, work = (Path(src), orig), Path(src)
        step("Step 1/2: removing hidden data (XMP, thumbnails, application data) and duplicates — appearance unchanged")
        sp = tmp / "stripped.pdf"
        ss = _strip_private(src, sp)
        if ss is None:
            STEP("(skipped)")
        else:
            STEP(f"→ {mb(ss)}")
            if ss < best[1]:
                best, work = (sp, ss), sp
        step("Step 2/2: lossless repacking of the file structure (qpdf)")
        qo = tmp / "q.pdf"
        qs = _qpdf_optimize(work, qo)
        if qs is None:
            STEP("(qpdf unavailable — skipping this step)")
        else:
            STEP(f"→ {mb(qs)}")
            if qs < best[1]:
                best = (qo, qs)
        if best[1] >= orig:
            raise RuntimeError(f"This PDF cannot be made smaller without quality loss — it is already as small as it gets ({mb(orig)}). "
                               f"To shrink it further, give a size limit (at the cost of image quality).")
        shutil.copy(best[0], dst)
        saved = 100.0 * (orig - best[1]) / orig
        return (f"smallest size without quality loss: {mb(orig)} → {mb(best[1])} (−{saved:.0f}%); "
                f"XMP metadata and application editing data (e.g. Illustrator) were removed — PDF/A no longer conforms to the standard, "
                f"and the file may not be fully editable in the program that created it")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def merge_pdfs(files, dst):
    """Merging PDFs without re-rendering: pdfunite (Poppler); Ghostscript as a fallback."""
    files = [str(f) for f in files]
    try:
        unite = tool("pdfunite")
    except RuntimeError:
        unite = None
    if unite:
        try:
            step(f"Merging {len(files)} files (pdfunite)")
            run([unite, *files, str(dst)])
            return
        except RuntimeError as e:
            STEP(f"pdfunite could not do it ({str(e)[:90]}) — trying Ghostscript")
    step(f"Merging {len(files)} files (Ghostscript)")
    run_gs([tool("gs"), "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.7", "-dNOPAUSE", "-dBATCH", "-dQUIET",
            "-sstdout=%stderr", f"-sOutputFile={dst}", *files], "merging")


# ----------------------------------------------------------------------------
# Dispatcher
# ----------------------------------------------------------------------------
SAME_FALLBACK = {"video": "mp4", "image": "png"}   # when "change resolution" applies to a rare format (m4v, heic...)


def out_path(src, d, tag=""):
    src = Path(src)
    stem = src.stem + (tag if ext_of(src) == d else "")
    p = src.parent / f"{stem}.{d}"
    n = 1
    while p.exists():
        p = src.parent / f"{stem} ({n}).{d}"
        n += 1
    return p


def pdf_pages_count(src):
    try:
        m = re.search(r"Pages:\s+(\d+)", run([tool("pdfinfo"), str(src)]).stdout)
        return int(m.group(1)) if m else 1
    except Exception:
        return 1


def _unique_dir(parent, name):
    p = Path(parent) / name
    n = 1
    while p.exists():
        p = Path(parent) / f"{name} ({n})"
        n += 1
    return p


def pdf_render_dpi(src):
    """Rendering resolution of PDF pages -> image, close to the original: the median (weighted by size)
    dpi of the embedded images, within PDF_DPI_MIN..PDF_DPI_MAX. A PDF without images -> PDF_DPI_FALLBACK.
    Returns (dpi, description)."""
    try:
        imgs = [i for i in pdf_images(src) if i[1] > 1]
    except Exception:
        imgs = []
    if not imgs:
        return PDF_DPI_FALLBACK, "the PDF has no images to read the dpi from — default used"
    total = sum(i[0] for i in imgs) or float(len(imgs))
    acc, med = 0.0, imgs[0][1]
    for size, ppi, _ in sorted(imgs, key=lambda i: i[1]):
        acc += size or 1.0
        med = ppi
        if acc >= total / 2:
            break
    dpi = int(round(min(PDF_DPI_MAX, max(PDF_DPI_MIN, med)) / 10.0) * 10)
    if med > PDF_DPI_MAX:
        why = f"images in the PDF are ~{med:.0f} dpi — capped at {PDF_DPI_MAX}"
    elif med < PDF_DPI_MIN:
        why = f"images in the PDF are ~{med:.0f} dpi — raised to {PDF_DPI_MIN} to keep text sharp"
    else:
        why = f"matches the images in the PDF (~{med:.0f} dpi)"
    return dpi, why


ASK_PASSWORD = None       # the window plugs in function(file_name, attempt) -> password, or None (= give up)
PDF_PASSWORDS = []        # passwords that already worked in this run - tried first for the following files


def pdf_needs_password(path):
    """True when the PDF cannot be opened without a password (pdfinfo says so)."""
    try:
        p = prun([tool("pdfinfo"), str(path)])
    except RuntimeError:
        return False                                  # no pdfinfo yet - the conversion itself will report problems
    err = (p.stderr or b"").decode("utf-8", "replace").lower()
    return p.returncode != 0 and "password" in err


def _unlock_pdf(src):
    """Returns (path to work on, temp folder or None). A password-protected PDF is decrypted into a temporary copy;
    the password is asked for in the window (or the known ones are tried first)."""
    if not pdf_needs_password(src):
        return Path(src), None
    pk = _need_pikepdf()
    name = Path(src).name
    candidates, attempt, tried = list(PDF_PASSWORDS), 0, set()
    while True:
        if not candidates:                         # a parallel worker may have just learned a password
            candidates = [x for x in PDF_PASSWORDS if x not in tried]
        if candidates:
            pw = candidates.pop(0)
            tried.add(pw)
        else:
            pw = ASK_PASSWORD(name, attempt) if ASK_PASSWORD else None
            attempt += 1
            if pw is None:
                raise RuntimeError(f"“{name}” is password-protected. Run the conversion from the window and enter "
                                   f"the password, or remove the password first.")
        tmp = Path(tempfile.mkdtemp())
        try:
            with pk.open(str(src), password=pw) as pdf:
                pdf.save(str(tmp / name), encryption=False)
        except pk.PasswordError:
            shutil.rmtree(tmp, ignore_errors=True)
            continue
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        if pw not in PDF_PASSWORDS:
            PDF_PASSWORDS.append(pw)
        return tmp / name, tmp


def conv_pdf_images(src, d, target, res, name_src=None):
    """PDF -> images. One page = one file next to the PDF; many pages = a folder <name>_<format> with numbered
    files (to avoid clutter). Pages are rendered one at a time (no gigantic temporary files).
    Without a size limit nothing is restricted or skipped. With a limit it applies to every image separately,
    and a page that does not fit is saved without the limit (no page is skipped). Returns (path of file or folder, note)."""
    global STEP
    src = Path(src)
    nm = Path(name_src or src)                     # the name the results are called after (differs for unlocked copies)
    n = pdf_pages_count(src)
    tmp = Path(tempfile.mkdtemp())
    pdftoppm = tool("pdftoppm")
    step("Choosing the rendering resolution to match the images in the PDF")
    dpi, why = pdf_render_dpi(src)
    STEP(f"→ {dpi} dpi ({why})")
    try:
        if n <= 1:
            dst = out_path(nm, d, "_compressed" if target else (f"_{res_label(res)}" if res else ""))
            step(f"Rendering the PDF page ({dpi} dpi)")
            base = tmp / "page"
            run([pdftoppm, "-png", "-r", str(dpi), "-f", "1", "-l", "1", "-singlefile", str(src), str(base)])
            return dst, "; ".join(x for x in (f"{dpi} dpi", conv_image(str(base) + ".png", str(dst), target, res)) if x)
        folder = _unique_dir(nm.parent, f"{nm.stem}_{d}")
        folder.mkdir()
        step(f"The PDF has {n} pages — saving them as .{d} into the folder “{folder.name}”")
        width = len(str(n))
        done, over, notes = 0, [], set()
        saved = STEP
        STEP = lambda msg: None                    # do not flood the log with hundreds of entries (one per page)
        try:
            for i in range(1, n + 1):
                PROGRESS((i - 1) / n)
                DETAIL(f"Page {i}/{n}")
                base = tmp / "page"
                png = Path(str(base) + ".png")
                out = folder / f"{nm.stem}_{i:0{width}d}.{d}"
                try:
                    # a rendering error aborts everything (nothing is silently skipped)
                    check_cancel()
                    run([pdftoppm, "-png", "-r", str(dpi), "-f", str(i), "-l", str(i), "-singlefile",
                         str(src), str(base)])
                    if d == "png" and not target and not res:
                        shutil.copy(png, out)          # PNG -> PNG: no re-encoding
                    elif not target:
                        nt = conv_image(str(png), str(out), None, res)
                        if nt:
                            notes.add(nt)
                    else:
                        try:
                            nt = conv_image(str(png), str(out), target, res)
                            if nt:
                                notes.add(nt)
                        except RuntimeError:
                            # the page does not fit within the limit: we save it anyway (without the limit), nothing is skipped
                            if out.exists():
                                out.unlink()
                            conv_image(str(png), str(out), None, res)
                            over.append(i)
                    done += 1
                finally:
                    if png.exists():
                        png.unlink()
        finally:
            STEP = saved
        parts = [f"{done} files in the folder", f"{dpi} dpi"] + sorted(notes)
        if over:
            parts.append("pages that did not fit within the limit (saved without the limit): "
                         + ", ".join(map(str, over[:20])) + ("…" if len(over) > 20 else ""))
        elif target:
            parts.append("the limit applies to every image separately")
        return folder, "; ".join(parts)
    except BaseException:                          # also Cancelled: we do not leave an unfinished folder behind
        if "folder" in locals():
            shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def convert_one(src, d, target_bytes, res=None, lossless_only=False, ocr_only=False, trim=None):
    """res: spec from parse_res() or None. lossless_only: PDF -> PDF of the smallest size without
    quality loss (no limit). ocr_only: PDF (or image) -> PDF with a text layer added by OCR.
    trim: (start_s, end_s or None, exact) - cut a clip out of a video/audio file (same format).
    Returns (result path, note)."""
    src = Path(src)
    cat = category(src)
    if cat is None:
        raise RuntimeError(f"Unsupported input format: .{ext_of(src)}")
    if trim:
        if cat not in ("video", "audio"):
            raise RuntimeError("Trimming works on video and audio files.")
        st, en, exact = trim[:3]
        frame = trim[4] if len(trim) > 4 else None
        if frame:
            if cat != "video":
                raise RuntimeError("A frame can only be taken from a video.")
            total = probe_duration(probe(str(src)))
            if total and st >= total:
                raise RuntimeError(f"The time ({fmt_time(st)}) is after the end of the video ({fmt_time(total)}).")
            dst = out_path(src.with_name(f"{src.stem}_frame_{fmt_time(st)}{src.suffix}"), frame)
            try:
                run_ff([tool("ffmpeg"), "-y", "-ss", f"{st:.3f}", "-i", str(src), "-frames:v", "1", "-an",
                        *(["-q:v", "1"] if frame == "jpg" else []), str(dst)], None)
                if not dst.exists() or dst.stat().st_size == 0:
                    raise RuntimeError("Could not take a frame at that time.")
            except BaseException:
                if dst.exists():
                    try:
                        dst.unlink()
                    except OSError:
                        pass
                raise
            return dst, f"full-resolution frame at {fmt_time(st)}" + (" (maximum JPEG quality)" if frame == "jpg" else " (lossless PNG)")
        as_gif = len(trim) > 3 and trim[3]
        if as_gif and cat != "video":
            raise RuntimeError("Only a video can be cut into a GIF.")
        tagc = f"_cut_{fmt_time(st)}-{fmt_time(en) if en else 'end'}"
        dst = out_path(src.with_name(src.stem + tagc + src.suffix), "gif") if as_gif else out_path(src, ext_of(src), tagc)
        try:
            if as_gif:
                total = probe_duration(probe(str(src)))
                if total and st >= total:
                    raise RuntimeError(f"The start ({fmt_time(st)}) is after the end of the file ({fmt_time(total)}).")
                if en is not None and en <= st:
                    raise RuntimeError("The end of the clip must be later than its start.")
                note = conv_video_to_gif(str(src), str(dst), None, None, (st, (en - st) if en else None))
            else:
                note = conv_trim(str(src), str(dst), st, en, exact)
        except BaseException:
            if dst.exists() and dst.is_file():
                try:
                    dst.unlink()
                except OSError:
                    pass
            raise
        return dst, note
    if d not in TARGETS[cat]:
        raise RuntimeError(f"Cannot convert .{ext_of(src)} to .{d}")
    # the resolution applies to video (not to audio alone), images and PDF -> image
    if not (cat == "video" and d not in AUDIO_ENC or cat == "image"):
        res = None
    lossless_only = bool(lossless_only) and cat == "pdf" and d == "pdf"
    ocr_only = bool(ocr_only) and d == "pdf" and cat in ("pdf", "image")
    if ext_of(src) == d and not target_bytes and not res and not lossless_only and not ocr_only:
        raise RuntimeError("The source and target formats are the same "
                           "(tick compression or change the resolution).")
    tag = ("_ocr" if ocr_only else "_compressed" if (target_bytes or lossless_only)
           else (f"_{res_tag(src, cat, res)}" if res else ""))
    dst = out_path(src, d, tag)
    note = ""
    orig_src, unlock_tmp = src, None
    if (target_bytes and not res and cat in ("video", "audio", "image") and ext_of(src) == d
            and src.stat().st_size <= target_bytes):
        shutil.copy(src, dst)                      # nothing to compress - no re-encoding and no quality loss
        return dst, f"the file is already {mb(src.stat().st_size)} (limit {mb(target_bytes)}) — copied unchanged"
    try:
        if cat == "pdf":
            src, unlock_tmp = _unlock_pdf(src)         # password-protected PDF -> temporary decrypted copy
        if cat in ("video", "audio"):
            note = conv_av(str(src), str(dst), target_bytes, res)
        elif cat == "pdf" and d in IMAGE_OUT:
            dst, note = conv_pdf_images(src, d, target_bytes, res, name_src=orig_src)
        elif cat == "image" and ocr_only:
            note = conv_image_ocr(str(src), str(dst), res)
        elif cat == "image":
            note = conv_image(str(src), str(dst), target_bytes, res)
        elif cat == "pdf" and d == "pdf":
            if ocr_only:
                note = conv_pdf_ocr(str(src), str(dst))
            elif lossless_only:
                note = conv_pdf_lossless(str(src), str(dst))
            else:
                note = conv_pdf_compress(str(src), str(dst), target_bytes)
        elif cat == "pdf":
            note = conv_pdf_text(str(src), str(dst), target_bytes) or ""
        else:
            conv_text(str(src), str(dst), target_bytes)
    except BaseException:                          # also Cancelled: the unfinished result file disappears
        if dst.exists() and dst.is_file():
            try:
                dst.unlink()
            except OSError:
                pass
        raise
    finally:
        if unlock_tmp:
            shutil.rmtree(unlock_tmp, ignore_errors=True)
    notes = [note] if note else []
    if unlock_tmp:
        notes.append("the PDF was password-protected — the result is saved WITHOUT the password")
    if target_bytes and dst.is_file() and dst.stat().st_size > target_bytes * 1.02:
        notes.append("the result slightly exceeds the requested size")
    return dst, "; ".join(notes)


# ----------------------------------------------------------------------------
# Metadata: show / remove (images, PDF, audio, video)
# ----------------------------------------------------------------------------
def _short(v, n=160):
    if isinstance(v, bytes):
        try:
            v = v.decode("utf-8")
        except UnicodeDecodeError:
            return f"<{len(v)} bytes of binary data>"
    v = str(v).replace("\x00", "").strip()
    return v if len(v) <= n else v[:n] + "…"


def _gps_deg(vals, ref):
    d = [float(x) for x in vals]
    deg = d[0] + d[1] / 60 + d[2] / 3600
    return -deg if str(ref).upper() in ("S", "W") else deg


def _meta_image(p):
    lines = []
    tools_manager.use_python_path()
    try:
        from PIL import Image, ExifTags
        im = Image.open(p)
    except Exception:
        im = None
    if im is None:                                   # HEIC, AVIF... : let ImageMagick read it
        r = prun([tool("magick"), "identify", "-format", "%[EXIF:*]\n", str(p)], text=True, encoding="utf-8", errors="replace")
        return [("  " + ln) for ln in (r.stdout or "").splitlines() if ln.strip()] or ["  (no readable metadata)"]
    lines.append(f"Format: {im.format}, {im.size[0]}x{im.size[1]} px, mode {im.mode}")
    exif = im.getexif()
    gps_line = None
    if exif:
        lines.append("EXIF:")
        for tag, val in exif.items():
            lines.append(f"  {ExifTags.TAGS.get(tag, tag)}: {_short(val)}")
        try:
            for tag, val in exif.get_ifd(0x8769).items():
                lines.append(f"  {ExifTags.TAGS.get(tag, tag)}: {_short(val)}")
        except Exception:
            pass
        try:
            gps = {ExifTags.GPSTAGS.get(t, t): v for t, v in exif.get_ifd(0x8825).items()}
        except Exception:
            gps = {}
        if gps:
            lines.append("GPS location:")
            for k, v in gps.items():
                lines.append(f"  {k}: {_short(v)}")
            try:
                lat = _gps_deg(gps["GPSLatitude"], gps.get("GPSLatitudeRef", "N"))
                lon = _gps_deg(gps["GPSLongitude"], gps.get("GPSLongitudeRef", "E"))
                gps_line = f"⚠ This photo contains a GPS location: {lat:.6f}, {lon:.6f}  (https://maps.google.com/?q={lat:.6f},{lon:.6f})"
            except Exception:
                gps_line = "⚠ This photo contains GPS data"
    for k, v in im.info.items():
        if k in ("exif", "icc_profile", "photoshop", "xmp", "XML:com.adobe.xmp"):
            if k != "exif":
                lines.append(f"{'ICC colour profile' if k == 'icc_profile' else k}: present ({len(v)} bytes)")
        elif isinstance(v, (str, bytes)):
            lines.append(f"{k}: {_short(v)}")
    if gps_line:
        lines.insert(0, gps_line)
    if len(lines) <= 1:
        lines.append("No EXIF / GPS / text metadata found.")
    return lines


def _meta_pdf(p):
    if pdf_needs_password(p):
        return ["The PDF is password-protected - its metadata cannot be read."]
    pk = _need_pikepdf()
    lines = []
    with pk.open(str(p)) as pdf:
        lines.append(f"Pages: {len(pdf.pages)}  |  PDF {pdf.pdf_version}")
        info = pdf.docinfo
        if len(info):
            lines.append("Document information:")
            for k, v in info.items():
                lines.append(f"  {str(k).lstrip('/')}: {_short(v)}")
        else:
            lines.append("No document information (author, title, creator...).")
        if "/Metadata" in pdf.Root:
            lines.append(f"XMP metadata: present ({len(pdf.Root.Metadata.read_bytes())} bytes)")
        if "/PieceInfo" in pdf.Root:
            lines.append("Private application data (PieceInfo): present")
    return lines


def _meta_av(p):
    info = probe(str(p))
    lines = []
    fmt = info.get("format", {})
    lines.append(f"Container: {fmt.get('format_long_name', '?')}, duration {float(fmt.get('duration') or 0):.1f} s")
    tags = fmt.get("tags") or {}
    if tags:
        lines.append("Tags:")
        lines += [f"  {k}: {_short(v)}" for k, v in tags.items()]
    for s in info.get("streams", []):
        t = s.get("tags") or {}
        lines.append(f"Stream #{s.get('index')}: {s.get('codec_type')} {s.get('codec_name')}"
                     + (" — " + ", ".join(f"{k}={_short(v, 60)}" for k, v in t.items()) if t else ""))
    if not tags:
        lines.append("No global tags (title, artist, location...).")
    loc = _av_location(tags)
    if loc:
        lines.insert(0, f"⚠ This file contains a GPS location: {loc[0]:.6f}, {loc[1]:.6f}  "
                        f"(https://maps.google.com/?q={loc[0]:.6f},{loc[1]:.6f})")
    return lines


def _av_location(tags):
    """Latitude, longitude from the ISO 6709 tags of phone videos (e.g. +54.2610+018.6316/), or None."""
    import re
    for k, v in (tags or {}).items():
        if "location" not in k.lower() and "iso6709" not in k.lower():
            continue
        m = re.match(r"\s*([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", str(v))
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
    return None


def metadata_report(path):
    """Human-readable metadata of a file, as a list of lines."""
    p = Path(path)
    cat = category(p)
    lines = [f"File: {p.name}   ({mb(p.stat().st_size)})"]
    if cat == "image":
        lines += _meta_image(p)
    elif cat == "pdf":
        lines += _meta_pdf(p)
    elif cat in ("video", "audio"):
        lines += _meta_av(p)
    else:
        lines.append("Metadata is not available for this file type.")
    return lines


def _strip_jpeg(src, dst):
    """Lossless: drops EXIF/XMP/IPTC/comment segments (the picture data is copied byte for byte). Only the rotation
    flag is kept, so that photos taken sideways do not turn."""
    data = Path(src).read_bytes()
    if data[:2] != b"\xff\xd8":
        raise RuntimeError("Not a JPEG file.")
    orientation = 1
    try:
        tools_manager.use_python_path()
        import io
        from PIL import Image
        orientation = int(Image.open(io.BytesIO(data)).getexif().get(0x0112, 1) or 1)
    except Exception:
        pass
    out, i, dropped = bytearray(b"\xff\xd8"), 2, 0
    while i < len(data) - 3:
        if data[i] != 0xFF:
            raise RuntimeError("Damaged JPEG file.")
        m = data[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m in (0xD9, 0xDA) or m == 0x00:
            out += data[i:]
            break
        if m == 0x01 or 0xD0 <= m <= 0xD7:
            out += data[i:i + 2]
            i += 2
            continue
        ln = int.from_bytes(data[i + 2:i + 4], "big")
        seg = data[i:i + 2 + ln]
        keep = m in (0xE0, 0xEE) or (m == 0xE2 and seg[4:16] == b"ICC_PROFILE") or not (0xE1 <= m <= 0xEF or m == 0xFE)
        if keep:
            out += seg
        else:
            dropped += 1
        i += 2 + ln
    if orientation not in (0, 1):
        tiff = b"MM\x00\x2a\x00\x00\x00\x08" + b"\x00\x01" + b"\x01\x12\x00\x03\x00\x00\x00\x01" + orientation.to_bytes(2, "big") \
               + b"\x00\x00" + b"\x00\x00\x00\x00"
        seg = b"\xff\xe1" + (2 + 6 + len(tiff)).to_bytes(2, "big") + b"Exif\x00\x00" + tiff
        pos = 2 + (2 + int.from_bytes(out[4:6], "big") if out[2:4] == b"\xff\xe0" else 0)
        out[pos:pos] = seg
    Path(dst).write_bytes(bytes(out))
    return f"{dropped} metadata block(s) removed (EXIF/GPS, XMP, IPTC, comments); the picture itself is unchanged"


def _strip_png(src, dst):
    data = Path(src).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("Not a PNG file.")
    out, i, dropped = bytearray(data[:8]), 8, 0
    while i < len(data):
        ln = int.from_bytes(data[i:i + 4], "big")
        typ = data[i + 4:i + 8]
        chunk = data[i:i + 12 + ln]
        if typ in (b"eXIf", b"tEXt", b"iTXt", b"zTXt", b"tIME"):
            dropped += 1
        else:
            out += chunk
        i += 12 + ln
    Path(dst).write_bytes(bytes(out))
    return f"{dropped} metadata chunk(s) removed; the picture itself is unchanged"


def _strip_webp(src, dst):
    data = Path(src).read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise RuntimeError("Not a WebP file.")
    out, i, dropped = bytearray(), 12, 0
    while i < len(data) - 7:
        typ, ln = data[i:i + 4], int.from_bytes(data[i + 4:i + 8], "little")
        chunk = data[i:i + 8 + ln + (ln & 1)]
        if typ in (b"EXIF", b"XMP "):
            dropped += 1
        else:
            if typ == b"VP8X":
                chunk = bytearray(chunk)
                chunk[8] &= ~(0x08 | 0x04)             # clear the EXIF and XMP flags
                chunk = bytes(chunk)
            out += chunk
        i += 8 + ln + (ln & 1)
    Path(dst).write_bytes(b"RIFF" + (len(out) + 4).to_bytes(4, "little") + b"WEBP" + bytes(out))
    return f"{dropped} metadata chunk(s) removed; the picture itself is unchanged"


def _strip_image(src, dst):
    e = ext_of(src)
    if e == "jpg":
        return _strip_jpeg(src, dst)
    if e == "png":
        return _strip_png(src, dst)
    if e == "webp":
        return _strip_webp(src, dst)
    if e in ("tiff", "bmp", "gif", "ico"):
        run([tool("magick"), str(src), "-strip", str(dst)])          # lossless formats: a plain rewrite
        return "metadata removed (the format is lossless, so the picture is unchanged)"
    raise RuntimeError(f".{e} would have to be re-encoded to remove its metadata (quality loss). "
                       f"Convert it to JPG / PNG / WebP first.")


def _strip_pdf(src, dst):
    work, tmp = _unlock_pdf(src)
    try:
        pk = _need_pikepdf()
        with pk.open(str(work)) as pdf:
            had = len(pdf.docinfo) if "/Info" in pdf.trailer else 0
            if "/Info" in pdf.trailer:
                del pdf.trailer["/Info"]
            for key in ("/Metadata", "/PieceInfo"):
                if key in pdf.Root:
                    del pdf.Root[key]
            for page in pdf.pages:
                for key in ("/PieceInfo", "/Thumb", "/Metadata"):
                    if key in page.obj:
                        del page.obj[key]
            pdf.save(str(dst), encryption=False)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return (f"document information ({had} field(s)), XMP metadata, thumbnails and application data removed; "
            f"pages unchanged" + ("; saved WITHOUT the password" if tmp else ""))


def _strip_av(src, dst):
    run_ff([tool("ffmpeg"), "-y", "-i", str(src), "-map", "0", "-c", "copy", "-map_metadata", "-1",
            "-map_metadata:s:v", "-1", "-map_metadata:s:a", "-1", "-map_chapters", "-1",
            "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact", str(dst)], probe_duration(probe(str(src))))
    return "tags, location, encoder and chapter data removed; audio and video streams copied unchanged"


def strip_metadata(src):
    """Saves a copy of the file without its metadata (name_clean.ext). Returns (path, note)."""
    src = Path(src)
    cat = category(src)
    if cat not in ("image", "pdf", "video", "audio"):
        raise RuntimeError("Metadata removal is not supported for this file type.")
    dst = out_path(src, ext_of(src), "_clean")
    try:
        note = {"image": _strip_image, "pdf": _strip_pdf, "video": _strip_av, "audio": _strip_av}[cat](src, dst)
    except BaseException:
        if dst.exists() and dst.is_file():
            try:
                dst.unlink()
            except OSError:
                pass
        raise
    return dst, note


# ----------------------------------------------------------------------------
# Collecting many selected files into one window
# ----------------------------------------------------------------------------
def collect_files(first):
    """The first process started becomes the "leader": it waits a moment for the other processes (one per file)
    to write their files into the queue, and opens one window for all of them. The other processes exit at once.

    No race: files are written atomically (write to .tmp + rename), picked up atomically (renamed to a unique name
    before being read), and the leader releases the lock and only THEN picks up the queue
    once more - a process that wrote its file just before a failed attempt to become leader always makes it into it
    (its file existed before the lock was released)."""
    qd = Path(tempfile.gettempdir()) / "FileConverterQueue"
    qd.mkdir(exist_ok=True)
    mine = uuid.uuid4().hex
    tmpf = qd / f"{mine}.tmp"
    tmpf.write_text(first, encoding="utf-8")
    tmpf.replace(qd / f"{mine}.txt")               # only now is the file visible to the leader
    lock = qd / "leader.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        try:  # a stale lock left after a crash
            if time.time() - lock.stat().st_mtime > 20:
                lock.rmdir()
                lock.mkdir()
            else:
                return None
        except OSError:
            return None
    time.sleep(1.2)
    files = []

    def drain():
        for f in list(qd.glob("*.txt")):
            taken = f.with_name(f"{f.stem}.{uuid.uuid4().hex}.taken")
            try:
                f.replace(taken)                   # atomic: only one process will take this file
            except OSError:
                continue
            try:
                files.append(taken.read_text(encoding="utf-8").strip())
            except OSError:
                pass
            finally:
                try:
                    taken.unlink()
                except OSError:
                    pass

    drain()
    try:
        lock.rmdir()
    except OSError:
        pass
    drain()                                        # the tail: whoever managed to write a file before the lock was released
    return sorted(set(f for f in files if f), key=str.lower)


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------
LOG_FILE = Path(tempfile.gettempdir()) / "Konwertexpol_last.log"       # the last run's log (also shown in "Diagnostics")


def _tool_version(path, name):
    """First line of the tool's version output (best effort, 6 s limit)."""
    flag = {"ffmpeg": "-version", "ffprobe": "-version", "pdftotext": "-v", "pdftoppm": "-v", "pdfinfo": "-v",
            "pdfimages": "-v", "pdftocairo": "-v", "pdfunite": "-v"}.get(name, "--version")
    if name == "browser":
        return ""
    try:
        r = subprocess.run([str(path), flag], capture_output=True, text=True, timeout=6, errors="replace",
                           creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        out = (r.stdout or r.stderr or "").strip().splitlines()
        return out[0][:80] if out else ""
    except Exception:
        return ""


def diagnostics_text():
    """What the program found on this computer: tools (path, version, where from), Python modules, OCR data."""
    import platform
    from importlib.metadata import version as pkg_version
    tools_manager.use_python_path()
    L = ["Konwertexpol diagnostics", "",
         f"Python {sys.version.split()[0]} ({platform.architecture()[0]}) on {platform.platform()}",
         f"Program folder: {Path(__file__).resolve().parent}", f"Tools folder:   {tools_manager.TOOLS}", ""]
    ov = tools_manager._overrides()
    if ov:
        L += ["paths.txt:"] + [f"  {k} = {v}" for k, v in ov.items()] + [""]
    L.append("Tools:")
    for name in ("ffmpeg", "ffprobe", "magick", "cjxl", "djxl", "pandoc", "pdftotext", "pdftoppm", "pdfinfo",
                 "pdfimages", "pdftocairo", "pdfunite", "qpdf", "gs", "tesseract", "browser"):
        try:
            p = tool(name)
        except RuntimeError:
            L.append(f"  {name:11} NOT FOUND (downloaded automatically when it is needed)")
            continue
        where = ("paths.txt" if name in ov else "tools folder" if str(p).lower().startswith(str(tools_manager.TOOLS).lower())
                 else "this computer (PATH / registry / Program Files)")
        L.append(f"  {name:11} {_tool_version(p, name)}  [{where}]\n              {p}")
    L += ["", "Python modules:"]
    for mod, dist in (("pikepdf", "pikepdf"), ("PIL", "Pillow"), ("pdf2docx", "pdf2docx"), ("ocrmypdf", "ocrmypdf")):
        try:
            L.append(f"  {dist:11} {pkg_version(dist)}" + ("" if tools_manager.module_available(mod) else "  (not importable!)"))
        except Exception:
            L.append(f"  {dist:11} NOT INSTALLED (downloaded automatically when it is needed)")
    td = tools_manager.TESSDATA
    langs = sorted(p.stem for p in td.glob("*.traineddata")) if td.is_dir() else []
    L += ["", f"OCR language data ({td}): {', '.join(langs) or 'none yet'}",
          f"OCR default language: {tools_manager.default_ocr_lang()}", f"Last log file: {LOG_FILE}"]
    return "\n".join(L)


class DiagWindow(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Konwertexpol — diagnostics")
        self.geometry("820x560")
        self.q = queue.Queue()
        fr = ttk.Frame(self)
        fr.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        self.text = tk.Text(fr, wrap="none")
        sb = ttk.Scrollbar(fr, command=self.text.yview)
        self.text.config(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.text.insert("end", "Collecting information…")
        btns = ttk.Frame(self)
        btns.pack(pady=8)
        ttk.Button(btns, text="Copy", command=self.copy).pack(side="left", padx=4)
        ttk.Button(btns, text="Save…", command=self.save).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="left", padx=4)
        threading.Thread(target=lambda: self.q.put(diagnostics_text()), daemon=True).start()
        self.after(150, self.poll)

    def poll(self):
        try:
            txt = self.q.get_nowait()
        except queue.Empty:
            self.after(150, self.poll)
            return
        self.text.delete("1.0", "end")
        self.text.insert("end", txt)

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", "end"))

    def save(self):
        p = filedialog.asksaveasfilename(parent=self, defaultextension=".txt", filetypes=[("Text", "*.txt")],
                                         initialfile="konwertexpol_diagnostics.txt")
        if p:
            Path(p).write_text(self.text.get("1.0", "end"), encoding="utf-8")


AUTHOR_URL = "https://www.facebook.com/ajtiguy"
LOGO_FILE = Path(__file__).with_name("icons") / "logo.png"
ICON_FILE = Path(__file__).with_name("icons") / "konwertexpol.ico"


def add_logo(win):
    """The author's logo at the bottom of a window; a click opens his page. Call it right after creating the
    window, so the footer keeps its place however the rest is laid out."""
    import webbrowser
    if not isinstance(win, tk.Tk):                          # a tab: the host window has the logo
        return None
    img = None
    try:
        img = tk.PhotoImage(file=str(LOGO_FILE))
        if img.width() > 300:
            img = img.subsample(max(1, round(img.width() / 280)))
    except Exception:
        img = None
    if img is not None:
        lbl = tk.Label(win, image=img, bg="#000000", cursor="hand2", bd=0, highlightthickness=0)
        lbl.image = img                                     # keep a reference, or Tk drops the picture
    else:
        lbl = tk.Label(win, text="Created by IT Guy", fg="#1a5fb4", cursor="hand2", font=("TkDefaultFont", 9, "underline"))
    lbl.pack(side="bottom", pady=(0, 6))
    lbl.bind("<Button-1>", lambda e: webbrowser.open(AUTHOR_URL))
    try:                                                    # the application icon in the title bar / taskbar
        if ICON_FILE.exists() and isinstance(win, tk.Tk):
            win.iconbitmap(str(ICON_FILE))
    except Exception:
        pass
    return lbl


class DialogsMixin:
    """Questions the worker thread can ask in the window (needs self.q, self._tool_skipped)."""

    def _init_window(self, master=None):
        """A window of its own (master None), or a tab inside the "Video tools" window (master = the notebook)."""
        self._embedded = master is not None
        if master is None:
            tk.Tk.__init__(self)
        else:
            tk.Frame.__init__(self, master)
            self._top = self.winfo_toplevel()

    def _confirm_install(self, pkg, label, exe):
        """Called from the worker thread when Ghostscript / Tesseract cannot be found: asks what to do.
        Returns "install", "skip" or the full path of an existing copy."""
        while not _ASK_LOCK.acquire(timeout=0.3):         # parallel workers: one question at a time
            check_cancel()
        try:
            if pkg in self._tool_skipped:
                return "skip"
            ev, box = threading.Event(), {}
            self.q.put(("ask_tool", (label, exe, box, ev)))
            while not ev.wait(0.3):
                check_cancel()
            choice = box.get("choice", "skip")
            if choice == "skip":
                self._tool_skipped.add(pkg)
            return choice
        finally:
            _ASK_LOCK.release()

    def _tool_dialog(self, label, exe, box, ev):
        dlg = tk.Toplevel(self.winfo_toplevel())
        dlg.title(f"{label} not found")
        dlg.transient(self.winfo_toplevel())
        ttk.Label(dlg, justify="left",
                  text=f"{label} is needed for this, but it was not found on this computer\n"
                       f"(checked the tools folder, PATH, the Windows registry and the usual Program Files folders).\n\n"
                       f"It can be installed into the tools folder now (asks for administrator permission).\n"
                       f"If you already have it, choose “Locate…” and pick {exe}."
                  ).pack(anchor="w", padx=14, pady=(12, 8))

        def finish(choice):
            box["choice"] = choice
            ev.set()
            dlg.destroy()

        def locate():
            p = filedialog.askopenfilename(parent=dlg, title=f"Locate {exe}", filetypes=[(exe, exe), ("Programs", "*.exe")])
            if p:
                finish(p)

        row = ttk.Frame(dlg)
        row.pack(pady=12)
        ttk.Button(row, text="Install it", command=lambda: finish("install")).pack(side="left", padx=4)
        ttk.Button(row, text="Locate… (I already have it)", command=locate).pack(side="left", padx=4)
        ttk.Button(row, text="Skip", command=lambda: finish("skip")).pack(side="left", padx=4)
        dlg.protocol("WM_DELETE_WINDOW", lambda: finish("skip"))
        try:
            dlg.grab_set()
        except tk.TclError:
            pass

    def _ask_password(self, name, attempt):
        """Called from the worker thread: asks for the password of a PDF. Returns the password or None."""
        while not _ASK_LOCK.acquire(timeout=0.3):
            check_cancel()
        try:
            ev, box = threading.Event(), {}
            self.q.put(("ask_password", (name, attempt, box, ev)))
            while not ev.wait(0.3):
                check_cancel()
            return box.get("pw")
        finally:
            _ASK_LOCK.release()

    def _password_dialog(self, name, attempt, box, ev):
        dlg = tk.Toplevel(self.winfo_toplevel())
        dlg.title("Password required")
        dlg.transient(self.winfo_toplevel())
        ttk.Label(dlg, justify="left",
                  text=f"“{name}” is password-protected.\n"
                       + ("The password was wrong - try again.\n" if attempt else "")
                       + "Enter the password (the result will be saved without it):").pack(anchor="w", padx=14, pady=(12, 6))
        entry = ttk.Entry(dlg, show="•", width=34)
        entry.pack(padx=14)
        entry.focus_set()

        def finish(pw):
            box["pw"] = pw
            ev.set()
            dlg.destroy()

        row = ttk.Frame(dlg)
        row.pack(pady=12)
        ttk.Button(row, text="OK", command=lambda: finish(entry.get())).pack(side="left", padx=4)
        ttk.Button(row, text="Skip this file", command=lambda: finish(None)).pack(side="left", padx=4)
        entry.bind("<Return>", lambda e: finish(entry.get()))
        dlg.protocol("WM_DELETE_WINDOW", lambda: finish(None))
        try:
            dlg.grab_set()
        except tk.TclError:
            pass


class _AppBody(DialogsMixin):
    def __init__(self, files, to=None, compress=False, res=None, focus_res=False, ocr=False, trim=False, master=None):
        self._init_window(master)
        add_logo(self)
        self.files = files
        self.q = queue.Queue()
        self.running = False
        self.same = bool(res) or bool(trim)   # "Resize to ..." / "Trim": every file stays in its own format
        self.active = {}               # files being converted right now: idx -> {name, t0, frac, detail}
        self.done_n, self.batch_t0 = 0, 0.0
        self.failed = []               # [(path, reason)] of the last run
        self.retry_btn = None
        self.trim_mode = bool(trim)
        self.show_btn = None
        self.closing = False
        self._tool_skipped = set()
        self._ac_n = None                       # auto-close countdown (None = not running)
        self.autoclose = tk.BooleanVar(value=not self._embedded)
        self.title("Konwertexpol")
        self.minsize(580, 420)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind_all("<Button-1>", self._ac_stop, add="+")      # any click / key keeps the window open
        self.bind_all("<Key>", self._ac_stop, add="+")
        try:
            LOG_FILE.write_text("", encoding="utf-8")
        except OSError:
            pass

        pad = dict(padx=12, pady=6)
        self.files_lbl = ttk.Label(self, text=f"Selected files ({len(files)}):")
        self.files_lbl.pack(anchor="w", **pad)
        fr = ttk.Frame(self)
        fr.pack(fill="x", padx=12)
        self.lb = tk.Listbox(fr, height=5)
        sb = ttk.Scrollbar(fr, command=self.lb.yview)
        self.lb.config(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="x", expand=True)
        sb.pack(side="right", fill="y")
        for f in files:
            self.lb.insert("end", f.name)

        row = ttk.Frame(self)
        if not trim:
            row.pack(fill="x", **pad)
        ttk.Label(row, text="Convert to:").pack(side="left")
        cats = {category(f) for f in files if category(f)}
        self.cats = set(cats)
        fmts = []
        for c in ("video", "audio", "image", "text", "pdf"):
            if c in cats:
                for x in TARGETS[c]:
                    if x not in fmts:
                        fmts.append(x)
        self.fmt = ttk.Combobox(row, values=fmts, state="readonly", width=10)
        first_ext = ext_of(files[0]) if files else ""
        self.fmt.set(next((x for x in fmts if x != first_ext), fmts[0] if fmts else ""))
        self.fmt.pack(side="left", padx=8)

        row2 = ttk.Frame(self)
        if not trim:
            row2.pack(fill="x", **pad)
        self.comp = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="Compress to size:", variable=self.comp,
                        command=self._toggle).pack(side="left")
        self.size = ttk.Entry(row2, width=8, state="disabled")
        self.size.pack(side="left", padx=6)
        self.unit = ttk.Combobox(row2, values=["KB", "MB"], state="disabled", width=5)
        self.unit.set("MB")
        self.unit.pack(side="left")

        # rarely used options live in a collapsible section, so the window also fits small screens
        self.lossless_only = tk.BooleanVar(value=False)
        self.ocr_pdf = tk.BooleanVar(value=False)
        self.ocr_lang = tk.StringVar(value=tools_manager.default_ocr_lang())
        self.true_lossless = tk.BooleanVar(value=False)
        self.use_gpu = tk.BooleanVar(value=False)
        self.adv = ttk.Frame(self)
        self.adv_open = False
        self.adv_btn = None
        if cats & {"pdf", "image", "video"} and not trim:
            self.adv_btn = ttk.Button(self, text="More options ▸", command=self._toggle_adv)
            self.adv_btn.pack(anchor="w", padx=12, pady=(0, 2))
        if "pdf" in cats:
            ttk.Checkbutton(self.adv, text="PDF: smallest possible size WITHOUT quality loss (instead of a size limit)",
                            variable=self.lossless_only, command=self._toggle_lossless).pack(anchor="w", padx=12, pady=(2, 0))
        if "pdf" in cats or "image" in cats:
            # PDF output + OCR: adds a text layer to scanned pages / photos of documents; the images stay as they are
            ttk.Checkbutton(self.adv, text="PDF output: add a searchable text layer (OCR) — for scans and photos of documents",
                            variable=self.ocr_pdf, command=self._toggle_ocr).pack(anchor="w", padx=12, pady=(2, 0))
            row2e = ttk.Frame(self.adv)
            row2e.pack(fill="x", padx=12, pady=(2, 0))
            ttk.Label(row2e, text="OCR language(s):").pack(side="left")
            ttk.Combobox(row2e, textvariable=self.ocr_lang, values=OCR_PRESETS, width=14).pack(side="left", padx=6)
            ttk.Label(row2e, foreground="#666", text="e.g. eng, pol+eng, deu (also used for scanned PDF → text / Word)"
                      ).pack(side="left")
        if "video" in cats:
            ttk.Checkbutton(self.adv, text="Video: use the graphics card for high-quality H.264 encoding (faster; same "
                                           "settings, slightly larger file). Not used for size limits.",
                            variable=self.use_gpu).pack(anchor="w", padx=12, pady=(2, 0))
            ttk.Checkbutton(self.adv, text="Video: force lossless encoding (crf 0) — huge files, many devices cannot play them",
                            variable=self.true_lossless).pack(anchor="w", padx=12, pady=(2, 0))

        # resolution: video, images and PDF -> image
        self.res = self.rw = self.rh = None
        self.keep = tk.BooleanVar(value=True)
        self._ar = None                 # aspect ratio (w/h) of the first video/image file
        self._ar_done = False
        if cats & {"video", "image"} and not trim:
            presets = ["Original"]
            if "video" in cats:
                presets += VIDEO_RES_PRESETS
            if "image" in cats:
                presets += IMAGE_RES_PRESETS
            row3 = ttk.Frame(self)
            row3.pack(fill="x", padx=12, pady=(6, 0))
            ttk.Label(row3, text="Resolution – preset:").pack(side="left")
            self.res = ttk.Combobox(row3, values=presets, width=14)
            self.res.set("Original")
            self.res.pack(side="left", padx=8)
            self.res.bind("<<ComboboxSelected>>", lambda e: self._clear_wh())
            row4 = ttk.Frame(self)
            row4.pack(fill="x", padx=12, pady=(4, 0))
            ttk.Label(row4, text="…or custom:").pack(side="left")
            self.rw = ttk.Entry(row4, width=7)
            self.rw.pack(side="left", padx=(8, 4))
            ttk.Label(row4, text="×").pack(side="left")
            self.rh = ttk.Entry(row4, width=7)
            self.rh.pack(side="left", padx=4)
            ttk.Label(row4, text="px  (width × height)").pack(side="left", padx=4)
            self.rw.bind("<KeyRelease>", lambda e: self._link("w"))
            self.rh.bind("<KeyRelease>", lambda e: self._link("h"))
            row5 = ttk.Frame(self)
            row5.pack(fill="x", padx=12, pady=(4, 0))
            ttk.Checkbutton(row5, text="Keep aspect ratio", variable=self.keep,
                            command=lambda: self._link("w")).pack(side="left")
            ttk.Label(row5, foreground="#666",
                      text="  (typed size overrides the preset; unticked = exactly that size, stretched if needed)").pack(side="left")

        self.t_start = self.t_end = None
        self.t_exact = tk.BooleanVar(value=True)
        if trim:
            self.title("Konwertexpol — cut a clip / grab a frame")
            rt = ttk.Frame(self)
            rt.pack(fill="x", padx=12, pady=(10, 0))
            ttk.Label(rt, text="Cut from").pack(side="left")
            self.t_start = ttk.Entry(rt, width=10)
            self.t_start.insert(0, "0:00")
            self.t_start.pack(side="left", padx=6)
            ttk.Label(rt, text="to").pack(side="left")
            self.t_end = ttk.Entry(rt, width=10)
            self.t_end.pack(side="left", padx=6)
            ttk.Label(rt, foreground="#666", text="e.g. 0:30 and 0:45  (seconds, m:ss or h:mm:ss; empty end = to the end)"
                      ).pack(side="left")
            self.t_gif = tk.BooleanVar(value=False)
            if "video" in cats:
                rtg = ttk.Frame(self)
                rtg.pack(fill="x", padx=12, pady=(6, 0))
                ttk.Checkbutton(rtg, text="Save the clip as a GIF (video only)", variable=self.t_gif).pack(side="left")
            rt2 = ttk.Frame(self)
            rt2.pack(fill="x", padx=12, pady=(6, 0))
            ttk.Checkbutton(rt2, text="Exact cut (frame-accurate; re-encodes the clip in high quality)",
                            variable=self.t_exact).pack(anchor="w")
            ttk.Label(rt2, foreground="#666", justify="left",
                      text="Unticked: streams are copied - no quality loss and instant, but the clip may start a few\n"
                           "seconds EARLIER than typed (at the previous keyframe). Audio files are always copied.").pack(anchor="w")
            self.t_frame = tk.StringVar(value="no")
            if "video" in cats:
                rt3 = ttk.Frame(self)
                rt3.pack(fill="x", padx=12, pady=(8, 0))
                ttk.Label(rt3, text="…or save ONE FRAME at the “from” time as:").pack(side="left")
                ttk.Combobox(rt3, textvariable=self.t_frame, values=["no", "jpg", "png"], state="readonly",
                             width=6).pack(side="left", padx=6)
                ttk.Label(rt3, foreground="#666", text="(full resolution; the “to” field is ignored)").pack(side="left")
            self.t_start.focus_set()
        btns = ttk.Frame(self)
        btns.pack(pady=8)
        self.btn = ttk.Button(btns, text="Cut clip" if trim else "Convert", command=self.start)
        self.btn.pack(side="left", padx=4)
        self.cancel_btn = ttk.Button(btns, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=4)
        self.add_btn = ttk.Button(btns, text="Add files…", command=self.add_files)
        self.add_btn.pack(side="left", padx=4)
        self.upd = ttk.Button(btns, text="Update tools", command=self.update_tools)
        self.upd.pack(side="left", padx=4)
        ttk.Button(btns, text="Diagnostics…", command=lambda: DiagWindow(self)).pack(side="left", padx=4)

        self.status = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status).pack(anchor="w", padx=12)
        self.fbar = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.fbar.pack(fill="x", padx=12, pady=(2, 4))
        ttk.Label(self, text="All files:").pack(anchor="w", padx=12)
        self.bar = ttk.Progressbar(self, mode="determinate")
        self.bar.pack(fill="x", padx=12, pady=(2, 0))

        self.log = tk.Text(self, height=7, state="disabled", wrap="word")
        self.log.tag_config("err", foreground="#c0392b")
        self.log.tag_config("ok", foreground="#1e8449")
        self.log.pack(fill="both", expand=True, padx=12, pady=(8, 2))
        lrow = ttk.Frame(self)
        lrow.pack(fill="x", padx=12, pady=(0, 8))
        ttk.Button(lrow, text="Copy log", command=self.copy_log).pack(side="left")
        ttk.Button(lrow, text="Save log…", command=self.save_log).pack(side="left", padx=6)
        if not self._embedded:
            ttk.Checkbutton(lrow, text="Close 5 s after a successful conversion", variable=self.autoclose).pack(side="right")
        self.after(100, self.poll)
        self.after(50, self._fit)

        if ocr:                              # "To PDF (searchable, OCR)" from the menu
            self.ocr_pdf.set(True)
        if to and to in fmts:
            self.fmt.set(to)
        if res and self.res is not None:
            self.res.set(res)
            self.fmt.config(state="disabled")
            self.after(300, self.start)       # "Resize to 1080p" etc. - starts without asking
        elif focus_res and self.res is not None:
            if first_ext in fmts:
                self.fmt.set(first_ext)
            self.res.focus_set()
        elif compress:
            self.comp.set(True)
            self._toggle()
            if first_ext in fmts:
                self.fmt.set(first_ext)
            self.size.focus_set()
        elif to:
            self.after(300, self.start)  # "To JPG" etc. - starts without asking

    def _wh(self):
        return (self.rw.get().strip() or self.rh.get().strip()) if self.rw is not None else ""

    def _aspect(self):
        """Aspect ratio (w/h) of the first video/image file; None when it cannot be determined."""
        if not self._ar_done:
            self._ar_done = True
            for f in self.files:
                try:
                    if category(f) == "image":
                        w, h = img_dims(f)
                    elif category(f) == "video":
                        w, h = video_wh(first_stream(probe(f), "video"))
                    else:
                        continue
                except Exception:
                    continue
                if w and h:
                    self._ar = w / h
                    break
        return self._ar

    def _link(self, src):
        """With the aspect ratio kept, editing one field recalculates the other."""
        self.res.set("Original") if self._wh() else None
        if not self.keep.get():
            return
        ar = self._aspect()
        if not ar:
            return
        a, b = (self.rw, self.rh) if src == "w" else (self.rh, self.rw)
        try:
            v = int(a.get().strip())
        except ValueError:
            return
        if v <= 0:
            return
        nv = max(1, round(v / ar if src == "w" else v * ar))
        b.delete(0, "end")
        b.insert(0, str(nv))

    def _clear_wh(self):
        self.rw.delete(0, "end")
        self.rh.delete(0, "end")

    def get_res_spec(self):
        """Custom dimensions (fields) take priority over the preset resolution. Raises ValueError."""
        if self.res is None:
            return None
        w, h = self.rw.get().strip(), self.rh.get().strip()
        if w or h:
            try:
                wi, hi = int(w or 0), int(h or 0)
            except ValueError:
                raise ValueError("Enter width and height as whole numbers (in pixels).")
            if wi < 0 or hi < 0 or not (wi or hi):
                raise ValueError("Enter a positive width and/or height.")
            # with the aspect ratio kept: fits in the box (with linked fields this is exactly that size);
            # dimensions typed by hand are applied even when they upscale
            return ("box", wi, hi, not self.keep.get(), True)
        spec = parse_res(self.res.get())
        if spec and spec[0] == "box" and spec[1] and spec[2] and not self.keep.get():
            return ("box", spec[1], spec[2], True)         # "Keep aspect ratio" off: exactly WxH, stretched if needed
        return spec

    def _toggle(self):
        st = "normal" if self.comp.get() else "disabled"
        self.size.config(state=st)
        self.unit.config(state="readonly" if self.comp.get() else "disabled")
        if self.comp.get():
            self.lossless_only.set(False)          # a size limit, "lossless only" and OCR exclude each other
            self.ocr_pdf.set(False)

    def _ask_audio(self, name, tracks):
        """Called from the worker thread: asks (in the window) which audio tracks to keep. Returns the list of
        stream indexes to keep, or None = keep all."""
        while not _ASK_LOCK.acquire(timeout=0.3):
            check_cancel()
        try:
            if getattr(self, "_audio_keep_all", False):
                return None
            ev, box = threading.Event(), {}
            self.q.put(("ask_audio", (name, tracks, box, ev)))
            while not ev.wait(0.3):
                check_cancel()
            return box.get("keep")
        finally:
            _ASK_LOCK.release()

    def _audio_dialog(self, name, tracks, box, ev):
        dlg = tk.Toplevel(self.winfo_toplevel())
        dlg.title("Audio tracks")
        dlg.transient(self.winfo_toplevel())
        ttk.Label(dlg, justify="left",
                  text=f"“{name}” has {len(tracks)} audio tracks.\n"
                       f"With a size limit every kept track is re-encoded and takes part of the limit.\n"
                       f"Untick the tracks you want to REMOVE:").pack(anchor="w", padx=14, pady=(12, 6))
        checks = []
        for idx, label in tracks:
            v = tk.BooleanVar(value=True)
            ttk.Checkbutton(dlg, text=label, variable=v).pack(anchor="w", padx=24)
            checks.append((idx, v))

        def finish(keep):
            box["keep"] = keep
            ev.set()
            dlg.destroy()

        def keep_all_always():
            self._audio_keep_all = True
            finish(None)

        row = ttk.Frame(dlg)
        row.pack(pady=12)
        ttk.Button(row, text="Keep selected and continue",
                   command=lambda: finish([i for i, v in checks if v.get()])).pack(side="left", padx=4)
        ttk.Button(row, text="Keep all, don’t ask again", command=keep_all_always).pack(side="left", padx=4)
        dlg.protocol("WM_DELETE_WINDOW", lambda: finish(None))        # closing the question = keep all
        try:
            dlg.grab_set()
        except tk.TclError:
            pass

    def _toggle_lossless(self):
        if self.lossless_only.get():
            self.comp.set(False)
            self.ocr_pdf.set(False)
            self._toggle()
            if "pdf" in self.fmt.cget("values"):
                self.fmt.set("pdf")

    def _toggle_ocr(self):
        if self.ocr_pdf.get():
            self.comp.set(False)
            self.lossless_only.set(False)
            self._toggle()
            if "pdf" in self.fmt.cget("values"):
                self.fmt.set("pdf")

    def write(self, msg, tag=None):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(msg + "\n")
        except OSError:
            pass

    def copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.log.get("1.0", "end"))
        self.status.set("Log copied to the clipboard.")

    def save_log(self):
        p = filedialog.asksaveasfilename(parent=self, defaultextension=".txt", filetypes=[("Text", "*.txt")],
                                         initialfile="konwertexpol_log.txt")
        if p:
            Path(p).write_text(self.log.get("1.0", "end"), encoding="utf-8")

    def _fit(self):
        """Window height = what the content needs (but never more than the screen has)."""
        self.update_idletasks()
        h = min(self.winfo_reqheight(), self.winfo_screenheight() - 110)
        self.geometry(f"{max(640, self.winfo_reqwidth())}x{h}")

    def _toggle_adv(self):
        self.adv_open = not self.adv_open
        if self.adv_open:
            self.adv.pack(fill="x", after=self.adv_btn)
            self.adv_btn.config(text="More options ▾")
        else:
            self.adv.pack_forget()
            self.adv_btn.config(text="More options ▸")
        self._fit()

    def _ac_stop(self, event=None):
        """A click or key press keeps the window open (cancels the auto-close countdown)."""
        if self._ac_n is not None:
            self._ac_n = None
            if not self.running:
                self.status.set(self.status.get().split(" — closing")[0])

    def _ac_tick(self):
        if self._ac_n is None or self.running:
            return
        if self._ac_n <= 0:
            self.destroy()
            return
        base = self.status.get().split(" — closing")[0]
        self.status.set(f"{base} — closing in {self._ac_n} s (click anywhere to keep this window)")
        self._ac_n -= 1
        self.after(1000, self._ac_tick)

    def _set_busy(self, busy):
        self.running = busy
        st = "disabled" if busy else "normal"
        self.btn.config(state=st)
        self.upd.config(state=st)
        self.add_btn.config(state=st)
        self.cancel_btn.config(state="normal" if busy else "disabled")

    def cancel(self):
        """Stops the current tool; the unfinished file and temporary folders are removed by the worker."""
        if not self.running or CANCEL.is_set():
            return
        self.cancel_btn.config(state="disabled")
        self.status.set("Cancelling…")
        self.write("⏹ Cancelling: stopping the tools and removing unfinished files…", "err")
        cancel_all()

    def on_close(self):
        if not self.running:
            self.destroy()
            return
        if not messagebox.askyesno("Konwertexpol",
                                   "Work is in progress. Cancel it and close the window?\n\n"
                                   "The unfinished file and temporary folders will be removed.", parent=self):
            return
        self.closing = True
        self.cancel()
        self.after(20000, self._force_close)       # as a safety net in case the cleanup hangs

    def _force_close(self):
        try:
            self.destroy()
        except tk.TclError:
            pass

    def start(self):
        global VIDEO_TRUE_LOSSLESS, OCR_LANG, ASK_AUDIO, ASK_PASSWORD, USE_GPU
        if self.running:
            return
        ASK_AUDIO = self._ask_audio
        ASK_PASSWORD = self._ask_password
        self._audio_keep_all = False
        tools_manager.CONFIRM_INSTALL = self._confirm_install
        lossless_only = bool(self.lossless_only.get())
        ocr_pdf = bool(self.ocr_pdf.get())
        trim = None
        if self.trim_mode:
            try:
                st = parse_time(self.t_start.get(), "Start") or 0.0
                en = parse_time(self.t_end.get(), "End")
            except ValueError as e:
                messagebox.showerror("Error", str(e))
                return
            if en is not None and en <= st:
                messagebox.showerror("Error", "The end of the clip must be later than its start.")
                return
            fr = self.t_frame.get()
            trim = (st, en, bool(self.t_exact.get()), bool(self.t_gif.get()), fr if fr in ("jpg", "png") else None)
            if trim[4]:
                trim = (st, None, True, False, trim[4])
            lossless_only = ocr_pdf = False
        if ocr_pdf and self.fmt.get() != "pdf":
            messagebox.showerror("Error", "The OCR option produces a searchable PDF.\n"
                                          "Choose “pdf” as the target format.")
            return
        try:
            tools_manager.check_ocr_langs(self.ocr_lang.get())
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            return
        OCR_LANG = self.ocr_lang.get().strip()
        if lossless_only and self.fmt.get() != "pdf":
            messagebox.showerror("Error", "“Smallest size without quality loss” only works for PDF → PDF.\n"
                                         "Choose “pdf” as the target format.")
            return
        target = None
        if self.comp.get():
            try:
                v = float(self.size.get().replace(",", "."))
                if v <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Error", "Enter a valid size (a number greater than zero).")
                return
            target = int(v * (1024 if self.unit.get() == "KB" else 1024 * 1024))
        spec = None
        if self.res is not None:
            try:
                spec = self.get_res_spec()
            except ValueError as e:
                messagebox.showerror("Error", str(e))
                return
        d = self.fmt.get()
        VIDEO_TRUE_LOSSLESS = bool(self.true_lossless.get())
        USE_GPU = bool(self.use_gpu.get())
        CANCEL.clear()
        self.active, self.done_n, self.batch_t0, self.failed = {}, 0, time.time(), []
        if self.retry_btn is not None:
            self.retry_btn.destroy()
            self.retry_btn = None
        self._set_busy(True)
        self.bar.config(maximum=len(self.files), value=0)
        threading.Thread(target=self.worker, args=(d, target, spec, lossless_only, ocr_pdf, trim), daemon=True).start()

    def _tool_cb(self, msg, frac=None):
        self.q.put(("tool", (msg, frac)))

    def _report_tools(self, msgs, errs):
        for m in msgs:
            self.q.put(("log", (f"🔧 {m}", None)))
        for e in errs:
            self.q.put(("log", (f"⚠ {e}", "err")))

    def update_tools(self):
        if self.running:
            return
        CANCEL.clear()
        tools_manager.CONFIRM_INSTALL = self._confirm_install
        self._set_busy(True)

        def job():
            msgs, errs = tools_manager.ensure(tools_manager.all_names(), self._tool_cb, force=True)
            if CANCEL.is_set():
                msgs, errs = ["Update cancelled (tools that were already in the tools folder keep working as before)."], []
            elif not msgs and not errs:
                msgs = ["All tools are up to date."]
            self._report_tools(msgs, errs)
            self.q.put(("idle", None))
        threading.Thread(target=job, daemon=True).start()

    def target_ext(self, f, d):
        """Output format for a file (in 'Resize to' / trim mode - its own)."""
        if self.trim_mode:
            return ext_of(f)
        if not self.same:
            return d
        e = ext_of(f)
        c = category(f)
        return e if c and e in TARGETS[c] else SAME_FALLBACK.get(c, e)

    def _is_heavy(self, f, dd, target, lossless_only, ocr_pdf):
        """Jobs that already use all processor cores (or a lot of memory) run one at a time; the light ones
        (images, text, audio, plain PDF conversions) run side by side."""
        c = category(f)
        if c == "video":
            return True
        if c == "pdf" and (target or lossless_only or ocr_pdf or dd in ("docx", "md", "html", "txt")):
            return True
        return bool(ocr_pdf and c == "image" and dd == "pdf")

    def worker(self, d, target, spec, lossless_only=False, ocr_pdf=False, trim=None):
        global PROGRESS, STEP, DETAIL
        n = len(self.files)
        cpu = os.cpu_count() or 2
        dds = [(trim[4] if len(trim) > 4 and trim[4] and category(f) == "video" else
                "gif" if len(trim) > 3 and trim[3] and category(f) == "video" else ext_of(f)) if trim
               else self.target_ext(f, d) for f in self.files]
        heavy = [bool(trim and category(f) == "video") or (not trim and self._is_heavy(f, dd, target, lossless_only, ocr_pdf))
                 for f, dd in zip(self.files, dds)]
        par = 1 if n == 1 else max(2, min(4, cpu // 2))
        multi = par > 1 and heavy.count(False) > 1       # several light jobs may run at the same moment
        pre = (lambda i: f"[{i}] ") if multi else (lambda i: "")

        def idx():
            return getattr(_CTX, "idx", 0)

        def progress(frac):
            check_cancel()
            self.q.put(("frac", (idx(), frac)))

        def detail(msg):
            check_cancel()
            self.q.put(("detail", (idx(), msg)))

        PROGRESS = progress
        STEP = lambda msg: self.q.put(("log", (f"   • {pre(idx())}{msg}", None)))
        DETAIL = detail
        try:
            # 1. tools: download the missing ones into tools\\ (existing ones stay, updating only on request)
            names = []
            for f, dd in zip(self.files, dds):
                for t in needed_tools(category(f), ext_of(f), dd):
                    if t not in names:
                        names.append(t)
            if not target:          # without a size limit Ghostscript is not needed (OCR asks for it itself)
                names = [t for t in names if t != "gs"]
            if names:
                msgs, errs = tools_manager.ensure(names, self._tool_cb)
                self._report_tools(msgs, errs)
            check_cancel()
        except Cancelled:
            self.q.put(("cancelled", (0, [])))
            return
        # 2. conversion (up to `par` files at the same time)
        heavy_lock = threading.Lock()
        key_locks, key_guard = {}, threading.Lock()
        results, res_lock = {}, threading.Lock()

        def key_lock(f):                 # the same name in one folder (a.png + a.jpg -> a.webp) is done one after another
            k = (str(f.parent).lower(), f.stem.lower())
            with key_guard:
                return key_locks.setdefault(k, threading.Lock())

        def acquire(lock):
            while not lock.acquire(timeout=0.3):
                check_cancel()

        def run_one(i):
            f, dd, hv = self.files[i - 1], dds[i - 1], heavy[i - 1]
            _CTX.idx = i
            kl = key_lock(f)
            got_heavy = False
            try:
                check_cancel()
                acquire(kl)
                try:
                    if hv:
                        acquire(heavy_lock)
                        got_heavy = True
                    check_cancel()
                    self.q.put(("start", (i, f.name)))
                    what = (f"-> cut {fmt_time(trim[0])} - {fmt_time(trim[1]) if trim[1] else 'end'}" if trim else
                            f"→ .{dd}" + (f" ({res_label(spec)})" if spec else "") +
                            (" (smallest without quality loss)" if lossless_only and dd == "pdf" and ext_of(f) == "pdf" else "") +
                            (" (OCR)" if ocr_pdf and dd == "pdf" and category(f) in ("pdf", "image") else ""))
                    self.q.put(("log", (f"[{i}/{n}] {f.name} {what} ...", None)))
                    try:
                        dst, note = convert_one(f, dd, target, spec, lossless_only, ocr_pdf, trim)
                        if dst.is_dir():
                            files_in = [x for x in dst.iterdir() if x.is_file()]
                            size = sum(x.stat().st_size for x in files_in) / 1024
                            label = f"{dst.name}\\ — {len(files_in)} files"
                        else:
                            size = dst.stat().st_size / 1024
                            label = dst.name
                        txt = (f"   ✔ {pre(i)}{label} ({size / 1024:.2f} MB)" if size > 1024
                               else f"   ✔ {pre(i)}{label} ({size:.0f} KB)")
                        if note:
                            txt += f"  [{note}]"
                        self.q.put(("log", (txt, "ok")))
                        self.last = dst
                        out = (True, "")
                    except Exception as e:
                        msg = str(e).strip() or e.__class__.__name__
                        self.q.put(("log", (f"   ✘ {pre(i)}{f.name if multi else ''}{': ' if multi else ''}ERROR: {msg}", "err")))
                        out = (False, msg)
                finally:
                    if got_heavy:
                        heavy_lock.release()
                    kl.release()
            except Cancelled:
                out = None
            with res_lock:
                results[i] = out
            self.q.put(("prog", i))

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=par) as pool:
            for fut in [pool.submit(run_one, i) for i in range(1, n + 1)]:
                try:
                    fut.result()
                except BaseException:
                    pass
        ok = sum(1 for r in results.values() if r and r[0])
        failed = [(self.files[i - 1], r[1]) for i, r in sorted(results.items()) if r and not r[0]]
        if CANCEL.is_set():
            self.q.put(("cancelled", (ok, failed)))
            return
        self.q.put(("done", (ok, failed)))

    def _update_bars(self):
        """Per-file bar = average of the running files; overall bar = finished files + the running ones' progress."""
        fr = [a["frac"] for a in self.active.values()]
        known = [x for x in fr if x is not None]
        if fr and not known:
            self._fbar_mode(True)
        else:
            self._fbar_mode(False)
            if fr:
                self.fbar.config(value=100 * sum(x or 0 for x in fr) / len(fr))
        self.bar.config(value=self.done_n + sum(x or 0 for x in fr))

    def _status_text(self):
        now = time.time()
        act = sorted(self.active.items())
        n = len(self.files)
        if len(act) == 1:
            i, a = act[0]
            el = int(now - a["t0"])
            txt = f"[{i}/{n}] {a['name']} — "
            if a["detail"]:
                txt += f"{a['detail']} — "
            if a["frac"] is not None:
                txt += f"{int(a['frac'] * 100)}% — "
            txt += f"{el // 60:02d}:{el % 60:02d}"
            if a["frac"] and a["frac"] > 0.03 and n == 1:
                eta = int(el / a["frac"] - el)
                txt += f" (~{eta // 60:02d}:{eta % 60:02d} left)"
            elif a["frac"] is None:
                txt += " (working...)"
            return txt
        el = int(now - self.batch_t0)
        prog = self.done_n + sum(a["frac"] or 0 for _, a in act)
        txt = f"{self.done_n}/{n} done — {len(act)} at once: " + ", ".join(a["name"] for _, a in act[:3])
        if len(act) > 3:
            txt += f" +{len(act) - 3}"
        txt += f" — {el // 60:02d}:{el % 60:02d}"
        if prog > 0.5 and n > 1:
            eta = int(el / prog * (n - prog))
            txt += f" (~{eta // 60:02d}:{eta % 60:02d} left)"
        return txt

    def add_files(self):
        """Adds more files to this window (only kinds the window is set up for)."""
        if self.running:
            return
        paths = filedialog.askopenfilenames(parent=self, title="Add files", initialdir=str(self.files[0].parent)
                                            if self.files else None)
        have = {str(f).lower() for f in self.files}
        added = skipped = 0
        for x in paths:
            f = Path(x)
            c = category(f)
            if str(f).lower() in have:
                continue
            if c is None or c not in self.cats or (self.trim_mode and c not in ("video", "audio")):
                skipped += 1
                continue
            self.files.append(f)
            self.lb.insert("end", f.name)
            have.add(str(f).lower())
            added += 1
        self.files_lbl.config(text=f"Selected files ({len(self.files)}):")
        if added or skipped:
            self.write(f"Added {added} file(s)" + (f"; {skipped} skipped (a different kind of file than this window handles)"
                                                  if skipped else ""), None)

    def retry(self):
        """Runs only the files that failed last time, with the same settings."""
        if self.running or not self.failed:
            return
        self.files = [f for f, _ in self.failed]
        self.lb.delete(0, "end")
        for f in self.files:
            self.lb.insert("end", f.name)
        self.files_lbl.config(text=f"Selected files ({len(self.files)}):")
        if self.retry_btn is not None:
            self.retry_btn.destroy()
            self.retry_btn = None
        self.write(f"\n↻ Retrying {len(self.files)} failed file(s)...", None)
        self.start()

    def _fbar_mode(self, indeterminate):
        if indeterminate and str(self.fbar["mode"]) != "indeterminate":
            self.fbar.config(mode="indeterminate")
            self.fbar.start(12)
        elif not indeterminate and str(self.fbar["mode"]) == "indeterminate":
            self.fbar.stop()
            self.fbar.config(mode="determinate", value=0)

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.write(*val)
                elif kind == "tool":
                    msg, frac = val
                    self.status.set(msg)
                    if frac is None:
                        self._fbar_mode(True)
                    else:
                        self._fbar_mode(False)
                        self.fbar.config(value=frac * 100)
                elif kind == "idle":
                    self._fbar_mode(False)
                    self._set_busy(False)
                    self.status.set("Ready.")
                elif kind == "detail":
                    i, msg = val
                    if i in self.active:
                        self.active[i]["detail"] = msg
                elif kind == "start":
                    i, name = val
                    self.active[i] = {"name": name, "t0": time.time(), "frac": None, "detail": ""}
                    self._update_bars()
                elif kind == "frac":
                    i, fr = val
                    if i in self.active:
                        self.active[i]["frac"] = fr
                    self._update_bars()
                elif kind == "prog":
                    self.active.pop(val, None)
                    self.done_n += 1
                    self._update_bars()
                elif kind == "ask_audio":
                    self._audio_dialog(*val)
                elif kind == "ask_tool":
                    self._tool_dialog(*val)
                elif kind == "ask_password":
                    self._password_dialog(*val)
                elif kind == "cancelled":
                    ok, failed = val
                    self.active.clear()
                    self._fbar_mode(False)
                    self.fbar.config(value=0)
                    self._set_busy(False)
                    self.status.set(f"Cancelled. Finished files: {ok}/{len(self.files)}.")
                    self.write(f"\n⏹ Cancelled. The unfinished files and temporary folders were removed "
                               f"(finished earlier: {ok}/{len(self.files)}).", "err")
                    self.btn.config(text="Convert again")
                elif kind == "done":
                    ok, failed = val
                    self.active.clear()
                    self._fbar_mode(False)
                    self._set_busy(False)
                    self.status.set(f"Done: {ok}/{len(self.files)} files" + (f" — {len(failed)} failed." if failed else "."))
                    self.write(f"\nDone: {ok}/{len(self.files)} files.", "ok" if not failed else "err")
                    self.failed = failed
                    if failed:
                        self.write(f"\nFailed files ({len(failed)}):", "err")
                        for f, why in failed:
                            self.write(f"  ✘ {f.name} — {why.splitlines()[0][:200] if why else 'unknown error'}", "err")
                        if self.retry_btn is None:
                            self.retry_btn = ttk.Button(self, text=f"Retry failed ({len(failed)})", command=self.retry)
                            self.retry_btn.pack(pady=(0, 6))
                    self.btn.config(text="Convert again")
                    if self.autoclose.get() and ok == len(self.files) and not self.closing:
                        self._ac_n = 5                         # close the window 5 s after a fully successful run
                        self.after(1000, self._ac_tick)
                    if ok and hasattr(self, "last") and self.show_btn is None:
                        self.show_btn = ttk.Button(
                            self, text="Show in folder",
                            command=lambda: subprocess.Popen(["explorer", "/select,", str(self.last)]))
                        self.show_btn.pack(pady=(0, 8))
        except queue.Empty:
            pass
        if self.closing and not self.running:
            self.destroy()
            return
        if self.running and self.active:
            self.status.set(self._status_text())
        self.after(300, self.poll)


class _MergeBody(DialogsMixin):
    """PDF merge window (also: several images -> ONE PDF): file order, result name, optional OCR, one click."""

    def __init__(self, files, kind="pdf", master=None):
        self._init_window(master)
        add_logo(self)
        self.kind = kind
        self.files = list(files)
        self.q = queue.Queue()
        self.running = False
        self.closing = False
        self._tool_skipped = set()
        self.last = None
        self.out_ext = ext_of(self.files[0]) if kind == "video" and self.files else "pdf"
        self.title("Konwertexpol — " + {"images": "images → one PDF", "video": "join videos"}.get(kind, "merge PDFs"))
        self.geometry("620x520")
        self.minsize(520, 420)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        ttk.Label(self, text={"images": "Images to combine (order from top to bottom = page order):",
                              "video": "Videos to join (order from top to bottom = playing order):"}.get(
                                  kind, "Files to merge (order from top to bottom = page order):")
                  ).pack(anchor="w", padx=12, pady=(10, 4))
        fr = ttk.Frame(self)
        fr.pack(fill="both", padx=12)
        self.lb = tk.Listbox(fr, height=9, selectmode="browse", activestyle="none")
        sb = ttk.Scrollbar(fr, command=self.lb.yview)
        self.lb.config(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        side = ttk.Frame(fr)
        side.pack(side="left", padx=(8, 0), anchor="n")
        for txt, cmd in (("▲ Up", lambda: self._move(-1)), ("▼ Down", lambda: self._move(1)),
                         ("Sort A–Z", self._sort), ("Remove from list", self._remove)):
            ttk.Button(side, text=txt, command=cmd, width=13).pack(pady=2)
        self.info = tk.StringVar()
        ttk.Label(self, textvariable=self.info, foreground="#666").pack(anchor="w", padx=12, pady=(4, 0))
        row = ttk.Frame(self)
        row.pack(fill="x", padx=12, pady=8)
        ttk.Label(row, text="Output name:").pack(side="left")
        self.name = ttk.Entry(row)
        self.name.pack(side="left", fill="x", expand=True, padx=8)
        self.ocr = tk.BooleanVar(value=False)
        self.ocr_lang = tk.StringVar(value=tools_manager.default_ocr_lang())
        orow = ttk.Frame(self)
        if kind != "video":
            orow.pack(fill="x", padx=12)
        ttk.Checkbutton(orow, text="Make the result searchable (OCR) — language(s):", variable=self.ocr).pack(side="left")
        ttk.Combobox(orow, textvariable=self.ocr_lang, values=OCR_PRESETS, width=12).pack(side="left", padx=6)
        btns = ttk.Frame(self)
        btns.pack(pady=4)
        self.btn = ttk.Button(btns, text={"images": "Combine into PDF", "video": "Join videos"}.get(kind, "Merge PDFs"),
                              command=self.start)
        self.btn.pack(side="left", padx=4)
        self.cancel_btn = ttk.Button(btns, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=4)
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status).pack(anchor="w", padx=12)
        self.bar = ttk.Progressbar(self, mode="indeterminate")
        self.bar.pack(fill="x", padx=12, pady=(2, 4))
        self.log = tk.Text(self, height=7, state="disabled", wrap="word")
        self.log.tag_config("err", foreground="#c0392b")
        self.log.tag_config("ok", foreground="#1e8449")
        self.log.pack(fill="both", expand=True, padx=12, pady=8)
        self._refresh()
        self.name.insert(0, self._default_name())
        self.after(100, self.poll)

    def _default_name(self):
        tag = {"images": "combined", "video": "joined"}.get(self.kind, "merged")
        return f"{self.files[0].stem}_{tag}.{self.out_ext}" if self.files else f"{tag}.{self.out_ext}"

    def _refresh(self, sel=None):
        self.lb.delete(0, "end")
        for f in self.files:
            self.lb.insert("end", f.name)
        if sel is not None and self.files:
            sel = max(0, min(sel, len(self.files) - 1))
            self.lb.selection_set(sel)
            self.lb.activate(sel)
        total = sum(f.stat().st_size for f in self.files if f.exists())
        self.info.set(f"{len(self.files)} files, total {mb(total)}  •  the result goes to: {self.files[0].parent if self.files else ''}")

    def _sel(self):
        s = self.lb.curselection()
        return s[0] if s else None

    def _move(self, d):
        i = self._sel()
        if i is None or not 0 <= i + d < len(self.files):
            return
        self.files[i], self.files[i + d] = self.files[i + d], self.files[i]
        self._refresh(i + d)

    def _sort(self):
        self.files.sort(key=lambda f: f.name.lower())
        self._refresh(0)

    def _remove(self):
        i = self._sel()
        if i is None:
            return
        del self.files[i]
        self._refresh(i)

    def write(self, msg, tag=None):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def cancel(self):
        if not self.running or CANCEL.is_set():
            return
        self.cancel_btn.config(state="disabled")
        self.status.set("Cancelling…")
        self.write("⏹ Cancelling: stopping the merge and removing the unfinished file…", "err")
        cancel_all()

    def on_close(self):
        if not self.running:
            self.destroy()
            return
        if not messagebox.askyesno("Konwertexpol",
                                   "Merging is in progress. Cancel it and close the window?\n\n"
                                   "The unfinished file will be removed.", parent=self):
            return
        self.closing = True
        self.cancel()
        self.after(20000, self._force_close)

    def _force_close(self):
        try:
            self.destroy()
        except tk.TclError:
            pass

    def start(self):
        if self.running:
            return
        if len(self.files) < 2:
            messagebox.showerror("Error", "The list must contain at least two files.")
            return
        name = self.name.get().strip() or self._default_name()
        if not name.lower().endswith("." + self.out_ext):
            name += "." + self.out_ext
        if any(c in name for c in '\\/:*?"<>|'):
            messagebox.showerror("Error", "The file name contains illegal characters.")
            return
        dst = self.files[0].parent / name
        if dst.exists() and not messagebox.askyesno("Konwertexpol", f"The file {name} already exists. Replace it?"):
            return
        global OCR_LANG, ASK_PASSWORD
        if self.kind != "video":
            try:
                tools_manager.check_ocr_langs(self.ocr_lang.get())
            except ValueError as e:
                messagebox.showerror("Error", str(e))
                return
            OCR_LANG = self.ocr_lang.get().strip()
        ASK_PASSWORD = self._ask_password
        CANCEL.clear()
        tools_manager.CONFIRM_INSTALL = self._confirm_install
        self.running = True
        self.btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.bar.start(12)
        threading.Thread(target=self.worker_video if self.kind == "video" else self.worker,
                         args=(list(self.files), dst) if self.kind == "video"
                         else (list(self.files), dst, bool(self.ocr.get())), daemon=True).start()

    def worker_video(self, files, dst):
        global STEP, DETAIL
        STEP = lambda msg: self.q.put(("log", (f"   • {msg}", None)))
        DETAIL = lambda msg: (check_cancel(), self.q.put(("status", msg)))[1]
        try:
            self.q.put(("status", "Checking tools..."))
            msgs, errs = tools_manager.ensure(["ffmpeg", "ffprobe"], lambda m, f=None: self.q.put(("status", m)))
            for m in msgs:
                self.q.put(("log", (f"🔧 {m}", None)))
            for e in errs:
                self.q.put(("log", (f"⚠ {e.replace(tools_manager.NOT_INSTALLED, '')}", "err")))
            self.q.put(("status", "Joining..."))
            note = join_videos(files, dst)
            self.last = dst
            self.q.put(("log", (f"   ✔ {dst.name} ({mb(dst.stat().st_size)})" + (f"  [{note}]" if note else ""), "ok")))
            self.q.put(("done", True))
        except Cancelled:
            self.q.put(("cancelled", None))
        except Exception as e:
            self.q.put(("log", (f"   ✘ ERROR: {e}", "err")))
            self.q.put(("done", False))

    def worker(self, files, dst, ocr):
        global STEP, DETAIL
        STEP = lambda msg: self.q.put(("log", (f"   • {msg}", None)))
        DETAIL = lambda msg: self.q.put(("status", msg))
        tmpdirs = [Path(tempfile.mkdtemp())]
        work = tmpdirs[0]
        try:
            self.q.put(("status", "Checking tools..."))
            names = ["pdfunite", "pdfinfo", "pdftotext"]
            if self.kind == "images" and any(ext_of(f) != "jpg" for f in files):
                names.append("magick")
            msgs, errs = tools_manager.ensure(names, lambda m, f=None: self.q.put(("status", m)))
            for m in msgs:
                self.q.put(("log", (f"🔧 {m}", None)))
            for e in errs:
                self.q.put(("log", (f"⚠ {e.replace(tools_manager.NOT_INSTALLED, '')}", "err")))
            pdfs = []
            for i, f in enumerate(files, 1):
                check_cancel()
                self.q.put(("status", f"Preparing {i}/{len(files)}: {f.name}"))
                if self.kind == "images":
                    p = work / f"{i:05d}.pdf"
                    conv_image(str(f), str(p), None, None)       # a JPEG is embedded unchanged, other formats losslessly
                    pdfs.append(p)
                else:
                    p, t = _unlock_pdf(f)                       # asks for the password of protected files
                    if t:
                        tmpdirs.append(t)
                    pdfs.append(p)
            self.q.put(("status", "Merging..."))
            tmp = dst.with_name(dst.name + ".part")
            try:
                merged = work / "merged.pdf" if ocr else tmp
                _merge_many(pdfs, merged, work)
                if ocr:
                    conv_pdf_ocr(str(merged), str(tmp))
                os.replace(tmp, dst)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
            self.last = dst
            self.q.put(("log", (f"   ✔ {dst.name} ({mb(dst.stat().st_size)})", "ok")))
            self.q.put(("done", True))
        except Cancelled:
            self.q.put(("cancelled", None))
        except Exception as e:
            self.q.put(("log", (f"   ✘ ERROR: {e}", "err")))
            self.q.put(("done", False))
        finally:
            for t in tmpdirs:
                shutil.rmtree(t, ignore_errors=True)

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.write(*val)
                elif kind == "status":
                    self.status.set(val)
                elif kind == "ask_tool":
                    self._tool_dialog(*val)
                elif kind == "ask_password":
                    self._password_dialog(*val)
                elif kind == "cancelled":
                    self.running = False
                    self.bar.stop()
                    self.btn.config(state="normal")
                    self.cancel_btn.config(state="disabled")
                    self.status.set("Cancelled.")
                    self.write("⏹ Cancelled. The unfinished file was removed.", "err")
                elif kind == "done":
                    self.running = False
                    self.bar.stop()
                    self.btn.config(state="normal", text="Run again")
                    self.cancel_btn.config(state="disabled")
                    self.status.set("Done." if val else "Merging failed.")
                    if val and self.last and getattr(self, "show_btn", None) is None:
                        self.show_btn = ttk.Button(
                            self, text="Show in folder",
                            command=lambda: subprocess.Popen(["explorer", "/select,", str(self.last)]))
                        self.show_btn.pack(pady=(0, 8))
        except queue.Empty:
            pass
        if self.closing and not self.running:
            self.destroy()
            return
        self.after(300, self.poll)


# ----------------------------------------------------------------------------
# Extracting audio tracks and subtitles from videos (streams are copied - no quality loss)
# ----------------------------------------------------------------------------
AUDIO_EXT = {"aac": "m4a", "mp3": "mp3", "ac3": "ac3", "eac3": "eac3", "opus": "opus", "vorbis": "ogg",
             "flac": "flac", "dts": "dts", "alac": "m4a"}
BITMAP_SUBS = {"hdmv_pgs_subtitle": "sup", "dvd_subtitle": "mks", "dvb_subtitle": "mks", "xsub": "mks"}


def list_tracks(path):
    """[(stream_index, kind, codec, language, title)] for the audio and subtitle tracks of a file."""
    out = []
    for x in probe(str(path)).get("streams", []):
        if x.get("codec_type") in ("audio", "subtitle"):
            t = x.get("tags") or {}
            out.append((x["index"], x["codec_type"], x.get("codec_name") or "?",
                        t.get("language") or t.get("LANGUAGE") or "", t.get("title") or t.get("TITLE") or ""))
    return out


def extract_track(src, track, n):
    """Saves one audio / subtitle track next to the video; n = running number of tracks of that kind.
    Returns (path, note)."""
    idx, kind, codec, lang, _ = track
    src = Path(src)
    note = ""
    if kind == "audio":
        ext = AUDIO_EXT.get(codec, "wav" if (codec or "").startswith("pcm_") else "mka")
        args = ["-c", "copy"]
    elif codec in BITMAP_SUBS:
        ext, args = BITMAP_SUBS[codec], ["-c", "copy"]
        note = "picture-based subtitles cannot be turned into text"
    elif codec in ("ass", "ssa"):
        ext, args = "ass", ["-c", "copy"]
    else:
        ext, args = "srt", ["-c:s", "srt"]
    base = f"{src.stem}.{'audio' if kind == 'audio' else 'sub'}{n}" + (f".{lang}" if lang else "")
    dst = src.parent / f"{base}.{ext}"
    k = 1
    while dst.exists():
        dst = src.parent / f"{base} ({k}).{ext}"
        k += 1
    try:
        run_ff([tool("ffmpeg"), "-y", "-i", str(src), "-map", f"0:{idx}", *args, str(dst)], probe_duration(probe(str(src))))
    except BaseException:
        if dst.exists():
            try:
                dst.unlink()
            except OSError:
                pass
        raise
    return dst, note


def add_subtitles(src, sub, lang, burn):
    """Adds a subtitle file (.srt/.ass/.vtt) to a video. burn=False: a selectable subtitle track (picture and sound
    are copied, no loss); burn=True: the text is drawn into the picture (re-encoded in high quality).
    Returns (result path, note)."""
    src, sub = Path(src), Path(sub)
    ff = tool("ffmpeg")
    info = probe(str(src))
    dur = probe_duration(info)
    d = ext_of(src)
    lang = (lang or "und").strip().lower()[:3] or "und"
    if burn:
        enc = VIDEO_ENC.get(d)
        if not enc:
            raise RuntimeError(f".{d} cannot be written with re-encoding; use .mp4 / .mkv for burned-in subtitles.")
        dst = out_path(src.with_name(src.stem + "_subtitled" + src.suffix), d)
        esc = sub.resolve().as_posix().replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        lv, _ = V_GOOD[d]
        maps, aargs, sargs, notes = _track_args(d, info, enc)
        keep = ["-map", "0:v:0", "-map", "0:a?"]              # picture + all audio tracks
        step("Drawing the subtitles into the picture (re-encoding in high quality)")
        try:
            run_ff([ff, "-y", "-i", str(src), *keep, "-vf", f"subtitles='{esc}'", *lv, *aargs, *enc["extra"], str(dst)], dur)
        except BaseException:
            if dst.exists():
                try:
                    dst.unlink()
                except OSError:
                    pass
            raise
        return dst, "subtitles are part of the picture now (they cannot be switched off)"
    kind = SUB_CONTAINER.get(d)
    note = ""
    out_ext = d
    if kind is None:                           # avi / wmv / flv / mpg cannot hold text subtitles
        out_ext, kind = "mkv", "copy"
        note = f".{d} cannot hold subtitles - saved as .mkv (streams copied)"
    codec = {"copy": "srt", "mov_text": "mov_text", "webvtt": "webvtt"}[kind]
    if out_ext == "mkv" and sub.suffix.lower() in (".ass", ".ssa"):
        codec = "copy"
    n_old = sum(1 for x in info.get("streams", []) if x.get("codec_type") == "subtitle")
    dst = out_path(src.with_name(src.stem + "_subtitled" + src.suffix), out_ext)
    base = [ff, "-y", "-i", str(src), "-i", str(sub), "-map", "0:v", "-map", "0:a?"]
    tail = ["-map", "1:0", "-c", "copy", "-c:s", codec, f"-metadata:s:s:{n_old}", f"language={lang}", str(dst)]
    tail_new = ["-map", "1:0", "-c", "copy", "-c:s", codec, "-metadata:s:s:0", f"language={lang}", str(dst)]
    try:
        try:
            run_ff(base + ["-map", "0:s?"] + tail, dur)
        except RuntimeError:                   # old subtitles that this container cannot keep: replace them
            if dst.exists():
                dst.unlink()
            run_ff(base + tail_new, dur)
            note = (note + "; " if note else "") + "the video's earlier subtitle tracks could not be kept in this format"
    except BaseException:
        if dst.exists():
            try:
                dst.unlink()
            except OSError:
                pass
        raise
    return dst, (note + "; " if note else "") + f"new subtitle track ({lang}) added; picture and sound untouched"


class _ExtractBody(DialogsMixin):
    """Lists the audio tracks and subtitles of the selected videos; the chosen ones are saved as separate files."""

    def __init__(self, files, master=None):
        self._init_window(master)
        add_logo(self)
        self.files = list(files)
        self.q = queue.Queue()
        self._tool_skipped = set()
        self.running, self.closing = True, False
        self.rows = []                                  # (file, track)
        self.last = None
        self.title("Konwertexpol — subtitles & audio tracks")
        self.geometry("760x540")
        self.minsize(560, 380)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.status = tk.StringVar(value="Reading the tracks...")
        ttk.Label(self, textvariable=self.status).pack(anchor="w", padx=12, pady=(10, 2))
        fr = ttk.Frame(self)
        fr.pack(fill="both", expand=True, padx=12, pady=4)
        self.lb = tk.Listbox(fr, selectmode="extended", activestyle="none", exportselection=False)
        sb = ttk.Scrollbar(fr, command=self.lb.yview)
        self.lb.config(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        ttk.Label(self, foreground="#666", justify="left",
                  text="Select the tracks to save (Ctrl/Shift for several). Audio keeps its codec (no re-encoding); text subtitles\n"
                       "become .srt (styled ASS stays .ass). Files are saved next to the video.").pack(anchor="w", padx=12)
        btns = ttk.Frame(self)
        btns.pack(pady=6)
        self.btn = ttk.Button(btns, text="Extract selected", command=self.start, state="disabled")
        self.btn.pack(side="left", padx=4)
        ttk.Button(btns, text="Select all", command=lambda: self.lb.selection_set(0, "end")).pack(side="left", padx=4)
        ttk.Button(btns, text="Subtitles only", command=lambda: self._select("subtitle")).pack(side="left", padx=4)
        ttk.Button(btns, text="Audio only", command=lambda: self._select("audio")).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.cancel).pack(side="left", padx=4)
        sep = ttk.Separator(self)
        sep.pack(fill="x", padx=12, pady=(2, 6))
        ar = ttk.Frame(self)
        ar.pack(fill="x", padx=12)
        ttk.Label(ar, text="ADD subtitles to the video:").pack(side="left")
        self.sub_path = tk.StringVar()
        self.sub_lang = tk.StringVar(value="und")
        self.sub_burn = tk.BooleanVar(value=False)
        ttk.Button(ar, text="Choose .srt / .ass / .vtt…", command=self.pick_sub).pack(side="left", padx=6)
        ttk.Label(ar, text="language:").pack(side="left")
        ttk.Entry(ar, textvariable=self.sub_lang, width=5).pack(side="left", padx=4)
        ttk.Label(ar, textvariable=self.sub_path, foreground="#666").pack(side="left", padx=6)
        ar2 = ttk.Frame(self)
        ar2.pack(fill="x", padx=12, pady=(4, 0))
        self.add_btn = ttk.Button(ar2, text="Add to the video", command=self.start_add, state="disabled")
        self.add_btn.pack(side="left")
        ttk.Checkbutton(ar2, text="Burn into the picture (re-encodes; cannot be switched off)",
                        variable=self.sub_burn).pack(side="left", padx=10)
        if len(self.files) != 1:
            ttk.Label(self, foreground="#c0392b", text="Adding subtitles works on ONE video - select a single file."
                      ).pack(anchor="w", padx=12)
        self.log = tk.Text(self, height=6, state="disabled", wrap="word")
        self.log.tag_config("err", foreground="#c0392b")
        self.log.tag_config("ok", foreground="#1e8449")
        self.log.pack(fill="both", padx=12, pady=(6, 10))
        threading.Thread(target=self.scan, daemon=True).start()
        self.after(100, self.poll)

    def pick_sub(self):
        p = filedialog.askopenfilename(parent=self, title="Subtitle file", initialdir=str(self.files[0].parent),
                                       filetypes=[("Subtitles", "*.srt *.ass *.ssa *.vtt *.sub"), ("All files", "*.*")])
        if not p:
            return
        self.sub_path.set(p)
        m = re.search(r"[._-]([a-z]{2,3})\.[^.]+$", Path(p).name.lower())
        if m and self.sub_lang.get() in ("", "und"):
            self.sub_lang.set(m.group(1))
        if len(self.files) == 1 and not self.running:
            self.add_btn.config(state="normal")

    def start_add(self):
        if self.running or len(self.files) != 1 or not self.sub_path.get():
            return
        global STEP, DETAIL
        STEP = lambda msg: None
        DETAIL = lambda msg: (check_cancel(), self.q.put(("status", msg)))[1]
        CANCEL.clear()
        self.running = True
        self.add_btn.config(state="disabled")
        self.btn.config(state="disabled")
        args = (self.files[0], Path(self.sub_path.get()), self.sub_lang.get(), bool(self.sub_burn.get()))
        threading.Thread(target=self.add_work, args=args, daemon=True).start()

    def add_work(self, src, sub, lang, burn):
        try:
            self.q.put(("status", "Adding the subtitles..."))
            dst, note = add_subtitles(src, sub, lang, burn)
            self.last = dst
            self.q.put(("log", (f"✔ {dst.name} ({mb(dst.stat().st_size)})  [{note}]", "ok")))
            self.q.put(("idle", "Done."))
        except Cancelled:
            self.q.put(("idle", "Cancelled."))
        except Exception as e:
            self.q.put(("log", (f"✘ {str(e).strip()[-300:]}", "err")))
            self.q.put(("idle", "Failed."))

    def _select(self, kind):
        self.lb.selection_clear(0, "end")
        for i, (_, t) in enumerate(self.rows):
            if t[1] == kind:
                self.lb.selection_set(i)

    def write(self, msg, tag=None):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def scan(self):
        try:
            msgs, errs = tools_manager.ensure(["ffmpeg", "ffprobe"], lambda m, f=None: self.q.put(("status", m)))
            rows = []
            for f in self.files:
                check_cancel()
                try:
                    for t in list_tracks(f):
                        rows.append((f, t))
                except Exception as e:
                    self.q.put(("log", (f"✘ {f.name}: {str(e).strip()[:200]}", "err")))
            self.q.put(("rows", rows))
        except Cancelled:
            self.q.put(("idle", "Cancelled."))

    def start(self):
        sel = list(self.lb.curselection())
        if self.running or not sel:
            return
        global STEP, DETAIL
        STEP = lambda msg: None
        DETAIL = lambda msg: (check_cancel(), self.q.put(("status", msg)))[1]
        CANCEL.clear()
        self.running = True
        self.btn.config(state="disabled")
        threading.Thread(target=self.work, args=([self.rows[i] for i in sel],), daemon=True).start()

    def work(self, chosen):
        counts, ok = {}, 0
        try:
            for f, t in chosen:
                check_cancel()
                counts[(f, t[1])] = counts.get((f, t[1]), 0) + 1
                self.q.put(("status", f"{f.name}: {t[1]} #{t[0]}"))
                try:
                    dst, note = extract_track(f, t, counts[(f, t[1])])
                    self.q.put(("log", (f"✔ {dst.name} ({mb(dst.stat().st_size)})" + (f"  [{note}]" if note else ""), "ok")))
                    self.last = dst
                    ok += 1
                except Cancelled:
                    raise
                except Exception as e:
                    self.q.put(("log", (f"✘ {f.name} #{t[0]}: {str(e).strip()[-200:]}", "err")))
            self.q.put(("idle", f"Done: {ok}/{len(chosen)} tracks saved."))
        except Cancelled:
            self.q.put(("idle", "Cancelled."))

    def cancel(self):
        if self.running:
            cancel_all()

    def on_close(self):
        if self.running:
            self.closing = True
            cancel_all()
            self.after(15000, self._force_close)
        else:
            self.destroy()

    def _force_close(self):
        try:
            self.destroy()
        except tk.TclError:
            pass

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.write(*val)
                elif kind == "status":
                    self.status.set(val)
                elif kind == "ask_tool":
                    self._tool_dialog(*val)
                elif kind == "rows":
                    self.rows = val
                    self.running = False
                    multi = len(self.files) > 1
                    for f, t in val:
                        self.lb.insert("end", (f"{f.name}  —  " if multi else "") + f"#{t[0]}  {t[1]}: {t[2]}"
                                       + (f", {t[3]}" if t[3] else "") + (f"  “{t[4]}”" if t[4] else ""))
                    self.lb.selection_set(0, "end")
                    self.btn.config(state="normal" if val else "disabled")
                    self.status.set(f"{len(val)} audio / subtitle track(s) found." if val
                                    else "No separate audio or subtitle tracks found in the selected file(s).")
                elif kind == "idle":
                    self.running = False
                    self.btn.config(state="normal" if self.rows else "disabled")
                    if len(self.files) == 1 and self.sub_path.get():
                        self.add_btn.config(state="normal")
                    self.status.set(val)
        except queue.Empty:
            pass
        if self.closing and not self.running:
            self.destroy()
            return
        self.after(250, self.poll)


# ----------------------------------------------------------------------------
# PDF: keep only some pages / remove some pages (lossless - pages are copied as they are)
# ----------------------------------------------------------------------------
def parse_pages(text, n):
    """'5,8,10', '3-7', '12-' (to the end), '-4' (up to 4) -> list of page numbers (1-based, in the typed order).
    Raises ValueError with a readable message."""
    out = []
    for tok in re.split(r"[,;\s]+", (text or "").strip()):
        if not tok:
            continue
        m = re.fullmatch(r"(\d*)\s*-\s*(\d*)", tok)
        if m and (m[1] or m[2]):
            a, b = int(m[1] or 1), int(m[2] or n)
            if a > b:
                a, b = b, a
            rng = list(range(a, b + 1))
        elif tok.isdigit():
            rng = [int(tok)]
        else:
            raise ValueError(f"“{tok}” is not a page number. Use e.g. 5,8,10 or 3-7 or 12- (to the end).")
        bad = [x for x in rng if x < 1 or x > n]
        if bad:
            raise ValueError(f"Page {bad[0]} does not exist (the PDF has {n} pages).")
        out += rng
    if not out:
        raise ValueError("Enter the page numbers, e.g. 5,8,10 or 3-7.")
    return out


def compress_pages(pages):
    """[1,2,3,5,8,9] -> '1-3,5,8-9'"""
    pages, out, i = sorted(set(pages)), [], 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        out.append(str(pages[i]) if i == j else f"{pages[i]}-{pages[j]}")
        i = j + 1
    return ",".join(out)


def select_pages(src, spec, mode, tag=None):
    """mode 'keep': a new PDF made only of the listed pages (in that order); 'remove': everything except them.
    The original is not touched. Returns (result path, note)."""
    src = Path(src)
    work, tmp = _unlock_pdf(src)
    try:
        n = pdf_pages_count(work)
        pages = parse_pages(spec, n)
        if mode == "remove":
            gone = set(pages)
            pages = [i for i in range(1, n + 1) if i not in gone]
            if not pages:
                raise RuntimeError("That would remove every page.")
            note = f"{len(gone)} page(s) removed, {len(pages)} left"
        else:
            note = f"{len(pages)} page(s) kept (of {n})"
        dst = out_path(src, "pdf", tag or ("_pages" if mode == "keep" else "_removed"))
        step(f"Saving {len(pages)} page(s)")
        try:
            p = prun([tool("qpdf"), str(work), "--pages", ".", ",".join(map(str, pages)), "--", str(dst)],
                     text=True, encoding="utf-8", errors="replace")
            if p.returncode not in (0, 3) or not dst.exists():
                raise RuntimeError((p.stderr or p.stdout or "qpdf failed").strip()[-400:])
        except BaseException:
            if dst.exists():
                try:
                    dst.unlink()
                except OSError:
                    pass
            raise
        if tmp:
            note += "; the PDF was password-protected - the result has no password"
        return dst, note
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


class ReorderWindow(tk.Toplevel):
    """Page order of ONE PDF: drag pages in the list (or use the arrows), see the selected page, save a new PDF."""

    def __init__(self, master, src):
        super().__init__(master)
        self.src = Path(src)
        self.n = pdf_pages_count(self.src)
        self.order = list(range(1, self.n + 1))
        self.cur = None
        self._img = None
        self.title(f"Reorder pages — {self.src.name}")
        self.geometry("760x580")
        self.minsize(560, 420)
        self.transient(master)
        left = ttk.Frame(self)
        left.pack(side="left", fill="y", padx=(12, 6), pady=12)
        ttk.Label(left, text="Drag a page up / down:").pack(anchor="w")
        box = ttk.Frame(left)
        box.pack(fill="y", expand=True)
        self.lb = tk.Listbox(box, width=22, selectmode="browse", exportselection=False, activestyle="none")
        sb = ttk.Scrollbar(box, command=self.lb.yview)
        self.lb.config(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="y", expand=True)
        sb.pack(side="left", fill="y")
        row = ttk.Frame(left)
        row.pack(pady=(6, 0))
        for txt, cmd in (("▲", lambda: self._step(-1)), ("▼", lambda: self._step(1)),
                         ("Reverse", self._reverse), ("Reset", self._reset)):
            ttk.Button(row, text=txt, command=cmd, width=7 if len(txt) > 1 else 3).pack(side="left", padx=2)
        self.info = tk.StringVar()
        ttk.Label(left, textvariable=self.info, foreground="#666", wraplength=230, justify="left").pack(anchor="w", pady=6)
        ttk.Button(left, text="Save as new PDF", command=self.save).pack(fill="x", pady=(4, 0))
        self.prev = ttk.Label(self, anchor="center")
        self.prev.pack(side="left", fill="both", expand=True, padx=(6, 12), pady=12)
        self.lb.bind("<<ListboxSelect>>", lambda e: self._show())
        self.lb.bind("<ButtonPress-1>", self._press)
        self.lb.bind("<B1-Motion>", self._drag)
        self._fill(0)

    def _fill(self, sel=None):
        self.lb.delete(0, "end")
        for k, pg in enumerate(self.order):
            self.lb.insert("end", f"{k + 1:>3}.   page {pg}" + ("" if pg == k + 1 else "   ◂ moved"))
        if sel is not None:
            sel = max(0, min(sel, self.n - 1))
            self.lb.selection_clear(0, "end")
            self.lb.selection_set(sel)
            self.lb.activate(sel)
            self.lb.see(sel)
            self.cur = sel
            self._show()
        moved = sum(1 for k, pg in enumerate(self.order) if pg != k + 1)
        self.info.set("Order unchanged." if not moved else f"{moved} page(s) are in a new place.")

    def _press(self, event):
        self.cur = self.lb.nearest(event.y)

    def _drag(self, event):
        i = self.lb.nearest(event.y)
        if self.cur is None or i == self.cur or not 0 <= i < self.n:
            return
        self.order.insert(i, self.order.pop(self.cur))
        self._fill(i)

    def _step(self, d):
        sel = self.lb.curselection()
        if not sel:
            return
        i, j = sel[0], sel[0] + d
        if 0 <= j < self.n:
            self.order[i], self.order[j] = self.order[j], self.order[i]
            self._fill(j)

    def _reverse(self):
        self.order.reverse()
        self._fill(0)

    def _reset(self):
        self.order = list(range(1, self.n + 1))
        self._fill(0)

    def _show(self):
        sel = self.lb.curselection()
        if not sel:
            return
        pg = self.order[sel[0]]
        tmp = Path(tempfile.mkdtemp())
        try:
            prun([tool("pdftoppm"), "-png", "-f", str(pg), "-l", str(pg), "-singlefile", "-scale-to", "460",
                  str(self.src), str(tmp / "p")])
            self._img = tk.PhotoImage(file=str(tmp / "p.png"))
            self.prev.config(image=self._img, text="")
        except Exception:
            self.prev.config(image="", text=f"(no preview for page {pg})")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def save(self):
        if self.order == list(range(1, self.n + 1)):
            messagebox.showinfo("Konwertexpol", "The order has not been changed.", parent=self)
            return
        try:
            dst, note = select_pages(self.src, ",".join(map(str, self.order)), "keep", tag="_reordered")
        except Exception as e:
            messagebox.showerror("Error", str(e)[-400:], parent=self)
            return
        self.master.write(f"✔ {dst.name} ({mb(dst.stat().st_size)})  [pages reordered, nothing else changed]", "ok")
        self.master.last = dst
        self.destroy()


class PickWindow(tk.Toplevel):
    """Tick pages of ONE PDF in a list (with a preview of the page you click); OK fills the 'Pages' field."""

    def __init__(self, master, src, on_ok):
        super().__init__(master)
        self.src, self.on_ok = Path(src), on_ok
        self.n = pdf_pages_count(self.src)
        self._img = None
        self.title(f"Pick pages — {self.src.name}")
        self.geometry("740x580")
        self.minsize(540, 420)
        self.transient(master)
        left = ttk.Frame(self)
        left.pack(side="left", fill="y", padx=(12, 6), pady=12)
        ttk.Label(left, text="Click pages (Ctrl = add, Shift = range):").pack(anchor="w")
        box = ttk.Frame(left)
        box.pack(fill="y", expand=True)
        self.lb = tk.Listbox(box, width=18, selectmode="extended", exportselection=False, activestyle="none")
        sb = ttk.Scrollbar(box, command=self.lb.yview)
        self.lb.config(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="y", expand=True)
        sb.pack(side="left", fill="y")
        for k in range(1, self.n + 1):
            self.lb.insert("end", f"page {k}")
        row = ttk.Frame(left)
        row.pack(pady=(6, 0))
        ttk.Button(row, text="All", command=lambda: self.lb.selection_set(0, "end"), width=6).pack(side="left", padx=2)
        ttk.Button(row, text="None", command=lambda: self.lb.selection_clear(0, "end"), width=6).pack(side="left", padx=2)
        ttk.Button(row, text="Invert", command=self._invert, width=6).pack(side="left", padx=2)
        ttk.Button(left, text="OK - use the selected pages", command=self.ok).pack(fill="x", pady=(8, 0))
        self.prev = ttk.Label(self, anchor="center")
        self.prev.pack(side="left", fill="both", expand=True, padx=(6, 12), pady=12)
        for ev in ("<ButtonRelease-1>", "<KeyRelease-Up>", "<KeyRelease-Down>"):
            self.lb.bind(ev, lambda e: self.after(10, self._show))
        self.lb.activate(0)
        self._show()

    def _invert(self):
        for i in range(self.n):
            if self.lb.selection_includes(i):
                self.lb.selection_clear(i)
            else:
                self.lb.selection_set(i)

    def _show(self):
        pg = int(self.lb.index("active")) + 1
        tmp = Path(tempfile.mkdtemp())
        try:
            prun([tool("pdftoppm"), "-png", "-f", str(pg), "-l", str(pg), "-singlefile", "-scale-to", "460",
                  str(self.src), str(tmp / "p")])
            self._img = tk.PhotoImage(file=str(tmp / "p.png"))
            self.prev.config(image=self._img, text="")
        except Exception:
            self.prev.config(image="", text=f"(no preview for page {pg})")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def ok(self):
        sel = [i + 1 for i in self.lb.curselection()]
        if not sel:
            messagebox.showinfo("Konwertexpol", "Select at least one page.", parent=self)
            return
        self.on_ok(compress_pages(sel))
        self.destroy()


class PagesApp(DialogsMixin, tk.Tk):
    """Keep only chosen pages of a PDF, or remove chosen pages - the result is a new PDF."""

    def __init__(self, files):
        super().__init__()
        add_logo(self)
        self.files = list(files)
        self.q = queue.Queue()
        self._tool_skipped = set()
        self.running, self.closing = False, False
        self.last = None
        self.title("Konwertexpol — PDF pages")
        self.minsize(560, 380)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        names = self.files[0].name if len(self.files) == 1 else f"{len(self.files)} PDF files"
        ttk.Label(self, text=f"{names}" + (f"  ({pdf_pages_count(self.files[0])} pages)" if len(self.files) == 1 else
                                          "  (the same page numbers are used for every file)")
                  ).pack(anchor="w", padx=12, pady=(10, 4))
        self.mode = tk.StringVar(value="keep")
        ttk.Radiobutton(self, text="Keep ONLY these pages (new PDF made of them, in the typed order)",
                        variable=self.mode, value="keep").pack(anchor="w", padx=12)
        ttk.Radiobutton(self, text="REMOVE these pages (everything else stays)",
                        variable=self.mode, value="remove").pack(anchor="w", padx=12)
        ttk.Radiobutton(self, text="REORDER pages - drag them in a list with a preview (one PDF)",
                        variable=self.mode, value="reorder").pack(anchor="w", padx=12)
        row = ttk.Frame(self)
        row.pack(fill="x", padx=12, pady=8)
        ttk.Label(row, text="Pages:").pack(side="left")
        self.spec = ttk.Entry(row)
        self.spec.pack(side="left", fill="x", expand=True, padx=8)
        self.spec.focus_set()
        self.spec.bind("<Return>", lambda e: self.start())
        self.pick_btn = ttk.Button(row, text="Pick from a list…", command=self.open_pick)
        self.pick_btn.pack(side="left")
        ttk.Label(self, foreground="#666", text="e.g.  5,8,10   or   3-7   or   12-  (page 12 to the end)   or   1-3,9").pack(
            anchor="w", padx=12)
        btns = ttk.Frame(self)
        btns.pack(pady=8)
        self.btn = ttk.Button(btns, text="Create PDF", command=self.start)
        self.btn.pack(side="left", padx=4)
        self.cancel_btn = ttk.Button(btns, text="Cancel", command=lambda: cancel_all(), state="disabled")
        self.cancel_btn.pack(side="left", padx=4)
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status).pack(anchor="w", padx=12)
        self.log = tk.Text(self, height=7, state="disabled", wrap="word")
        self.log.tag_config("err", foreground="#c0392b")
        self.log.tag_config("ok", foreground="#1e8449")
        self.log.pack(fill="both", expand=True, padx=12, pady=8)
        self.after(100, self.poll)

    def write(self, msg, tag=None):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def on_close(self):
        if self.running:
            self.closing = True
            cancel_all()
            self.after(15000, self._force_close)
        else:
            self.destroy()

    def _force_close(self):
        try:
            self.destroy()
        except tk.TclError:
            pass

    def open_pick(self):
        """Tick pages in a list with a preview instead of typing numbers (one PDF)."""
        if self.running:
            return
        if len(self.files) != 1:
            messagebox.showerror("Error", "Picking from a list works on one PDF at a time.")
            return
        if pdf_needs_password(self.files[0]):
            messagebox.showerror("Error", "This PDF is password-protected. Use “Remove password” first.")
            return
        self.running = True
        self.status.set("Checking tools...")

        def prep():
            tools_manager.ensure(["pdfinfo", "pdftoppm"], lambda m, f=None: self.q.put(("status", m)))
            self.q.put(("open_pick", self.files[0]))
        threading.Thread(target=prep, daemon=True).start()

    def _picked(self, spec):
        self.spec.delete(0, "end")
        self.spec.insert(0, spec)
        if self.mode.get() == "reorder":
            self.mode.set("keep")

    def start(self):
        if self.running:
            return
        spec, mode = self.spec.get(), self.mode.get()
        if mode == "reorder":
            if len(self.files) != 1:
                messagebox.showerror("Error", "Reordering works on one PDF at a time.")
                return
            if pdf_needs_password(self.files[0]):
                messagebox.showerror("Error", "This PDF is password-protected. Use “Remove password” first.")
                return
            self.running = True
            self.btn.config(state="disabled")
            self.status.set("Checking tools...")

            def prep():
                msgs, errs = tools_manager.ensure(["qpdf", "pdfinfo", "pdftoppm", "pikepdf"],
                                                  lambda m, f=None: self.q.put(("status", m)))
                self.q.put(("open_reorder", self.files[0]))
            threading.Thread(target=prep, daemon=True).start()
            return
        try:
            parse_pages(spec, 10 ** 6)
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            return
        global ASK_PASSWORD, STEP, DETAIL
        ASK_PASSWORD = self._ask_password
        STEP = lambda msg: None
        DETAIL = lambda msg: (check_cancel(), self.q.put(("status", msg)))[1]
        tools_manager.CONFIRM_INSTALL = self._confirm_install
        CANCEL.clear()
        self.running = True
        self.btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        threading.Thread(target=self.work, args=(spec, mode), daemon=True).start()

    def work(self, spec, mode):
        ok = 0
        try:
            self.q.put(("status", "Checking tools..."))
            msgs, errs = tools_manager.ensure(["qpdf", "pdfinfo", "pikepdf"], lambda m, f=None: self.q.put(("status", m)))
            for e in errs:
                self.q.put(("log", (f"⚠ {e.replace(tools_manager.NOT_INSTALLED, '')}", "err")))
            for i, f in enumerate(self.files, 1):
                check_cancel()
                self.q.put(("status", f"{i}/{len(self.files)}: {f.name}"))
                try:
                    dst, note = select_pages(f, spec, mode)
                    self.last = dst
                    self.q.put(("log", (f"✔ {dst.name} ({mb(dst.stat().st_size)})  [{note}]", "ok")))
                    ok += 1
                except Cancelled:
                    raise
                except Exception as e:
                    self.q.put(("log", (f"✘ {f.name}: {str(e).strip()[:300]}", "err")))
            self.q.put(("done", f"Done: {ok}/{len(self.files)}."))
        except Cancelled:
            self.q.put(("done", "Cancelled."))

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.write(*val)
                elif kind == "status":
                    self.status.set(val)
                elif kind == "ask_tool":
                    self._tool_dialog(*val)
                elif kind == "ask_password":
                    self._password_dialog(*val)
                elif kind == "open_pick":
                    self.running = False
                    self.status.set("Ready.")
                    PickWindow(self, val, self._picked)
                elif kind == "open_reorder":
                    self.running = False
                    self.btn.config(state="normal")
                    self.status.set("Ready.")
                    ReorderWindow(self, val)
                elif kind == "done":
                    self.running = False
                    self.btn.config(state="normal", text="Run again")
                    self.cancel_btn.config(state="disabled")
                    self.status.set(val)
        except queue.Empty:
            pass
        if self.closing and not self.running:
            self.destroy()
            return
        self.after(250, self.poll)


class MetaApp(DialogsMixin, tk.Tk):
    """Shows the metadata of the selected files (mode "show"), or saves clean copies without it (mode "strip")."""

    def __init__(self, files, mode):
        super().__init__()
        add_logo(self)
        self.files, self.mode = list(files), mode
        self.q = queue.Queue()
        self._tool_skipped = set()
        self.running, self.closing = True, False
        self.title("Konwertexpol — " + {"show": "metadata", "strip": "remove metadata",
                                         "unlock": "remove PDF password"}[mode])
        self.geometry("780x560")
        self.minsize(520, 360)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.status = tk.StringVar(value="Reading…")
        ttk.Label(self, textvariable=self.status).pack(anchor="w", padx=12, pady=(10, 2))
        fr = ttk.Frame(self)
        fr.pack(fill="both", expand=True, padx=12, pady=4)
        self.text = tk.Text(fr, wrap="word", state="disabled")
        sb = ttk.Scrollbar(fr, command=self.text.yview)
        self.text.config(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.text.tag_config("head", font=("TkDefaultFont", 10, "bold"))
        self.text.tag_config("warn", foreground="#c0392b")
        self.text.tag_config("ok", foreground="#1e8449")
        btns = ttk.Frame(self)
        btns.pack(pady=8)
        self.rm_btn = None
        if mode == "show":                 # one menu entry for both: look first, then save clean copies if wanted
            self.rm_btn = ttk.Button(btns, text="Remove metadata (save clean copies)", command=self.remove_now,
                                     state="disabled")
            self.rm_btn.pack(side="left", padx=4)
        ttk.Button(btns, text="Copy", command=self.copy).pack(side="left", padx=4)
        ttk.Button(btns, text="Save as text…", command=self.save).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=self.on_close).pack(side="left", padx=4)
        threading.Thread(target=self.worker, daemon=True).start()
        self.after(100, self.poll)

    def remove_now(self):
        """After looking at the metadata: save clean copies of the same files (originals stay untouched)."""
        if self.running:
            return
        self.mode, self.running = "strip", True
        self.rm_btn.config(state="disabled")
        self.title("Konwertexpol — remove metadata")
        self.text.config(state="normal")
        self.text.insert("end", "\n── Removing metadata ──\n", "head")
        self.text.config(state="disabled")
        CANCEL.clear()
        threading.Thread(target=self.worker, daemon=True).start()

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", "end"))
        self.status.set("Copied to the clipboard.")

    def save(self):
        p = filedialog.asksaveasfilename(parent=self, defaultextension=".txt", filetypes=[("Text", "*.txt")],
                                         initialfile="metadata.txt")
        if p:
            Path(p).write_text(self.text.get("1.0", "end"), encoding="utf-8")

    def on_close(self):
        if self.running:
            self.closing = True
            cancel_all()
            self.after(15000, self._force_close)
        else:
            self.destroy()

    def _force_close(self):
        try:
            self.destroy()
        except tk.TclError:
            pass

    def worker(self):
        global ASK_PASSWORD, STEP, DETAIL
        ASK_PASSWORD = self._ask_password
        tools_manager.CONFIRM_INSTALL = self._confirm_install
        STEP = lambda msg: None
        DETAIL = lambda msg: self.q.put(("status", msg))
        put = lambda text, tag=None: self.q.put(("line", (text, tag)))
        try:
            names = set()
            for f in self.files:
                cat = category(f)
                if cat in ("video", "audio"):
                    names.add("ffmpeg")
                elif cat == "pdf":
                    names |= {"pikepdf", "pdfinfo"}
                elif cat == "image" and ext_of(f) in ("tiff", "bmp", "gif", "ico", "heic", "avif", "jxl"):
                    names.add("magick")
            if names:
                self.q.put(("status", "Checking tools..."))
                msgs, errs = tools_manager.ensure(sorted(names), lambda m, f=None: self.q.put(("status", m)))
                for e in errs:
                    put(f"⚠ {e.replace(tools_manager.NOT_INSTALLED, '')}", "warn")
            for i, f in enumerate(self.files, 1):
                check_cancel()
                self.q.put(("status", f"{i}/{len(self.files)}: {f.name}"))
                try:
                    if self.mode == "show":
                        lines = metadata_report(f)
                        put(lines[0], "head")
                        for ln in lines[1:]:
                            put(ln, "warn" if ln.startswith("⚠") else None)
                    elif self.mode == "unlock":
                        if ext_of(f) != "pdf":
                            raise RuntimeError("this is not a PDF file")
                        if not pdf_needs_password(f):
                            put(f"ℹ {f.name} has no password - nothing to remove.")
                        else:
                            work, tmp = _unlock_pdf(f)
                            try:
                                dst = out_path(f, "pdf", "_unlocked")
                                shutil.copy(work, dst)
                            finally:
                                if tmp:
                                    shutil.rmtree(tmp, ignore_errors=True)
                            put(f"✔ {f.name} → {dst.name}", "ok")
                            put("   saved WITHOUT the password (the original was not changed)")
                    else:
                        dst, note = strip_metadata(f)
                        put(f"✔ {f.name} → {dst.name}", "ok")
                        put(f"   {note}")
                except Cancelled:
                    raise
                except Exception as e:
                    put(f"✘ {f.name}: {str(e).strip()[:300]}", "warn")
                put("")
            self.q.put(("done", f"{len(self.files)} file(s)." if self.mode == "show" else "Done."))
        except Cancelled:
            self.q.put(("done", "Cancelled."))

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "line":
                    self.text.config(state="normal")
                    self.text.insert("end", val[0] + "\n", val[1])
                    self.text.config(state="disabled")
                elif kind == "status":
                    self.status.set(val)
                elif kind == "ask_tool":
                    self._tool_dialog(*val)
                elif kind == "ask_password":
                    self._password_dialog(*val)
                elif kind == "done":
                    self.running = False
                    self.status.set(val)
                    if self.rm_btn is not None and self.mode == "show" and val != "Cancelled.":
                        self.rm_btn.config(state="normal")
        except queue.Empty:
            pass
        if self.closing and not self.running:
            self.destroy()
            return
        self.after(200, self.poll)


class TabBase(tk.Frame):
    """A tool window living inside a tab: the window-level calls are ignored, closing closes the host window."""

    def title(self, *a, **k):
        return ""

    def geometry(self, *a, **k):
        return ""

    def minsize(self, *a, **k):
        return ""

    def protocol(self, *a, **k):
        return ""

    def iconbitmap(self, *a, **k):
        return ""

    def destroy(self):
        top = getattr(self, "_top", None)
        if top is None or getattr(top, "_closing", False):      # the host is closing: really remove this tab
            tk.Frame.destroy(self)
        else:                                                    # "close" inside a tab = close the host window
            top.destroy()


class App(_AppBody, tk.Tk):
    pass


class MergeApp(_MergeBody, tk.Tk):
    pass


class ExtractApp(_ExtractBody, tk.Tk):
    pass


class AppTab(_AppBody, TabBase):
    pass


class MergeTab(_MergeBody, TabBase):
    pass


class ExtractTab(_ExtractBody, TabBase):
    pass


class VideoToolsWindow(tk.Tk):
    """One window with tabs for everything you do with a video besides converting it."""

    def __init__(self, files):
        super().__init__()
        add_logo(self)
        self.title("Konwertexpol — video tools")
        self.geometry("900x820")
        self.minsize(780, 640)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        nb = self.nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=6)
        self.tabs = []

        def add(widget, text, runner=True):
            nb.add(widget, text=text)
            if runner:
                self.tabs.append(widget)

        t1 = AppTab(files, trim=True, master=nb)
        t1.pack(fill="both", expand=True)
        add(t1, "  Cut · GIF · frame  ")
        if len(files) >= 2:
            t2 = MergeTab(files, kind="video", master=nb)
            t2.pack(fill="both", expand=True)
            add(t2, "  Join videos  ")
        else:
            ph = ttk.Frame(nb)
            ttk.Label(ph, justify="center", foreground="#666",
                      text="To join videos, select two or more of them in Explorer,\n"
                           "right-click one and choose Konwertexpol → Video tools.").pack(expand=True)
            add(ph, "  Join videos  ", runner=False)
        t3 = ExtractTab(files, master=nb)
        t3.pack(fill="both", expand=True)
        add(t3, "  Subtitles & audio tracks  ")
        self.after(400, self._sync)

    def _sync(self):
        """While one tool works the other tabs are locked (they share the progress / cancel machinery)."""
        busy = [t for t in self.tabs if getattr(t, "running", False)]
        for i, w in enumerate(self.nb.tabs()):
            widget = self.nametowidget(w)
            self.nb.tab(i, state="disabled" if busy and widget not in busy else "normal")
        self.after(400, self._sync)

    def on_close(self):
        busy = [t for t in self.tabs if getattr(t, "running", False)]
        if busy:
            busy[0].on_close()
        else:
            self.destroy()

    def destroy(self):
        self._closing = True
        tk.Tk.destroy(self)


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    refresh_path()
    if len(sys.argv) < 2:
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo("Konwertexpol",
                            "Right-click a file and choose “Konwertexpol”.\n"
                            "(Windows 11: “Show more options” or Shift+F10)")
        return
    args = sys.argv[1:]
    to, compress, first, res, focus_res, merge, ocr = None, False, None, None, False, False, False
    meta, merge_images, list_file, trim, unlock, join, extract, pages, vtools = (None, False, None, False, False, False,
                                                                                  False, False, False)
    i = 0
    while i < len(args):
        if args[i] == "--to" and i + 1 < len(args):
            to = norm(args[i + 1])
            i += 2
            continue
        if args[i] == "--res" and i + 1 < len(args):
            res = args[i + 1]
            i += 2
            continue
        if args[i] == "--meta" and i + 1 < len(args):
            meta = args[i + 1]
            i += 2
            continue
        if args[i] == "--list" and i + 1 < len(args):
            list_file = args[i + 1]
            i += 2
            continue
        if args[i] == "--pick":
            pass                                     # just open the window and let the user choose any format
        elif args[i] == "--merge-images":
            merge_images = True
        elif args[i] == "--merge":
            merge = True
        elif args[i] == "--compress":
            compress = True
        elif args[i] == "--resize":
            focus_res = True
        elif args[i] == "--trim":
            trim = True
        elif args[i] == "--unlock":
            unlock = True
        elif args[i] == "--pages":
            pages = True
        elif args[i] == "--vtools":
            vtools = True
        elif args[i] == "--join":
            join = True
        elif args[i] == "--extract":
            extract = True
        elif args[i] == "--ocr":
            ocr = True
        elif first is None:
            first = args[i]
        i += 1
    if list_file:                                    # the launcher (launch.py) already gathered all selected files
        try:
            files = [x for x in Path(list_file).read_text(encoding="utf-8").split("\n") if x.strip()]
        finally:
            try:
                os.remove(list_file)
            except OSError:
                pass
    else:
        if first is None:
            return
        files = collect_files(first)
        if files is None:
            return
    paths = [Path(f) for f in files if Path(f).is_file()]
    if not paths:
        return
    if meta in ("show", "strip"):
        MetaApp(paths, meta).mainloop()
        return
    if vtools:
        vids = [p for p in paths if category(p) == "video"]
        if not vids:
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("Konwertexpol", "Video tools work on video files.")
            return
        VideoToolsWindow(vids).mainloop()
        return
    if pages:
        pdfs = [p for p in paths if ext_of(p) == "pdf"]
        if not pdfs:
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("Konwertexpol", "Selecting pages works on PDF files.")
            return
        PagesApp(pdfs).mainloop()
        return
    if join:
        vids = [p for p in paths if category(p) == "video"]
        if len(vids) < 2:
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("Konwertexpol", "Select at least two video files, right-click one of them and choose\n"
                                                "Konwertexpol → Join selected videos into ONE.")
            return
        MergeApp(vids, kind="video").mainloop()
        return
    if extract:
        vids = [p for p in paths if category(p) == "video"]
        if not vids:
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("Konwertexpol", "Extracting tracks works on video files.")
            return
        ExtractApp(vids).mainloop()
        return
    if unlock:
        MetaApp([p for p in paths if ext_of(p) == "pdf"] or paths, "unlock").mainloop()
        return
    if trim:
        clips = [p for p in paths if category(p) in ("video", "audio")]
        if not clips:
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("Konwertexpol", "Cutting a clip works on video and audio files.")
            return
        App(clips, trim=True).mainloop()
        return
    if merge_images:
        imgs = [p for p in paths if category(p) == "image"]
        if len(imgs) < 2:
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("Konwertexpol", "Select at least two images, right-click one of them and choose\n"
                                                "Konwertexpol → To PDF → Combine selected images into ONE PDF.")
            return
        MergeApp(imgs, kind="images").mainloop()
        return
    if merge:
        pdfs = [p for p in paths if ext_of(p) == "pdf"]
        if len(pdfs) < 2:
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("Konwertexpol",
                                "Merging works on several PDF files at once.\n"
                                "Select at least two PDF files in a folder, right-click one of them\n"
                                "and choose Konwertexpol → Merge PDFs.")
            return
        MergeApp(pdfs).mainloop()
        return
    App(paths, to=to, compress=compress, res=res, focus_res=focus_res, ocr=ocr).mainloop()


if __name__ == "__main__":
    main()
