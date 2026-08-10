#!/usr/bin/env bash
#
# Publish site/ to https://logos.substratestudios.xyz/
#
# The catalog is three files that must stay consistent with each other:
#
#   logos-repo.json  the descriptor a user pastes into Basecamp. Names index.json.
#   index.json       schemaVersion 2. Aggregates .lgx releases from BOTH app repos,
#                    which works because Basecamp treats indexUrl as opaque and never
#                    checks that a version's url shares a host with the descriptor.
#   index.html       the human-facing page.
#
# Content updates need no service restart: Caddy serves /srv/logos-catalog straight
# off a read-only bind mount. Only a change to the Caddyfile itself needs a restart,
# because that Caddy runs with `admin off` and so cannot hot-reload.
#
# The one-time host setup this script depends on is in README.md ("Hosting").

set -euo pipefail

HOST="${CATALOG_HOST:-vps}"
REMOTE_DIR="/srv/logos-catalog"
SITE_URL="https://logos.substratestudios.xyz"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_DIR="$REPO_ROOT/site"

# Other sites sharing this Caddy instance. A Caddyfile mistake takes them down too,
# so each run re-checks them. Deliberately NOT hardcoded: this file is tracked in a
# public repo and the co-tenants are private infrastructure. Supply them per-machine:
#
#   export CATALOG_NEIGHBOURS="https://example.com:200 https://other.example.com:302"
#
# Empty is allowed; you just lose the blast-radius check.
read -r -a NEIGHBOURS <<< "${CATALOG_NEIGHBOURS:-}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31merror: %s\033[0m\n' "$*" >&2; exit 1; }

code() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$1" 2>/dev/null || echo 000; }

# ---------------------------------------------------------------- preflight

say "Preflight"

for f in index.html logos-repo.json index.json; do
  [ -f "$LOCAL_DIR/$f" ] || die "missing $LOCAL_DIR/$f"
done

# Malformed JSON here is invisible until Basecamp silently drops the repository,
# so fail before publishing rather than after.
for f in logos-repo.json index.json; do
  python3 -m json.tool "$LOCAL_DIR/$f" >/dev/null || die "$f is not valid JSON"
done

# The three files cross-reference each other by URL. A stale hostname is the failure
# mode that looks fine locally and 404s only for real users, so assert it here.
python3 - "$LOCAL_DIR" "$SITE_URL" <<'PY'
import json, pathlib, sys
site, base = pathlib.Path(sys.argv[1]), sys.argv[2]

repo = json.loads((site / "logos-repo.json").read_text())
for key in ("name", "displayName", "indexUrl"):
    if not repo.get(key):
        sys.exit(f"logos-repo.json: missing required field {key!r} "
                 "(Basecamp drops the whole repository without it)")
want = f"{base}/index.json"
if repo["indexUrl"] != want:
    sys.exit(f"logos-repo.json: indexUrl is {repo['indexUrl']!r}, expected {want!r}")

idx = json.loads((site / "index.json").read_text())
if idx.get("schemaVersion") != 2:
    sys.exit(f"index.json: schemaVersion is {idx.get('schemaVersion')!r}, must be 2")
if idx.get("repositoryName") != repo["name"]:
    sys.exit("index.json repositoryName does not match logos-repo.json name")

for pkg in idx.get("packages", []):
    for v in pkg.get("versions", []):
        # rootHash must equal the .lgx's own manifest root or the install is rejected.
        if v["rootHash"] != v["manifest"]["hashes"]["root"]:
            sys.exit(f"{pkg['name']} {v['manifest']['version']}: "
                     "rootHash does not match manifest.hashes.root")
        # An unsigned catalog must omit the key entirely, not send null or "".
        if "signature" in v:
            sys.exit(f"{pkg['name']}: 'signature' present; omit it when unsigned")

html = (site / "index.html").read_text()
if "modules.substratestudios.xyz" in html:
    sys.exit("index.html still references the old modules.* hostname "
             "(the QR code encodes a URL — regenerate it, do not hand-edit)")
if base not in html:
    sys.exit(f"index.html never mentions {base}")

n = sum(len(p.get("versions", [])) for p in idx.get("packages", []))
print(f"  ok: {len(idx['packages'])} packages / {n} versions, hostnames consistent")
PY

# The served index.json is NOT owned by this repo — a cron on the host rewrites it
# in place every 15 minutes from the app repos' rolling release indexes. So this
# checkout is a mirror that goes stale on its own, and the publish below is
# `rsync --delete`. Every check above passes on a stale copy, because a stale copy
# is perfectly self-consistent; the only way to catch it is to ask what is live.
# Without this, deploying an old checkout silently rolls users back to whatever
# versions it happens to contain.
say "Drift (is the live index newer than this checkout?)"
python3 - "$LOCAL_DIR" "$SITE_URL" <<'PY' || die "local index.json is behind the live one — resync before deploying"
import json, pathlib, sys, urllib.request

site, base = pathlib.Path(sys.argv[1]), sys.argv[2]
local = json.loads((site / "index.json").read_text())

try:
    with urllib.request.urlopen(f"{base}/index.json", timeout=20) as r:
        served = json.loads(r.read().decode())
except Exception as e:
    # Not fatal: a first-ever deploy has nothing live to compare against, and a
    # network blip should not block a deploy the operator is watching.
    print(f"  skipped: could not fetch {base}/index.json ({e})")
    sys.exit(0)

def key(v):
    nums = [int(x) for x in __import__("re").findall(r"\d+", v or "")]
    return tuple(nums) if nums else (0,)

