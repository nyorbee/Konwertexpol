Konwertexpol – konwerter plików z menu prawego przycisku (Windows)
==================================================================
Autor: IT Guy – https://www.facebook.com/ajtiguy

Zaznaczasz plik (albo setki naraz), prawy przycisk → Konwertexpol → akcja. Wszystko lokalnie i offline.
Oryginały nigdy nie są ruszane, wynik ląduje obok (zajęta nazwa dostaje „ (1)”).
Bez utraty jakości, dopóki sam nie poprosisz o kompresję do rozmiaru lub zmianę rozdzielczości.
Interfejs programu jest po angielsku, stąd oryginalne nazwy poniżej.

CO POTRAFI
----------
WIDEO (mp4 mkv webm avi mov wmv flv mpg m4v 3gp ts)
 • konwersja do MP4 / MKV / WEBM / MOV / AVI (bez ponownego kodowania, jeśli się da), wszystkie ścieżki audio i napisy zostają
 • wyciągnięcie dźwięku: MP3, M4A, WAV, FLAC, AAC, OGG
 • Compress to… – do zadanego rozmiaru (np. 25 MB)    • Resize… – zmiana rozdzielczości
 • Video tools…: wycięcie fragmentu (dokładne lub bez utraty jakości) · wycięcie do GIF-a · zapis klatki (JPG/PNG) ·
   łączenie filmów · wyciąganie ścieżek audio i napisów · dodanie napisów (jako ścieżka lub wypalone)
 • opcjonalnie karta graficzna (NVENC / Quick Sync / AMF)

AUDIO (mp3 wav flac aac m4a ogg opus wma aiff ac3 amr)
 • konwersja między formatami · Compress to… · Cut a clip… (wycięcie fragmentu)

OBRAZY (jpg png webp avif jxl bmp tiff gif ico heic)
 • konwersja między formatami · Compress to… (np. max 200 KB) · Resize…
 • To PDF: osobno, wyszukiwalny (OCR) albo kilka obrazów w JEDEN PDF

PDF
 • Compress to… (lub najmniejszy bez utraty jakości) · Merge PDFs · Make searchable (OCR) · Remove password
 • Keep / remove pages… (wpisujesz „5,8,10” / „3-7” albo klikasz na liście z podglądem; tryb zmiany kolejności stron)
 • PDF → JPG / PNG / WEBP / TIFF · PDF → DOCX / TXT / MD / HTML (skany przez OCR)
 • tekst zostaje tekstem – strony nigdy nie są zamieniane na obrazki

DOKUMENTY (txt md html docx odt rtf epub tex)
 • konwersja między sobą i do PDF

METADANE (obrazy, PDF, audio, wideo)
 • Metadata: podgląd (EXIF, GPS, autor…) i zapis czystej kopii bez metadanych

W OKNIE
-------
Pasek postępu na plik i ogólny · praca równoległa przy wielu plikach · Cancel · Retry failed (tylko nieudane) ·
Add files… · Update tools · Diagnostics… (lista narzędzi i log) · More options (OCR, karta graficzna, tryb bezstratny).

INSTALACJA
----------
Wersja przenośna: rozpakuj folder, uruchom install.bat (potrzebny Python 3; narzędzia pobierają się same).
Aktualizacja: update_tools.bat, usunięcie: uninstall.bat. Po instalacji może być potrzebny restart Eksploratora.
Wersja z instalatorem (Setup.exe, narzędzia w środku) – jeśli ją masz: Dalej → Dalej, menu dodaje się samo.

UWAGI
-----
• Windows pokazuje w menu max 16 pozycji – brakujące formaty są w „Convert to…”.
• Limit w KB = 1024 bajty; program celuje w ok. 98% limitu. „Rozmiar na dysku” w Windowsie zaokrągla do 4 KB.
• Korzysta z ffmpeg, ImageMagick, Pandoc, Poppler, qpdf, Ghostscript, Tesseract i innych – każde ma własną licencję.
• Coś nie działa: Diagnostics… → Save log (log jest też w %TEMP%\Konwertexpol_last.log).
