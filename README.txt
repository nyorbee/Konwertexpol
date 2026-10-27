Konwertexpol - right-click file converter for Windows
=====================================================
Created by IT Guy - https://www.facebook.com/ajtiguy   (the logo at the bottom of every window opens this page)

INSTALL / UPDATE / REMOVE
-------------------------
Setup.exe: installer\build_installer.bat builds a normal installer (Program Files, private Python, all tools included,
          start-menu "update tools", uninstaller) - see installer\README_BUILD.txt. The text below describes the
          portable folder version (install.bat).
Install : run install.bat (needs Python 3; if it is missing the script offers to install it). Everything the program
          needs is downloaded into the "tools" folder - ffmpeg, ImageMagick, libjxl, Pandoc, Poppler, qpdf, the Python
          modules (pikepdf, Pillow, pdf2docx, ocrmypdf) and OCR data - so the folder can be copied to another computer
          and used offline. Only what is MISSING is downloaded; nothing is repeated on a second run.
          Ghostscript and Tesseract are searched for on the computer first (PATH, registry, Program Files, all drives).
          If one is missing you are asked: install it / point to your copy / skip. A path can also be set in
          tools\paths.txt, e.g.  gs=C:\...\gswin64c.exe
Update  : update_tools.bat or the "Update tools" button - installs newer versions only if there are any.
Remove  : uninstall.bat (removes the menu and the Explorer setting below; your files are never touched).
After changes to the menu (new version) run install.bat again and restart Explorer (or sign out and in).
Icons   : the "icons" folder holds the menu icon (konwertexpol.ico) and the logo. Optional extra icons for single
          entries: compress.ico, resize.ico, trim.ico, unlock.ico, mp3.ico ... (see icons\README.txt).

THE RIGHT-CLICK MENU  (Konwertexpol)
------------------------------------
One rule everywhere: what you do most is on top - conversions first for video, images and audio, tools first for PDF;
then "Convert to..." and "Metadata" at the bottom.
Select one or MANY files, right-click, Konwertexpol. All selected files open in ONE window (hundreds are fine:
install.bat raises Explorer's multi-selection limit from 15 to 1000 - setting MultipleInvokePromptMinimum; it applies
to Explorer in general, takes effect after a restart of Explorer and is removed by uninstall.bat).

Explorer shows at most 16 entries in TOTAL (the 17th is cut off silently), so the menu lists the common entries only.
Every entry has a priority: if a menu would be longer, the least important formats drop out first. The must-have
entries are always there, and every format that drops out is still available in "Convert to... (choose any format)".

VIDEO (mp4 mkv webm avi mov wmv flv mpg m4v 3gp ts)
  To MP3 (first; keeps the cover picture) - MP4 / MKV / WEBM / MOV / AVI - M4A / WAV / FLAC / AAC / OGG (as many as fit) -
  Compress to... - Resize... - Video tools... - Convert to... - Metadata
  (Converting a whole video to GIF is not in the menu on purpose - use "Video tools" to cut a part into a GIF; the GIF
  format is still available in "Convert to...".)
AUDIO (mp3 wav flac aac m4a ogg opus wma aiff ac3 amr)
  audio formats - Compress to... - Cut a clip... - Convert to... - Metadata
IMAGES (jpg png webp avif jxl bmp tiff gif ico heic)
  image formats - To PDF / To PDF (searchable, OCR) / Combine selected images into ONE PDF - Compress to... - Resize... -
  Convert to... - Metadata
PDF
  Compress to... - Merge PDFs - Make searchable (OCR) - Remove password - Keep / remove pages... - JPG / PNG / WEBP / TIFF -
  DOCX / TXT / MD / HTML - Convert to... - Metadata
TEXT / DOCUMENTS (txt md html docx odt rtf epub tex)
  conversions between these formats and to PDF.

THE CONVERSION WINDOW  ("Convert to...", "Compress to...", "Resize...", or any format entry)
-----------------------------------------------------------------------------------------
* Convert to: any target format valid for the selected files. Entries like "To JPG" start immediately.
* Compress to size: enter a limit (KB / MB). The result is as good as possible AND within the limit whenever that is
  physically possible; quality is lowered only as far as needed.
    - images: JPEG/WebP quality is searched; pixels are reduced only if that is not enough
    - video: two-pass encoding to the bitrate the limit allows (asks which audio tracks to keep when there are several;
      subtitles are kept); audio: bitrate computed from the limit; GIF: width is reduced
    - PDF: private data is stripped, images are re-compressed (own JPEG search, Ghostscript only as a helper). Text
      stays selectable; pages are never turned into pictures. If a limit is truly unreachable the result says how
      close it got.
* Resolution (video, images): preset or custom WxH, "Keep aspect ratio". Ticked = the result fits the box; unticked =
  exactly WxH (stretched). Up- and downscaling are plain resampling (Lanczos, no AI). File names carry the real
  resulting size, e.g. photo_1280x720.jpg.
* More options (collapsible):
    - PDF: smallest size WITHOUT quality loss (never lowers quality, no size limit - it only removes what is unused)
    - PDF / image -> PDF: add a searchable text layer (OCR) + OCR language(s) (also used for scanned PDF -> text / Word)
    - Video: use the graphics card for high-quality H.264 encoding and for decoding (NVENC / Quick Sync / AMF, tested
      automatically; falls back to the processor). Never used for "Compress to size". The log says what was used.
    - Video: force truly lossless encoding (crf 0) - huge files that many devices cannot play
