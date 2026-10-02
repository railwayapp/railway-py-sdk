# Contributing

## Setup

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Unit tests must stay offline and must not call Railway.

## Package checks

```bash
pip install build twine
python -m build
twine check --strict dist/*
```

## Releases

This repo uses release labels, not semantic commits or PR title conventions.

Every PR must have exactly one release label:

- `release/patch`
- `release/minor`
- `release/major`
- `release/skip`

Merged release-labeled PRs trigger an automatic version bump and tag. The bump
is the highest label among all PRs merged since the previous tag. Tag releases
create a draft GitHub release, run checks, publish `railway-sdk` to PyPI
through Trusted Publishing (OIDC, no stored token), then publish the GitHub
release.

To cut a release without merging a PR, run the `Create Release` workflow from
the Actions tab and pick a bump type (or `current` to tag the version already
in `pyproject.toml`).

The version lives only in `pyproject.toml`. Bump it with
`python scripts/bump_version.py <patch|minor|major>`; the workflows use the
same script.
