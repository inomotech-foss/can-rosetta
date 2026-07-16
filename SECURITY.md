# Security policy

## Reporting a vulnerability

Please report security issues privately via GitHub Security Advisories
("Report a vulnerability" on the repo's Security tab) rather than a public issue.
Include reproduction steps and impact. We aim to acknowledge within a few days.

## Scope

Of particular interest:

- any path by which the edge tooling could issue a **non-read** service to a
  vehicle (this would be a serious defect — see [SAFETY.md](SAFETY.md));
- deserialization / parsing flaws in the session loaders (a session file is
  untrusted input to the server);
- leakage of personal data (GPS, video, VIN) beyond what a user intends.
