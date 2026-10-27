# -*- coding: utf-8 -*-
"""
Konwertexpol tool manager.

All the tools (ffmpeg, ImageMagick, Pandoc, Poppler, libjxl, qpdf, Ghostscript) live in the folder
  <folder programu>\\tools\\
and that is where the converter looks for them first.

Downloads happen:
    * automatically - only when the tool is NOT in the tools folder,
    * on request ("Update tools" button / update_tools.bat) - when a newer version exists;
        the old version is then replaced straight away. Nothing updates by itself.

Ghostscript and Tesseract OCR only ship as installers (gs#####w64.exe, tesseract-ocr-w64-setup-*.exe). If you drop
one into the tools folder, it will be installed into tools\\ghostscript / tools\\tesseract (without touching the
rest of the system) and used without downloading. OCR language data goes to tools\\tessdata.

Running from the command line:   py -3 tools_manager.py        (downloads / updates everything)
The Python modules (pikepdf, Pillow, pdf2docx) also end up in the tools folder (tools\\python), so the whole tools folder
is self-contained - after a one-time download the program works without internet (after moving it to another
computer the same Python 3.x version must be installed there, because the modules contain compiled parts; otherwise the installer
downloads them again).
Standard library only. The GitHub API is NOT used (it has a limit of 60 requests/h) - versions are
read from the /releases/latest redirect.
"""
import i18n
from i18n import tr
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
TOOLS = BASE / "tools"
STATE = TOOLS / "versions.json"
UA = {"User-Agent": "Konwertexpol"}
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CANCEL_CHECK = lambda: False       # converter.py plugs in a function here that returns True after "Cancel" is pressed


class Cancelled(RuntimeError):
    pass


def _run_cancellable(cmd, timeout=None, **kw):
    """subprocess.run that aborts itself (together with its children) when the user cancels. Returns a CompletedProcess."""
    kw.setdefault("creationflags", CREATE_NO_WINDOW)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    t0 = time.time()
    while True:
        try:
            out, err = p.communicate(timeout=0.4)
            return subprocess.CompletedProcess(cmd, p.returncode, out, err)
        except subprocess.TimeoutExpired:
            if CANCEL_CHECK() or (timeout and time.time() - t0 > timeout):
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True,
                                   creationflags=CREATE_NO_WINDOW)
                p.kill()
                p.communicate()
                if CANCEL_CHECK():
                    raise Cancelled("Cancelled.")
                raise subprocess.TimeoutExpired(cmd, timeout)


# ----------------------------------------------------------------------------
# HTTP / GitHub without the API
# ----------------------------------------------------------------------------
def curl_exe():
    """curl.exe supplied with Windows 10/11. It uses the Windows certificate machinery (Schannel) like a browser: it fetches
    missing root and intermediate certificates by itself, which Python's ssl module does not."""
    p = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
    return str(p) if os.name == "nt" and p.exists() else None


def _is_cert_error(e):
    return isinstance(e, ssl.SSLCertVerificationError) or isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError)


def curl_download(url, dest, timeout=60, poll=None):
    """Downloads url into dest with the Windows curl.exe; returns the final address after redirects. poll(bytes_so_far) is
    called a few times per second and may raise (cancelling)."""
    exe = curl_exe()
    if not exe:
        raise urllib.error.URLError("curl.exe is not available")
    cmd = [exe, "-sS", "-L", "--fail", "--ssl-revoke-best-effort", "--connect-timeout", str(timeout), "--retry", "2",
           "-A", UA["User-Agent"], "-o", str(dest), "-w", "%{url_effective}", url]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=0.3)
                break
            except subprocess.TimeoutExpired:
                if poll:
                    poll(Path(dest).stat().st_size if Path(dest).exists() else 0)
    except BaseException:
        proc.kill()
        proc.communicate()
        raise
    if proc.returncode != 0:
        raise urllib.error.URLError(err.decode("utf-8", "replace").strip() or f"curl.exe error {proc.returncode}")
    return out.decode("utf-8", "replace").strip() or url


def _remote_size(url):
    exe = curl_exe()
    try:
        r = subprocess.run([exe, "-sIL", "--ssl-revoke-best-effort", "-A", UA["User-Agent"], url], capture_output=True,
                           timeout=60, creationflags=CREATE_NO_WINDOW)
        sizes = re.findall(r"(?im)^content-length:\s*(\d+)", r.stdout.decode("latin-1"))
        return int(sizes[-1]) if sizes else 0
    except Exception:
        return 0


class _FileResponse:
    """What _open returns when the download went through curl.exe: the file is read from disk (and deleted afterwards)."""
    def __init__(self, path, url):
        self._path, self._url = path, url
        self.headers = {"Content-Length": str(os.path.getsize(path))}
        self._f = open(path, "rb")

    def read(self, n=-1):
        return self._f.read(n)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._f.close()
        try:
            os.unlink(self._path)
        except OSError:
            pass


