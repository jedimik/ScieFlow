"""scite access: policy (config/scite.yml), cache, ledger and transports.

Every request goes policy -> cache -> ledger -> transport (`gateway.request`).
Design: docs/superpowers/specs/2026-10-13-scite-review-and-discovery-design.md §2.
"""
