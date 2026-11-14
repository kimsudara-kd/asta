"""Order-path LOGIC for the future M3-B demo milestone: offline, no connector.

Design: docs/m3b/ORDER-PATH-DESIGN.md. Nothing here holds a credential, signs a request or talks
to an exchange. The only I/O is the PostgreSQL adapter in sql_store.py (the durable store); the
exchange is an abstract port (ports.py) whose real adapter is M3-B work and is NOT authorized.
The package exists so the duplicate-order controls (finding F-12, readiness blocker B-6) can be
specified and proven against a fake exchange BEFORE any order path exists.
"""
