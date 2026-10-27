# -*- coding: utf-8 -*-
"""Collects what is needed to finish the licence documentation. Run it on Windows after build_installer.bat:

    installer\\payload\\python\\python.exe installer\\collect_info.py

(or  py -3 installer\\collect_info.py ). Needs only Python and Internet access. It does not change anything in the payload.
Result: installer\\collect_for_claude.zip - send that file."""
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAYLOAD = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "payload"
OUT = HERE / "collect_out"
ZIP = HERE / "collect_for_claude.zip"
UA = {"User-Agent": "Konwertexpol-collect"}
LOG = []

LICENSE_RE = re.compile(r"^(licen[sc]e|copying|copyright|notice|authors|patents|readme\.ijg|unlicense)([._-].*)?$", re.I)


def log(msg):
    print(msg)
    LOG.append(msg)


def save(rel, data):
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_bytes(data)


def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def step(name):
    def deco(fn):
        def run():
            log(f"\n== {name}")
            try:
                fn()
            except Exception as e:                      # one failing step must not stop the others
                log(f"   FAILED: {type(e).__name__}: {e}")
        return run
    return deco


def find_dirs(root, name):
    return [p for p in root.rglob("*") if p.is_dir() and p.name.lower() == name.lower()]


# ------------------------------------------------------------------------------------------------------------------
@step("1. Python packages (METADATA of every package, full METADATA of pymupdf*)")
def python_packages():
    rows = []
    for d in sorted(PAYLOAD.rglob("*.dist-info")):
        md = d / "METADATA"
        if not md.is_file():
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        g = lambda k: (re.search(rf"^{k}:\s*(.+)$", text, re.M) or [None, ""])[1].strip()
        cl = re.findall(r"^Classifier:\s*License :: (.+)$", text, re.M)
        rows.append(f"{d.name} | License: {g('License')[:100]} | Expr: {g('License-Expression')} | Classifiers: {'; '.join(cl)} "
                    f"| files: {', '.join(x.name for x in d.iterdir() if LICENSE_RE.match(x.stem) or x.is_dir())[:150]}")
        if d.name.lower().startswith(("pymupdf", "pdf2docx", "pikepdf", "ocrmypdf", "fpdf2", "img2pdf")):
            save(f"python_metadata/{d.name}_METADATA.txt", "\n".join(text.splitlines()[:80]))
    bins = [f"{p.stat().st_size:>10}  {p.relative_to(PAYLOAD)}" for p in sorted((PAYLOAD / "tools").rglob("*"))
            if p.is_file() and p.suffix.lower() in (".dll", ".pyd") and "python" in p.parts]
    save("python_binaries.txt", "\n".join(bins))
    save("python_packages.txt", "\n".join(rows) or "no *.dist-info found under " + str(PAYLOAD))
    log(f"   {len(rows)} packages")


# ------------------------------------------------------------------------------------------------------------------
def version_info(folder, pattern="*.dll,*.exe"):
    """Text table of the Windows version resources (product, version, company, copyright) of the files (PowerShell)."""
    import tempfile
    pats = ",".join("'%s'" % x for x in pattern.split(","))
    tmp = Path(tempfile.gettempdir()) / "konwertexpol_versions.txt"
    ps = ("Get-ChildItem -LiteralPath '%s' -Recurse -File -Include %s | ForEach-Object { $v = $_.VersionInfo; "
          "'{0} | {1} | {2} | {3} | {4}' -f $_.FullName.Substring(%d), $v.ProductName, $v.FileVersion, $v.CompanyName, "
          "$v.LegalCopyright } | Out-File -Encoding utf8 -LiteralPath '%s'" % (str(folder).replace("'", "''"), pats,
                                                                              len(str(folder)) + 1, str(tmp).replace("'", "''")))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        return f"(PowerShell could not be run: {e})"
    if tmp.is_file():
        text = tmp.read_text(encoding="utf-8-sig", errors="replace")
        tmp.unlink(missing_ok=True)
        return text
    return "(PowerShell produced no output) " + (r.stderr or b"").decode("utf-8", "replace")[:300]


