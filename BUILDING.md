# Building the Windows application

The project can be exported as a Windows desktop application using PyInstaller.
The build bundles the Qt styles, SVG assets, Tesseract executable, required
Tesseract DLLs, and English OCR data.

From the project directory, install the Python dependencies and build:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm ".\Game Bot.spec"
```

If Tesseract is installed somewhere other than
`C:\Program Files\Tesseract-OCR`, set `TESSERACT_ROOT` to its installation
directory before building.

The application is created at `dist\Game Bot\Game Bot.exe`. Distribute the
entire `dist\Game Bot` folder; the executable depends on its neighboring
`_internal` directory. The app stores OCR test screenshots in the user's
`%LOCALAPPDATA%\Poke Nexus\screenshot` directory.

The application bundles Tesseract and English OCR data, so users do not need
Tesseract installed or added to `PATH`. When running from source or building,
the project also checks the existing `dist\Game Bot\_internal\tesseract`
bundle, standard install locations, and `PATH`. If Tesseract is installed
elsewhere, set `TESSERACT_ROOT` to its installation directory.
