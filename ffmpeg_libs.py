# -*- coding: utf-8 -*-
"""Finds the libraries that are compiled into a prebuilt FFmpeg (and their versions) and knows where their source lives.

The versions are read from text that the libraries leave inside ffmpeg.exe (version banners, source-file paths such as
'../../lame-3.100/libmp3lame/lame.c'). Anything that cannot be found this way is reported, never guessed."""
import re

# name -> (label, licence, enable flag in the FFmpeg configuration or None, [source URL templates])
# templates: {v} version, {v_} version with underscores, {mm} major.minor, {h} git hash (for 'git describe' versions)
LIBS = {
    "lame":       ("LAME (MP3 encoder)", "LGPL-2.0+", "libmp3lame",
                   ["https://downloads.sourceforge.net/project/lame/lame/{v}/lame-{v}.tar.gz",
                    "https://deb.debian.org/debian/pool/main/l/lame/lame_{v}.orig.tar.gz"]),
    "gnutls":     ("GnuTLS", "LGPL-2.1+", "gnutls",
                   ["https://www.gnupg.org/ftp/gcrypt/gnutls/v{mm}/gnutls-{v}.tar.xz",
                    "https://gitlab.com/gnutls/gnutls/-/archive/{v}/gnutls-{v}.tar.gz",
                    "https://github.com/gnutls/gnutls/archive/refs/tags/{v}.tar.gz"]),
    "gmp":        ("GMP", "LGPL-3.0+ or GPL-2.0+", "gmp",
                   ["https://ftp.gnu.org/gnu/gmp/gmp-{v}.tar.xz", "https://gmplib.org/download/gmp/gmp-{v}.tar.xz"]),
    "nettle":     ("Nettle", "LGPL-3.0+ or GPL-2.0+", None,
                   ["https://ftp.gnu.org/gnu/nettle/nettle-{v}.tar.gz"]),
    "libssh":     ("libssh", "LGPL-2.1+", "libssh",
                   ["https://www.libssh.org/files/{mm}/libssh-{v}.tar.xz"]),
    "zeromq":     ("ZeroMQ (libzmq)", "MPL-2.0 (with static linking exception)", "libzmq",
                   ["https://github.com/zeromq/libzmq/releases/download/v{v}/zeromq-{v}.tar.gz"]),
    "libsodium":  ("libsodium", "ISC", None,
                   ["https://download.libsodium.org/libsodium/releases/libsodium-{v}.tar.gz",
                    "https://github.com/jedisct1/libsodium/releases/download/{v}-RELEASE/libsodium-{v}.tar.gz"]),
    "libunibreak": ("libunibreak", "zlib-style", None,
                    ["https://github.com/adah1972/libunibreak/releases/download/libunibreak_{v_}/libunibreak-{v}.tar.gz"]),
    "openssl":    ("OpenSSL", "Apache-2.0", None,
                   ["https://github.com/openssl/openssl/releases/download/openssl-{v}/openssl-{v}.tar.gz"]),
    "pixman":     ("pixman", "MIT", None, ["https://cairographics.org/releases/pixman-{v}.tar.gz",
                                                          "https://gitlab.freedesktop.org/pixman/pixman/-/archive/pixman-{v}/pixman-pixman-{v}.tar.gz"]),
    "expat":      ("expat", "MIT", None,
                   ["https://github.com/libexpat/libexpat/releases/download/R_{v_}/expat-{v}.tar.xz"]),
    "libvpx":     ("libvpx (VP8/VP9)", "BSD-3-Clause", "libvpx",
                   ["https://github.com/webmproject/libvpx/archive/{h}.tar.gz"]),
    "libaom":     ("libaom (AV1)", "BSD-2-Clause", "libaom",
                   ["https://aomedia.googlesource.com/aom/+archive/{h}.tar.gz"]),
    "opus":       ("libopus", "BSD-3-Clause", "libopus", ["https://github.com/xiph/opus/archive/{h}.tar.gz"]),
    "vorbis":     ("libvorbis", "BSD-3-Clause", "libvorbis",
                   ["https://downloads.xiph.org/releases/vorbis/libvorbis-{v}.tar.xz",
                    "https://github.com/xiph/vorbis/archive/refs/tags/v{v}.tar.gz"]),
    "theora":     ("libtheora", "BSD-3-Clause", "libtheora",
                   ["https://downloads.xiph.org/releases/theora/libtheora-{v}.tar.xz",
                    "https://github.com/xiph/theora/archive/refs/tags/v{v}.tar.gz"]),
    "fribidi":    ("FriBidi", "LGPL-2.1+", "libfribidi",
                   ["https://github.com/fribidi/fribidi/releases/download/v{v}/fribidi-{v}.tar.xz"]),
}

