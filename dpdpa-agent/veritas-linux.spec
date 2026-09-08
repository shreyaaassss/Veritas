# -*- mode: python ; coding: utf-8 -*-
# Veritas Runtime — PyInstaller Spec for Linux
# ==============================================
# Must be run ON a Linux machine or inside a Docker container.
#
# Build:
#   python -m PyInstaller veritas-linux.spec --noconfirm
#
# Or via Docker (build Linux binary from any machine):
#   docker build -f build-linux.Dockerfile -t veritas-builder .
#   docker cp veritas-builder:/build/dist/veritas-runtime ./dist/linux/

from PyInstaller.utils.hooks import collect_data_files, collect_all

block_cipher = None

spacy_datas, spacy_binaries, spacy_hiddenimports = collect_all('en_core_web_lg')
spacy_core_datas = collect_data_files('spacy')
presidio_datas   = collect_data_files('presidio_analyzer')
presidio_datas  += collect_data_files('presidio_anonymizer')
# Explicitly add presidio conf/ — collect_data_files sometimes misses it
import pathlib as _pl, presidio_analyzer as _pa
_pa_conf = str(_pl.Path(_pa.__file__).parent / 'conf')
presidio_datas  += [(_pa_conf, 'presidio_analyzer/conf')]

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
        ('dashboard/login.html',               'dashboard'),
        ('dashboard/setup.html',               'dashboard'),
        ('dashboard/static',                   'dashboard/static'),
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
        'httpx', 'httpx._transports', 'httpx._transports.default',
        'anyio', 'anyio._backends', 'anyio._backends._asyncio',
        'passlib', 'passlib.handlers', 'passlib.handlers.bcrypt',
        'jose', 'jose.jwt',
    ],
    hookspath=[],
    runtime_hooks=['pyi_rth_spacy.py'],
    excludes=['pytest', 'pytest_asyncio', '_pytest', 'IPython', 'matplotlib', 'PIL'],
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
    name='veritas-runtime',   # No .exe on Linux
    debug=False,
    strip=False,
    upx=False,
    console=True,
)
