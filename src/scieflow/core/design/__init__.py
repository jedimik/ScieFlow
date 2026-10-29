"""Study design (M-D1): reporting checklists under config/reporting/ and `scieflow design`.

Data, validation, rendering and lock bookkeeping. Nothing here runs an analysis. Run writes are
limited to the run's design/ directory (design.md, pending.json, amendment diffs, the lock ledger and
locked copies); the ledger and locked copies change only when a human answers the design-lock gate.
"""
