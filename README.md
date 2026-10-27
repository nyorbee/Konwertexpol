# Konwertexpol

A right-click file converter for Windows. Select one file (or hundreds), right-click → **Konwertexpol**, pick an action.
Everything runs locally and offline.

**Video** – convert (remux when possible, otherwise high quality), extract audio, compress to a size limit, resize,
cut a clip, cut to GIF, grab a frame, join videos, extract / add subtitles and audio tracks, optional GPU encoding.
**Audio** – convert, compress to a size, cut a clip.
**Images** – convert (jpg, png, webp, avif, jxl, bmp, tiff, gif, ico, heic), compress to a size, resize, to PDF (also searchable / combined).
**PDF** – compress (to a size or losslessly), merge, OCR, remove password, keep / remove / reorder pages, PDF → images / DOCX / TXT / MD / HTML.
**Documents** – txt, md, html, docx, odt, rtf, epub, tex.
**Metadata** – show (EXIF, GPS with a map link, author…) and save clean copies.

Principles: lossless unless you ask for compression or a resolution change; originals are never touched; PDF pages are never
turned into pictures; nothing is installed behind your back.

## Install
Requires Windows and Python 3 (from python.org).

1. Download the repository (**Code → Download ZIP**) and unzip it somewhere permanent.
2. Run `install.bat`. It downloads the tools (ffmpeg, ImageMagick, Pandoc, Poppler, qpdf, …) into `tools\` and adds the menu.
3. Restart Explorer (or sign out and in) if the menu does not show up.

Update tools: `update_tools.bat`. Remove: `uninstall.bat` (your files are never touched).

Ghostscript and Tesseract are searched for on the computer first; if missing you are asked whether to install them.

A ready-made installer can be built with `installer\build_installer.bat` (needs Inno Setup 6, see `installer\README_BUILD.txt`).

More details: `README.txt` (English), `README_PL.txt` (Polski).

## Notes
* The program interface is in English.
* Windows shows at most 16 context-menu entries, so the least used formats are under **Convert to…**.
* Windows 10/11, 64-bit. Please report problems in Issues (attach the log from **Diagnostics… → Save log**).
* Privacy: there is no telemetry. The only network traffic is downloading the tools (GitHub, PyPI) when you install or update them.
* The logo at the bottom of every window opens the author's page in the browser when clicked.

## License
MIT – see `LICENSE`. The downloaded tools have their own licenses – see `THIRD_PARTY_NOTICES.md`.

Created by IT Guy – https://www.facebook.com/ajtiguy
