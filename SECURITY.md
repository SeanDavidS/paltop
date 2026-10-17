# Security

Paltop web is a small HTTPS server that faces players, sometimes from the
internet, so security problems matter.

## Reporting a problem

Please report security problems privately through
[GitHub's security advisories](https://github.com/SeanDavidS/paltop/security/advisories/new),
not as a public issue. Say what the problem is, how to reproduce it, and which
version you tested. You will get an answer as soon as possible.

## What Paltop does to stay safe

- Paltop web has no admin actions. Its only call to the Palworld REST API is
  the chat's announce, which is limited to logged-in users, one line of text
  and a few messages a minute.
- Passwords are stored as salted scrypt hashes, and session cookies are
  stored only as hashes. Login attempts are limited per address.
- The page is served over HTTPS with a strict Content-Security-Policy and
  HttpOnly, SameSite=Strict, Secure cookies.
- The live data the web page shows goes through an allow-list: no IP
  addresses, Steam or Xbox ids, account names or admin commands.
- Save files and the game's files are opened read-only.

## Supported versions

Only the latest release gets security fixes.
