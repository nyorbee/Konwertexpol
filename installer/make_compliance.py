# -*- coding: utf-8 -*-
"""Licence compliance step of the installer build (called by build_installer.bat after the tools were bundled).

    python make_compliance.py <payload folder> <output folder> [--no-download]

It makes three things:
  1. payload\\licenses\\<tool>\\   - every LICENSE / COPYING / NOTICE file found in the bundled tools,
  2. payload\\SOURCES.txt          - what is bundled (exact versions), under which licence, where the corresponding source is,
                                    and the written offer for the source (GPLv3 section 6(b) / AGPLv3 section 6(b)),
  3. payload\\sources\\ (installed with the program) and the source archives of
                                    the GPL / AGPL / LGPL / MPL components that could be downloaded. The folder travels inside
                                    Setup.exe.
Whatever cannot be downloaded automatically is listed at the end - fetch it by hand or fix the URL in SOURCE_URLS."""
import hashlib
import json
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ffmpeg_libs  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO_URL = "https://github.com/nyorbee/Konwertexpol"
UA = {"User-Agent": "Konwertexpol-build"}
OFFER_YEARS = 3

# programs bundled in tools\<dir>:   key in versions.json -> (label, licence, how to get the source)
PROGRAMS = {
    "ffmpeg": ("FFmpeg (Gyan.dev 'essentials' build)", "GPLv3",
               {"urls": ["https://ffmpeg.org/releases/ffmpeg-{ver}.tar.xz",
                         "https://github.com/FFmpeg/FFmpeg/archive/refs/tags/n{ver}.tar.gz"],      # official mirror
                "page": "https://github.com/GyanD/codexffmpeg/releases/tag/{ver}",
                "also": "https://www.gyan.dev/ffmpeg/builds/ (build page: lists the libraries compiled into the build)",
                "copyleft": True}),
    "pandoc": ("Pandoc", "GPL v2 or later",
               {"urls": ["https://github.com/jgm/pandoc/archive/refs/tags/{ver}.tar.gz"],
                "page": "https://github.com/jgm/pandoc/releases/tag/{ver}", "copyleft": True}),
    "poppler": ("Poppler (oschwartz10612 Windows build)", "GPL (the Windows build also bundles other libraries)",
                {"urls": ["https://poppler.freedesktop.org/poppler-{ver}.tar.xz"],
                 "page": "https://github.com/oschwartz10612/poppler-windows/releases",
                 "also": "https://github.com/oschwartz10612/poppler-windows (how the Windows build was made)",
                 "copyleft": True}),
    "ghostscript": ("Ghostscript", "AGPLv3 (or Artifex commercial licence)",
                    {"urls": ["https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/download/gs{ver}/"
                              "ghostscript-{gsver}.tar.gz"],
                     "page": "https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/tag/gs{ver}",
                     "copyleft": True}),
    "imagemagick": ("ImageMagick", "ImageMagick License (permissive)", {"copyleft": False}),
    "qpdf": ("qpdf", "Apache-2.0", {"copyleft": False}),
    "libjxl": ("libjxl", "BSD-3-Clause", {"copyleft": False}),
    "tesseract": ("Tesseract OCR (UB-Mannheim build)", "Apache-2.0 (bundled DLLs have their own licences)",
                  {"copyleft": False}),
}
# Python packages with a copyleft licence: name (normalised) -> licence. Their source distribution is fetched from PyPI.
COPYLEFT_PY = {
    "pymupdf": "AGPL-3.0 (or Artifex commercial licence)",
    "fpdf2": "LGPL-3.0",
    "img2pdf": "LGPL-3.0",
    "pikepdf": "MPL-2.0",
    "ocrmypdf": "MPL-2.0",
}
LICENSE_NAMES = re.compile(r"^(licen[sc]e|copying|copyright|notice|authors|unlicense)([._-].*)?$", re.I)


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def app_version():
    try:
        m = re.search(r'#define\s+AppVersion\s+"([^"]+)"', (HERE / "Konwertexpol.iss").read_text(encoding="utf-8-sig"))
        return m.group(1) if m else "x"
    except OSError:
        return "x"


