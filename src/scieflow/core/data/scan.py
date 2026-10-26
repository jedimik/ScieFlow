"""Scan a run for undeclared data that looks like patient data (M-B Part A 5.6). RED stub."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    detail: str


@dataclass(frozen=True)
class ScanResult:
    findings: tuple
    files_seen: int
    truncated: bool


def _read_bytes(path, limit):
    raise NotImplementedError("M-B t4: scan not implemented yet")


def scan(ws, pol, *, clock=None):
    raise NotImplementedError("M-B t4: scan not implemented yet")
