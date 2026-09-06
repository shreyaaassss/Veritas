# PyInstaller Runtime Hook — spaCy model path fix
#
# When running from a PyInstaller bundle, bundled packages (including
# en_core_web_lg) are extracted to sys._MEIPASS but that directory is NOT
# automatically added to sys.path. spaCy's is_package() checks sys.path,
# so it returns False for bundled models, causing the engine to fail silently.
#
# This runtime hook runs before any application code and adds sys._MEIPASS
# to sys.path so spaCy finds the bundled model as a proper Python package.

import sys

if hasattr(sys, '_MEIPASS') and sys._MEIPASS not in sys.path:
    sys.path.insert(0, sys._MEIPASS)
