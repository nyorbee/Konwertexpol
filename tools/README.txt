Folder for Konwertexpol's external tools.

The program uses the tools from this folder first:
  ffmpeg\        - audio / video           (downloaded from gyan.dev, "essentials" build)
  imagemagick\   - images                  (portable build, GitHub)
  libjxl\        - cjxl / djxl, JPEG XL    (GitHub)
  pandoc\        - text documents          (GitHub)
  poppler\       - PDF -> text / image     (GitHub)
  qpdf\          - lossless PDF optimisation (GitHub)
  ghostscript\   - shrinking images in PDFs (installed from gs#####w64.exe)
  tesseract\     - OCR engine for scanned PDFs (installed silently from the UB-Mannheim installer, one UAC prompt)
  tessdata\      - OCR language data (tessdata_fast); languages are downloaded when first needed
  python\        - Python modules: pikepdf + Pillow (PDF cleaning and compression), pdf2docx (PDF -> Word keeping the layout),
                   ocrmypdf (scanned PDF -> searchable PDF / text); downloaded by pip straight into this folder
                   (not into the system Python)

Missing tools are downloaded automatically the first time they are needed. Updates do NOT happen
by themselves - only when you press "Update tools" in the window (or run update_tools.bat); the old
version is then replaced straight away. Versions are recorded in versions.json.

Ghostscript / Tesseract are first looked for on this computer (PATH, the Windows registry incl. Apps & features, the usual
Program Files folders on every fixed drive). If a copy is found it is simply used - nothing is installed. If none is found
you are asked: install it, point to your copy (remembered in tools\paths.txt as `gs=C:\...\gswin64c.exe` or
`tesseract=C:\...\tesseract.exe`; you can also edit that file by hand), or skip. Nothing is installed without your answer.
Ghostscript / Tesseract: drop your own installer here (e.g. gs10080w64.exe or
tesseract-ocr-w64-setup-5.4.0.20240606.exe). It is installed silently into tools\ghostscript / tools\tesseract
(one UAC confirmation) and used without downloading. A newer Ghostscript installer downloaded during an
update replaces the older one.

OCR languages: put your own <code>.traineddata files (e.g. from tessdata_best) into tools\tessdata and use the
code in the window (e.g. pol+eng). Missing languages are downloaded automatically (tessdata_fast).

You can also drop your own .exe (e.g. ffmpeg.exe) straight into this folder - it will be used.

The installer (install.bat) only fetches what is MISSING. Tools that are already in this folder - or already
installed on the computer (e.g. Ghostscript, Tesseract) - are left alone, and so are the Python modules. Looking for
newer versions is done by update_tools.bat / the "Update tools" button; if that check or a download fails
(no internet, firewall), the program carries on with what is here. An old version is replaced only after the new
one has been downloaded and unpacked successfully.

OFFLINE MODE: once everything is downloaded (install.bat), Konwertexpol does not need internet. You can copy
the whole tools folder together with the program to a computer without a network. One exception: the modules in
tools\python contain compiled parts, so the same Python 3.x version (e.g. 3.12) must be installed on that
computer - otherwise the program will report that they have to be downloaded again. The old tools\pikepdf
folder (from an earlier version) is removed after the update.
