"""ASTA - spot-only trading research platform. M0: public market data only, NO order execution."""

import os

# Some Windows App Control policies block SQLAlchemy compiled extensions: use pure Python.
os.environ.setdefault("DISABLE_SQLALCHEMY_CEXT_RUNTIME", "1")

__version__ = "0.0.1"
