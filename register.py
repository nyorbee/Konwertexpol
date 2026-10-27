# -*- coding: utf-8 -*-
"""Registers / removes the 'Konwertexpol' context menu with a submenu for every file type.
Note: Windows shows at most 16 entries per menu level, so long lists are grouped into submenus."""
import sys
import winreg
from pathlib import Path

from i18n import tr
from converter import (ALIASES, VIDEO_IN, AUDIO_IN, IMAGE_IN, TEXT_IN, PDF_IN,
                       VIDEO_OUT, AUDIO_OUT, TEXT_OUT, TARGETS, norm, category,
                       VIDEO_RES_PRESETS, IMAGE_RES_PRESETS)

NAME = "Konwertexpol"
BASE = r"Software\Classes\SystemFileAssociations"
OLD_KEY = r"Software\Classes\*\shell\FileConverter"
EXTS = sorted(VIDEO_IN | AUDIO_IN | IMAGE_IN | TEXT_IN | PDF_IN | set(ALIASES) | {"doc"})
HKCU = winreg.HKEY_CURRENT_USER
HIVE = HKCU          # where the menu is written: the current user (default) or the whole machine (--machine, installer)

ICON_DIR = Path(__file__).with_name("icons")
PYW = Path(sys.executable).with_name("pythonw.exe")
SCRIPT = Path(__file__).with_name("converter.py")
LAUNCH = Path(__file__).with_name("launch.py")      # tiny launcher: gathers all selected files into ONE window
EXPLORER_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer"
MULTI_LIMIT = 1000        # Explorer hides legacy verbs when more than 15 files are selected unless this value is raised


def setval(path, value, name="", kind=winreg.REG_SZ):
    with winreg.CreateKeyEx(HIVE, path, 0, winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, name, 0, kind, value)


