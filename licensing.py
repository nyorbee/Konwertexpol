# -*- coding: utf-8 -*-
r"""Licence texts and source code of the bundled third-party programs.

Used by tools_manager.py after "Update tools": the licence files and the source archive of the NEW version replace those of
the old one in the SAME places the installer uses - <app>\licenses\<tool>, <app>\sources and a section at the end of
<app>\SOURCES.txt - so there is only one set of files and no contradiction between them.
It never raises into the program: a failure only means the licence record could not be refreshed."""
import json
import re
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = {"User-Agent": "Konwertexpol"}

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
# Python packages with a copyleft licence: name (normalised) -> licence. Their source distribution comes from PyPI.
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


def gs_version(tag):
    """gs10080 -> 10.08.0 (the name used in the source archive)."""
    d = re.sub(r"\D", "", tag)
    if len(d) < 4:
        return d
    patch, minor, major = d[-1], d[-3:-1], d[:-3]
    return f"{major}.{int(minor) if minor == '00' else minor}.{patch}"


def ffmpeg_commit(ver):
    """The FFmpeg commit that Gyan built (its release page says 'Source: FFmpeg/FFmpeg@<commit>'), or None."""
    try:
        html = get_text(f"https://github.com/GyanD/codexffmpeg/releases/tag/{ver}")
    except (urllib.error.URLError, OSError, TimeoutError):
        return None
    m = re.search(r"github\.com/FFmpeg/FFmpeg/commit/([0-9a-f]{7,40})", html) or re.search(r"FFmpeg/FFmpeg@([0-9a-f]{7,40})", html)
    return m.group(1) if m else None


def get_text(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        import tools_manager                          # Windows: curl.exe (Schannel) trusts what the browser trusts
        if not (tools_manager._is_cert_error(e) and tools_manager.curl_exe()):
            raise
        with tools_manager._open(url, timeout) as r:
            return r.read().decode("utf-8", "replace")


def source_urls(key, ver):
    """The download addresses (alternatives, in order of preference) of the source of version `ver`."""
    info = PROGRAMS[key][2]
    fmt = {"ver": ver or "", "gsver": gs_version(ver or "") if key == "ghostscript" else ""}
    urls = [u.format(**fmt) for u in info.get("urls", [])]
    if key == "ffmpeg" and ver:                          # Gyan builds from a given commit, not always from the release tarball
        c = ffmpeg_commit(ver)
        if c:
            urls.insert(0, f"https://github.com/FFmpeg/FFmpeg/archive/{c}.tar.gz")
    return urls, (info.get("page") or "").format(**fmt)


def fetch(url, dest, timeout=60, tries=4, log=print):
    """Downloads url into dest; a dropped connection is retried a few times (TLS verification is never switched off)."""
    ctx = ssl.create_default_context()
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f)
            return
        except urllib.error.HTTPError:
            raise                                   # 403 / 404 ...: retrying does not help
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            import tools_manager
            if tools_manager._is_cert_error(e) and tools_manager.curl_exe():
                tools_manager.curl_download(url, dest, timeout=timeout)       # Windows trust store like the browser's
                return
            last = e
            log(f"    connection problem ({e}); try {k + 1}/{tries}")
            time.sleep(2 + 3 * k)
    raise last


def fetch_first(urls, dest_dir, log=print, prefix=""):
    """Tries the alternatives one by one. Returns (file name or None, [error texts])."""
    errors = []
    for url in urls:
        dest = Path(dest_dir) / (prefix + url.rsplit("/", 1)[1])
        try:
            log(f"  downloading {url}")
            fetch(url, dest, log=log)
            return dest.name, []
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            errors.append(f"{url}  ({e})")
            dest.unlink(missing_ok=True)
    return None, errors


def pypi_sdist(name, version):
    """(url, file name) of the source distribution of name==version on PyPI, or None."""
    import tools_manager
    with tools_manager._open(f"https://pypi.org/pypi/{name}/{version}/json", 30) as r:
        data = json.load(r)
    for f in data.get("urls", []):
        if f.get("packagetype") == "sdist":
            return f["url"], f["filename"]
    return None


