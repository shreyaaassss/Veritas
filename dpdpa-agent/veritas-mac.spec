# -*- mode: python ; coding: utf-8 -*-
# Veritas Runtime — PyInstaller Spec for macOS
# ===============================================
# Must be run ON a Mac (Intel or Apple Silicon).
#
# Build (run on Mac):
#   pip install pyinstaller
#   python -m PyInstaller veritas-mac.spec --noconfirm
#
# Output:
#   dist/veritas-runtime          (native binary for the Mac you run this on)
#
# For a universal binary (runs on both Intel + Apple Silicon):
#   python -m PyInstaller veritas-mac.spec --noconfirm --target-arch universal2
#   (Requires Rosetta 2 installed on Apple Silicon Mac)

from PyInstaller.utils.hooks import collect_data_files, collect_all

block_cipher = None

spacy_datas, spacy_binaries, spacy_hiddenimports = collect_all('en_core_web_lg')
spacy_core_datas = collect_data_files('spacy')
presidio_datas   = collect_data_files('presidio_analyzer')
presidio_datas  += collect_data_files('presidio_anonymizer')

try:
    tldextract_datas = collect_data_files('tldextract')
except Exception:
    tldextract_datas = []

a = Analysis(
    ['run_pipeline.py'],
    pathex=['.'],
    binaries=spacy_binaries,
    datas=[
        ('dashboard/index.html',               'dashboard'),
        ('llm_explainer/statute_snippets.json','llm_explainer'),
        ('org_config/configs',                 'org_config/configs'),
        *spacy_datas,
        *spacy_core_datas,
        *presidio_datas,
        *tldextract_datas,
    ],
    hiddenimports=[
        'en_core_web_lg',
        'spacy', 'spacy.lang.en', 'spacy.pipeline', 'spacy.tokens',
        'blis', 'blis.cy', 'thinc', 'thinc.backends', 'thinc.backends.numpy_ops',
        'cymem', 'murmurhash', 'preshed', 'srsly', 'catalogue', 'confection', 'weasel',
        'presidio_analyzer', 'presidio_analyzer.nlp_engine',
        'presidio_analyzer.predefined_recognizers', 'presidio_anonymizer',
        'phonenumbers',
        'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        '_sqlite3',
        'cryptography', 'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.asymmetric',
        'cryptography.hazmat.primitives.asymmetric.padding',
        'cryptography.hazmat.primitives.hashes',
        'openai', 'yaml', 'pydantic',
        # httpx required by weasel (spaCy CLI dep) at import time
        'httpx', 'httpx._transports', 'httpx._transports.default',
        'httpcore',
        'anyio', 'anyio._backends._asyncio',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'pytest', 'pytest_asyncio', '_pytest',
        'IPython', 'matplotlib', 'PIL',
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='veritas-runtime',   # No .exe on macOS
    debug=False,
    strip=False,
    upx=False,
    console=True,             # Keep console for service logs
)