# how to find each version: regexes on printable strings of ffmpeg.exe (first group = version)
PATTERNS = {
    "libvpx": r"WebM Project VP9 Encoder v(\d[\w.\-]*)",
    "libaom": r"AOMedia Project AV1 Encoder (\d[\w.\-]*)",
    "opus": r"libopus (\d[\w.\-]*)",
    "vorbis": r"libVorbis (\d+(?:\.\d+)+)",
    "theora": r"libtheora (\d+(?:\.\d+)+)",
    "fribidi": r"FriBidi (\d+(?:\.\d+)+)",
    "expat": r"expat_(\d+(?:\.\d+)+)",
}
# libraries whose version is the number in a source path name-<version>/
SRCDIR = {"lame", "gnutls", "gmp", "nettle", "libssh", "zeromq", "libsodium", "libunibreak", "openssl", "pixman"}

_STR = re.compile(rb"[ -~]{6,200}")
_DIR = re.compile(r"(?<![\w.+-])([A-Za-z][\w.+]*(?:-[A-Za-z][\w.+]*)*)-(\d+(?:\.\d+)+)/")


def scan_versions(exe):
    """{library key: version string} found inside the binary."""
    pats = {k: re.compile(v) for k, v in PATTERNS.items()}
    found, dirs = {}, {}
    with open(exe, "rb") as f:
        tail = b""
        while True:
            chunk = f.read(32 * 1024 * 1024)
            if not chunk:
                break
            data = tail + chunk
            tail = data[-300:]
            for m in _STR.finditer(data):
                s = m.group().decode("ascii", "ignore")
                if "/" in s and "-" in s:
                    for dm in _DIR.finditer(s):
                        dirs.setdefault(dm.group(1).lower(), dm.group(2))
                for k, rx in pats.items():
                    if k not in found:
                        mm = rx.search(s)
                        if mm:
                            found[k] = mm.group(1)
    for k in SRCDIR:
        if k in dirs:
            found[k] = dirs[k]
    return found


def source_urls(key, ver):
    v = ver
    h = ""
    m = re.search(r"-g([0-9a-f]{7,40})$", ver)
    if m:
        h = m.group(1)
        v = ver[: m.start()]
    if key in ("libvpx", "libaom", "opus") and not h:       # a clean release: use the tag-like address of the release
        return {"libvpx": [f"https://github.com/webmproject/libvpx/archive/refs/tags/v{v}.tar.gz"],
                "libaom": [f"https://storage.googleapis.com/aom-releases/libaom-{v}.tar.gz"],
                "opus": [f"https://downloads.xiph.org/releases/opus/opus-{v}.tar.gz"]}[key]
    mm = ".".join(v.split(".")[:2])
    return [t.format(v=v, v_=v.replace(".", "_"), mm=mm, h=h) for t in LIBS[key][3]]


def enabled_flags(buildconf_text):
    """Library-like --enable-xxx flags from the FFmpeg configuration."""
    flags = set(re.findall(r"--enable-([\w\-]+)", buildconf_text))
    extra = {"gnutls", "gmp", "cairo", "fontconfig", "sdl2", "openal", "avisynth", "libxml2"}
    return sorted(f for f in flags if f.startswith("lib") or f in extra)


def undetected(found, flags):
    """Enabled libraries for which no version was found (and so no source can be fetched automatically)."""
    known = {LIBS[k][2] for k in found if LIBS[k][2]}
    return [f for f in flags if f not in known]
