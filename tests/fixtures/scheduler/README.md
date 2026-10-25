# Scheduler status fixtures (M-E t2)

Each file is the stdout that one status command returns over SSH (or locally),
exactly as the adapter composes it. The tests feed these to `parse_status`.

Provenance (M-E-D9): every file is SYNTHETIC. None is a live capture.

- `pbs/`: `qstat -xf` key = value layout, as pinned by `tests/test_remote_pbs_golden.py`.
- `slurm/`: `sacct -X -n -P` rows (`JobID|State|ExitCode|Elapsed`), then the literal
  `#squeue` marker, then `squeue -h -o '%i|%T'` rows. Field shapes follow the public
  sacct and squeue man pages. Not captured from a real cluster.
- `ssh/`: the one-line strings the wrapper's status command prints (`EXIT n`,
  `RUNNING`, `CANCELLED`, `GONE`). These come from the design in
  `reviews/m-e-plan.md` section 5.5, not from a real host.

Slurm and plain SSH are experimental (D19). Passing these fixtures does not show
that a real Slurm cluster or SSH host behaves this way.
