#!/usr/bin/env python3
"""
sync-catalog.py — keep every package in /srv/logos-catalog/index.json in lockstep
with its app repo's rolling release index, without ever mirroring the .lgx
binaries (the GitHub release asset URLs are referenced directly).

Design (mirrors the repo's canary/leg-catalog.sh validation logic):
  * Each app repo's index.json (a GitHub release asset) is the source of truth
    for the packages listed against it in ORG_INDEXES — today all four:
    lez_faucet + lez_faucet_ui from lez-faucet, swap + swap_ui from
    eth-lez-atomic-swaps. Every run replaces those entries with the org's,
    preserving the served file's existing package ordering.
  * Anything in the served file that no ORG_INDEXES entry claims is treated as
    locally-owned and carried over byte-for-byte. Nothing is, today — all four
    packages are org-sourced — but the branch stays so adding a hand-maintained
    entry does not silently delete it.
  * Publishing never removes a version the served file already has (see
    regressions()), so a mid-write upstream index cannot truncate the catalog.
  * The one local edit is display_name (see DISPLAY_NAMES) — display-only, and
    re-applied on every run so it cannot be lost the way it was when the swap
    entries first started being copied through verbatim.
  * Before publishing, the merged JSON is validated the same way a Basecamp
    client / the leg-catalog canary would traverse it: schema/shape checks on
    every package, manifest.name/version internal consistency, and a live HEAD
    of each org-sourced .lgx asset (200 + Content-Length == index size).

Safety properties:
  * Network failure fetching ANY org index  -> log + exit 0, leave old file.
    Deliberately all-or-nothing: merging a fresh index with a stale one would
    silently roll a package backwards.
  * Validation failure of the merged result -> log + exit 0, leave old file.
  * No change vs. current served file        -> log "no change", no write.
  * A real change                            -> dated backup of the previous
    file, then atomic temp+os.replace of the served file.
No secrets are read or logged.
"""

import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Every app repo whose rolling `index` release feeds this catalog, mapped to the
# packages we take from it. Both are public assets: no auth, no lgx, no Nix.
#
# The faucet was originally absent here and its entries were carried through from
# the served file untouched, which meant a faucet release could never reach the
# catalog on its own. It only ever looked current because upstream had not
# shipped past 0.3.0.
ORG_INDEXES = {
    "https://github.com/logos-co/lez-faucet/"
    "releases/download/index/index.json": ("lez_faucet", "lez_faucet_ui"),
    "https://github.com/logos-co/eth-lez-atomic-swaps/"
    "releases/download/index/index.json": ("swap", "swap_ui"),
}
SERVED_PATH = "/srv/logos-catalog/index.json"
# Packages whose entries are sourced from an org index (anything else in the
# served file is locally-owned and preserved untouched). Derived so the two
# cannot drift.
ORG_PACKAGES = tuple(p for pkgs in ORG_INDEXES.values() for p in pkgs)

# Basecamp's App Manager renders a module with no display_name as an empty name.
# Upstream shipped swap 0.3.0/0.2.0 and swap_ui 0.2.0 without the field, so we
# add it. Display-only, and not one of the five fields re-verified after download
# (name, version, main, dependencies, type), so installs still verify against the
# real asset. Applied with setdefault, so it is idempotent and retires itself as
# upstream releases carry the field — which they have since swap 0.3.1.
DISPLAY_NAMES = {
    "swap": "ETH ↔ LEZ Atomic Swap",
    "swap_ui": "ETH ↔ LEZ Atomic Swap",
}

# Versions that must never reach the public catalog.
#
# eth-lez-atomic-swaps' canary-channel.yml publishes throwaway builds stamped
# 0.99.<run-number> as a fast branch-testing path. Those are real GitHub releases
# carrying real .lgx assets, so the upstream rebuild-index picks them up like any
# other release and they sort ABOVE every genuine version. Copying one through
# would put a canary at versions[0] — the entry Basecamp shows as current and
# installs by default.
#
# This is not hypothetical: 0.99.4 appeared at the tip of the upstream index on
# 2026-08-10 and would have been published to users on the next run.
SENTINEL_RE = re.compile(r"^0\.99\.")

BASE_DIR = os.path.join(os.path.expanduser("~"), "logos-catalog-sync")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
LOG_PATH = os.path.join(BASE_DIR, "sync.log")

USER_AGENT = "logos-catalog-sync/1"
TIMEOUT = 30


def log(msg):
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(BASE_DIR, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def ver_key(v):
    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums) if nums else (0,)


