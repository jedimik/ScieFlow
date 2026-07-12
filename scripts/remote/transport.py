"""The only module that shells out to ssh/rsync.

Auth is GSSAPI (Kerberos) with BatchMode — no passwords, no keys, no
prompts: a missing ticket fails fast and remote.py turns that into a
kinit instruction for the user.
"""

import subprocess


def _default_runner(argv):
    return subprocess.run(argv, capture_output=True, text=True)


class Transport:
    def __init__(self, remote, runner=None):
        self.remote = remote
        self.runner = runner or _default_runner

    @property
    def target(self) -> str:
        return f"{self.remote.user}@{self.remote.host}"

    def local(self, argv: list):
        return self.runner(list(argv))

    def ssh(self, command: str):
        return self.runner([
            "ssh", "-o", "BatchMode=yes", "-o", "GSSAPIAuthentication=yes",
            self.target, command,
        ])

    def rsync_from(self, remote_path: str, dest: str):
        return self.runner(["rsync", "-az", f"{self.target}:{remote_path}", dest])
