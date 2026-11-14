# Sanitized fast-forward publishing plan (M3-B-FINAL phase 7)

**Prepared, not executed.** No rewrite of the active branch, no push (reserved action 10).

## Candidate
Built in a scratch clone (`/tmp/.../sanitize`, branch `sanitized`) from the real repository by `scripts/history_scrub.py` (tree-filter over `924da22..HEAD`
only; files identical to the public base are untouched) plus an author-identity normalisation (local-hostname author `<user>@<host>.local` → the
already-public author identity; **owner to confirm**). Private map and original-commit bundle stay outside the repository under `~/asta-private/r6/`.
Rebuild command (run again on the final local HEAD before publishing):
```
git clone --no-hardlinks <repo> <scratch>; cd <scratch>; git checkout -b sanitized
BASE=924da22 git filter-branch -f --tree-filter "python3 <copy>/history_scrub.py --map ~/asta-private/r6/replace-map.tsv" \
  --env-filter '<author-normalisation>' -- 924da22..HEAD
rm refs/original/*, delete the clone's other branches/remotes, reflog expire, gc --prune=now
```
## Verification of the candidate (results in `M3B-FINAL-ACCEPTANCE.md` §15)
full-history gitleaks (no leaks) · `scripts/privacy_scan.py` over the candidate range (clean; whole history shows 1 identifier in 2 **already public**
commits, see below) · `scripts/secret_scan.py` · ruff/mypy · tests · tree comparison with the local HEAD (only alias edits differ) · fast-forward check (`git merge-base --is-ancestor 924da22 sanitized`).

## Finding the earlier reports got wrong
The already-public commits `1cdb07a` and `23cc022` (before `924da22`) contain **one internal container name** (internal metadata, not a credential). Removing it would
rewrite public history (force-push) and does not remove copies already fetched. Recommendation: accept as internal metadata unless the owner wants a forced rewrite (separate decision).

## Publish steps (owner approval required for each)
0. After rebuilding the candidate on the final HEAD: `python scripts/review_manifest.py --write`, commit "manifest regenerated" on top of the candidate (the scrubbed docs change four manifest hashes), then rerun all scans and the full suite on the candidate.
1. Verify bundle sha256 and that the remote head is still `924da22`. 2. `git push origin sanitized:main` is a **fast-forward** (no force). 3. Watch the hosted CI run (29 earlier runs; the new
`ci.yml` jobs backend/security/egress-boundary/web/required have never run) — CI stays **unverified** until it is green. 4. Keep the private bundle; delete the scratch clone.
If the owner prefers the local branch to match: `git branch backup/pre-sanitize; git reset --hard origin/main` after the push.
