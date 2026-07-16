# Contributing to CAN-Rosetta

Thanks for your interest! This project spans three components joined by one file
format — please keep that seam clean.

## Ground rules

- **The [session data format](docs/data-format.md) is the contract.** If you
  change a [schema](schemas/), bump `schema_version` per the versioning rules and
  update *all three* components plus the sample dataset.
- **Discovery stays read-only.** PRs adding vehicle-write, actuator, security-
  access, or programming-session capabilities will be declined. See
  [SAFETY.md](SAFETY.md).
- **Tests must run without hardware.** Use the simulated CAN transport (edge) and
  the synthetic sample session (server). No PR should require a real vehicle or a
  physical phone to pass CI.

## Per-component setup

- **server** — `cd server && pip install -e ".[dev]" && pytest`. Lint with
  `ruff check` and `ruff format`.
- **edge** — `cd edge/autopi && pip install -e ".[dev]" && pytest`.
- **companion/ios** — open in Xcode 15+ (see [its README](companion/ios/README.md)).

## Style

- Python: `ruff` for lint+format, type hints on public APIs, `pytest` for tests.
- Swift: SwiftUI + async/await, follow the Swift API Design Guidelines.
- Commit messages: imperative mood, explain *why* in the body when non-obvious.

## Reporting

Open an issue with the component label (`edge`, `companion`, `server`, `format`).
For anything with security or vehicle-safety implications, see
[SECURITY.md](SECURITY.md) and do not file a public issue with exploit details.