def fetch(url, method="GET", extra=None):
    headers = {"User-Agent": USER_AGENT}
    if extra:
        headers.update(extra)
    req = urllib.request.Request(url, method=method, headers=headers)
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def head_asset(url):
    """(code, content_length) without downloading the body. HEAD first, then a
    1-byte Range GET fallback for CDNs that reject HEAD (403/405/400)."""
    try:
        with fetch(url, method="HEAD") as r:
            return r.status, r.headers.get("Content-Length")
    except urllib.error.HTTPError as e:
        if e.code not in (403, 405, 400):
            raise
    with fetch(url, method="GET", extra={"Range": "bytes=0-0"}) as r:
        cr = r.headers.get("Content-Range")  # "bytes 0-0/12078859"
        total = cr.split("/")[-1] if cr else r.headers.get("Content-Length")
        return (200 if r.status in (200, 206) else r.status), total


def version_set(idx_or_pkgs):
    """{package: {version, ...}} for a whole index or a packages list."""
    pkgs = idx_or_pkgs.get("packages", []) if isinstance(idx_or_pkgs, dict) else idx_or_pkgs
    out = {}
    for pkg in pkgs:
        name = pkg.get("name")
        if not name:
            continue
        vs = set()
        for ver in pkg.get("versions", []):
            v = (ver.get("manifest") or {}).get("version") or ver.get("version")
            if v:
                vs.add(v)
        out[name] = vs
    return out


def regressions(served_pkgs, merged_pkgs):
    """Versions the served file has that the merged result would drop.

    A published .lgx is immutable and a release is not normally retracted, so an
    upstream index that has *fewer* versions than we are already serving means
    something upstream is mid-write or broken — rebuild-index treats a release
    missing its .lgx or sidecar.json as unpublished, so a partial upload
    genuinely truncates the index it publishes. Copying that through would delete
    installable versions from users' App Manager. Refuse instead and keep serving
    what we have; the next run heals it once upstream settles."""
    before, after = version_set(served_pkgs), version_set(merged_pkgs)
    out = {}
    for name, vs in before.items():
        lost = vs - after.get(name, set())
        if lost:
            out[name] = sorted(lost)
    return out


def latest_version(pkg):
    latest = None
    for ver in pkg.get("versions", []):
        v = ver.get("manifest", {}).get("version") or ver.get("version")
        if latest is None or ver_key(v) > ver_key(latest):
            latest = v
    return latest


def validate_merged(merged):
    """Structural + live-asset validation of the merged index. Returns a list of
    problem strings (empty == valid).

    Every package is org-sourced now, so the HEAD pass covers all of them — 21
    requests at time of writing. This used to skip the faucet, and the comment
    here used to claim a faucet hiccup could not block a swap update. That is no
    longer true: validation is all-or-nothing, so a flaky asset in either app
    holds up the whole publish until the next run. Acceptable because the failure
    mode is "keep serving the current file", which is safe, and the next attempt
    is fifteen minutes away."""
    problems = []
    if merged.get("schemaVersion") != 2:
        problems.append(f"schemaVersion is {merged.get('schemaVersion')!r}, expected 2")
    pkgs = merged.get("packages", [])
    if not pkgs:
        problems.append("merged index has zero packages")
        return problems
    names = {p.get("name") for p in pkgs}
    for required in ORG_PACKAGES:
        if required not in names:
            problems.append(f"org package {required!r} missing from merged index")

    for pkg in pkgs:
        name = pkg.get("name")
        versions = pkg.get("versions", [])
        if not name:
            problems.append("a package has no name")
            continue
        if not versions:
            problems.append(f"{name}: package has zero versions")
        for ver in versions:
            man = ver.get("manifest", {})
            v = man.get("version") or ver.get("version")
            url = ver.get("url")
            size = ver.get("size")
            if man.get("name") and man.get("name") != name:
                problems.append(f"{name}: manifest.name={man.get('name')} != package name")
            if man.get("version") and v and man.get("version") != v:
                problems.append(f"{name} {v}: manifest.version={man.get('version')} != {v}")
            if not url:
                problems.append(f"{name} {v}: version entry has no url")
                continue
            if size is None:
                problems.append(f"{name} {v}: index entry has no size field")
            # Only HEAD-verify the org-sourced (freshly changed) assets.
            if name in ORG_PACKAGES:
                try:
                    code, clen = head_asset(url)
                except Exception as e:
                    problems.append(f"{name} {v}: asset unreachable {url} ({e})")
                    continue
                if code != 200:
                    problems.append(f"{name} {v}: asset HTTP {code} {url}")
                elif clen is None:
                    problems.append(f"{name} {v}: asset size unverifiable (no length) {url}")
                elif size is not None and int(clen) != int(size):
                    problems.append(f"{name} {v}: asset size {clen} != index size {size}")
    return problems


