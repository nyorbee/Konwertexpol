# -*- coding: utf-8 -*-
"""Interface language (English / Polish) for Konwertexpol.

The program logic always works with the English texts; only what is SHOWN is translated, at the moment it is shown
(window texts, titles, dialogs, log lines, console messages, context-menu labels).  The language is chosen when the
program is installed (install.bat / the installer write language.txt next to this file); the environment variable
KONWERTEXPOL_LANG (en / pl) overrides it.  Texts without a Polish entry are simply shown in English."""
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _detect():
    v = os.environ.get("KONWERTEXPOL_LANG", "").strip().lower()
    if not v:
        try:
            v = (HERE / "language.txt").read_text(encoding="utf-8-sig").strip().lower()
        except OSError:
            v = ""
    return "pl" if v.startswith("pl") else "en"


LANG = _detect()
_CAT = {}
_TEMPLATES = []          # (compiled regex, polish template, number of placeholders), most specific first
_CACHE = {}
_PH = re.compile(r"\{(\d*)\}")
_PFX = re.compile(r"^(\s*(?:[•⚠🔧✔✖✓+!*-]+\s+)?(?:\[\d+\]\s*)?)(\S.*)$", re.S)     # "   • ", "⚠ ", "[2] " in front of a log line



def set_language(lang):
    """Switches the language (used at start-up and by the command line helper below)."""
    global LANG, _CAT
    LANG = "pl" if str(lang).lower().startswith("pl") else "en"
    _CAT = {}
    _TEMPLATES.clear()
    _CACHE.clear()
    if LANG != "pl":
        return
    try:
        from lang_pl import PL as cat
        _CAT = cat
    except Exception:            # a missing / broken catalogue must never stop the program
        _CAT = {}
    for en, pl in _CAT.items():
        if "{}" in en:
            parts = en.split("{}")
            rx = "^" + "(.*?)".join(re.escape(p) for p in parts) + "$"
            _TEMPLATES.append((re.compile(rx, re.S), pl, len(parts) - 1, sum(len(p) for p in parts)))
    _TEMPLATES.sort(key=lambda t: -t[3])


set_language(LANG)


def _fill(template, values):
    seq = iter(values)

    def rep(m):
        if m.group(1):
            i = int(m.group(1))
            return values[i] if i < len(values) else m.group(0)
        return next(seq, "")
    return _PH.sub(rep, template)


def _one(text):
    """Translates one piece of text (no per-line splitting)."""
    if text in _CAT:
        return _CAT[text]
    for rx, pl, n, _ in _TEMPLATES:
        m = rx.match(text)
        if m:
            vals = [_CAT.get(g, g) for g in m.groups()]       # an inserted value may itself be a known word
            return _fill(pl, vals)
    return None


def tr(text):
    """English text -> text in the chosen language (unknown texts are returned unchanged)."""
    if LANG != "pl" or not isinstance(text, str) or not text.strip():
        return text
    hit = _CACHE.get(text)
    if hit is not None:
        return hit
    out = text
    core = text.strip()
    lead, trail = text[:len(text) - len(text.lstrip())], text[len(text.rstrip()):]
    r = _one(text)                      # exact (some entries keep their leading / trailing spaces or newlines)
    if r is not None:
        lead = trail = ""
    else:
        r = _one(core)
    if r is None and "\n" in core:                                   # a multi-line text: line by line
        r = "\n".join((_one(l.strip()) and (l[:len(l) - len(l.lstrip())] + _one(l.strip()))) or l
                      for l in core.split("\n"))
        if r == core:
            r = None
    if r is None and core.endswith(("...", "…", ":", ".")):         # "Foo..." when only "Foo" is known (and so on)
        for suf in ("...", "…", ":", "."):
            if core.endswith(suf):
                b = _one(core[:-len(suf)])
                if b is not None:
                    r = b + suf
                    break
    if r is None:
        m = _PFX.match(text)
        if m and m.group(1).strip():
            rest = tr(m.group(2))
            if rest != m.group(2):
                out = m.group(1) + rest
    if r is not None:
        out = lead + r + trail
    if len(_CACHE) < 20000:
        _CACHE[text] = out
    return out


def trs(items):
    return [tr(i) for i in items]


def join_and(items):
    """'a, b and c' in the chosen language."""
    items = [tr(i) for i in items]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + tr(" and ") + items[-1]


def ac_base(status):
    """The status line without the 'closing in N s' countdown (in either language)."""
    for mark in (" — closing", " — zamknięcie"):
        status = status.split(mark)[0]
    return status


def is_pl():
    return LANG == "pl"


# ---------------------------------------------------------------------------------------------------------------
# tkinter hooks: every widget text, title and dialog goes through tr() without touching the ~hundreds of call sites
# ---------------------------------------------------------------------------------------------------------------
_hooked = False


