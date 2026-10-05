"""
Test-suite bootstrap.

Point Veritas at a throwaway data directory and preload the fixture org
configs into it. Nothing org-specific ships with the product: real customers
upload their own org config at onboarding. The fixture orgs below exist only
for tests.

This runs before any test module (and therefore any `runtime_paths` /
`org_config.store` import) is loaded, so every database, config, and key the
tests create lands in the temp directory instead of the source tree.
"""
import os
import shutil
import tempfile
from pathlib import Path

_FIXTURE_ORGS = Path(__file__).parent / "tests" / "fixtures" / "org_configs"

if "VERITAS_DATA_DIR" not in os.environ:
    _data_dir = Path(tempfile.mkdtemp(prefix="veritas-test-data-"))
    os.environ["VERITAS_DATA_DIR"] = str(_data_dir)
else:
    _data_dir = Path(os.environ["VERITAS_DATA_DIR"])

_configs = _data_dir / "org_config" / "configs"
_configs.mkdir(parents=True, exist_ok=True)
for _org_dir in _FIXTURE_ORGS.iterdir():
    if _org_dir.is_dir() and not (_configs / _org_dir.name).exists():
        shutil.copytree(_org_dir, _configs / _org_dir.name)