def _open(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e:
        if not _is_cert_error(e) or not curl_exe():
            raise
        import tempfile                                   # Python does not trust the certificate: the same as the browser does
        fd, tmp = tempfile.mkstemp(prefix="konwertexpol_", suffix=".tmp")
        os.close(fd)
        try:
            final = curl_download(url, tmp, timeout=timeout, poll=lambda n: CANCEL_CHECK() and (_ for _ in ()).throw(Cancelled("Cancelled.")))
            return _FileResponse(tmp, final)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def http_text(url):
    with _open(url) as r:
        return r.read().decode("utf-8", "replace")


def gh_latest_tag(repo):
    """Tag of the latest release - from the /releases/latest redirect (no API and no API limits)."""
    with _open(f"https://github.com/{repo}/releases/latest") as r:
        url = r.geturl()
    if "/tag/" not in url:
        raise RuntimeError(f"No release found in the repository {repo}.")
    return urllib.parse.unquote(url.rsplit("/tag/", 1)[1])


def gh_asset(repo, tag, pattern):
    """(url, file name) of the first asset matching the regex; the list comes from the HTML expanded_assets."""
    html = http_text(f"https://github.com/{repo}/releases/expanded_assets/{urllib.parse.quote(tag)}")
    for path in dict.fromkeys(re.findall(r'href="(/[^"]+/releases/download/[^"]+)"', html)):
        name = urllib.parse.unquote(path.rsplit("/", 1)[1])
        if re.fullmatch(pattern, name):
            return "https://github.com" + path, name
    raise RuntimeError(f"Release {repo} {tag} has no file matching {pattern}.")


# ----------------------------------------------------------------------------
# Package definitions: latest() -> (version, url, file_name)
# ----------------------------------------------------------------------------
def _latest_ffmpeg():
    # The same builds as on gyan.dev, but from GitHub's fast CDN; the 7z variant is ~3x smaller than the zip.
    repo = "GyanD/codexffmpeg"
    tag = gh_latest_tag(repo)
    url, name = gh_asset(repo, tag, rf"ffmpeg-{re.escape(tag)}-essentials_build\.7z")
    return tag, url, name


def _latest_imagemagick():
    repo = "ImageMagick/ImageMagick"
    tag = gh_latest_tag(repo)
    url, name = gh_asset(repo, tag, rf"ImageMagick-{re.escape(tag)}-portable-Q16-HDRI-x64\.7z")
    return tag, url, name


def _latest_pandoc():
    repo = "jgm/pandoc"
    tag = gh_latest_tag(repo)
    url, name = gh_asset(repo, tag, rf"pandoc-{re.escape(tag)}-windows-x86_64\.zip")
    return tag, url, name


def _latest_poppler():
    repo = "oschwartz10612/poppler-windows"
    tag = gh_latest_tag(repo)
    url, name = gh_asset(repo, tag, r"Release-.*\.zip")
    return re.sub(r"-\d+$", "", tag.lstrip("v")), url, name


def _latest_libjxl():
    repo = "libjxl/libjxl"
    tag = gh_latest_tag(repo)
    # .7z, because .zip uses Deflate64 compression, which Python cannot unpack
    url, name = gh_asset(repo, tag, r"jxl-x64-windows-static\.7z")
    return tag.lstrip("v"), url, name


def _latest_qpdf():
    repo = "qpdf/qpdf"
    tag = gh_latest_tag(repo)                       # e.g. v12.4.2
    url, name = gh_asset(repo, tag, rf"qpdf-{re.escape(tag.lstrip('v'))}-msvc64\.zip")
    return tag.lstrip("v"), url, name


def _latest_gs():
    repo = "ArtifexSoftware/ghostpdl-downloads"     # note: NOT ArtifexBV - that repository returns 404
    tag = gh_latest_tag(repo)                       # e.g. gs10080
    url, name = gh_asset(repo, tag, r"gs\d+w64\.exe")
    return re.sub(r"\D", "", tag), url, name


def _latest_tesseract():
    repo = "UB-Mannheim/tesseract"                  # the usual Windows build of Tesseract OCR
    tag = gh_latest_tag(repo)                       # e.g. v5.4.0.20240606
    url, name = gh_asset(repo, tag, r"tesseract-ocr-w64-setup-.*\.exe")
    return tag.lstrip("v"), url, name


# dir: subfolder in tools; marker: the file by which we recognise the root; up: how many levels up the root is
PACKAGES = {
    "ffmpeg":      dict(label="FFmpeg", dir="ffmpeg", marker="ffmpeg.exe", up=0,
                        exes=["ffmpeg", "ffprobe"], latest=_latest_ffmpeg),
    "imagemagick": dict(label="ImageMagick", dir="imagemagick", marker="magick.exe", up=0,
                        exes=["magick"], latest=_latest_imagemagick),
    "pandoc":      dict(label="Pandoc", dir="pandoc", marker="pandoc.exe", up=0,
                        exes=["pandoc"], latest=_latest_pandoc),
    "poppler":     dict(label="Poppler", dir="poppler", marker="pdftotext.exe", up=2,   # ...\Library\bin\x.exe
                        exes=["pdftotext", "pdftoppm", "pdfimages", "pdfinfo", "pdfunite", "pdftocairo"], latest=_latest_poppler),
    "libjxl":      dict(label="libjxl (JPEG XL)", dir="libjxl", marker="cjxl.exe", up=0,
                        exes=["cjxl", "djxl"], latest=_latest_libjxl),
    "qpdf":        dict(label="qpdf", dir="qpdf", marker="qpdf.exe", up=0,
                        exes=["qpdf"], latest=_latest_qpdf),
    "ghostscript": dict(label="Ghostscript", dir="ghostscript", marker="gswin64c.exe", up=0,
                        exes=["gs"], latest=_latest_gs),
    "tesseract":   dict(label="Tesseract OCR", dir="tesseract", marker="tesseract.exe", up=0,
                        exes=["tesseract"], latest=_latest_tesseract),
}
PKG_OF = {exe: pkg for pkg, spec in PACKAGES.items() for exe in spec["exes"]}
EXE_FILE = {"gs": "gswin64c.exe"}

# how to read the version of a tool that is already there: (exe, arguments, regex)
PROBE = {
    "ffmpeg":      ("ffmpeg", ["-version"], r"ffmpeg version n?(\d+(?:\.\d+)+)"),
    "imagemagick": ("magick", ["-version"], r"ImageMagick (\d+\.\d+\.\d+-\d+)"),
    "pandoc":      ("pandoc", ["--version"], r"pandoc(?:\.exe)? (\d+(?:\.\d+)+)"),
    "poppler":     ("pdftotext", ["-v"], r"version (\d+(?:\.\d+)+)"),
    "libjxl":      ("cjxl", ["--version"], r"v(\d+\.\d+\.\d+)"),
    "qpdf":        ("qpdf", ["--version"], r"qpdf version (\d+\.\d+\.\d+)"),
    "tesseract":   ("tesseract", ["--version"], r"tesseract v?(\d+(?:\.\d+)+)"),
}


# ----------------------------------------------------------------------------
# State / file locations
# ----------------------------------------------------------------------------
def load_state():
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    st.setdefault("versions", {})
    st.setdefault("checked", {})
    return st


def save_state(st):
    try:
        TOOLS.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")
    except OSError:
        pass


_CACHE = {}


NOT_INSTALLED = "[not installed] "     # prefix of the notes about programs that were skipped (not real errors)
USES = {"ghostscript": "shrinking images when you compress a PDF to a size limit, and for OCR",
        "tesseract": "OCR (recognising text in scanned PDFs and images)"}
CONFIRM_INSTALL = None      # function(pkg, label, exe_name) -> "install" | "skip" | full path of an existing exe.
                            # None = never install such programs automatically.


def set_override(name, path):
    """Remembers where an already installed program is (tools\\paths.txt, line `name=path`)."""
    p = TOOLS / "paths.txt"
    try:
        lines = [ln for ln in p.read_text(encoding="utf-8-sig").splitlines()
                 if ln.split("=", 1)[0].strip().lower() != name.lower()]
    except OSError:
        lines = []
    lines.append(f"{name}={path}")
    TOOLS.mkdir(exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _CACHE.pop(name, None)


def _overrides():
    """tools\\paths.txt: one `name=full path` per line (e.g. gs=C:\\gs\\gs10.05.1\\bin\\gswin64c.exe) - a way to
    tell Konwertexpol where an already installed program is."""
    out = {}
    try:
        for line in (TOOLS / "paths.txt").read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                out[k.strip().lower()] = v.strip().strip('"')
    except OSError:
        pass
    return out


def local_path(name):
    """Path to a tool from the tools folder (or None). The result is cached."""
    ov = _overrides().get(name.lower())
    if ov and os.path.isfile(ov):
        return ov
    hit = _CACHE.get(name)
    if hit and os.path.isfile(hit):
        return hit
    pkg = PKG_OF.get(name)
    if not pkg:
        return None
    exe = EXE_FILE.get(name, name + ".exe")
    found = None
    direct = TOOLS / exe                      # someone dropped a bare .exe straight into tools
    if direct.is_file():
        found = str(direct)
    else:
        d = TOOLS / PACKAGES[pkg]["dir"]
        if d.is_dir():
            found = next((str(p) for p in d.rglob(exe)), None)
    if found:
        _CACHE[name] = found
    return found


def is_installed(pkg):
    return all(local_path(e) for e in PACKAGES[pkg]["exes"])


def vkey(v):
    nums = [int(x) for x in re.findall(r"\d+", str(v))]
    return tuple(nums + [0] * (6 - len(nums)))


def gs_installers():
    """Ghostscript installers lying in tools (gs10080w64.exe), newest first."""
    found = []
    if TOOLS.is_dir():
        for p in TOOLS.glob("gs*w64.exe"):
            m = re.fullmatch(r"gs(\d+)w64\.exe", p.name, re.I)
            if m:
                found.append((int(m.group(1)), p))
    return sorted(found, reverse=True)


# Where a normal (system-wide) installation of these programs can live: PATH, the Windows registry (the official
# installers record their folder there), then the usual folders. Used so that a program that is ALREADY installed
# on this computer is never installed a second time.
SYSTEM_EXES = {"ghostscript": ["gswin64c", "gswin32c"], "tesseract": ["tesseract"]}
SYSTEM_REGISTRY = {                       # (key, value) - GS_DLL points to ...\bin\gsdll64.dll, InstallDir to the folder
    "ghostscript": [(r"SOFTWARE\GPL Ghostscript", "GS_DLL"), (r"SOFTWARE\Artifex\GPL Ghostscript", "GS_DLL"),
                    (r"SOFTWARE\Artifex\Ghostscript", "GS_DLL")],
    "tesseract": [(r"SOFTWARE\Tesseract-OCR", "InstallDir")],
}
SYSTEM_PATTERNS = {
    "ghostscript": [r"%ProgramFiles%\gs\gs*\bin\gswin64c.exe", r"%ProgramW6432%\gs\gs*\bin\gswin64c.exe",
                    r"%ProgramFiles(x86)%\gs\gs*\bin\gswin32c.exe", r"%ProgramFiles(x86)%\gs\gs*\bin\gswin64c.exe",
                    r"%LOCALAPPDATA%\Programs\gs\gs*\bin\gswin64c.exe", r"C:\gs\gs*\bin\gswin64c.exe",
                    r"%ProgramData%\chocolatey\bin\gswin64c.exe",
                    r"%USERPROFILE%\scoop\apps\ghostscript\current\bin\gswin64c.exe"],
    "tesseract": [r"%ProgramFiles%\Tesseract-OCR\tesseract.exe", r"%ProgramW6432%\Tesseract-OCR\tesseract.exe",
                  r"%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe", r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe",
                  r"%ProgramData%\chocolatey\bin\tesseract.exe"],
}


def _registry_dirs(pkg):
    """Install folders recorded in the Windows registry by the official installers (all views, HKLM and HKCU)."""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    found = []
    for key_path, value in SYSTEM_REGISTRY.get(pkg, []):
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as k:
                        keys = [k]
                        i = 0
                        while True:                  # Ghostscript keeps one subkey per version
                            try:
                                keys.append(winreg.OpenKey(k, winreg.EnumKey(k, i), 0, winreg.KEY_READ | view))
                            except OSError:
                                break
                            i += 1
                        for kk in keys:
                            try:
                                v = winreg.QueryValueEx(kk, value)[0]
                                found.append(os.path.dirname(v) if value == "GS_DLL" else v)
                            except OSError:
                                pass
                except OSError:
                    continue
    return found


def _first_path(value):
    """First file path inside a registry string (quoted path followed by arguments, or 'path,icon-index')."""
    v = str(value).strip()
    if v.startswith('"') and '"' in v[1:]:
        return v[1:v.index('"', 1)]
    return v.split(",")[0].split(" /")[0].strip().strip('"')


def _uninstall_dirs(pkg):
    """Folders from the 'Apps & features' (uninstall) registry entries whose name mentions the program."""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    out = []
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                root = winreg.OpenKey(hive, base, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with root:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(root, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(root, name, 0, winreg.KEY_READ | view) as k:
                            try:
                                disp = str(winreg.QueryValueEx(k, "DisplayName")[0])
                            except OSError:
                                disp = name
                            if pkg not in disp.lower() and pkg not in name.lower():
                                continue
                            for val in ("InstallLocation", "InstallDir", "DisplayIcon", "UninstallString"):
                                try:
                                    p = _first_path(winreg.QueryValueEx(k, val)[0])
                                except OSError:
                                    continue
                                if p:
                                    out.append(p if os.path.isdir(p) else os.path.dirname(p))
                    except OSError:
                        continue
    return out


def _app_paths(exe_name):
    """HKLM\\...\\App Paths\\<exe>.exe - where Windows itself says the program is."""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    out = []
    key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}.exe"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as k:
                    out.append(str(winreg.QueryValueEx(k, "")[0]).strip('"'))
            except OSError:
                continue
    return out


def _drive_hits(pkg):
    """The usual install folders on EVERY fixed drive (a program installed on D: is still found)."""
    if os.name != "nt":
        return []
    import glob
    import string
    pats = {"ghostscript": [r"{d}:\Program Files\gs\gs*\bin\gswin64c.exe", r"{d}:\Program Files (x86)\gs\gs*\bin\gswin32c.exe",
                            r"{d}:\gs\gs*\bin\gswin64c.exe", r"{d}:\Programs\gs\gs*\bin\gswin64c.exe",
                            r"{d}:\Program Files\gs\gs*\bin\gswin32c.exe"],
            "tesseract": [r"{d}:\Program Files\Tesseract-OCR\tesseract.exe", r"{d}:\Tesseract-OCR\tesseract.exe",
                          r"{d}:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]}.get(pkg, [])
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        drives = [c for i, c in enumerate(string.ascii_uppercase)
                  if mask & (1 << i) and ctypes.windll.kernel32.GetDriveTypeW(f"{c}:\\") == 3]   # 3 = fixed disk
    except Exception:
        drives = ["C"]
    hits = []
    for d in drives:
        for pat in pats:
            hits += glob.glob(pat.format(d=d))
    return hits


def system_install(pkg):
    """Path of a copy of the program installed outside the tools folder, or None. Looks in: PATH, the App Paths and
    vendor registry keys, the uninstall (Apps & features) registry entries, the usual folders, every fixed drive."""
    import glob
    exes = SYSTEM_EXES.get(pkg)
    if not exes:
        return None
    for e in exes:                                   # PATH
        found = shutil.which(e)
        if found:
            return found
    for e in exes:                                   # Windows' own App Paths registration
        for p in _app_paths(e):
            if os.path.isfile(p):
                return p
    for d in _registry_dirs(pkg) + _uninstall_dirs(pkg):          # registry: vendor keys + uninstall entries
        for sub in ("", "bin"):
            for e in exes:
                cand = os.path.join(d, sub, e + ".exe")
                if os.path.isfile(cand):
                    return cand
    for pat in SYSTEM_PATTERNS.get(pkg, []):         # usual folders
        hit = glob.glob(os.path.expandvars(pat))
        if hit:
            return hit[-1]                           # the newest version sorts last
    hits = _drive_hits(pkg)                          # other drives
    return hits[-1] if hits else None


def system_exe(name):
    """Like system_install, by tool name ("gs", "tesseract")."""
    return system_install(PKG_OF.get(name, name))


def tess_installers():
    """Tesseract installers lying in tools (tesseract-ocr-w64-setup-5.4.0.20240606.exe), newest first."""
    found = []
    if TOOLS.is_dir():
        for p in TOOLS.glob("tesseract-ocr-w64-setup-*.exe"):
            m = re.search(r"setup-v?(\d+(?:\.\d+)+)", p.name, re.I)
            if m:
                found.append((m.group(1), p))
    return sorted(found, key=lambda x: vkey(x[0]), reverse=True)


def local_version(pkg, st):
    if pkg == "ghostscript":
        v = st["versions"].get(pkg)
        if v:
            return v
        inst = gs_installers()
        return str(inst[0][0]) if inst else None
    if st["versions"].get(pkg):
        return st["versions"][pkg]
    exe, args, rx = PROBE[pkg]
    path = local_path(exe)
    if not path:
        return None
    try:
        p = subprocess.run([path, *args], capture_output=True, text=True, errors="replace",
                           timeout=20, creationflags=CREATE_NO_WINDOW)
        m = re.search(rx, (p.stdout or "") + (p.stderr or ""))
        return m.group(1) if m else None
    except Exception:
        return None


# ----------------------------------------------------------------------------
# Downloading and installing
# ----------------------------------------------------------------------------
def download(url, dest, say, label):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers=UA, method="HEAD"), timeout=30).close()
    except urllib.error.URLError as e:
        if _is_cert_error(e) and curl_exe():
            total = _remote_size(url)

            def poll(n):
                if CANCEL_CHECK():
                    raise Cancelled("Cancelled.")
                say(f"Downloading {label}: {n / 1048576:.0f}" + (f" / {total / 1048576:.0f}" if total else "") + " MB",
                    n / total if total else None)
            try:
                curl_download(url, part, timeout=60, poll=poll)
            except BaseException:
                part.unlink(missing_ok=True)
                raise
            part.replace(dest)
            return
    except OSError:
        pass
    with _open(url, timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, 0.0
        while True:
            if CANCEL_CHECK():
                raise Cancelled("Cancelled.")
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total and time.time() - last > 0.2:
                last = time.time()
                say(f"Downloading {label}: {done / 1048576:.0f} / {total / 1048576:.0f} MB", done / total)
    part.replace(dest)


def _extract_7z(archive, out, say):
    """7z: first the tar.exe built into Windows (libarchive), as a fallback 7zr.exe from 7-zip.org."""
    tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if tar.exists():
        p = _run_cancellable([str(tar), "-xf", str(archive), "-C", str(out)])
        if p.returncode == 0 and any(out.iterdir()):
            return
    z = TOOLS / "_7zr" / "7zr.exe"
    if not z.exists():
        download("https://www.7-zip.org/a/7zr.exe", z, say, "7zr")
    p = _run_cancellable([str(z), "x", "-y", f"-o{out}", str(archive)])
    if p.returncode != 0:
        raise RuntimeError("Could not unpack the 7z archive.")


def extract(archive, out, say):
    archive = Path(archive)
    if archive.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(archive) as z:
                z.extractall(out)
            return
        except (NotImplementedError, zipfile.BadZipFile):   # e.g. Deflate64 - try tar.exe
            tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
            p = _run_cancellable([str(tar), "-xf", str(archive), "-C", str(out)])
            if p.returncode != 0:
                raise RuntimeError("Unsupported compression in the ZIP archive.")
    else:
        _extract_7z(archive, out, say)


def _swap(new, dest):
    """Replaces the tool folder with the new one. The old one is deleted only after a successful swap;
    if it fails, the old one goes back in place (the tool from the tools folder keeps working)."""
    old = dest.with_name(dest.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if dest.exists():
        try:
            dest.rename(old)
        except OSError as e:
            raise RuntimeError(f"Cannot replace the folder {dest.name} - is the tool in use right now?") from e
    try:
        shutil.move(str(new), str(dest))
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        if old.exists():
            old.rename(dest)
        raise
    shutil.rmtree(old, ignore_errors=True)


def _install_archive(pkg, archive, say):
    spec = PACKAGES[pkg]
    tmp = TOOLS / f"_tmp_{pkg}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        say(f"Unpacking {spec['label']}...", None)
        extract(archive, tmp, say)
        exe = next(tmp.rglob(spec["marker"]), None)
        if not exe:
            raise RuntimeError(f"The archive does not contain {spec['marker']}.")
        root = exe.parent
        for _ in range(spec["up"]):
            root = root.parent
        _swap(root, TOOLS / spec["dir"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _install_nsis(installer, pkg, check_exe, say):
    """Silent installation of an NSIS installer (Ghostscript, Tesseract) into tools\\<package> (one UAC prompt)."""
    spec = PACKAGES[pkg]
    dest = TOOLS / spec["dir"]
    say(f"Installing {spec['label']} into the tools folder (confirm the UAC prompt)...", None)
    inst = str(installer).replace("'", "''")
    ps = (f"Start-Process -FilePath '{inst}' "
          f"-ArgumentList '/S /D={str(dest)}' -Verb RunAs -Wait")        # /D must be last and without quotes
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                   creationflags=CREATE_NO_WINDOW)
    _CACHE.clear()
    if not local_path(check_exe) and not system_install(pkg):
        raise RuntimeError(f"The {spec['label']} installer did not finish successfully (UAC declined?). If it is already "
                           f"installed on this computer, tell Konwertexpol where: add a line such as "
                           f"{check_exe}=C:\\path\\to\\{SYSTEM_EXES[pkg][0]}.exe to tools\\paths.txt")


def _install_gs(installer, say):
    _install_nsis(installer, "ghostscript", "gs", say)


def _install_tesseract(installer, say):
    _install_nsis(installer, "tesseract", "tesseract", say)


INSTALLERS = {"ghostscript": (gs_installers, _install_gs), "tesseract": (tess_installers, _install_tesseract)}


def _install_from_remote(pkg, url, fname, say):
    spec = PACKAGES[pkg]
    _CACHE.clear()
    if pkg == "ghostscript":
        installer = TOOLS / fname                  # the installer stays in tools (also as a copy for later)
        download(url, installer, say, spec["label"])
        _install_gs(installer, say)
        for _, p in gs_installers():               # older installers are not needed
            if p != installer:
                try:
                    p.unlink()
                except OSError:
                    pass
    elif pkg == "tesseract":
        installer = TOOLS / "_dl" / fname
        try:
            download(url, installer, say, spec["label"])
            _install_tesseract(installer, say)
        finally:
            shutil.rmtree(TOOLS / "_dl", ignore_errors=True)
    else:
        archive = TOOLS / "_dl" / fname
        try:
            download(url, archive, say, spec["label"])
            _install_archive(pkg, archive, say)
        finally:
            shutil.rmtree(TOOLS / "_dl", ignore_errors=True)



# ----------------------------------------------------------------------------
# Python modules (pikepdf + Pillow + pdf2docx) - installed into tools\\python (pip --target), next to the other
# tools, without touching the system Python. The names "pikepdf" and "pdf2docx" in ensure() mean the same package.
# ----------------------------------------------------------------------------
PY_DIR = TOOLS / "python"
LEGACY_PIKE_DIR = TOOLS / "pikepdf"            # old layout (pikepdf + Pillow only) - still recognised
PY_MODULES = ["pikepdf", "Pillow", "pdf2docx", "ocrmypdf"]
PY_IMPORTS = ("pikepdf", "PIL", "pdf2docx", "ocrmypdf")     # (dependencies such as PyMuPDF are installed by pip)
PY_NAMES = ("pikepdf", "pdf2docx", "ocrmypdf")
PYVER_FILE = PY_DIR / ".pyver"


def python_paths():
    return [str(d) for d in (PY_DIR, LEGACY_PIKE_DIR) if d.is_dir()]


def use_python_path():
    for d in reversed(python_paths()):
        if d not in sys.path:
            sys.path.insert(0, d)


use_pikepdf_path = use_python_path             # backward compatibility


def python_exe():
    py = sys.executable or "python"
    if py.lower().endswith("pythonw.exe"):
        alt = py[:-11] + "python.exe"
        if os.path.isfile(alt):
            return alt
    return py


_python_exe = python_exe


def module_available(name):
    """Whether the module can be imported - without importing it (an import would lock .pyd files in tools\\python)."""
    import importlib.util
    use_python_path()
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _pyver():
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def pymods_installed():
    """All modules are in tools\\python and match this Python version."""
    use_python_path()
    try:
        if PYVER_FILE.is_file() and PYVER_FILE.read_text().strip() != _pyver():
            return False                       # folder moved from a computer with a different Python
    except OSError:
        pass
    return all(module_available(m) for m in PY_IMPORTS)


pikepdf_installed = pymods_installed           # backward compatibility


def _pymods_version():
    try:
        from importlib.metadata import version
        return f"pikepdf {version('pikepdf')}, pdf2docx {version('pdf2docx')}, ocrmypdf {version('ocrmypdf')}"
    except Exception:
        return None


def _pymods_outdated():
    """True when PyPI has a newer version of one of the main modules. Offline / any error -> False."""
    from importlib.metadata import version
    use_python_path()
    try:
        for name in ("pikepdf", "Pillow", "pdf2docx", "ocrmypdf"):
            have = version(name)
            with _open(f"https://pypi.org/pypi/{name}/json", timeout=15) as r:
                latest = json.loads(r.read().decode("utf-8"))["info"]["version"]
            if vkey(latest) > vkey(have):
                return True
    except Exception:
        return False
    return False


def _install_pymods(say):
    tmp = TOOLS / "_tmp_python"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        say("Downloading Python modules (pikepdf, Pillow, pdf2docx, ocrmypdf) into the tools\\python folder...", None)
        p = _run_cancellable([python_exe(), "-m", "pip", "install", "--quiet", "--upgrade",
                              "--disable-pip-version-check", "--target", str(tmp), *PY_MODULES],
                             timeout=1800, text=True, errors="replace")
        if p.returncode != 0 or not all(any(tmp.glob(m + "*")) for m in ("pikepdf", "pdf2docx", "ocrmypdf")):
            raise RuntimeError((p.stderr or p.stdout or "pip exited with an error").strip()[-300:])
        (tmp / ".pyver").write_text(_pyver())
        _swap(tmp, PY_DIR)
        shutil.rmtree(LEGACY_PIKE_DIR, ignore_errors=True)   # the old layout is no longer needed
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------
# OCR language data (Tesseract "tessdata_fast"), kept in tools\\tessdata independently of the installer's own data
# ----------------------------------------------------------------------------
TESSDATA = TOOLS / "tessdata"
TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{}.traineddata"
_LANG_RX = re.compile(r"[A-Za-z][A-Za-z_]{1,14}")
# Windows UI language (primary language id) -> Tesseract language code
_LANGID = {0x09: "eng", 0x15: "pol", 0x07: "deu", 0x0C: "fra", 0x0A: "spa", 0x10: "ita", 0x16: "por", 0x13: "nld",
           0x19: "rus", 0x22: "ukr", 0x05: "ces", 0x1B: "slk", 0x0E: "hun", 0x1F: "tur", 0x08: "ell", 0x1D: "swe",
           0x14: "nor", 0x06: "dan", 0x0B: "fin", 0x18: "ron", 0x02: "bul", 0x1A: "hrv", 0x24: "slv", 0x04: "chi_sim",
           0x11: "jpn", 0x12: "kor", 0x01: "ara", 0x0D: "heb", 0x39: "hin", 0x1E: "tha", 0x2A: "vie"}


def default_ocr_lang():
    """OCR language(s) matching the Windows UI language, plus English: e.g. 'pol+eng'."""
    code = "eng"
    try:
        import ctypes
        code = _LANGID.get(ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF, "eng")
    except Exception:
        pass
    return code if code == "eng" else f"{code}+eng"


def check_ocr_langs(langs):
    """Validates a string like 'pol+eng'; returns the list of codes or raises ValueError."""
    codes = [c for c in str(langs).replace(",", "+").replace(" ", "+").split("+") if c]
    if not codes or any(not _LANG_RX.fullmatch(c) for c in codes):
        raise ValueError("OCR language: use Tesseract codes joined with +, e.g. eng, pol+eng, deu, chi_sim.")
    return codes


# Tesseract's own config files (the language files alone are not enough: ocrmypdf asks for the "hocr" config).
# Normally copied from the Tesseract installation; these minimal copies are only a fallback.
MIN_CONFIGS = {
    "hocr": "tessedit_create_hocr 1\nhocr_font_info 0\n",
    "txt": "tessedit_create_txt 1\n",
    "pdf": "tessedit_create_pdf 1\n",
    "tsv": "tessedit_create_tsv 1\n",
    "alto": "tessedit_create_alto 1\n",
}


def _ensure_tess_configs(tesseract_exe=None):
    if (TESSDATA / "configs" / "hocr").is_file():
        return
    import glob
    sources = [Path(tesseract_exe).parent / "tessdata"] if tesseract_exe else []
    sources += [Path(p) for p in glob.glob("/usr/share/tesseract-ocr/*/tessdata")]      # Linux / tests
    for src in sources:
        for sub in ("configs", "tessconfigs"):
            if (src / sub).is_dir() and not (TESSDATA / sub).is_dir():
                try:
                    shutil.copytree(src / sub, TESSDATA / sub)
                except OSError:
                    pass
    if not (TESSDATA / "configs" / "hocr").is_file():
        (TESSDATA / "configs").mkdir(parents=True, exist_ok=True)
        for name, body in MIN_CONFIGS.items():
            (TESSDATA / "configs" / name).write_text(body)


def ensure_tessdata(langs, say=None, tesseract_exe=None):
    """Downloads the missing language files into tools\\tessdata (and makes sure Tesseract's config files are
    there too). Returns a list of messages."""
    import urllib.error
    say = say or (lambda msg, frac=None: None)
    msgs = []
    TESSDATA.mkdir(parents=True, exist_ok=True)
    _ensure_tess_configs(tesseract_exe)
    for code in check_ocr_langs(langs):
        dest = TESSDATA / f"{code}.traineddata"
        if dest.is_file() and dest.stat().st_size > 10000:
            continue
        try:
            download(TESSDATA_URL.format(code), dest, say, f"OCR language data ({code})")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise RuntimeError(f"Unknown OCR language '{code}' (see the Tesseract language codes, e.g. eng, pol, deu, fra).") from e
            raise
        msgs.append(f"OCR language data downloaded: {code}")
    return msgs


# ----------------------------------------------------------------------------
# API for converter.py
# ----------------------------------------------------------------------------
def ensure(names, progress=None, force=False):
    """Makes sure the given tools are in tools. Missing ones are always downloaded; existing ones are updated
    only with force=True. Never raises (no internet does not block working with what is already there).
    Returns (messages, errors)."""
    say = progress or (lambda msg, frac=None: None)
    pkgs = list(dict.fromkeys(PKG_OF[n] for n in names if n in PKG_OF))
    st = load_state()
    msgs, errors = [], []
    TOOLS.mkdir(exist_ok=True)
    if any(n in PY_NAMES for n in names):
        have_pk = pymods_installed()
        need_pk = not have_pk or (force and _pymods_outdated())
        if have_pk and force and not need_pk:
            msgs.append("Python modules: up to date")
        if need_pk:
            try:
                before = _pymods_version() if have_pk else None
                _install_pymods(say)
                if have_pk:                                  # an update: keep the licence record in step with the new versions
                    try:
                        import licensing
                        m = licensing.record_pymods(TOOLS)
                        if m:
                            msgs.append(m)
                    except Exception:
                        pass
                msgs.append("Python modules (pikepdf, Pillow, pdf2docx, ocrmypdf): " + ("updated" if have_pk else "downloaded") +
                            (f" (was {before})" if before else ""))
            except Exception as e:
                if have_pk:
                    msgs.append(f"Python modules: could not update ({e}) - using the ones from the tools folder")
                else:
                    errors.append(f"Python modules (pikepdf / pdf2docx / ocrmypdf): {e}")
    for pkg in pkgs:
        if CANCEL_CHECK():
            break
        spec = PACKAGES[pkg]
        have = is_installed(pkg)
        if not have and pkg in INSTALLERS:                # Ghostscript / Tesseract: NEVER installed behind your back
            found = system_install(pkg)
            if "--bundle" in sys.argv:                    # package build: take the copy of THIS PC into tools (no installer, no UAC)
                choice = "install"
                if found:
                    try:
                        exe_p = Path(found)
                        root = exe_p.parent.parent if exe_p.parent.name.lower() == "bin" else exe_p.parent
                        say(f"Copying {spec['label']} from {root} ...", None)
                        dest = TOOLS / spec["dir"]
                        tmpd = TOOLS / f"_tmp_{pkg}"
                        shutil.rmtree(tmpd, ignore_errors=True)
                        shutil.copytree(root, tmpd)
                        _swap(tmpd, dest)
                        _CACHE.clear()
                        if not local_path(spec["exes"][0]):
                            raise RuntimeError("the copy does not contain the program")
                        msgs.append(f"{spec['label']}: copied from {root}")
                        continue
                    except Exception as e:
                        shutil.rmtree(TOOLS / f"_tmp_{pkg}", ignore_errors=True)
                        msgs.append(f"{spec['label']}: copying failed ({e}) - trying the installer")
                    found = None
            if found:
                msgs.append(f"{spec['label']}: already installed on this computer ({found}) — using it")
                continue
            exe = PACKAGES[pkg]["exes"][0]
            exe_file = SYSTEM_EXES[pkg][0] + ".exe"
            if "--bundle" not in sys.argv:
                choice = CONFIRM_INSTALL(pkg, spec["label"], exe_file) if CONFIRM_INSTALL else "skip"
            if choice == "skip" or not choice:
                errors.append(f"{NOT_INSTALLED}{spec['label']} was not found and was not installed (needed for "
                              f"{USES.get(pkg, 'some features')}). You will be asked again when it is needed. If you already "
                              f"have it, add a line such as {exe}=C:\\path\\to\\{exe_file} to tools\\paths.txt.")
                continue
            if choice != "install":                       # the user pointed to an existing copy
                if os.path.isfile(choice):
                    set_override(exe, choice)
                    msgs.append(f"{spec['label']}: using {choice} (remembered in tools\\paths.txt)")
                else:
                    errors.append(f"{spec['label']}: file not found: {choice}")
                continue
        try:
            # an installer dropped into tools by hand (Ghostscript, Tesseract) - install without downloading
            lister, installer_fn = INSTALLERS.get(pkg, (None, None))
            if lister and not have and lister():
                num, inst = lister()[0]
                installer_fn(inst, say)
                st["versions"][pkg] = str(num)
                save_state(st)
                have = True
                msgs.append(f"{spec['label']}: installed from {inst.name}")
        except Exception as e:
            errors.append(f"{spec['label']}: {e}")
            continue                                       # do not try a second installer after this one failed
        if have and not force:        # existing tools are updated ONLY on request (force=True)
            continue
        try:
            say(f"Checking version: {spec['label']}...", None)
            ver, url, fname = spec["latest"]()
        except Exception as e:
            if have:
                msgs.append(f"{spec['label']}: cannot check for a newer version ({e}) - using the one from the tools folder")
            else:
                errors.append(f"{spec['label']}: cannot download ({e})")
            continue
        try:
            if have:
                cur = local_version(pkg, st)
                if cur is None or vkey(cur) >= vkey(ver):
                    st["versions"].setdefault(pkg, cur or ver)
                    st["checked"][pkg] = time.time()
                    save_state(st)
                    msgs.append(f"{spec['label']}: up to date ({cur or ver})")
                    continue
                msgs.append(f"{spec['label']}: updating {cur} → {ver}")
            else:
                msgs.append(f"{spec['label']}: downloaded version {ver}")
            _install_from_remote(pkg, url, fname, say)
            st["versions"][pkg] = ver
            st["checked"][pkg] = time.time()
            save_state(st)
            if True:                                          # every download (update or reinstall): licences + source of this version
                try:
                    import licensing
                    m = licensing.record_update(TOOLS, pkg, ver)
                    if m:
                        msgs.append(m)
                except Exception:
                    pass
        except Exception as e:
            if have:   # a newer version exists but cannot be downloaded - what is in tools stays
                msgs.append(f"{spec['label']}: could not download the newer version ({e}) - using the one from the tools folder")
            else:
                errors.append(f"{spec['label']}: {e}")
    if "tesseract" in pkgs and not CANCEL_CHECK() and is_installed("tesseract"):
        try:                                       # OCR language data for the Windows UI language + English
            msgs.extend(ensure_tessdata(default_ocr_lang(), say, local_path("tesseract")))
        except Exception as e:
            errors.append(f"OCR language data: {e}")
    return msgs, errors


def all_names():
    return list(PKG_OF) + ["pikepdf", "pdf2docx"]


if __name__ == "__main__":
    def _say(msg, frac=None):
        print("\r" + tr(msg).ljust(70), end="" if frac is not None else "\n", flush=True)

    def _console_confirm(pkg, label, exe):
        print(tr(f"\n{label} was not found on this computer"))
        print(tr("(checked: the tools folder, PATH, the Windows registry, the usual Program Files folders on every drive)."))
        print(tr(f"It is needed for {USES.get(pkg, 'some features')}."))
        print(tr("  Enter or Y = download and install it now into the tools folder (asks for administrator permission)"))
        print(tr(f"  P          = I already have it - let me enter the full path to {exe}"))
        print(tr("  N          = skip (you will be asked again when it is needed)"))
        while True:
            try:
                ans = input(tr("Your choice [Y/P/N]: ")).strip().lower()
            except EOFError:
                return "skip"
            if ans in ("", "y", "yes", "t", "tak", "1"):
                return "install"
            if ans in ("p", "2"):
                p = input(tr(f"Full path to {exe}: ")).strip().strip('"')
                if os.path.isfile(p):
                    return p
                print(tr("That file does not exist - try again."))
                continue
            return "skip"

    if sys.stdin and sys.stdin.isatty():
        CONFIRM_INSTALL = _console_confirm
    m, e = ensure(all_names(), _say, force="--no-force" not in sys.argv)
    print()
    real = [x for x in e if not x.startswith(NOT_INSTALLED)]
    for line in m:
        print(" +", tr(line))
    for line in e:
        print(" -" if line.startswith(NOT_INSTALLED) else " !", tr(line.replace(NOT_INSTALLED, "")))
    print(tr("Done.") if not real else tr("Finished with errors."))
    sys.exit(1 if real else 0)
