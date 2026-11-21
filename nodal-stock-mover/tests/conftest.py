import os
import sys
from pathlib import Path
import pytest

os.environ["NODAL_NO_DOTENV"] = "1"            # never load the real .env in tests
os.environ.pop("SARVAM_API_KEY", None)
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "nodal-stock-mover" / "backend"))
DATA = ROOT / "manyavar-vch3-data" / "csv"


def pytest_collection_modifyitems(config, items):
    if not DATA.exists():
        skip = pytest.mark.skip(reason="organiser CSVs not found at manyavar-vch3-data/csv")
        for it in items:
            it.add_marker(skip)


@pytest.fixture(scope="session")
def result():
    from nodal.engine import run
    return run(str(DATA))


@pytest.fixture(scope="session")
def data():
    from nodal.loader import load_all
    return load_all(DATA)