@step("2. Tesseract: DLL list with versions + UB-Mannheim wiki")
def tesseract():
    d = PAYLOAD / "tools" / "tesseract"
    if d.is_dir():
        save("tesseract/dll_versions.txt", version_info(d, "*.dll,*.exe"))
        save("tesseract/files.txt", "\n".join(str(p.relative_to(d)) for p in sorted(d.rglob("*")) if p.is_file()))
    else:
        log(f"   {d} does not exist")
    html = get("https://github.com/UB-Mannheim/tesseract/wiki").decode("utf-8", "replace")
    save("tesseract/wiki.html", html)
    save("tesseract/wiki.txt", re.sub(r"\n\s*\n+", "\n", re.sub(r"<[^>]+>", " ", html)))


@step("3. Poppler: DLL list with versions, licence files of Poppler, conda recipe")
def poppler():
    d = PAYLOAD / "tools" / "poppler"
    if d.is_dir():
        save("poppler/dll_versions.txt", version_info(d, "*.dll,*.exe"))
        save("poppler/files.txt", "\n".join(str(p.relative_to(d)) for p in sorted(d.rglob("*")) if p.is_file()))
        for m in d.rglob("conda-meta"):
            log(f"   conda-meta found: {m}")
    for name, url in {"COPYING": "https://gitlab.freedesktop.org/poppler/poppler/-/raw/master/COPYING",
                      "COPYING3": "https://gitlab.freedesktop.org/poppler/poppler/-/raw/master/COPYING3"}.items():
        try:
            save(f"poppler/licenses/{name}", get(url))
        except (urllib.error.URLError, OSError) as e:
            log(f"   {name}: {e}")
    for name in ("recipe/meta.yaml", "recipe/recipe.yaml", "recipe/conda_build_config.yaml"):
        try:
            save("poppler/recipe/" + name.split("/")[1], get("https://raw.githubusercontent.com/conda-forge/poppler-feedstock/main/" + name))
        except (urllib.error.URLError, OSError) as e:
            log(f"   {name}: {e}")


# ------------------------------------------------------------------------------------------------------------------
LIBS = ["HEVC encoder", "x265", "kvazaar", "x264", "x265", "xvid", "libvpx", "vp8", "vp9", "aom", "lame", "opus", "vorbis", "theora", "speex", "openjpeg", "libwebp",
        "libass", "freetype", "fribidi", "harfbuzz", "vmaf", "vidstab", "zimg", "rubberband", "libsrt", "srt ", "libssh", "gnutls",
        "gmp", "zeromq", "libzmq", "sdl", "openal", "game-music", "gme", "gsm", "opencore", "amrwb", "openmpt", "cairo", "fontconfig",
        "libxml", "libiconv", "bzip2", "xz ", "liblzma", "zlib", "avisynth", "libvpl", "oneVPL", "nettle", "hogweed", "libpng",
        "expat", "pixman", "libunistring", "p11-kit", "libtasn1", "brotli", "fftw", "libsamplerate", "libsndfile", "ffnvcodec", "libwebp", "WebP", "libopenjp", "OpenJPEG", "zimg", "vid.stab", "libass", "ASS", "pixman", "libxml2", "SDL"]
VER_RE = re.compile(r"\d+\.\d+|core \d+|\br\d{3,}\b")