def install_tk_hooks():
    global _hooked
    if _hooked or LANG != "pl":
        return
    _hooked = True
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog

    keys = ("text", "label", "title")
    orig_options = tk.Misc._options

    def _options(self, cnf, kw=None):
        if kw:
            kw = {k: (tr(v) if k in keys and isinstance(v, str) else v) for k, v in kw.items()}
        if cnf and isinstance(cnf, dict):
            cnf = {k: (tr(v) if k in keys and isinstance(v, str) else v) for k, v in cnf.items()}
        return orig_options(self, cnf, kw)
    tk.Misc._options = _options

    o_title = tk.Wm.wm_title

    def wm_title(self, string=None):
        return o_title(self, tr(string) if isinstance(string, str) else string)
    tk.Wm.wm_title = wm_title
    tk.Wm.title = wm_title

    # ttk pieces that build their options without Misc._options
    o_add = ttk.Notebook.add

    def nb_add(self, child, **kw):
        if "text" in kw:
            kw["text"] = tr(kw["text"])
        return o_add(self, child, **kw)
    ttk.Notebook.add = nb_add
    o_head = ttk.Treeview.heading

    def tv_heading(self, column, option=None, **kw):
        if "text" in kw:
            kw["text"] = tr(kw["text"])
        return o_head(self, column, option, **kw)
    ttk.Treeview.heading = tv_heading

    def wrap_dialog(mod, name):
        fn = getattr(mod, name, None)
        if fn is None:
            return

        def w(*a, **kw):
            a = [tr(x) if isinstance(x, str) else x for x in a]
            for k in ("title", "message", "detail"):
                if isinstance(kw.get(k), str):
                    kw[k] = tr(kw[k])
            if "filetypes" in kw and kw["filetypes"]:
                kw["filetypes"] = [(tr(n), p) for n, p in kw["filetypes"]]
            return fn(*a, **kw)
        setattr(mod, name, w)
    for n in ("showinfo", "showwarning", "showerror", "askyesno", "askokcancel", "askyesnocancel",
              "askretrycancel", "askquestion"):
        wrap_dialog(messagebox, n)
    for n in ("askopenfilename", "askopenfilenames", "asksaveasfilename", "askdirectory", "askopenfile",
              "askopenfiles", "asksaveasfile"):
        wrap_dialog(filedialog, n)


def make_trvar():
    """A StringVar that translates what is put into it (status lines)."""
    import tkinter as tk

    class TrVar(tk.StringVar):
        def __init__(self, master=None, value=None, name=None):
            super().__init__(master, tr(value) if isinstance(value, str) else value, name)

        def set(self, value):
            super().set(tr(value) if isinstance(value, str) else value)
    return TrVar


# ---------------------------------------------------------------------------------------------------------------
# Texts printed by the .bat files (they stay pure ASCII - UTF-8 batch files with labels are unreliable - and ask this
# module for their messages):   python i18n.py [--lang pl|en] KEY [value ...]
# ---------------------------------------------------------------------------------------------------------------
BAT = {
    "install_intro": (
        "The tools (ffmpeg, ImageMagick, libjxl, Pandoc, Poppler, Ghostscript, qpdf, Tesseract OCR) and the Python "
        "modules (pikepdf, Pillow, pdf2docx, ocrmypdf) go into the folder:\n  {}\n"
        "Only what is MISSING is downloaded and installed. Tools that are already there (or already installed on this "
        "computer, e.g. Ghostscript / Tesseract) are left alone. To look for newer versions run update_tools.bat.\n"
        "Ghostscript and Tesseract are looked for on this computer first (PATH, the Windows registry, Program Files on "
        "every drive). They are NEVER installed behind your back: if one is missing you are asked whether to install "
        "it, or to point to your copy (it is remembered in tools\\paths.txt).\n"
        "Once tools is complete, the program works without internet. You can move the folder to another computer "
        "(it needs the same Python 3.x version there - otherwise the modules are downloaded again)."),
    "install_warn": (
        "WARNING: some tools could not be downloaded (no internet?). The installation continues - the tools already "
        "in the tools folder will be used. Missing ones are downloaded when first needed."),
    "install_done": (
        "Done! Right-click any file and choose Konwertexpol.\n"
        "(Windows 11: choose 'Show more options' or press Shift+F10)\n"
        "If you move this folder, run install.bat again."),
    "update_tools": "Checking and updating the tools in the tools folder ...",
    "update_tools_installed": ("Checking for newer versions of the tools - ffmpeg, ImageMagick, Pandoc, Poppler, qpdf, ... "
                               "- and updating them ..."),
    "S1": "[1/6] Cleaning ...",
    "S2": "[2/6] Copying the program ...",
    "S3": "[3/6] Private Python - copying the Python installed on this PC (without extras) ...",
    "SRC": "      source: {}",
    "S4": ("[4/6] Downloading the tools into the package (ffmpeg, ImageMagick, Pandoc, Poppler, qpdf, Python modules, "
           "OCR data ...)\n      Ghostscript and Tesseract: if this PC has them they are COPIED into the package, "
           "otherwise their installers are run (UAC)."),
    "WARN": "WARNING: some tools could not be downloaded - check the messages above.",
    "S5": "[5/6] Precompiling ...",
    "S6": "[6/6] Building the installer ...",
    "DONE": "Done: {}\nThe installer asks for the language (English / Polski) when it starts.",
    "STORE": "ERROR: this is the Microsoft Store Python, which cannot be copied. Install Python from python.org instead.",
    "COPYFAIL": "ERROR: copying Python failed.",
    "PYNOTK": "ERROR: the copied Python does not start or has no Tk. Send the messages above.",
    "BADPY": ("ERROR: {} is not a complete Python with Tk (python.exe or the tcl folder is missing).\n"
              "Install the normal Python from https://www.python.org/downloads/ (keep 'tcl/tk and IDLE' ticked)."),
    "NOINNO": "Inno Setup 6 was not found. Install it from https://jrsoftware.org/isdl.php and run this again.",
    "FAIL": "Build failed - see the messages above.",
}


def _cli(argv):
    import sys
    if argv[:1] == ["--lang"] and len(argv) >= 2:
        set_language(argv[1])
        argv = argv[2:]
    if not argv or argv[0] not in BAT:
        return 2
    text = BAT[argv[0]]
    for v in argv[1:]:
        text = text.replace("{}", v, 1)
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    print(tr(text))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
