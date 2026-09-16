from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Metric:
    name: str
    fn: Callable
    higher_is_better: bool


REGISTRY: dict[str, Metric] = {}


def register(name: str, fn: Callable, higher_is_better: bool) -> None:
    REGISTRY[name] = Metric(name, fn, higher_is_better)


def get_metric(name: str) -> Metric:
    if name not in REGISTRY:
        raise KeyError(f"unknown metric '{name}'; available: {sorted(REGISTRY)}")
    return REGISTRY[name]


def compute_metrics(names, output_path, reference_path) -> dict[str, float]:
    return {
        n: float(get_metric(n).fn(Path(output_path), Path(reference_path)))
        for n in names
    }


def load_local_metrics(directory: str | Path) -> None:
    directory = Path(directory)
    if not directory.is_dir():
        return
    for py in sorted(directory.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"expx_local_{py.stem}", py)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name, (fn, higher) in getattr(module, "METRICS", {}).items():
            register(name, fn, higher)


from . import image  # noqa: E402,F401  (registers built-in image metrics)
