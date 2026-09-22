# Optional DVC transport

By default ScieFlow never moves a bundle: `backup` writes a file, `restore`
reads one, and getting it from PC A to PC B is your own business.

If you already run DVC storage for this repo, you can let it carry bundles
too. It is **off until you turn it on**, and it is deliberately narrower than
`scripts/dvc_sync.py`: it moves **bundle files only, one at a time**, never a
workspace or a directory.

## Turn it on

```yaml
# config/chats.yml
remote:
  enabled: true
  dvc_remote: null       # null = the repo's DVC core.remote
  dir: workspace/chats   # where .dvc pointers live inside the repo
```

## The short way

Two scripts do the whole round trip, asking which agents and which projects
you mean:

```bash
./scripts/chats-push.sh       # pick agents → pick projects → bundle → upload
./scripts/chats-pull.sh       # pick a bundle → fetch → inspect → restore
```

Both are also in the menu (`uv run scieflow` → Chats). Everything is ticked by
default, so enter three times backs up everything. Give flags to skip the
questions entirely:

```bash
./scripts/chats-push.sh --tool claude --project SegSnake --yes
./scripts/chats-push.sh --tool codex --since 2026-09-01 --commit
./scripts/chats-pull.sh --latest --tool claude
./scripts/chats-pull.sh --latest --no-restore          # fetch and inspect only
```

`--commit` git-commits the `.dvc` pointer after a successful upload, so the
bundle is findable from another machine once you push the branch. `--no-push`
builds the bundle without uploading. On pull, the restore is a dry run and
you are asked before anything is written; `--map OLD=NEW` passes through.

The rest of this page is what those scripts call, in case you want the pieces
separately.

## Push

```console
$ uv run scieflow chats push ~/scieflow-chat-bundles/scieflow-chats-pc1-20260920-1432.zip.gpg
uploaded scieflow-chats-pc1-20260920-1432.zip.gpg
pointer  /home/you/Github/ScieFlow/workspace/chats/scieflow-chats-pc1-20260920-1432.zip.gpg.dvc
next     git add workspace/chats/scieflow-chats-pc1-20260920-1432.zip.gpg.dvc && git commit -m 'chore(chats): track scieflow-chats-pc1-20260920-1432.zip.gpg'
```

The bundle is uploaded with `dvc add --to-remote`, so nothing is cached
locally in the repo — only a small `.dvc` pointer is left behind. Commit the
pointer: that is how the other machine finds the bundle.

**An unencrypted bundle is refused, with no override.** On shared storage it
would be readable by anyone with bucket access.

## Pull

On the other machine, after pulling the branch that carries the pointer:

```console
$ uv run scieflow chats pull
  scieflow-chats-pc1-20260920-1432.zip.gpg

Fetch one with: uv run scieflow chats pull <name>

$ uv run scieflow chats pull scieflow-chats-pc1-20260920-1432.zip.gpg
fetched /home/me/scieflow-chat-bundles/scieflow-chats-pc1-20260920-1432.zip.gpg
next    uv run scieflow chats restore /home/me/scieflow-chat-bundles/scieflow-chats-pc1-20260920-1432.zip.gpg
```

`--to DIR` fetches somewhere other than `bundle_dir`. An existing file is
never overwritten.

## The other machine needs a DVC remote

`.dvc/config` is **gitignored**, so it does not travel with the branch and
your endpoint does not leak into every checkout. On a new machine, recreate it
once:

```bash
dvc remote add -d s3remote s3://dvc-projects/scieflow
dvc remote modify s3remote endpointurl https://s3.cl4.du.cesnet.cz
dvc remote modify s3remote region eu-central-1
# credentials stay local, in .dvc/config.local:
dvc remote modify --local s3remote access_key_id <key>
dvc remote modify --local s3remote secret_access_key <secret>
```

## What this is not

It does not replace `scripts/dvc_sync.py`, and it never touches a workspace
run. Two separate things share one bucket:

| | `scieflow chats push/pull` | `scripts/dvc_sync.py push/pull` |
|---|---|---|
| Moves | one bundle file | one workspace run, as a zip |
| Opt-in | `remote.enabled` in `config/chats.yml` | always available |
| Selection | one named bundle | one or more named slugs |
| Encryption | required | not applicable |

## Agents do not push

`src/scieflow/chats/AGENTS.md` forbids an agent from pushing, pulling or
otherwise transmitting a bundle. These commands are yours to run.