def ffmpeg_commit(ver):
    """The FFmpeg commit that Gyan built, or None."""
    import licensing
    return licensing.ffmpeg_commit(ver)


def gs_version(tag):
    """gs10080 -> 10.08.0 (the name used in the source archive)."""
    d = re.sub(r"\D", "", tag)
    if len(d) < 4:
        return d
    patch, minor, major = d[-1], d[-3:-1], d[:-3]
    return f"{major}.{int(minor) if minor == '00' else minor}.{patch}"


def read_versions(tools):
    try:
        return json.loads((tools / "versions.json").read_text(encoding="utf-8")).get("versions", {})
    except (OSError, ValueError):
        return {}


def collect_licenses(payload, tools):
    """Copies licence files of every bundled tool into payload\\licenses\\<tool>. Returns {tool: number of files}."""
    out = payload / "licenses"
    shutil.rmtree(out, ignore_errors=True)
    counts = {}
    for key in PROGRAMS:
        src = tools / key
        if not src.is_dir():
            continue
        n = 0
        for p in sorted(src.rglob("*")):
            if not p.is_file() or len(p.relative_to(src).parts) > 4 or p.stat().st_size > 2_000_000:
                continue
            if LICENSE_NAMES.match(p.stem) or LICENSE_NAMES.match(p.name):
                dst = out / key / p.relative_to(src)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                n += 1
        counts[key] = n
    # Python: the licence file of the interpreter that is really bundled (payload\\python\\LICENSE.txt) comes first
    for name in ("LICENSE.txt", "LICENSE"):
        src_py = payload / "python" / name
        if src_py.is_file():
            dst = out / "python" / "LICENSE.txt"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_py, dst)
            counts["python"] = counts.get("python", 0) + 1
            break
    # licence texts kept in the repository (installer\\extra_licenses\\<name>): used where the downloaded package has none
    # (qpdf, libjxl) and for programs that are not downloaded as tools (7-Zip, Python)
    extra = HERE / "extra_licenses"
    for d in sorted(extra.iterdir()) if extra.is_dir() else []:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if p.is_file():
                dst = out / d.name / p.relative_to(d)
                if dst.exists():
                    continue                            # the package brought its own file of that name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                counts[d.name] = counts.get(d.name, 0) + 1
    return counts


def python_packages(tools):
    """[(name, version, licence text from the metadata)] of the Python packages in tools\\python."""
    res = []
    base = tools / "python"
    for d in sorted(base.glob("*.dist-info")) if base.is_dir() else []:
        meta = d / "METADATA"
        try:
            text = meta.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        name = re.search(r"^Name:\s*(.+)$", text, re.M)
        ver = re.search(r"^Version:\s*(.+)$", text, re.M)
        lic = re.search(r"^License-Expression:\s*(.+)$", text, re.M) or re.search(r"^License:\s*(.+)$", text, re.M)
        lic_text = lic.group(1).strip()[:80] if lic else ""
        if not lic_text or lic_text.upper() == "UNKNOWN":            # fall back to the trove classifier
            cl = re.findall(r"^Classifier:\s*License :: (?:OSI Approved :: )?(.+)$", text, re.M)
            lic_text = "; ".join(cl)[:80] if cl else "see the package"
        res.append((name.group(1).strip() if name else d.name, ver.group(1).strip() if ver else "?", lic_text))
    return res


def fetch(url, dest, timeout=60, tries=4):
    """Downloads url into dest (retries; on Windows a certificate Python does not trust is handled by curl.exe like in a browser)."""
    import licensing
    licensing.fetch(url, dest, timeout=timeout, tries=tries, log=print)


def pypi_sdist(name, version):
    """(url, file name) of the source distribution of name==version on PyPI, or None."""
    import licensing
    return licensing.pypi_sdist(name, version)


