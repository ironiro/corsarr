# Contributing to Corsarr

Thanks for helping! Bug reports, ideas and pull requests are all welcome.

## Reporting bugs and ideas

Use the [issue templates](https://github.com/ironiro/corsarr/issues/new/choose). For bugs, the version (footer of
the web interface) and the relevant lines from **Events** help most. Please remove API keys, tokens, IP addresses
and names before posting.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest   # Python 3.11 or newer
.venv/bin/python -m pytest -q
.venv/bin/python -m corsarr      # then open http://localhost:8787/
```

The tests need no credentials and no running services – Telegram, Jellyfin, Seerr and the language model are
replaced by fakes. [docs/Development.md](docs/Development.md) explains the architecture, the principles and how
to add a language.

## Pull requests

- Keep a change focused; describe *why* in the PR.
- Code, comments and identifiers in English, matching the surrounding style.
- Every user-facing text goes into `corsarr/i18n.py` in **German and English** (the tests check both have the
  same keys and placeholders).
- Changing the database or the meaning of a setting? Read "Changing the database or the configuration" in
  [docs/Development.md](docs/Development.md) – older installations must keep working.
- Add a line to [CHANGELOG.md](CHANGELOG.md) under **Unreleased**.
- Dependencies: edit `requirements.in` and regenerate the pinned `requirements.txt` ("Updating dependencies" in
  [docs/Development.md](docs/Development.md)). Dependabot opens weekly update PRs, and CI fails on known
  vulnerabilities (`pip-audit`), so bumps usually arrive on their own.
- Never commit real API keys, tokens, IP addresses or personal data – also not in screenshots.
- Corsarr is licensed under [GPL-3.0](LICENSE); by opening a pull request you agree that your contribution is
  published under it.

## Releases

Releases are git tags (`vX.Y.Z`, betas `vX.Y.Z-beta.N`); see "Publishing a release" in
[docs/Development.md](docs/Development.md).
