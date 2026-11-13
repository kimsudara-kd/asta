"""M3-A security contracts (credential isolation + demo environment binding).

Contracts and a mock/test harness ONLY. No real exchange credential exists or is accepted here, no
authenticated request is made, and nothing in this package can submit an order. Import boundary:
this package may import only ``asta.core`` and ``asta.audit``; strategies, research, risk, paper,
market data and AI-facing modules must never import it (enforced by tests/m3a).
"""
