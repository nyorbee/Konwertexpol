# Third-party software

Konwertexpol's own source code is under the MIT license (see `LICENSE`). The repository **does not contain** any of
the programs below. `install.bat` / `tools_manager.py` download them into the `tools` folder and Konwertexpol runs
most of them as separate programs. Each one keeps its own license.

Checked on 2026-10-27 against the sources linked in the last column (versions change - check again before you
redistribute a package that contains them).

## Programs (downloaded by `tools_manager.py`)

| Tool | Used for | License | Source of the information |
|---|---|---|---|
| FFmpeg (Gyan "essentials" build) | video / audio | GPLv3 ("All builds are 64-bit, static and licensed as GPLv3") | [gyan.dev/ffmpeg/builds](https://www.gyan.dev/ffmpeg/builds/) |
| ImageMagick | images | ImageMagick License (permissive, nearly identical to Apache-2.0; keep the license text and attribution) | [SPDX](https://spdx.org/licenses/ImageMagick) |
| Pandoc | text documents | GPL v2 or later (some parts under other licenses) | [Pandoc COPYRIGHT](https://hackage.haskell.org/package/pandoc-3.8/src/COPYRIGHT) |
| Poppler (oschwartz10612 Windows build) | PDF -> images / text | GPL (the package ships the GPL v2 text; the Windows build also bundles other libraries with their own licenses) | [Chocolatey package page](https://community.chocolatey.org/packages/poppler/26.9.0) |
| qpdf | PDF structure / optimisation | Apache-2.0 | [qpdf docs](https://qpdf.readthedocs.io/en/12.1/license.html) |
| libjxl | JPEG XL | BSD-3-Clause (third-party parts: BSD-3-Clause, Apache-2.0) | [libjxl copyright file](https://fossies.org/linux/libjxl/debian/copyright) |
| Ghostscript | PDF image compression, OCR helper | dual: AGPL or Artifex commercial license | [ghostscript.com/licensing](https://ghostscript.com/licensing) |
| Tesseract OCR (UB-Mannheim Windows installer) | OCR | Apache-2.0 (the installer also bundles DLLs, e.g. ICU, with their own licenses) | [tesseract-ocr/tesseract](https://github.com/tesseract-ocr/tesseract), [UB-Mannheim wiki](https://github.com/UB-Mannheim/tesseract/wiki) |
| 7-Zip (7zr.exe) | unpacking downloaded archives | mostly GNU LGPL (the 7-Zip license names `7z.dll`; it does not name `7zr.exe`) | [7-Zip license](https://7-zip.opensource.jp/license.txt) |

## Python modules (installed by pip into `tools\python`)

Read from the package metadata on PyPI (Python 3.13, win_amd64), 2026-10-27:

| Package | Used for | License |
|---|---|---|
| pikepdf | PDF | MPL-2.0 |
| ocrmypdf | OCR | MPL-2.0 |
| pdf2docx | PDF -> DOCX | MIT |
| Pillow | images | MIT-CMU |
| PyMuPDF (needed by pdf2docx) | PDF | dual: GNU AGPL-3.0 or Artifex commercial license |
| fpdf2, img2pdf (needed by ocrmypdf) | PDF | LGPL-3.0 |
| pypdfium2 | PDF | BSD-3-Clause, Apache-2.0 (and licenses of its dependencies) |
| lxml, numpy, opencv-python-headless, python-docx, fonttools, pdfminer-six, cryptography, ... | dependencies | permissive (MIT / BSD / Apache-2.0 / PSF; see each package) |

Not verified: `pymupdf-layout` and `pymupdf4llm` (pulled in by PyMuPDF 2.0). Their wheels contain no license
information, so check their pages on PyPI before you redistribute them.

Not re-verified in this check: Python itself (PSF License) when it is bundled by the installer.

## What this means
* **The source repository** contains none of the programs above, so it carries none of their obligations.
* **A package that bundles them** (for example `Konwertexpol_Setup.exe` built with `installer\build_installer.bat`)
  is a redistribution. You then have to follow their licenses: include the license texts, keep the notices, and for
  GPL / AGPL components (FFmpeg, Pandoc, Poppler, Ghostscript, PyMuPDF) make the corresponding source available.
  Ghostscript and PyMuPDF are AGPL (or Artifex commercial), so read their terms carefully before you give such a
  package to other people.
* This list was compiled carefully but may be incomplete or out of date. It is not legal advice.
