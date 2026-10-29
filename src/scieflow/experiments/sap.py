"""Binding to the locked study design's SAP (M-D2 plan 5.5).

`binding(run_dir)` reads the design's lock state and, only when it is locked, the SAP outcomes and
multiplicity of the in-force design. `conflicts(...)` lists where a campaign's analysis block
contradicts a locked SAP. Reading never writes. An unexpected error becomes status "error", and no
SAP binding is claimed (fail closed). Refusing or warning is the caller's decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.design import lock as design_lock
from ..core.design import model as design_model
from ..core.project import Project
from ..core.run import actions

# SAP multiplicity method -> campaign correction name (plan 5.5; M-D2-D6).
CORRECTION_MAP = {
    "none": "none", "bonferroni": "bonferroni", "holm": "holm", "hochberg": "hochberg",
    "bh-fdr": "bh",
}
UNSUPPORTED_CORRECTIONS = frozenset({"hierarchical", "gatekeeping"})
CONFIRMATORY_PRIORITIES = frozenset({"primary", "secondary"})


@dataclass(frozen=True)
class Binding:
    status: str
    version: object = None
    in_force_hash: object = None
    study_type: object = None
    outcomes: dict = field(default_factory=dict)  # metric -> {"outcome", "test", "priority"}
    multiplicity: dict | None = None  # {"method", "alpha"} from the SAP, or None
    problem: str | None = None

    @property
    def locked(self) -> bool:
        return self.status == "locked"

    @property
    def computational(self) -> bool:
        return self.study_type == "computational"


def binding(run_dir) -> Binding:
    """The SAP binding of the design held by one run workspace (plan 5.5)."""
    ws = Path(run_dir)
    try:
        st = design_lock.state(Project(ws.parent.parent), ws)
        if st.status != "locked":
            return Binding(status=st.status, version=st.version, in_force_hash=st.in_force_hash,
                           study_type=st.study_type, problem=st.problem)
        design = design_model.load(ws)
        if not isinstance(design, dict):
            return Binding(status="error", problem="design-unreadable")
        sap = design.get("sap") or {}
        outcomes = {}
        for out in sap.get("outcomes") or []:
            metric = out.get("metric") if isinstance(out, dict) else None
            if isinstance(metric, str):
                outcomes[metric] = {"outcome": out.get("id"), "test": out.get("test"),
                                    "priority": out.get("priority")}
        mult = sap.get("multiplicity")
        multiplicity = ({"method": mult.get("method"), "alpha": mult.get("alpha")}
                        if isinstance(mult, dict) else None)
        return Binding(status="locked", version=st.version, in_force_hash=st.in_force_hash,
                       study_type=st.study_type, outcomes=outcomes, multiplicity=multiplicity)
    except Exception as e:  # fail closed: no SAP binding is claimed
        return Binding(status="error", problem=type(e).__name__)


def binding_for_campaign(campaign_dir) -> Binding:
    """The binding of the run that holds a campaign folder; status none outside a run."""
    run = actions.run_for_path(Path(campaign_dir))
    if run is None:
        return Binding(status="none")
    return binding(run)


def expected_correction(b: Binding) -> str | None:
    """The campaign correction the locked SAP prescribes; None when the SAP's method is unsupported."""
    if b.multiplicity is None:
        return "none"
    method = b.multiplicity.get("method")
    if method in UNSUPPORTED_CORRECTIONS:
        return None
    return CORRECTION_MAP.get(method)


def sap_alpha(b: Binding, default: float) -> float:
    """The SAP's alpha when it states one, otherwise the campaign's (plan 5.5)."""
    if b.multiplicity is not None and isinstance(b.multiplicity.get("alpha"), (int, float)):
        return float(b.multiplicity["alpha"])
    return default


def conflicts(b: Binding, spec, metrics) -> list[dict]:
    """Where the campaign's analysis block contradicts a locked SAP (plan 5.5).

    Only a locked design is checked. Each item is {rule, metric, message}. `out-of-sap` marks a
    campaign metric that no SAP outcome names. Refusing (non-computational) or warning
    (computational) is the caller's decision.
    """
    if not b.locked:
        return []
    out: list[dict] = []
    for metric in metrics:
        named = b.outcomes.get(metric)
        if named is None:
            out.append({"rule": "out-of-sap", "metric": metric,
                        "message": f"campaign metric {metric} is not named by any SAP outcome"})
            continue
        if metric in spec.tests and spec.tests[metric] != named["test"]:
            out.append({"rule": "sap-test-mismatch", "metric": metric,
                        "message": (f"analysis.test for {metric} is {spec.tests[metric]}; the locked "
                                    f"SAP says {named['test']} for {metric} (remove analysis.test or "
                                    "amend the design with `scieflow design amend`)")})
    if "correction" in spec.given:
        expected = expected_correction(b)
        if expected != spec.correction:
            what = ("is not applied by this tool" if expected is None
                    else f"the locked SAP says {expected}")
            out.append({"rule": "sap-correction-mismatch", "metric": None,
                        "message": (f"analysis.correction is {spec.correction}; {what} "
                                    "(remove analysis.correction or amend the design)")})
    if "alpha" in spec.given and b.multiplicity is not None \
            and isinstance(b.multiplicity.get("alpha"), (int, float)) \
            and spec.alpha != float(b.multiplicity["alpha"]):
        out.append({"rule": "sap-alpha-mismatch", "metric": None,
                    "message": (f"analysis.alpha is {spec.alpha}; the locked SAP says "
                                f"{b.multiplicity['alpha']} (remove analysis.alpha or amend the design)")})
    return out
