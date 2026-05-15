# PyInstaller build spec — produces a single self-contained Windows executable.
#
#   Build:   pyinstaller build.spec --clean
#   Output:  dist/mc-name-checker.exe
#
# The exe bundles the Python runtime plus aiohttp/tqdm, so end users do not
# need Python installed. Local modules (wordlist, notifier) are picked up
# automatically via import analysis from checker.py.

block_cipher = None

analysis = Analysis(
    ["checker.py"],
    pathex=[],
    binaries=[],
    datas=[],
    # aiohttp pulls these in dynamically; declare them so the frozen build
    # does not fail with a missing-module error at startup.
    hiddenimports=[
        "aiohttp",
        "aiohttp.resolver",
        "tqdm",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pytest"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.zipfiles,
    analysis.datas,
    [],
    name="mc-name-checker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # CLI tool — keep the console window.
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
