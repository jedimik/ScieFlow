from types import SimpleNamespace

from remote import policy, transport

REMOTE = policy.Remote(
    name="meta", host="skirit.metacentrum.cz", user="testuser",
    auth="kerberos", scheduler="pbs",
    allowed_dirs=["/storage/x"], allowed_ops=["check"], limits={},
)


def make_transport(calls):
    def runner(argv):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
    return transport.Transport(REMOTE, runner=runner)


def test_ssh_argv():
    calls = []
    t = make_transport(calls)
    result = t.ssh("cd /storage/x && git pull --ff-only")
    assert result.returncode == 0
    assert calls == [[
        "ssh", "-o", "BatchMode=yes", "-o", "GSSAPIAuthentication=yes",
        "testuser@skirit.metacentrum.cz",
        "cd /storage/x && git pull --ff-only",
    ]]


def test_local_and_rsync_argv():
    calls = []
    t = make_transport(calls)
    t.local(["klist", "-s"])
    t.rsync_from("/storage/x/results/", "workspace/run/remote/data/")
    assert calls[0] == ["klist", "-s"]
    assert calls[1] == [
        "rsync", "-az",
        "testuser@skirit.metacentrum.cz:/storage/x/results/",
        "workspace/run/remote/data/",
    ]