* Buttons: Convert - Cancel (stops the tools and removes unfinished files) - Add files... (more files of the same kind) -
  Update tools - Diagnostics... (every tool found: path, version, source; last log; Copy log / Save log; the log is also
  in %TEMP%\Konwertexpol_last.log).
* Progress: per file and overall; with many files the light jobs (images, text, audio, plain PDF conversions) run several at
  a time (up to 4, by processor count); video, OCR and PDF compression already use the whole processor, so they run
  one after another.
* If some files fail the window lists them with the reason and offers "Retry failed" (only those, same settings).
* The window closes by itself 5 s after a fully successful run (click anywhere to keep it open). "Show in folder" opens
  the result.
* Closing the window during work asks whether to cancel; unfinished files and temporary folders are removed.

PRINCIPLES
----------
* Plain conversions are lossless whenever possible: streams are copied (remux) when the target container accepts them,
  images and PDFs keep full quality. Lossy encoding happens only for "Compress to size", a resolution change, or when
  the target format cannot hold the original streams (then high quality: H.264 crf 18 / VP9 crf 16 / AAC 256k).
* All audio tracks and subtitles of a video are kept wherever the target container can hold them.
* Originals are never changed or deleted. Results are saved next to the original; an existing name gets " (1)".
* Nothing is installed behind your back (see Install).
* Password-protected PDFs: the program asks for the password (a correct one is remembered for the other files of the
  run); the result is saved WITHOUT the password.

SPECIAL WINDOWS
---------------
Video tools  (one window with tabs: Cut / GIF / frame - Join videos - Subtitles & audio tracks)
  While one tab works, the other tabs are locked.
Cut a clip / GIF / grab a frame  (tab 1; audio files have a stand-alone "Cut a clip..." menu entry)
  Type "from 0:30 to 0:45" (seconds, m:ss or h:mm:ss; empty end = to the end) -> name_cut_0m30s-0m45s.ext.
  "Exact cut" (default) re-encodes only the clip in high quality, so it starts exactly where you typed. Unticked:
  streams are copied (no loss, instant) but the clip can start a few seconds early (previous keyframe). Audio files are
  always copied.
  "Save as GIF": the cut clip becomes a GIF.  "Save ONE FRAME at the from time as jpg/png": a full-resolution picture of
  that moment (maximum-quality JPEG or lossless PNG) -> name_frame_0m02s.jpg.
Join videos  (tab 2; select two or more videos before opening Video tools)
  Order the videos in the window, name the result. Identical videos (same codec, size, frame rate, audio) are joined
  without re-encoding; different ones are brought to the first video's size and frame rate and encoded once in high
  quality. Subtitles are not carried over.
Subtitles & audio tracks  (tab 3)
  Lists the audio tracks and subtitles of the video(s); the selected ones are saved next to the video: audio keeps its
  codec (no re-encoding), text subtitles become .srt (styled ASS stays .ass), picture subtitles (PGS) stay as they are.
  The lower part ADDS a subtitle file (.srt .ass .vtt) to ONE video as a selectable track (picture and sound untouched;
  .avi / .wmv / .flv / .mpg become .mkv) or burns it into the picture (re-encoded, cannot be switched off).
Merge PDFs / Combine selected images into ONE PDF
  Order the files, name the result, optionally make it searchable (OCR). JPEG images are embedded unchanged.
Keep / remove pages  (PDF)
  "Keep ONLY these pages" (a new PDF made of them, in the typed order - so 3,1,2 also reorders), "REMOVE these pages"
  (everything else stays) or "REORDER" (a list where you drag pages, with a preview of the selected page; saves
  name_reordered.pdf). Pages: 5,8,10 or 3-7 or 12- (to the end) or 1-3,9. "Pick from a list..." lets you click the pages with
  a preview instead of typing. Pages are copied as they are (lossless). Results: name_pages.pdf / name_removed.pdf.
Remove password  (PDF)
  Saves name_unlocked.pdf without the password (asks for it). The original is not touched.
Metadata
  Shows the metadata of images, PDFs, audio and video (EXIF, GPS location with a warning, author, software...). The button
  "Remove metadata (save clean copies)" saves name_clean.ext; JPEG / PNG / WebP pictures stay byte-identical in pixels.
Make searchable (OCR)
  Adds an invisible text layer to scans and photos of documents; pictures stay as they are. Needs Tesseract (asked for,
  never installed silently).
PDF -> image
  One page = one file; many pages = a folder name_jpg with numbered files. Resolution follows the images inside the PDF.
PDF -> Word / text
  DOCX keeps the page layout, columns and tables (scans are OCR-ed first); TXT does not keep the layout.

FILES
-----
converter.py      the program                      launch.py        tiny launcher used by the menu (gathers all selected files)
tools_manager.py  downloads / finds the tools      register.py      installs / removes the context menu
install.bat, update_tools.bat, uninstall.bat
tools\            ffmpeg, ImageMagick, ... (tools\python = Python modules, tools\tessdata = OCR data, paths.txt)
icons\            konwertexpol.ico (menu icon), logo.png (the logo in the windows), optional icons for single entries
