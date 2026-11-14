"""Order-path LOGIC for the future M3-B demo milestone: pure, offline, no connector.

Design: docs/m3b/ORDER-PATH-DESIGN.md. Nothing here performs I/O, holds a credential, signs a
request or talks to an exchange. The exchange and the durable store are abstract ports (ports.py);
the real adapters are M3-B work and are NOT authorized. The package exists so the duplicate-order
controls (finding F-12, readiness blocker B-6) can be specified and proven against a fake exchange
BEFORE any order path exists.
"""