def copy_license_files(src, dst):
    """Copies LICENSE / COPYING / NOTICE-type files of the folder `src` (at most 4 levels deep) into `dst`. The old licence
    files in `dst` are replaced ONLY if at least one licence file was found (otherwise they stay). Returns the count."""
    src, dst = Path(src), Path(dst)
    tmp = dst.with_name(dst.name + "._new")
    shutil.rmtree(tmp, ignore_errors=True)
    n = 0
    try:
        for p in sorted(src.rglob("*")):
            if not p.is_file() or len(p.relative_to(src).parts) > 4 or p.stat().st_size > 2_000_000:
                continue
            if LICENSE_NAMES.match(p.stem) or LICENSE_NAMES.match(p.name):
                out = tmp / p.relative_to(src)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, out)
                n += 1
        if n:
            shutil.rmtree(dst, ignore_errors=True)
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp.rename(dst)
    except OSError:
        n = 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return n


def python_packages(tools):
    """[(name, version, licence text from the metadata)] of the Python packages in tools\\python."""
    res = []
    base = Path(tools) / "python"
    for d in sorted(base.glob("*.dist-info")) if base.is_dir() else []:
        try:
            text = (d / "METADATA").read_text(encoding="utf-8", errors="replace")
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


# ---------------------------------------------------------------------------------------------------------------
# After "Update tools": the same files the installer installed are brought up to date (one place, no second copy)
# ---------------------------------------------------------------------------------------------------------------
MANIFEST = "manifest.json"                  # sources\manifest.json: which archive belongs to which tool + the entries
BEGIN = "===== UPDATED AFTER INSTALLATION (written by 'Update tools') ====="
END = "===== END OF UPDATED SECTION ====="
HEAD_TEXT = ("The entries below REPLACE the entries of the same programs in the list above: those programs were later updated\n"
             "with 'Update tools'. Licence files of the current versions are in the folder 'licenses', the source code of the\n"
             "current versions in the folder 'sources' (only the current version is kept; the old binaries no longer exist).\n")


def _manifest(base):
    mf = Path(base) / "sources" / MANIFEST
    try:
        return json.loads(mf.read_text(encoding="utf-8")) if mf.is_file() else {}
    except (OSError, ValueError):
        return {}


def _save_manifest(base, data):
    d = Path(base) / "sources"
    d.mkdir(parents=True, exist_ok=True)
    (d / MANIFEST).write_text(json.dumps(data, indent=1), encoding="utf-8")


def _write_section(base):
    """Rewrites the 'updated' section at the end of SOURCES.txt from the entries stored in the manifest."""
    p = Path(base) / "SOURCES.txt"
    entries = _manifest(base).get("_entries", {})
    try:
        text = p.read_text(encoding="utf-8-sig") if p.is_file() else (
            "Konwertexpol - third-party programs, licences and source code\n"
            "==============================================================\n\n")
        if BEGIN in text:
            text = text[:text.index(BEGIN)].rstrip() + "\n"
        sec = ["", BEGIN, HEAD_TEXT]
        for k in sorted(entries):
            sec.append(entries[k])
            sec.append("")
        sec.append(END)
        p.write_text(text.rstrip() + "\n" + "\n".join(sec) + "\n", encoding="utf-8-sig")
    except OSError:
        pass


def _keep_only(base, key, new_name, entry):
    """Remembers `new_name` and the entry text for `key`, deletes the archive of the previous version (it belongs to a binary
    that no longer exists on this computer) and refreshes the section in SOURCES.txt."""
    d = Path(base) / "sources"
    data = _manifest(base)
    old = data.get(key)
    if new_name and old and old != new_name and (d / old).is_file():
        try:
            (d / old).unlink()
        except OSError:
            pass
    if new_name:
        data[key] = new_name
    if entry is not None:
        data.setdefault("_entries", {})[key] = entry
    try:
        _save_manifest(base, data)
    except OSError:
        pass
    _write_section(base)


