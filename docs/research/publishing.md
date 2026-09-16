# Publishing docs publicly (private repo)

You can keep the **source repo private** and still serve a **public docs
site**. Which route you take depends on your GitHub plan.

!!! info "The determining rule"
    GitHub Pages can publish from a **private** repository only on **GitHub
    Pro** (personal) or **Team / Enterprise** (organizations). On **GitHub
    Free**, Pages publishes only from a *public* repo — so you deploy the
    built HTML to a separate public repo instead.

    Either way the *published site is public*. Truly access-controlled
    (private) Pages requires GitHub Enterprise Cloud.

Check your plan at [github.com/settings/billing](https://github.com/settings/billing).

---

## Route A — GitHub Pro / Team / Enterprise (same repo)

Publish Pages straight from this private repo. Three one-time steps:

1. **Settings → Pages → Source = "GitHub Actions".**
2. **Settings → Secrets and variables → Actions → Variables → New variable:**
   `DEPLOY_PAGES` = `true`.
3. Push any docs change (or Actions tab → **Docs** → **Run workflow**).

The `deploy` job in `.github/workflows/docs.yml` then builds and publishes to
`https://<user>.github.io/ScieFlow/` on every push. Source stays private; the
site is public. Nothing else to set up — no second repo, no tokens.

To stop publishing, delete the `DEPLOY_PAGES` variable; the build-only check
keeps running.

---

## Route B — GitHub Free (separate public repo)

Your private repo's CI builds the site and pushes **only the rendered HTML** to
a small public repo that serves Pages. Your source code never leaves the
private repo.

### One-time setup

1. **Create a public repo**, e.g. `ScieFlow-docs` (or `<user>.github.io` for a
   user-level site).
2. **Generate a deploy credential** the private repo can use to push to it:
    - Simplest: a **fine-grained Personal Access Token** scoped to the public
      repo with **Contents: read and write**.
    - Copy the token.
3. In the **private** repo: **Settings → Secrets and variables → Actions →
   Secrets → New secret:** `DOCS_DEPLOY_TOKEN` = the token.
4. In the **public** repo: **Settings → Pages → Source = "Deploy from a
   branch" → `gh-pages` / root** (after the first deploy creates that branch).
5. Set `site_url` in `mkdocs.yml` to the public site's URL
   (`https://<user>.github.io/ScieFlow-docs/`).

### Add this workflow to the PRIVATE repo

Save as `.github/workflows/docs-crossrepo.yml` and edit the `external_repository`
line:

```yaml
name: Publish docs to public repo

on:
  push:
    branches: [main]
    paths: ["docs/**", "mkdocs.yml"]
  workflow_dispatch:

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv run --group docs mkdocs build --strict
      - name: Push built site to the public repo
        uses: peaceiris/actions-gh-pages@v4
        with:
          personal_token: ${{ secrets.DOCS_DEPLOY_TOKEN }}
          external_repository: <user>/ScieFlow-docs   # <-- your public repo
          publish_branch: gh-pages
          publish_dir: ./site
```

On each push the private repo builds and force-pushes `site/` to the public
repo's `gh-pages` branch, which Pages serves. Only compiled HTML is exposed.

!!! warning "Don't publish what you don't mean to"
    The built site contains everything under `docs/` (minus `docs/superpowers/`,
    which `mkdocs.yml` already excludes). Anything you don't want public — API
    keys, internal notes, unreleased results — must not live in `docs/`.

---

## Which should you pick?

| | Route A (Pro) | Route B (Free) |
| --- | --- | --- |
| Extra repo | none | one public repo |
| Secrets/tokens | none | one deploy token |
| Exposes | rendered site only | rendered site only |
| Setup effort | ~2 minutes | ~10 minutes |
| Plan required | Pro / Team / Enterprise | any (incl. Free) |

If you have Pro, use Route A — it's the least moving parts. On Free, Route B is
the standard workaround and works indefinitely.