def ffmpeg_configuration(tools):
    """The 'configuration:' line of the bundled ffmpeg (lists every library compiled in), or ''."""
    exe = next(iter((tools / "ffmpeg").rglob("ffmpeg.exe")), None)
    if not exe:
        return ""
    try:
        out = subprocess.run([str(exe), "-version"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"^configuration:\s*(.+)$", out, re.M)
    return m.group(1).strip() if m else ""


MAIN_EXE = {"ffmpeg": "ffmpeg.exe", "imagemagick": "magick.exe", "pandoc": "pandoc.exe", "poppler": "pdftotext.exe",
            "ghostscript": "gswin64c.exe", "qpdf": "qpdf.exe", "libjxl": "cjxl.exe", "tesseract": "tesseract.exe"}


def exe_sha256(tools, key):
    """(relative path, sha256) of the main executable of a bundled tool - identifies the exact build."""
    exe = next(iter((tools / key).rglob(MAIN_EXE[key])), None) if key in MAIN_EXE else None
    if not exe:
        return None
    h = hashlib.sha256()
    with open(exe, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return exe.relative_to(tools).as_posix(), h.hexdigest()


def save_ffmpeg_buildconf(payload, tools):
    """Writes the output of 'ffmpeg -version' and 'ffmpeg -buildconf' to licenses\\ffmpeg\\ (what is compiled into the build)."""
    exe = next(iter((tools / "ffmpeg").rglob("ffmpeg.exe")), None)
    if not exe:
        return False
    try:
        text = ""
        for opt in ("-version", "-buildconf"):
            text += f"$ ffmpeg {opt}\n" + subprocess.run([str(exe), opt], capture_output=True, text=True,
                                                           timeout=30).stdout + "\n"
    except (OSError, subprocess.SubprocessError):
        return False
    d = payload / "licenses" / "ffmpeg"
    d.mkdir(parents=True, exist_ok=True)
    (d / "buildconf.txt").write_text(text, encoding="utf-8")
    return True


def download_first(urls, work, prefix):
    """Tries the alternative addresses one by one; the file is saved as <prefix><name>. Returns (name or None, [errors])."""
    errors = []
    for url in urls:
        dest = work / (prefix + url.rsplit("/", 1)[1])
        try:
            print(f"  downloading {url}")
            fetch(url, dest)
            return dest.name, []
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            errors.append(f"{url}  ({e})")
            dest.unlink(missing_ok=True)
    return None, errors


def dll_names(folder):
    return sorted({p.name for p in Path(folder).rglob("*.dll")}, key=str.lower) if Path(folder).is_dir() else []


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__)
        return 2
    payload, outdir = Path(args[0]), Path(args[1])
    download = "--no-download" not in argv
    tools = payload / "tools"
    ver_app = app_version()
    outdir.mkdir(parents=True, exist_ok=True)
    versions = read_versions(tools)
    lic_counts = collect_licenses(payload, tools)
    nolic = [PROGRAMS[k][0] for k in PROGRAMS if (tools / k).is_dir() and not lic_counts.get(k)]
    work = outdir / "_sources_work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    src_in_app = payload / "sources"                  # the source archives are installed together with the program
    shutil.rmtree(src_in_app, ignore_errors=True)

    lines = [
        "KONWERTEXPOL - THIRD-PARTY SOFTWARE, LICENCES AND SOURCE CODE",
        "=" * 62,
        f"Konwertexpol {ver_app} itself is under the MIT licence (file LICENSE). This package also contains programs written",
        "by other people. They are separate programs that keep their own licences; Konwertexpol runs most of them as",
        "separate processes. The licence texts are in the folder 'licenses' (and inside the tools folders).",
        "",
        "SOURCE CODE (GPL / AGPL / LGPL / MPL components)",
        "  The corresponding source of the components marked [source] below is INCLUDED in this installation: the folder",
        "  'sources' next to this file contains the source archives of the exact versions that were installed.",
        f"  Releases and source code: {REPO_URL}/releases",
        f"  If something is missing or a link no longer works, open an issue at {REPO_URL}/issues and the source will be",
        f"  provided to you free of charge; this offer is valid for at least {OFFER_YEARS} years from the release date",
        "  (GPLv3 section 6(b) / AGPLv3 section 6(b)).",
        "",
        "UPDATING THE TOOLS",
        "  This file, the folder 'licenses' and the folder 'sources' describe the versions that this installer installed.",
        "  The 'Update tools' command (update_tools.bat / the button in the program) downloads newer versions of the tools",
        "  directly from their authors' official download pages. For every updated tool the program replaces its licence files",
        "  in the folder 'licenses' and (for the copyleft ones: FFmpeg, Pandoc, Poppler, Ghostscript, Python modules) its",
        "  source archive in the folder 'sources' by those of the new version, and adds a section 'UPDATED AFTER INSTALLATION'",
        "  at the end of this file. So these files always describe the versions that are really installed.",
        "  If a source archive cannot be downloaded, that section says so and gives the address.",
        "",
        "PROGRAMS (folder tools)",
        "-" * 62,
    ]
    missing = []
    for n_ in nolic:
        missing.append(f"{n_}: no licence file (LICENSE / COPYING / NOTICE) was found in its folder in tools - add its licence text to payload\\licenses\\<folder> by hand")
    got = []

    for key, (label, lic, info) in PROGRAMS.items():
        ver = versions.get(key)
        present = (tools / key).is_dir()
        if not present:
            continue
        lines.append(f"* {label}  {ver or '(version unknown)'}")
        lines.append(f"    licence: {lic}")
        lines.append(f"    licence files: licenses\\{key}\\ ({lic_counts.get(key, 0)} file(s))")
        sha = exe_sha256(tools, key)
        if sha:
            lines.append(f"    SHA-256 of tools\\{sha[0]}: {sha[1]}")
        if info.get("copyleft"):
            fmt = {"ver": ver or "", "gsver": gs_version(ver or "") if key == "ghostscript" else ""}
            urls = list(info.get("urls", []))
            if key == "ffmpeg" and ver:                      # Gyan builds from a given commit, not always from the release tarball
                commit = ffmpeg_commit(ver)
                if commit:
                    urls.insert(0, f"https://github.com/FFmpeg/FFmpeg/archive/{commit}.tar.gz")
                    lines.append(f"    built from FFmpeg commit {commit} (according to the release page of the build)")
            for k, u in enumerate(urls):
                lines.append(("    [source] " if k == 0 else "    or       ") + (u.format(**fmt) if ver else "(see below)"))
            if not urls:
                lines.append("    [source] (see below)")
            if info.get("page"):
                lines.append("    release page: " + info["page"].format(**fmt))
            if info.get("also"):
                lines.append("    see also: " + info["also"])
            if download and ver:
                errors = []
                for u in urls:                  # the urls are alternatives: the first one that works is enough
                    url = u.format(**fmt)
                    dest = work / url.rsplit("/", 1)[1]
                    try:
                        print(f"  downloading {url}")
                        fetch(url, dest)
                        got.append(dest.name)
                        errors = []
                        break
                    except (urllib.error.URLError, OSError, TimeoutError) as e:
                        errors.append(f"{url}  ({e})")
                        dest.unlink(missing_ok=True)
                if errors:
                    missing.append(f"{label}: could not download - " + " | ".join(errors))
            elif not ver:
                missing.append(f"{label}: version not recorded in tools\\versions.json - add the source by hand")
        if key == "ffmpeg":
            if save_ffmpeg_buildconf(payload, tools):
                lines.append("    full 'ffmpeg -version' and 'ffmpeg -buildconf': licenses\\ffmpeg\\buildconf.txt")
            else:
                missing.append("FFmpeg: could not run ffmpeg -buildconf (run make_compliance.py on Windows)")
            exe = next(iter((tools / "ffmpeg").rglob("ffmpeg.exe")), None)
            libs = {}
            if exe:
                try:
                    libs = ffmpeg_libs.scan_versions(exe)
                except OSError as e:
                    missing.append(f"FFmpeg: the binary could not be scanned for library versions ({e})")
            lines.append("    libraries compiled into this FFmpeg build (versions read from the binary itself):")
            for lk in sorted(libs):
                lab, lic_, _, _ = ffmpeg_libs.LIBS[lk]
                urls_ = ffmpeg_libs.source_urls(lk, libs[lk])
                lines.append(f"      - {lab} {libs[lk]}  ({lic_})")
                for k_, u_ in enumerate(urls_):
                    lines.append(("          source: " if k_ == 0 else "          or:     ") + u_)
                if download:
                    name_, errs_ = download_first(urls_, work, f"ffmpeg-lib-{lk}-")
                    if name_:
                        got.append(name_)
                        lines.append(f"          included: sources\\{name_}")
                    else:
                        missing.append(f"FFmpeg library {lab} {libs[lk]}: could not download - " + " | ".join(errs_))
            bc = payload / "licenses" / "ffmpeg" / "buildconf.txt"
            flags = ffmpeg_libs.enabled_flags(bc.read_text(encoding="utf-8", errors="replace")) if bc.is_file() else []
            rest = ffmpeg_libs.undetected(libs, flags)
            if rest:
                lines.append("    compiled in, but the version could not be read from the binary (source NOT included; the build's")
                lines.append("    authors and the library projects publish them; available on request - see the offer above):")
                lines.append("      " + ", ".join(rest))
                missing.append("FFmpeg libraries without a detected version (add their sources by hand or accept the written offer): "
                               + ", ".join(rest))
            cfg = ffmpeg_configuration(tools)
            if cfg:
                lines.append("    build configuration (every library compiled in is listed here; its source is part of the")
                lines.append("    corresponding source - see the build page above):")
                for i in range(0, len(cfg), 110):
                    lines.append("      " + cfg[i:i + 110])
            else:
                missing.append("FFmpeg: could not read the build configuration (run make_compliance.py on Windows)")
        if key in ("poppler", "tesseract") and (tools / key).is_dir():
            dl = dll_names(tools / key)
            origin = {"poppler": "repacked conda-forge packages (https://conda-forge.org; recipes: https://github.com/conda-forge/<name>-feedstock)",
                      "tesseract": "MSYS2 / MinGW packages (https://packages.msys2.org; recipes: https://github.com/msys2/MINGW-packages)"}[key]
            lines.append(f"    libraries bundled in this build ({len(dl)} DLL files), built from {origin}:")
            for i in range(0, len(dl), 6):
                lines.append("      " + ", ".join(dl[i:i + 6]))
            lines.append("    The sources of these libraries are NOT included in this package (see the written offer above).")
            missing.append(f"{PROGRAMS[key][0]}: sources of the {len(dl)} bundled libraries are not included (list is in SOURCES.txt)")
        lines.append("")

    lines += ["PYTHON AND ITS PACKAGES (folders python and tools\\python)", "-" * 62,
              "* Python (PSF License) - permissive; licence text: licenses\\python\\LICENSE.txt"]
    cvdll = sorted((tools / "python").glob("cv2/opencv_videoio_ffmpeg*.dll")) if (tools / "python").is_dir() else []
    for d_ in cvdll:
        lines += [f"* {d_.name} (inside opencv-python-headless, folder tools\\python\\cv2) - an FFmpeg build made by the OpenCV project,",
                  "    LGPL-2.1+. Source and build scripts: https://github.com/opencv/opencv-python and",
                  "    https://github.com/opencv/opencv_3rdparty (branches 'ffmpeg/...'); FFmpeg itself: https://ffmpeg.org/releases/",
                  "    The DLL is a separate file that can be replaced. Its source is NOT included in this package (see the written offer)."]
        missing.append(f"{d_.name}: source of OpenCV's FFmpeg build is not included (listed in SOURCES.txt with addresses)")
    pkgs = python_packages(tools)
    seen = set()
    for name, ver, lic in pkgs:
        key = norm(name)
        if key in COPYLEFT_PY:
            seen.add(key)
            lines.append(f"* {name} {ver}  - {COPYLEFT_PY[key]}")
            if download:
                try:
                    found = pypi_sdist(name, ver)
                    if found:
                        url, fname = found
                        print(f"  downloading {url}")
                        fetch(url, work / fname, timeout=120)
                        got.append(fname)
                        lines.append(f"    [source] {url}")
                    else:
                        missing.append(f"{name} {ver}: no source distribution on PyPI")
                        lines.append(f"    [source] https://pypi.org/project/{name}/{ver}/#files")
                except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
                    missing.append(f"{name} {ver}: {e}")
                    lines.append(f"    [source] https://pypi.org/project/{name}/{ver}/#files")
            else:
                lines.append(f"    [source] https://pypi.org/project/{name}/{ver}/#files")
    for key, lic in COPYLEFT_PY.items():
        if key not in seen and pkgs:
            lines.append(f"  (not installed in this build: {key})")
    unknown = sorted(f"{n} {v}" for n, v, l in pkgs
                     if norm(n) not in COPYLEFT_PY and (l.lower() in ("see the package", "unknown", "") or "see " in l.lower()))
    others = sorted({f"{n} ({l})" for n, v, l in pkgs if norm(n) not in COPYLEFT_PY and f"{n} {v}" not in unknown})
    lines += ["* all other Python packages with a licence stated in their metadata (each folder 'name-version.dist-info'",
              "  contains its licence text):", "  " + (", ".join(others) if others else "(none found)"), ""]
    if unknown:
        lines += ["* LICENCE NOT STATED IN THE PACKAGE METADATA - check the project page on PyPI before you publish:",
                  "  " + ", ".join(unknown), ""]
        missing.append("Python packages without a licence in their metadata, check by hand: " + ", ".join(unknown))

    lines += ["7-ZIP (7zr.exe in the tools folder, used to unpack downloaded archives)", "-" * 62,
              "* licence: GNU LGPL (licence text: licenses\\7zip\\LICENSE.txt); source: https://www.7-zip.org/ ", ""]

    (payload / "SOURCES.txt").write_text("\n".join(lines), encoding="utf-8-sig")

    src_in_app.mkdir(parents=True, exist_ok=True)
    for p in sorted(work.iterdir()):                                    # the sources travel INSIDE Setup.exe
        shutil.copy2(p, src_in_app / p.name)
    shutil.rmtree(work, ignore_errors=True)
    todo = outdir / "TO_DO_BY_HAND.txt"
    todo.unlink(missing_ok=True)
    if missing:
        todo.write_text(
            "TO DO BY HAND before you publish Konwertexpol_Setup.exe\n"
            "=======================================================\n"
            "Put the source archives below into the folder  installer\\payload\\sources  (they could not be fetched\n"
            "automatically) and compile the installer again. They then end up in the Setup.exe and are installed into\n"
            "Program Files\\Konwertexpol\\sources. No separate file is needed.\n\n" + "\n".join("- " + m for m in missing) + "\n",
            encoding="utf-8-sig")

    print()
    print(f"licence files collected: {sum(lic_counts.values())} ({len(lic_counts)} tools)")
    print(f"SOURCES.txt written: {payload / 'SOURCES.txt'}")
    print(f"source archives: {len(got)} -> {src_in_app}  (installed with the program)")
    if missing:
        print("\nTO DO BY HAND (could not be fetched automatically):")
        for m in missing:
            print("  - " + m)
        print("The same list is saved in: " + str(outdir / "TO_DO_BY_HAND.txt"))
        print("Put those archives into " + str(src_in_app) + " and compile the installer again.")
        return 1                                    # a non-zero exit code: the build script shows a warning
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
