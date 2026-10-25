"""Local adapter. Experimental: the plain SSH command strings, run on this host.

It subclasses the ssh adapter, so its command text is the same. Only the
transport differs (LocalTransport runs the string through bash). It refuses
any host other than localhost and any auth other than none.
"""

from remote.errors import PolicyError
from remote.schedulers.ssh import SshScheduler


class LocalScheduler(SshScheduler):
    name = "local"
    transport = "local"
    needs_kerberos = False

    def validate_remote(self, entry: dict) -> None:
        if entry.get("host") != "localhost" or entry.get("auth") != "none":
            raise PolicyError(
                "local scheduler needs host: localhost and auth: none "
                "on the remote entry in config/remotes.yml"
            )