def delete_tree(path):
    try:
        with winreg.OpenKey(HIVE, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                delete_tree(path + "\\" + sub)
        winreg.DeleteKey(HIVE, path)
    except (FileNotFoundError, PermissionError):
        pass


def icon(name):
    """Path to icons\\<name>.ico if such a file exists."""
    p = ICON_DIR / f"{name}.ico"
    return str(p) if p.exists() else None


def to(fmt):
    return ("item", f"To {fmt.upper()}", f"--to {fmt}", False, fmt)


def resize_items(cat):
    """'Resize to' (ready-made resolutions, starts immediately) + 'Resize...' (a window with a custom value).
    Presets with '%' are left out of the menu - the % sign in a registry command can be troublesome."""
    return [("item", "Resize...", "--resize", True, "resize")]      # the window offers the presets and custom values


MAX_ENTRIES = 16      # Explorer shows at most 16 entries in TOTAL for a registry menu - nested entries count too
                      # (the 17th is silently cut off). Anything rarer is reachable through "Convert to..." (the window).


def total_entries(items):
    """Number of entries Explorer has to show: every item and every submenu header, nested ones included."""
    return sum(1 + (total_entries(it[2]) if it[0] == "menu" else 0) for it in items)


def fit(entries):
    """entries: [(rank, group, item)] in display order. Rank 0 = must always be there; a higher rank = less important.
    While the menu would exceed the Explorer limit, the least important entry (the last one among equals) is dropped -
    whatever is dropped is still reachable through "Convert to..." (a window with every format).
    A separator line is drawn after each group."""
    ents = list(entries)
    while total_entries([e[2] for e in ents]) > MAX_ENTRIES:
        worst = max(e[0] for e in ents)
        if worst == 0:
            raise AssertionError("the must-have entries alone do not fit in the menu")
        for k in range(len(ents) - 1, -1, -1):
            if ents[k][0] == worst:
                del ents[k]
                break
    out = []
    for k, (_, g, it) in enumerate(ents):
        sep = k + 1 < len(ents) and ents[k + 1][1] != g
        out.append(it[:3] + (sep,) + it[4:])
    return out


def layout(cat, e):
    """Returns the list of menu entries (at most MAX_ENTRIES in total - see above). Every entry has a rank (0 = always
    shown); when a menu is too long the least important formats drop out and are chosen in the "Convert to..." window."""
    def fm(fmts, ranks, group):
        return [(ranks.get(f, 9), group, to(f)) for f in fmts if f != e]

    def one(rank, group, item):
        return [(rank, group, item)]

    meta = one(1, "meta", ("item", "Metadata: show / remove (EXIF, GPS, author...)", "--meta show", False, "meta"))
    pick = one(0, "pick", ("item", "Convert to... (choose any format)", "--pick", False, "convert"))
    compress = one(0, "main", ("item", "Compress to...", "--compress", False, "compress"))
    resize = one(0, "main", resize_items(cat)[0])
    trim = one(0, "main", ("item", "Cut a clip / grab a frame...", "--trim", False, "trim"))
    if cat == "video":                  # converting comes first (MP3 at the very top); the tools sit below it
        items = (fm(["mp3"], {"mp3": 0}, "mp3")
                 + fm(("mp4", "mkv", "webm", "mov", "avi"), {"mp4": 2, "mkv": 2, "webm": 3, "mov": 4, "avi": 5}, "vid")
                 + fm(("m4a", "wav", "flac", "aac", "ogg"), {"m4a": 3, "wav": 3, "flac": 4, "aac": 6, "ogg": 6}, "aud")
                 + compress + resize
                 + one(0, "tools", ("item", "Video tools (cut, GIF, frame, join, subtitles)...", "--vtools", False, "trim"))
                 + pick + meta)
    elif cat == "pdf":
        images = [f for f in ("jpg", "png", "webp", "tiff") if f in TARGETS["pdf"]]
        docs = [f for f in ("docx", "txt", "md", "html") if f in TARGETS["pdf"]]
        items = (compress
                 + one(0, "main", ("item", "Merge PDFs (select several PDFs)", "--merge", False, "merge"))
                 + one(0, "main", ("item", "Make searchable (OCR)", "--to pdf --ocr", False, "pdf"))
                 + one(0, "main", ("item", "Remove password (save an unlocked copy)", "--unlock", False, "unlock"))
                 + one(0, "main", ("item", "Keep / remove / split pages...", "--pages", False, "pages"))
                 + fm(images, {"jpg": 2, "png": 2, "webp": 4, "tiff": 5}, "img")
                 + fm(docs, {"docx": 1, "txt": 2, "md": 4, "html": 4}, "doc") + pick + meta)
    elif cat == "image":                # images are mostly converted to other images; PDF is a small group at the end
        others = [f for f in TARGETS["image"] if f != "pdf"]
        rk = {"jpg": 1, "png": 1, "webp": 2, "heic": 3, "avif": 3, "tiff": 4, "bmp": 4, "gif": 4, "ico": 5, "jxl": 5}
        pdf = (fm(["pdf"], {"pdf": 1}, "pdf")
               + one(2, "pdf", ("item", "To PDF (searchable, OCR)", "--to pdf --ocr", False, "pdf"))
               + one(2, "pdf", ("item", "Combine selected images into ONE PDF", "--merge-images", False, "merge"))
               + one(1, "pdf", ("item", "Images to GIF / video slideshow...", "--slideshow", False, "merge")))
        items = fm(others, rk, "img") + pdf + compress + resize + pick + meta
    elif cat == "audio":
        cut = one(0, "main", ("item", "Cut a clip...", "--trim", False, "trim"))
        items = (fm(TARGETS["audio"], {"mp3": 1, "m4a": 1, "flac": 2, "wav": 2, "ogg": 3, "opus": 3, "aac": 3}, "fmt")
                 + compress + cut + pick + meta)
    elif cat == "text":
        items = fm(TARGETS[cat], {}, "fmt")
    else:
        items = compress + fm(TARGETS[cat], {}, "fmt")
    items = fit(items)
    assert total_entries(items) <= MAX_ENTRIES, f"menu for {cat}/{e} has {total_entries(items)} entries"
    return items


def build(root, items):
    for n, it in enumerate(items, 1):
        key = f"{root}\\shell\\{n:02d}"
        setval(key, tr(it[1]), "MUIVerb")
        if it[0] == "menu":
            ico = icon(it[3]) if len(it) > 3 else None
            if ico:
                setval(key, ico, "Icon")
            setval(key, "", "SubCommands")
            build(key, it[2])
        else:
            if it[3]:
                setval(key, 0x40, "CommandFlags", winreg.REG_DWORD)  # separator below the entry
            ico = icon(it[4]) if len(it) > 4 else None
            if ico:
                setval(key, ico, "Icon")
            setval(key + "\\command", f'"{PYW}" "{LAUNCH}" {it[2]} "%1"')


def set_multi_limit():
    """Raises Explorer's limit for multi-selection (default 15) so that the menu also appears for hundreds of files.
    (Microsoft: MultipleInvokePromptMinimum, HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer; it applies to all
    programs and needs Explorer to be restarted / a new sign-in.)"""
    try:
        with winreg.OpenKey(HKCU, EXPLORER_KEY, 0, winreg.KEY_READ) as k:
            cur = winreg.QueryValueEx(k, "MultipleInvokePromptMinimum")[0]
        if cur >= MULTI_LIMIT:
            return False
    except OSError:
        pass
    setval(EXPLORER_KEY, MULTI_LIMIT, "MultipleInvokePromptMinimum", winreg.REG_DWORD)
    return True


def reset_multi_limit():
    try:
        with winreg.OpenKey(HKCU, EXPLORER_KEY, 0, winreg.KEY_ALL_ACCESS) as k:
            if winreg.QueryValueEx(k, "MultipleInvokePromptMinimum")[0] == MULTI_LIMIT:    # only if it is ours
                winreg.DeleteValue(k, "MultipleInvokePromptMinimum")
    except OSError:
        pass


def uninstall():
    delete_tree(OLD_KEY)
    for ext in EXTS:
        delete_tree(f"{BASE}\\.{ext}\\shell\\{NAME}")


def install():
    uninstall()
    for ext in EXTS:
        cat = category(Path("x." + ext))
        if cat is None:
            continue
        root = f"{BASE}\\.{ext}\\shell\\{NAME}"
        setval(root, NAME, "MUIVerb")
        if icon("konwertexpol"):
            setval(root, icon("konwertexpol"), "Icon")
        setval(root, "", "SubCommands")
        build(root, layout(cat, norm(ext)))


def uninstall_everywhere():
    """Removes the menu from the current user AND from the machine (the latter needs administrator rights)."""
    global HIVE
    for hive in (HKCU, winreg.HKEY_LOCAL_MACHINE):
        HIVE = hive
        try:
            uninstall()
        except OSError:
            pass
    HIVE = HKCU


if __name__ == "__main__":
    # register.py [install|uninstall] [--machine]   |   register.py multi-on | multi-off
    args = sys.argv[1:]
    machine = "--machine" in args
    cmd = next((a for a in args if not a.startswith("--")), "install")
    if cmd == "uninstall-user":              # only the per-user menu of an older install.bat setup
        uninstall()
    elif cmd == "multi-on":                    # Explorer limit for multi-selection (per user)
        set_multi_limit()
    elif cmd == "multi-off":
        reset_multi_limit()
    elif cmd == "uninstall":
        uninstall_everywhere()
        reset_multi_limit()
        print(tr("Konwertexpol menu removed."))
    else:
        if machine:
            HIVE = winreg.HKEY_LOCAL_MACHINE     # for every user of this computer (installer, run as administrator)
        install()
        print(tr("Konwertexpol menu installed" + (" for all users." if machine else ".")))
        if not machine and set_multi_limit():
            print(tr(f"Explorer's multi-selection limit was raised from 15 to {MULTI_LIMIT} files (needed to convert hundreds of files at once)."))
            print(tr("It takes effect after Explorer restarts (sign out and in, or restart explorer.exe)."))