def latest(idx):
    out = {}
    for pkg in idx.get("packages", []):
        vs = [v.get("manifest", {}).get("version") for v in pkg.get("versions", [])]
        vs = [v for v in vs if v]
        if vs:
            out[pkg.get("name")] = max(vs, key=key)
    return out

lo, sv = latest(local), latest(served)
behind = {n: (lo.get(n), v) for n, v in sv.items() if n not in lo or key(v) > key(lo[n])}
if behind:
    for n, (have, live) in sorted(behind.items()):
        print(f"  BEHIND: {n} — local {have or 'absent'}, live {live}")
    print("\n  The host cron has published versions this checkout does not have.")
    print("  Deploying now would roll the live catalog backwards. Resync first:\n")
    print(f"    curl -s {base}/index.json -o {site}/index.json\n")
    sys.exit(1)

print(f"  ok: no package is behind live ({len(sv)} compared)")
PY

ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" true 2>/dev/null \
  || die "cannot ssh to '$HOST' (expects a Host entry in ~/.ssh/config)"
echo "  ok: ssh $HOST"

ssh "$HOST" "test -d $REMOTE_DIR" \
  || die "$REMOTE_DIR does not exist on $HOST — do the one-time setup in README.md (\"Hosting\") first"
echo "  ok: $REMOTE_DIR exists"

# ---------------------------------------------------------------- baseline

say "Baseline (shared host — these must survive)"
if [ "${#NEIGHBOURS[@]}" -eq 0 ]; then
  echo "  (none configured — set CATALOG_NEIGHBOURS to enable the blast-radius check)"
else
  for entry in "${NEIGHBOURS[@]}"; do
    url="${entry%:*}"; want="${entry##*:}"
    got=$(code "$url")
    printf '  %-46s %s (want %s)\n' "$url" "$got" "$want"
  done
fi

# ---------------------------------------------------------------- publish

say "Publishing $LOCAL_DIR -> $HOST:$REMOTE_DIR"
# No --chmod: macOS still ships rsync 2.6.9, which rejects it. Normalise modes
# server-side instead, so Caddy can always read what we just pushed.
rsync -az --delete --exclude '.DS_Store' -e ssh "$LOCAL_DIR/" "$HOST:$REMOTE_DIR/"
ssh "$HOST" "chmod -R a+rX $REMOTE_DIR && find $REMOTE_DIR -type f -printf '%10s  %P\n' | sort -k2"

# ---------------------------------------------------------------- verify

say "Verifying $SITE_URL"

fail=0
check() { # url expected_code label
  local got; got=$(code "$1")
  if [ "$got" = "$2" ]; then
    printf '  \033[32mok\033[0m   %-52s %s\n' "$3" "$got"
  else
    printf '  \033[31mFAIL\033[0m %-52s %s (want %s)\n' "$3" "$got" "$2"; fail=1
  fi
}

check "$SITE_URL/"                 200 "page"
check "$SITE_URL/logos-repo.json"  200 "descriptor"
check "$SITE_URL/index.json"       200 "index"

# Basecamp requires https and will refuse a bad chain, so verify the cert properly
# rather than trusting that a 200 implies valid TLS.
if curl -sSf --max-time 15 "$SITE_URL/logos-repo.json" >/dev/null 2>&1; then
  printf '  \033[32mok\033[0m   %-52s\n' "TLS chain verifies"
else
  printf '  \033[31mFAIL\033[0m %-52s\n' "TLS chain does NOT verify"; fail=1
fi

# Basecamp parses these as JSON; a text/plain or text/html content-type means a
# misconfigured file_server or an HTML error page wearing a .json name.
for f in logos-repo.json index.json; do
  ct=$(curl -s -o /dev/null -w '%{content_type}' --max-time 15 "$SITE_URL/$f")
  case "$ct" in
    application/json*) printf '  \033[32mok\033[0m   %-52s %s\n' "$f content-type" "$ct" ;;
    *)                 printf '  \033[31mFAIL\033[0m %-52s %s\n' "$f content-type" "$ct"; fail=1 ;;
  esac
done

# The served bytes must equal what we just pushed.
for f in logos-repo.json index.json; do
  if [ "$(curl -s --max-time 15 "$SITE_URL/$f" | shasum -a 256 | cut -d' ' -f1)" \
     = "$(shasum -a 256 "$LOCAL_DIR/$f" | cut -d' ' -f1)" ]; then
    printf '  \033[32mok\033[0m   %-52s\n' "$f matches local bytes"
  else
    printf '  \033[31mFAIL\033[0m %-52s\n' "$f differs from local"; fail=1
  fi
done

if [ "${#NEIGHBOURS[@]}" -gt 0 ]; then
  say "Neighbours still up?"
  for entry in "${NEIGHBOURS[@]}"; do
    url="${entry%:*}"; want="${entry##*:}"
    check "$url" "$want" "$url"
  done
fi

if [ "$fail" -ne 0 ]; then
  printf '\n\033[31mDeploy finished with failures.\033[0m Roll back the proxy with:\n'
  printf '  ssh %s "sudo cp /opt/services/caddy/Caddyfile.pre-logos-catalog.bak /opt/services/caddy/Caddyfile && sudo docker restart caddy"\n' "$HOST"
  exit 1
fi

say "Done"
cat <<EOF
  Paste this into Basecamp -> Settings -> Repositories -> Add repository:

    $SITE_URL/logos-repo.json

EOF
