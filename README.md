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

| App | Modules | Version | Source repo |
| --- | --- | --- | --- |
| LEZ Faucet | `lez_faucet` (core), `lez_faucet_ui` (ui_qml) | 0.2.0, 0.1.0 | [`logos-co/lez-faucet`](https://github.com/logos-co/lez-faucet) |
| ETH ↔ LEZ Atomic Swaps | `swap` (core), `swap_ui` (ui_qml) | 0.2.0 | [`logos-co/eth-lez-atomic-swaps`](https://github.com/logos-co/eth-lez-atomic-swaps) |

macOS Apple Silicon only, public testnet only, unsigned. See
[Known limits](#known-limits).

## The index is assembled by hand and is NOT on a cron

**`site/index.json` is hand-assembled from the two upstream app repos' own release
indexes. Nothing polls them.** A new upstream release does *not* appear in this
catalog automatically — someone has to merge it in here and re-run the deploy. If a
version is missing from the App Manager, that is the first thing to check.

This is the one piece of cross-repo coupling in the project, and it is deliberate:
see [Refreshing the index](#refreshing-the-index-after-an-upstream-release).

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

1. Publish the app's `.lgx` assets from its own repo (nothing is hosted here).
2. Copy the `packages[]` entry from that repo's release index into
   `site/index.json`, keeping every field byte-for-byte as upstream published it —
   `url`, `rootHash`, `manifest`, `releasedAt`, `size`, `sha256`. Never hand-compute
   a `rootHash`: it must equal the `.lgx`'s own `manifest.hashes.root` or Basecamp
   rejects the install.
3. Add a plate to `site/index.html` (screenshot in `site/assets/`, 4:3) and update
   the counts in the section heading and the footer.
4. `./scripts/deploy-catalog.sh`

### Refreshing the index after an upstream release

Merge the `packages` arrays from the two upstream indexes:

```sh
curl -sSL https://github.com/logos-co/lez-faucet/releases/download/index/index.json
curl -sSL https://github.com/logos-co/eth-lez-atomic-swaps/releases/download/index/index.json
```

Validate with the real tool
([`logos-co/logos-modules-release-tool`](https://github.com/logos-co/logos-modules-release-tool)),
which needs Python ≥ 3.10 (macOS system `python3` is 3.9 and crashes on its
`str | None` annotations):

```sh
python3.12 /path/to/logos-modules-release-tool/index.py validate site/index.json
```

### One local edit we make, and why

The upstream `swap` and `swap_ui` manifests have no `display_name`, so Basecamp's App
Manager would render them with an empty name (`package_downloader_lib.cpp` reads the
snake_case `display_name` off `versions[0].manifest`). We add
`"display_name": "ETH ↔ LEZ Atomic Swap"` to our copy. This is display-only and is
not one of the five fields re-verified after download (`name`, `version`, `main`,
`dependencies`, `type`), so installs still verify against the real asset.

**The real fix belongs upstream** in `eth-lez-atomic-swaps/swap-module/metadata.json`
and `swap-ui/metadata.json`. Drop this edit once that ships.

## Deploying

```sh
./scripts/deploy-catalog.sh
```

Content changes need no service restart — Caddy serves the content directory from a
read-only bind mount. The script refuses to publish on a stale hostname, invalid
JSON, a `rootHash` that disagrees with its manifest, or a `signature` key on an
unsigned entry; then it rsyncs, and verifies TLS, content types, and served-vs-local
bytes.

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

- **macOS Apple Silicon only.** Every published variant is `darwin-arm64`.
- **`swap` needs `delivery_module`, which this catalog does not carry.** It resolves
  from the official Logos repository, which Basecamp ships enabled by default
  (`package_downloader_lib.cpp:369-373`). A user who disabled the default repo cannot
  install the swap from here.
- **Everything is unsigned.** `trustedSigners` is empty, so Basecamp verifies the
  bytes against `rootHash` but cannot attest a publisher.
- **Faucet 0.3.0 is not installable from here.** It is built but untagged; the catalog
  offers 0.2.0. The page says so.
- **The index is not automated.** See
  [above](#the-index-is-assembled-by-hand-and-is-not-on-a-cron).

## License

MIT OR Apache-2.0, matching the app repos. See [LICENSE-MIT](LICENSE-MIT) and
[LICENSE-APACHE](LICENSE-APACHE).
