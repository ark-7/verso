# Releasing Verso

Verso ships through GitHub Releases. A tag push builds the wheel and source
distribution, verifies the version and tests, publishes the release, and then
installs the published release on Linux, macOS, and Windows as a smoke test.

## Version policy

`src/verso/__init__.py` is the only place the version is written. Hatchling
reads that line at build time, and `verso --version` reports it at runtime.
`pyproject.toml` uses a dynamic version, so there is nothing to keep in sync.

- Stable tag: `vX.Y.Z`, with source version `X.Y.Z`.
- Release candidate: `vX.Y.Z-rcN`, with source version `X.Y.ZrcN` (PEP 440).
- The release workflow normalizes both sides, so `v0.2.0-rc1` and `0.2.0rc1`
  match, but a stable source version with an RC tag fails verification.

Never move or delete a published tag. If a release is wrong, publish a new
patch version instead of replacing the bytes under the old one.

## Artifacts

Each release contains:

| Asset | Name |
|---|---|
| Wheel | `verso_cli-<version>-py3-none-any.whl` |
| Source distribution | `verso_cli-<version>.tar.gz` |
| Checksums | `SHA256SUMS` |
| Unix installer | `install.sh` |
| Windows installer | `install.ps1` |

The distribution name is `verso-cli`, but Python normalizes it to `verso_cli`
in wheel filenames. The installers resolve the wheel through the GitHub API
rather than constructing the filename, so RC versions and future build tags
work without changes.

## One-time prerequisites

1. **Make the repository public.** Install scripts download release assets
   anonymously, so assets on a private repository cannot be installed.
   Settings → General → Danger Zone → Change visibility.
2. **Commit all feature work.** In particular, `src/verso/export/fountain.py`
   must be tracked; `cli.py` imports it. The release job requires a clean
   checkout.
3. **Keep tags on `main`.** The workflow rejects a tag whose commit is not an
   ancestor of `origin/main`.
4. Optional but recommended: add a tag ruleset for `v*` and branch protection
   on `main` with the CI checks required.
5. No repository secrets are needed. The publish job uses the built-in
   `GITHUB_TOKEN` with `contents: write`.

## Cutting a release

Start from a clean, green `main`.

1. Bump the version in `src/verso/__init__.py`.

2. Run the local pre-flight:

   ```bash
   pip install -e ".[dev]"
   pip install build twine

   python scripts/check_version.py --tag v0.2.0
   ruff check src tests scripts
   pytest -q

   python -m build
   python -m twine check dist/*
   ```

3. Commit the version bump:

   ```bash
   git add src/verso/__init__.py
   git commit -m "Release 0.2.0"
   ```

4. Tag and push:

   ```bash
   git tag -a v0.2.0 -m "Verso 0.2.0"
   git push origin main
   git push origin v0.2.0
   ```

5. Watch the `Release` workflow. It runs four jobs:

   - **verify**: tag/version match, tag is on `main`, lint, tests, clean tree.
   - **build**: wheel and sdist, `twine check`, content checks, `SHA256SUMS`,
     clean-venv install.
   - **publish**: creates or updates the GitHub Release and uploads the assets.
   - **smoke**: downloads the published `install.sh` / `install.ps1` and
     installs the release into a clean venv on Ubuntu, macOS, and Windows.

6. Verify the result:

   ```bash
   gh release view v0.2.0 --json tagName,isPrerelease,isLatest,assets
   gh release download v0.2.0 --pattern "SHA256SUMS" --dir /tmp/verso-release
   ```

7. Install from the release in a clean environment:

   ```bash
   curl -fsSL https://github.com/ark-7/verso/releases/download/v0.2.0/install.sh \
     | bash -s -- --version v0.2.0
   verso --version
   ```

   On Windows:

   ```powershell
   iwr https://github.com/ark-7/verso/releases/download/v0.2.0/install.ps1 -OutFile install.ps1
   .\install.ps1 -Version v0.2.0
   verso --version
   ```

## Release-candidate rehearsal

Use an RC before the first stable release, or after changing the workflow or an
installer.

1. Set `__version__ = "0.2.0rc1"` and commit.
2. Confirm the guard accepts the normalized pair:

   ```bash
   python scripts/check_version.py --tag v0.2.0-rc1
   ```

3. Tag and push `v0.2.0-rc1`.
4. Confirm the release is marked **Pre-release**, has all five assets, and that
   `/releases/latest` does not point at it.
5. Install it on each platform with `--version v0.2.0-rc1` /
   `-Version v0.2.0-rc1`.
6. Delete the rehearsal:

   ```bash
   gh release delete v0.2.0-rc1 --yes --cleanup-tag
   ```

7. Set `__version__ = "0.2.0"`, commit, and cut the stable release.

For the very first release, the same rehearsal is `0.1.0rc1` →
`v0.1.0-rc1`, followed by `0.1.0` → `v0.1.0`.

## Re-running and rollback

Re-run a failed release without creating a duplicate:

```bash
gh workflow run release.yml -f tag=v0.2.0
```

The workflow is idempotent: if the release exists, it uploads assets with
`--clobber` and updates the title, prerelease flag, and latest flag. This is
only for repairing a failed or incomplete run. Do not publish different bytes
under a version users have already downloaded.

Demote a release:

```bash
gh release edit v0.2.0 --prerelease --latest=false
```

Restore `latest` to the previous stable release:

```bash
gh release edit v0.1.0 --latest
```

Delete a bad release and its tag:

```bash
gh release delete v0.2.0 --yes --cleanup-tag
```

GitHub Releases cannot be "yanked" the way PyPI projects can. Deletion is the
only removal path, which is why published tags are treated as immutable.

## Manual verification

Check the assets and checksum:

```bash
gh release download v0.2.0 --dir /tmp/verso-release
cd /tmp/verso-release
sha256sum -c SHA256SUMS
```

Check the installers without installing:

```bash
curl -fsSL https://github.com/ark-7/verso/releases/download/v0.2.0/install.sh \
  | bash -s -- --version v0.2.0 --dry-run
```

```powershell
iwr https://github.com/ark-7/verso/releases/download/v0.2.0/install.ps1 -OutFile install.ps1
.\install.ps1 -Version v0.2.0 -DryRun
```

Check the wheel by hand:

```bash
python -m venv /tmp/verso-check
/tmp/verso-check/bin/pip install verso_cli-0.2.0-py3-none-any.whl
/tmp/verso-check/bin/verso --version
/tmp/verso-check/bin/python -m verso --version
```