def packages_equal(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def main():
    os.makedirs(BACKUP_DIR, exist_ok=True)

    # 1. Load the current served file — source of truth for owned (faucet) pkgs.
    try:
        with open(SERVED_PATH) as f:
            served = json.load(f)
    except Exception as e:
        log(f"ABORT: cannot read served index {SERVED_PATH}: {e}")
        return 0
    served_pkgs = served.get("packages", [])

    # 2. Fetch every org index (any network failure -> leave old file untouched).
    #    All-or-nothing on purpose: publishing a merge of a fresh index and a
    #    stale one would silently roll a package backwards.
    org_by_name = {}
    for url, wanted in ORG_INDEXES.items():
        try:
            with fetch(url) as r:
                org = json.loads(r.read().decode())
        except Exception as e:
            log(f"SKIP: could not fetch org index ({url}): {e}")
            return 0
        by_name = {p.get("name"): p for p in org.get("packages", [])}
        for required in wanted:
            if required not in by_name:
                log(f"SKIP: {url} missing package {required!r}; leaving served file as-is")
                return 0
            org_by_name[required] = by_name[required]

    # 2a. Drop sentinel/canary versions before anything else looks at them.
    dropped = []
    for name, pkg in org_by_name.items():
        keep = []
        for ver in pkg.get("versions", []):
            v = (ver.get("manifest") or {}).get("version") or ver.get("version") or ""
            if SENTINEL_RE.match(v):
                dropped.append(f"{name} {v}")
            else:
                keep.append(ver)
        if not keep:
            log(f"SKIP: every version of {name!r} is a sentinel; leaving served file as-is")
            return 0
        pkg["versions"] = keep
    if dropped:
        log(f"filtered sentinel versions: {', '.join(sorted(dropped))}")

    # 2b. Re-apply the display_name backfill (see DISPLAY_NAMES).
    for name, pkg in org_by_name.items():
        if name not in DISPLAY_NAMES:
            continue
        for ver in pkg.get("versions", []):
            man = ver.get("manifest")
            if isinstance(man, dict):
                man.setdefault("display_name", DISPLAY_NAMES[name])

    # 3. Merge, preserving the served package ordering. Swap/swap_ui take the
    #    org entry; every other package is carried over untouched. Any org
    #    package not already present is appended in ORG_PACKAGES order.
    merged_pkgs = []
    present = set()
    for p in served_pkgs:
        nm = p.get("name")
        if nm in ORG_PACKAGES:
            merged_pkgs.append(org_by_name[nm])
        else:
            merged_pkgs.append(p)
        present.add(nm)
    for nm in ORG_PACKAGES:
        if nm not in present:
            merged_pkgs.append(org_by_name[nm])

    merged = {
        "schemaVersion": served.get("schemaVersion", 2),
        "repositoryName": served.get("repositoryName", "logos-lez-apps"),
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "packages": merged_pkgs,
    }

    # 4. Idempotency: compare everything except the generatedAt timestamp.
    if packages_equal(served.get("packages"), merged["packages"]) and \
       served.get("schemaVersion") == merged["schemaVersion"] and \
       served.get("repositoryName") == merged["repositoryName"]:
        latest = {nm: latest_version(org_by_name[nm]) for nm in ORG_PACKAGES}
        log(f"no change (org packages already current: {latest}); not rewriting")
        return 0

    # 4b. Never go backwards. Cheap, and it runs before the expensive HEAD pass.
    lost = regressions(served.get("packages", []), merged["packages"])
    if lost:
        detail = "; ".join(f"{n}: {', '.join(vs)}" for n, vs in sorted(lost.items()))
        log("REGRESSION; NOT publishing — the merged index would drop versions "
            f"currently served ({detail}). Upstream is probably mid-write; the "
            "next run will heal it. Investigate if this persists.")
        return 0

    # 5. Validate the merged result before publishing.
    problems = validate_merged(merged)
    if problems:
        log("VALIDATION FAILED; NOT publishing. Problems: " + "; ".join(problems))
        return 0

    # 6. Backup previous served file, then atomic replace.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = os.path.join(BACKUP_DIR, f"index.json.{stamp}")
    try:
        with open(SERVED_PATH, "rb") as src, open(backup_path, "wb") as dst:
            dst.write(src.read())
    except Exception as e:
        log(f"ABORT: could not write backup {backup_path}: {e}")
        return 0

    dir_ = os.path.dirname(SERVED_PATH)
    try:
        fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".index.json.", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, SERVED_PATH)
    except Exception as e:
        log(f"ABORT: atomic write failed: {e}")
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return 0

    latest = {nm: latest_version(org_by_name[nm]) for nm in ORG_PACKAGES}
    log(f"PUBLISHED updated index.json (backup {backup_path}); "
        f"org packages latest now {latest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
