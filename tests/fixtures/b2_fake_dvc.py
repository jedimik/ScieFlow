"""T-9 t3 fake `dvc` executable for tests. The real dvc is never run (B2 R1).

install(bin_dir) writes bin_dir/dvc, a Python script that
  - appends its argv (as JSON, one line per run) to bin_dir/argv.log;
  - reads bin_dir/script.json: an optional sleep, optional bytes written to the file named by its last
    argument without the trailing ".dvc" (the zip, relative to the working directory), lines printed to
    stdout and stderr, and the exit code.
Tests put bin_dir first on PATH themselves. set_script() chooses what the next run does; argv() reads back
every run. Nothing here touches the real home, a remote or the network.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

_TEMPLATE = '''#!{python}
import base64, json, os, sys, time
HERE = {here!r}
with open(os.path.join(HERE, "argv.log"), "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
with open(os.path.join(HERE, "script.json")) as fh:
    script = json.load(fh)
if script.get("sleep"):
    time.sleep(script["sleep"])
if script.get("content_b64") is not None:
    target = sys.argv[-1][: -len(".dvc")]
    if script.get("unlink") and os.path.lexists(target):
        os.unlink(target)  # DVC replaces an existing output (a link too) instead of writing through it
    with open(target, "wb") as fh:
        fh.write(base64.b64decode(script["content_b64"]))
for line in script.get("stdout", []):
    sys.stdout.write(line + "\\n")
sys.stdout.flush()
for line in script.get("stderr", []):
    sys.stderr.write(line + "\\n")
sys.stderr.flush()
sys.exit(script.get("exit", 0))
'''


def install(bin_dir: Path) -> Path:
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "dvc"
    exe.write_text(_TEMPLATE.format(python=sys.executable, here=str(bin_dir)))
    exe.chmod(0o755)
    set_script(bin_dir)
    return bin_dir


def set_script(bin_dir: Path, *, exit_code: int = 0, stdout=(), stderr=(),
               content: bytes | None = None, sleep: float = 0, unlink: bool = False) -> None:
    doc: dict = {"exit": exit_code, "stdout": list(stdout), "stderr": list(stderr), "sleep": sleep,
                 "unlink": unlink}
    if content is not None:
        doc["content_b64"] = base64.b64encode(content).decode("ascii")
    (Path(bin_dir) / "script.json").write_text(json.dumps(doc))


def argv(bin_dir: Path) -> list[list[str]]:
    log = Path(bin_dir) / "argv.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line]
