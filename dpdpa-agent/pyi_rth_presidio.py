"""
PyInstaller runtime hook for presidio_analyzer.

Problem: presidio loads its config files using:
    Path(__file__).parent.parent / 'conf' / 'default_recognizers.yaml'

where __file__ is inside `presidio_analyzer/recognizer_registry/`. This
produces a path like:
    <MEIPASS>/presidio_analyzer/recognizer_registry/../conf/default_recognizers.yaml

Linux requires every component of a path to exist for '..' resolution to work.
PyInstaller only extracts DATA files (conf/) to the filesystem; Python module
directories (recognizer_registry/) stay inside the PYZ archive and are never
created as real directories.

Fix: create the empty directory structure before any presidio code runs so
the '../' path component resolves correctly.
"""
import os
import sys

if hasattr(sys, '_MEIPASS'):
    _presidio_pkg = os.path.join(sys._MEIPASS, 'presidio_analyzer')
    for _subdir in ('recognizer_registry', 'nlp_engine', 'predefined_recognizers'):
        _path = os.path.join(_presidio_pkg, _subdir)
        os.makedirs(_path, exist_ok=True)
