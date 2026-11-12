"""ASTA - spot-only trading research platform. M0: public market data only, NO order execution."""

import os

# Some Windows App Control policies block SQLAlchemy's compiled extensions; fall back to pure Python.
os.environ.setdefault("DISABLE_SQLALCHEMY_CEXT_RUNTIME", "1")

__version__ = "0.0.1"
