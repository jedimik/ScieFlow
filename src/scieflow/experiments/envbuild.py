from __future__ import annotations

import subprocess
from pathlib import Path

from .stage import Stage, sif_path

PIP_TEMPLATE = """\
Bootstrap: docker
From: {base_image}

%post
    {apt_line}
    {pip_line}
"""

CONDA_TEMPLATE = """\
Bootstrap: docker
From: mambaorg/micromamba:1.5.10

%files
    {conda_file} /environment.yml

%post
    micromamba install -y -n base -f /environment.yml
    micromamba clean --all --yes

%environment
    export PATH=/opt/conda/bin:$PATH
"""


def generate_def(stage: Stage) -> str:
    env = stage.environment
    if env.conda_file:
        return CONDA_TEMPLATE.format(conda_file=env.conda_file)
    apt_line = (
        "apt-get update && apt-get install -y --no-install-recommends "
        + " ".join(env.apt)
        + " && rm -rf /var/lib/apt/lists/*"
        if env.apt
        else "true"
    )
    pip_line = (
        "pip install --no-cache-dir " + " ".join(env.pip) if env.pip else "true"
    )
    return PIP_TEMPLATE.format(
        base_image=env.base_image, apt_line=apt_line, pip_line=pip_line
    )


def build_sif(stage: Stage, force: bool = False) -> Path:
    env_dir = stage.dir / "env"
    env_dir.mkdir(exist_ok=True)
    def_path = env_dir / f"{stage.name}.def"
    def_path.write_text(generate_def(stage))
    sif = sif_path(stage)
    if sif.exists() and not force:
        return sif
    subprocess.run(
        ["apptainer", "build", "--force", str(sif), str(def_path)], check=True
    )
    return sif
