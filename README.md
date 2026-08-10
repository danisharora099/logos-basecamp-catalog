# Danish's Logos Apps — a Logos Basecamp module catalog

One repository URL that installs every LEZ app I publish:

```text
https://logos.substratestudios.xyz/logos-repo.json
```

Paste it into **Logos Basecamp → Settings → Repositories → Add repository**, then
install from the App Manager. Human-readable version of the same thing:
<https://logos.substratestudios.xyz/>.

This repo is the *catalog* — the two JSON files, the landing page, and the deploy
script. It contains no application code. The apps themselves live in their own
repositories and every `.lgx` is downloaded from there.

## What's in the catalog

| App | Modules | Source repo |
| --- | --- | --- |
| LEZ Faucet | `lez_faucet` (core), `lez_faucet_ui` (ui_qml) | [`logos-co/lez-faucet`](https://github.com/logos-co/lez-faucet) |
| ETH ↔ LEZ Atomic Swaps | `swap` (core), `swap_ui` (ui_qml) | [`logos-co/eth-lez-atomic-swaps`](https://github.com/logos-co/eth-lez-atomic-swaps) |

Versions are deliberately not listed here — they change without this file being
touched, and a third stale copy of a number helps nobody. The live answer:

```sh
curl -s https://logos.substratestudios.xyz/index.json \
  | jq '[.packages[] | {(.name): [.versions[].manifest.version]}]'
```

0.3.x and later ship `darwin-arm64`, `linux-amd64`, and `linux-arm64` variants;
0.2.0 and earlier are `darwin-arm64` only. Public testnet only, unsigned, and only
the macOS variant has actually been run. See [Known limits](#known-limits).

## How a release reaches users

Automatic end to end, in about twenty minutes, for both apps:

```text
bump metadata.json in a PR
  └─ merge to the app repo's default branch
       └─ release-on-merge.yml publishes <module>-v<version>   (core, then ui)
            └─ rebuild-index.yml refreshes that repo's rolling `index` release
                 └─ host cron merges both repos' indexes into the served
                    index.json, every 15 minutes
```

**A merge that changes no `metadata.json` version releases nothing**, by design —
the version *is* the release trigger. That is the first thing to check when a fix
is on the default branch but not in the App Manager: it probably has no version.

Two things this does *not* cover:

- **`site/index.json` in this repo is a mirror, not the source.** The host cron
  rewrites the served file in place; git is not in that path. Always resync before
  deploying — `scripts/deploy-catalog.sh` refuses to publish an index that is
  behind live, but do not rely on being saved.
- **`index.html` version numbers are hand-written** and go stale on their own.

The sync itself lives on the host at `~/logos-catalog-sync/sync-catalog.py`
(`*/15` in the `deploy` crontab), which validates, keeps dated backups, and leaves
the previous file in place on any fetch or validation failure.

## Layout

```text
site/
  logos-repo.json   the descriptor a user pastes into Basecamp; names index.json
  index.json        schemaVersion 2 package listing, aggregated across both app repos
  index.html        the landing page (self-contained: inline CSS/JS, offline QR SVG)
  assets/*.png      app screenshots used by the page
scripts/
  deploy-catalog.sh rsync to the host, then verify the live site
```

`site/` is exactly what is served at the document root. Nothing is built or
templated; what you edit is what ships.

## Why one URL can serve two repositories

Each app repo already publishes its own catalog, but a user would have to add two
repository URLs to get both apps. This catalog collapses that into one.

It works because Basecamp treats `indexUrl` as opaque. `fetchIndex` retrieves it
verbatim and `appendCatalogEntries` copies each version entry through without ever
comparing the asset URL's host against the descriptor's host
([`logos-package-downloader`](https://github.com/logos-co/logos-package-downloader),
`src/package_downloader_lib.cpp`, ~line 628). Trust comes from `rootHash` plus the
manifest binding, not from where the file is hosted, so a descriptor may point at
`.lgx` assets anywhere.

So `index.json` here lists **GitHub release assets from both app repos, unmodified**.
We host only the two JSON files and the page. We never re-host a `.lgx`, so we cannot
alter a package, and every download still comes from the publishing repo.

The format spec is
[`docs/catalog-format.md`](https://github.com/logos-co/logos-modules-release-tool/blob/main/docs/catalog-format.md)
in `logos-co/logos-modules-release-tool`.

## Adding an app

1. Publish the app's `.lgx` assets from its own repo (nothing is hosted here), and
   give that repo a `rebuild-index.yml` so it maintains a rolling `index` release.
2. Register it on the host, in `~/logos-catalog-sync/sync-catalog.py`:

   ```python
   ORG_INDEXES = {
       "https://github.com/logos-co/<repo>/releases/download/index/index.json":
           ("<core_module>", "<ui_module>"),
       ...
   }
   ```

   From then on its releases are picked up automatically. Do not hand-copy
   `packages[]` entries — and never hand-compute a `rootHash`, which must equal the
   `.lgx`'s own `manifest.hashes.root` or Basecamp rejects the install.
3. Add a plate to `site/index.html` (screenshot in `site/assets/`, 4:3) and update
   the counts in the section heading and the footer.
4. `./scripts/deploy-catalog.sh` — publishes the page. The index arrives on its own.

### Checking the index by hand

The two upstream sources, both public and unauthenticated:

```sh
curl -sSL https://github.com/logos-co/lez-faucet/releases/download/index/index.json
curl -sSL https://github.com/logos-co/eth-lez-atomic-swaps/releases/download/index/index.json
```

The host cron validates every merge it publishes (shape, `manifest` consistency,
and a live `HEAD` of each `.lgx` asserting `200` and `Content-Length` == the
index's `size`). To re-check independently, use the real tool
([`logos-co/logos-modules-release-tool`](https://github.com/logos-co/logos-modules-release-tool)),
which needs Python ≥ 3.10 (macOS system `python3` is 3.9 and crashes on its
`str | None` annotations):

```sh
curl -s https://logos.substratestudios.xyz/index.json -o /tmp/index.json
python3.12 /path/to/logos-modules-release-tool/index.py validate /tmp/index.json
```

Sync activity is in `~/logos-catalog-sync/sync.log` on the host, with dated
pre-change copies in `~/logos-catalog-sync/backups/`.

### One local edit we make, and why

Basecamp's App Manager renders a module with no `display_name` as an empty name
(`package_downloader_lib.cpp` reads the snake_case `display_name` off
`versions[0].manifest`). Upstream originally shipped both swap modules without it, so
our copy adds `"display_name": "ETH ↔ LEZ Atomic Swap"`. It is display-only and is
not one of the five fields re-verified after download (`name`, `version`, `main`,
`dependencies`, `type`), so installs still verify against the real asset.

This is no longer a hand edit: it is `DISPLAY_NAMES` in the host's
`sync-catalog.py`, applied with `setdefault` on every run, so it is idempotent and
retires itself as upstream releases carry the field. That matters because when the
sync first started copying upstream entries through verbatim it silently dropped the
patch, and the three entries below served a null name for four days before anyone
noticed. A rule that re-asserts itself every fifteen minutes cannot rot that way.

**As of `swap` 0.3.1 the upstream fix has landed for both modules**, so the edit no
longer touches any current release — only the three older entries that were published
without the field:

| Entry | `display_name` upstream | In our copy |
| --- | --- | --- |
| `swap` 0.3.1 | **yes** | upstream's, unmodified — no patch |
| `swap` 0.3.0 | no | patched |
| `swap` 0.2.0 | no | patched |
| `swap_ui` 0.3.0 | yes | upstream's, unmodified — no patch |
| `swap_ui` 0.2.0 | no | patched |

The root cause was never a source defect: `swap-module/metadata.json` has carried
`display_name` all along. `swap-module/flake.lock` pinned a revision of the packaging
tooling from before the field existed, so the packager silently dropped it on the way
into the bundle and stamped the manifest with the tooling's own stale
`"manifestVersion": "0.2.0"` — which is why `swap` 0.3.0 reports `manifestVersion`
`0.2.0` against `version` `0.3.0`. The lock is per-repo, which is why `lez_faucet` —
a core module built by the same tooling, pinned in `lez-faucet` — came out right all
along. Bumping the swap lock fixed both symptoms at once: `swap` 0.3.1 carries
`display_name` and `"manifestVersion": "0.3.0"`. Tracked
as [`eth-lez-atomic-swaps#60`](https://github.com/logos-co/eth-lez-atomic-swaps/issues/60).
`swap_ui` was not re-released, because its manifest was already correct.

Where the field is missing we still copy the manifest through exactly as published —
stale `manifestVersion` and all — and add only `display_name`. The patch stays on
those three entries permanently. Basecamp reads `display_name` off `versions[0]`
only, and every release since 0.3.1 carries the field upstream, so these three
change nothing the App Manager renders; they persist so each entry still describes
itself rather than silently inheriting a name from a newer release. **There is no future release that retires them:** a published
`.lgx` is immutable, and 0.2.0 and 0.3.0 will not be re-cut just to add a display
name. Treat these three as permanent, not as a TODO.

## Deploying

```sh
./scripts/deploy-catalog.sh
```

This publishes the **page**. The index arrives on its own — see
[How a release reaches users](#how-a-release-reaches-users) — so a deploy is only
needed for `index.html`, `logos-repo.json`, or assets.

Content changes need no service restart — Caddy serves the content directory from a
read-only bind mount. The script refuses to publish on a stale hostname, invalid
JSON, a `rootHash` that disagrees with its manifest, or a `signature` key on an
unsigned entry; then it rsyncs, and verifies TLS, content types, and served-vs-local
bytes.

It also refuses to publish an `index.json` that is **behind the live one**. The host
cron owns the served index, so this checkout goes stale on its own, and the publish
is `rsync --delete` — every other check passes on a stale copy, because a stale copy
is perfectly self-consistent. If it stops you, resync and retry:

```sh
curl -s https://logos.substratestudios.xyz/index.json -o site/index.json
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `CATALOG_HOST` | `vps` | SSH alias of the target host. Needs a `Host` entry in `~/.ssh/config`. |
| `CATALOG_NEIGHBOURS` | *(empty)* | Space-separated `url:expected_code` pairs for the other sites on the same Caddy, checked before and after publishing. |

The host is a **shared** box running unrelated services, so it is referred to only by
SSH alias and the co-tenant hostnames are never committed to this public repo. Supply
them per-machine when you want the blast-radius check:

```sh
export CATALOG_NEIGHBOURS="https://example.com:200 https://other.example.com:302"
```

## Hosting

The one-time setup, recorded so it can be rebuilt. Already done for
`logos.substratestudios.xyz`. Scope every change to this site's own files and never
restart anything you did not add. Below, `$CADDY_DIR` is wherever the shared Caddy's
compose stack lives (`/opt/services/caddy` on the current host).

1. **DNS** — none needed here: the apex domain has a wildcard `A` record, so the
   subdomain already resolved and Caddy auto-issued the Let's Encrypt cert on the
   first request.
2. **Content dir**
   ```sh
   sudo install -d -o deploy -g deploy -m 0755 /srv/logos-catalog
   ```
3. **Bind mount** — add to the Caddy compose file under `volumes:`
   ```yaml
   - /srv/logos-catalog:/srv/logos-catalog:ro
   ```
4. **Site block** — append to the Caddyfile: security headers,
   `Access-Control-Allow-Origin "*"` on `/*.json` (the Basecamp client fetches them
   cross-origin), `root * /srv/logos-catalog`, `file_server`.
5. **Validate before restarting anything**, in a throwaway container so a bad config
   cannot take the shared proxy down:
   ```sh
   # Mount the Caddyfile plus any files it `import`s, or validate fails on the
   # imports rather than on your change.
   sudo docker run --rm \
     -v "$CADDY_DIR/Caddyfile:/etc/caddy/Caddyfile:ro" \
     caddy:2.10.2-alpine caddy validate --config /etc/caddy/Caddyfile
   ```
6. **Apply** — `docker compose up -d` in the Caddy service dir. A recreate is
   required only because a new bind mount was added; later Caddyfile-only edits need
   just `docker restart caddy`. Either way there is a ~2s blip **for every site on the
   box**, because this Caddy runs `admin off` and cannot hot-reload.

Keep a `.bak` of the Caddyfile and compose file from before the rollout; the deploy
script prints the rollback command if verification fails.

## Known limits

- **No Linux package has run under Basecamp itself.** Each 0.3.x `.lgx` carries
  `linux-amd64` and `linux-arm64` payloads next to `darwin-arm64`, verified present in
  the published bundles (`missingVariants: []`). Beyond that the two apps differ, and
  the difference is worth knowing before you rely on either:
  - **The faucet is loaded headlessly on every pull request**, on x86-64 and ARM64
    runners: `lgpm` installs the built package, the right variant is selected, the
    plugin is `dlopen`ed with every symbol bound (`RTLD_NOW`), and the `logoscore`
    module host loads `lez_faucet` and reads back its interface. That proves the
    Linux binaries link and load — not that the app works.
  - **The swap has no equivalent check.** Its Linux payloads are built and verified
    present, and nothing more.
  Neither app's Linux build has been exercised through the Basecamp GUI, and no view
  has been rendered on Linux. Interactive behaviour has only been confirmed on macOS
  Apple Silicon. There are no Windows packages, and 0.2.0 and earlier remain
  `darwin-arm64` only.
- **`swap` needs `delivery_module`, which this catalog does not carry.** It resolves
  from the official Logos repository, which Basecamp ships enabled by default
  (`package_downloader_lib.cpp:369-373`). A user who disabled the default repo cannot
  install the swap from here.
- **Everything is unsigned.** `trustedSigners` is empty, so Basecamp verifies the
  bytes against `rootHash` but cannot attest a publisher.
- **A release still starts with a human bumping a version.** Everything after that
  is automatic ([how a release reaches users](#how-a-release-reaches-users)), but a
  fix merged without a `metadata.json` bump ships nowhere and reports no error.
- **The landing page's version numbers are hand-written.** `index.json` is synced;
  `index.html` is not, so its badges and counts drift until someone edits them.

## License

MIT OR Apache-2.0, matching the app repos. See [LICENSE-MIT](LICENSE-MIT) and
[LICENSE-APACHE](LICENSE-APACHE).