@step("4. FFmpeg: library versions from the strings inside ffmpeg.exe")
def ffmpeg():
    exe = PAYLOAD / "tools" / "ffmpeg" / "ffmpeg.exe"
    if not exe.is_file():
        log(f"   {exe} does not exist")
        return
    found = {k: [] for k in LIBS}
    seen = set()
    srcdirs = set()
    dir_re = re.compile(r"(?<![\w.+-])([A-Za-z][\w.+]*(?:-[A-Za-z][\w.+]*)*)-(\d+(?:\.\d+)+)/")
    string_re = re.compile(rb"[ -~]{6,200}")
    kw = [(k, k.lower()) for k in LIBS]
    with open(exe, "rb") as f:
        tail = b""
        while True:
            chunk = f.read(32 * 1024 * 1024)
            if not chunk:
                break
            data = tail + chunk
            tail = data[-300:]
            for m in string_re.finditer(data):
                s = m.group().decode("ascii", "ignore")
                if "/" in s and "-" in s:                     # paths of source files: ../../lame-3.100/libmp3lame/...
                    for dm in dir_re.finditer(s):
                        srcdirs.add(dm.group(1) + " " + dm.group(2))
                if not VER_RE.search(s) or s in seen:
                    continue
                low = s.lower()
                for k, kl in kw:
                    if kl in low and len(found[k]) < 6:
                        found[k].append(s)
                        seen.add(s)
                        break
    out = []
    for k in LIBS:
        if found[k]:
            out.append(f"[{k}]")
            out += ["    " + s for s in found[k]]
    save("ffmpeg/strings_versions.txt", "\n".join(out))
    save("ffmpeg/source_dirs.txt", "\n".join(sorted(srcdirs)))
    log(f"   {sum(1 for k in LIBS if found[k])} libraries with a version-like string")
    try:
        r = subprocess.run([str(exe), "-hide_banner", "-version"], capture_output=True, text=True, timeout=60)
        save("ffmpeg/version.txt", r.stdout + r.stderr)
        for sub in ("-hide_banner -encoders", "-hide_banner -filters"):
            r = subprocess.run([str(exe)] + sub.split(), capture_output=True, text=True, timeout=60)
            save(f"ffmpeg/{sub.split()[-1][1:]}.txt", r.stdout)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"   ffmpeg could not be run: {e}")
    for name, url in {"gyan_builds_page.html": "https://www.gyan.dev/ffmpeg/builds/",
                      "gyan_release_page.html": "https://github.com/GyanD/codexffmpeg/releases/latest"}.items():
        try:
            save("ffmpeg/" + name, get(url))
        except (urllib.error.URLError, OSError) as e:
            log(f"   {name}: {e}")


# ------------------------------------------------------------------------------------------------------------------
JXL_LIBS = {"brotli": "google/brotli", "highway": "google/highway", "lcms": "mm2/Little-CMS",
            "libjpeg-turbo": "libjpeg-turbo/libjpeg-turbo", "libpng": "pnggroup/libpng", "sjpeg": "webmproject/sjpeg",
            "skcms": "google/skcms", "zlib": "madler/zlib"}


@step("5. libjxl: licence files of the libraries it contains (downloaded from their repositories)")
def libjxl():
    for name, repo in JXL_LIBS.items():
        try:
            z = zipfile.ZipFile(io.BytesIO(get(f"https://github.com/{repo}/archive/HEAD.zip", timeout=180)))
        except (urllib.error.URLError, OSError) as e:
            log(f"   {name} ({repo}): {e}")
            continue
        n = 0
        for info in z.infolist():
            parts = info.filename.split("/")
            if info.is_dir() or len(parts) > 3 or info.file_size > 500_000:
                continue
            base = parts[-1]
            if LICENSE_RE.match(Path(base).stem) or LICENSE_RE.match(base):
                save(f"libjxl_third_party/{name}/" + "/".join(parts[1:]), z.read(info))
                n += 1
        log(f"   {name}: {n} licence file(s)")
    try:                                                     # lives inside the libjxl repository itself
        listing = json.loads(get("https://api.github.com/repos/libjxl/libjxl/contents/third_party/HEVCSoftware"))
        for it in listing:
            if it.get("type") == "file" and LICENSE_RE.match(Path(it["name"]).stem):
                save("libjxl_third_party/HEVCSoftware/" + it["name"], get(it["download_url"]))
    except (urllib.error.URLError, OSError, ValueError) as e:
        log(f"   HEVCSoftware: {e}")


@step("6. What is in the tools folder, versions.json")
def inventory():
    t = PAYLOAD / "tools"
    if (t / "versions.json").is_file():
        save("versions.json", (t / "versions.json").read_bytes())
    save("payload_tree.txt", "\n".join(str(p.relative_to(PAYLOAD)) for p in sorted(PAYLOAD.glob("*")) ) + "\n\n" +
         "\n".join(str(p.relative_to(PAYLOAD)) for p in sorted((PAYLOAD / "tools").glob("*")) ) if t.is_dir() else "no tools folder")


def main():
    if not PAYLOAD.is_dir():
        sys.exit(f"Folder not found: {PAYLOAD}\nRun build_installer.bat first, or pass the payload folder as an argument.")
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    for fn in (python_packages, tesseract, poppler, ffmpeg, libjxl, inventory):
        fn()
    save("log.txt", "\n".join(LOG))
    ZIP.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(OUT.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(OUT))
    shutil.rmtree(OUT, ignore_errors=True)
    print(f"\nDone. Send me this file: {ZIP}")


if __name__ == "__main__":
    main()
