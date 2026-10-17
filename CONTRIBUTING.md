# Contributing to Paltop

Thanks for helping. Bug reports, ideas and pull requests are all welcome.

## Reporting a bug or an idea

Open an [issue](https://github.com/SeanDavidS/paltop/issues) and say which
part it is about: Paltop terminal, Paltop web, or both. For a bug, the
Paltop version (the `VERSION` file), the Palworld server version and any
error output help a lot. Please remove player names, IP addresses and
passwords from logs and screenshots first.

## Working on the code

Paltop is plain Python 3 (3.9 or newer) with no packages to install, and it
should stay that way: please use only the standard library.

```
core/       everything both parts share: the server, the save files, the
            game's data, the history database, the background collector
terminal/   Paltop terminal (curses)
web/        Paltop web (http.server, and static/ for the page itself)
docs/       one page per part, plus core.md
```

To try Paltop web without touching the live page, run a second copy on
another port, logged in as a player:

```bash
PALTOP_TEST_PLAYER="Your name" python3 web/palweb.py --port 18080 --bind 127.0.0.1 --http --test-no-login
```

Test mode turns off the login (and sending chat messages), so keep it on
`127.0.0.1` or your own network.

Some ground rules for a change:

- Paltop must never stop or restart the Palworld server by itself. Only the
  admin menu in Paltop terminal may change anything, and it asks first.
- Paltop web stays read-only: no admin actions.
- Save files and the game's files are opened read-only.
- Keep the style of the code around you: short functions, comments that say
  why, and docs in plain English.
- Update the docs in `docs/` when you change what a part shows or does.

Before you open a pull request, the checks in
[.github/workflows/ci.yml](.github/workflows/ci.yml) should pass:

```bash
python3 -m compileall -q core terminal web
```

```bash
bash -n install.sh && tools/package.sh
```

## Releases

Bump `VERSION`, add a section to [CHANGELOG.md](CHANGELOG.md), then push a
tag that matches it (`v1.1.0` for `1.1.0`). The release workflow builds the
three archives and publishes them.
