"""Commented YAML skeletons for `scieflow design init` (plan 5.2 `template`).

Every free-text field is TODO, so `design check` reports only `todo` errors until a person fills
the skeleton in. The skeletons hold no data. Never paste patient-level data into a design.
"""

from __future__ import annotations

import json


def template(study_type: str, title: str) -> str:
    comp = study_type == "computational"
    diag = study_type == "diagnostic-accuracy"
    rct = study_type == "rct"
    lines = [
        "# Study design (M-D1). Replace every TODO, then run: scieflow design check <slug>",
        "# Use synthetic or aggregate values while drafting; never paste patient-level data.",
        "version: 1",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"study_type: {study_type}",
        "question: TODO",
    ]
    if not comp:
        lines += ["pico:", "  population: TODO", "  intervention: TODO", "  comparator: TODO",
                  "  outcome: TODO", "  timeframe: TODO"]
    role = "index-test" if diag else "outcome"
    lines += ["variables:", f"  - {{id: outcome_var, name: TODO, role: {role}, type: continuous}}"]
    if diag:
        lines.append("  - {id: reference_var, name: TODO, role: reference-standard, type: binary}")
    if rct:
        lines += ["groups:", "  - {id: control, name: TODO, allocation: 0.5}",
                  "  - {id: intervention, name: TODO, allocation: 0.5}"]
    if not comp:
        lines.append("sampling: {method: consecutive, inclusion: [TODO], exclusion: [TODO]}")
    if rct:
        lines.append("randomisation: {method: TODO, unit: TODO}")
    if comp:
        lines.append("sample_size: {method: not-applicable, rationale: TODO}")
    else:
        lines.append("sample_size: {method: TODO, rationale: TODO}")
    lines.append("sap:")
    if comp:
        lines.append("  estimands: []")
    else:
        lines += ["  estimands:",
                  "    - {id: primary_estimand, outcome: primary_outcome, population: TODO, "
                  "summary_measure: TODO}"]
    lines += ["  outcomes:",
              "    - {id: primary_outcome, variable: outcome_var, priority: primary, test: TODO, "
              "metric: TODO}",
              "  multiplicity: {method: none, alpha: 0.05}"]
    if not comp:
        lines.append("  missing_data: {strategy: TODO}")
        lines.append("reporting_guidelines: [TODO]")
    lines.append("data: {levels_needed: [L0]}")
    return "\n".join(lines) + "\n"