def _ffmpeg_libs_update(base, tools):
    """Re-reads the library versions of the NEW ffmpeg.exe, fetches their sources and replaces those of the old build.
    A library whose source cannot be downloaded keeps its OLD archive (if any) and the entry says so. Returns text lines."""
    out = []
    try:
        import ffmpeg_libs
        exe = next(iter((Path(tools) / "ffmpeg").rglob("ffmpeg.exe")), None)
        if not exe:
            return ["  libraries inside the build: ffmpeg.exe not found, not re-checked"]
        libs = ffmpeg_libs.scan_versions(exe)
        data = _manifest(base)
        out.append("  libraries compiled into the new build (versions read from the binary):")
        for lk in sorted(libs):
            lab, lic_, _, _ = ffmpeg_libs.LIBS[lk]
            urls = ffmpeg_libs.source_urls(lk, libs[lk])
            out.append(f"    - {lab} {libs[lk]}  ({lic_})")
            out += [("        source: " if i == 0 else "        or:     ") + u for i, u in enumerate(urls)]
            (Path(base) / "sources").mkdir(parents=True, exist_ok=True)
            name, errs = fetch_first(urls, Path(base) / "sources", log=lambda *_: None, prefix=f"ffmpeg-lib-{lk}-")
            if name:
                _keep_only(base, "ffmpeg-lib:" + lk, name, None)
                out.append(f"        included: sources\\{name}")
            else:
                old = data.get("ffmpeg-lib:" + lk)
                out.append("        NOT downloaded" + (f" - the archive of the previous build stays: sources\\{old}" if old else ""))
        for k in [k for k in data if k.startswith("ffmpeg-lib:") and k[11:] not in libs]:   # no longer in this build
            old = data.pop(k)
            try:
                (Path(base) / "sources" / old).unlink()
            except OSError:
                pass
            _save_manifest(base, data)
        try:
            bc = subprocess.run([str(exe), "-buildconf"], capture_output=True, text=True, timeout=30).stdout
            rest = ffmpeg_libs.undetected(libs, ffmpeg_libs.enabled_flags(bc))
            if rest:
                out.append("  compiled in, but their version could not be read (source NOT included): " + ", ".join(rest))
        except (OSError, subprocess.SubprocessError):
            pass
    except Exception as e:                                  # never break an update because of the licence record
        out.append(f"  libraries inside the build: could not be re-checked ({type(e).__name__}: {e})")
    return out


def record_update(tools, key, ver, log=print):
    """A program in tools\\<key> was just updated to `ver`. Returns a message for the user (or None)."""
    tools = Path(tools)
    base = tools.parent
    if key not in PROGRAMS:
        return None
    label, lic, info = PROGRAMS[key]
    n = copy_license_files(tools / key, base / "licenses" / key)
    entry = [f"{label.upper()} - now version {ver} (updated {time.strftime('%Y-%m-%d')})",
             f"  licence: {lic}; licence files: licenses\\{key}\\ ({n} file(s))"]
    ok, name = True, None
    if info.get("copyleft"):
        urls, page = source_urls(key, ver)
        (base / "sources").mkdir(parents=True, exist_ok=True)
        name, errors = fetch_first(urls, base / "sources", log=lambda *_: None) if urls and ver else (None, ["no version"])
        if name:
            entry.append(f"  source code: sources\\{name}")
        else:
            ok = False
            entry.append("  source code: NOT downloaded - get it from: " + (" | ".join(e.split("  (")[0] for e in errors) or "the author"))
        if page:
            entry.append("  release page: " + page)
        if info.get("also"):
            entry.append("  see also: " + info["also"])
    if key == "ffmpeg":
        entry += _ffmpeg_libs_update(base, tools)
    _keep_only(base, key, name, "\n".join(entry))
    if not info.get("copyleft"):
        return f"{label}: licence texts of the new version saved in licenses"
    return (f"{label}: licence texts and source code of the new version saved in licenses and sources" if ok else
            f"{label}: licence texts saved; the source code of the new version could not be downloaded (see SOURCES.txt)")


def record_pymods(tools, log=print):
    """Python modules were just updated: save the source distributions of the copyleft ones. Returns a message or None."""
    tools = Path(tools)
    base = tools.parent
    done, failed = [], []
    for name, ver, lic in python_packages(tools):
        key = norm(name)
        if key not in COPYLEFT_PY:
            continue
        head = f"PYTHON MODULE {name.upper()} - now version {ver} (updated {time.strftime('%Y-%m-%d')})\n  licence: {COPYLEFT_PY[key]}"
        try:
            found = pypi_sdist(name, ver)
            if not found:
                raise RuntimeError("no source distribution on PyPI")
            url, fname = found
            (base / "sources").mkdir(parents=True, exist_ok=True)
            if not (base / "sources" / fname).is_file():
                fetch(url, base / "sources" / fname, timeout=120, log=lambda *_: None)
            _keep_only(base, "py:" + key, fname, head + f"\n  source code: sources\\{fname}")
            done.append(name)
        except (urllib.error.URLError, OSError, TimeoutError, ValueError, RuntimeError) as e:
            _keep_only(base, "py:" + key, None, head + f"\n  source code: NOT downloaded ({e}); get it from https://pypi.org/project/{name}/{ver}/#files")
            failed.append(name)
    if not done and not failed:
        return None
    if failed:
        return "Python modules: licence record updated; some source code could not be downloaded (see SOURCES.txt)"
    return "Python modules: source code of the new versions saved in sources"
