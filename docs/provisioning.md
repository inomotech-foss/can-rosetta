# Provisioning the AutoPi (bootstrap once, then phone-driven)

Getting the edge software onto an AutoPi used to mean SSHing in and wiring up a
service by hand. Instead we use **bootstrap-once, then OTA**: a one-time
installer puts an always-on control service on the device, and from then on the
**phone provisions and updates it** over the local control link — no SSH, no
laptop, per drive.

## Step 1 — bootstrap (once per device)

SSH into the AutoPi and run the installer. It installs `canrosetta-edge` from the
**official repo over HTTPS** and registers a restart-always systemd service:

```bash
curl -fsSL https://raw.githubusercontent.com/inomotech-foss/can-rosetta/main/edge/autopi/scripts/bootstrap.sh \
  | sudo bash -s -- main      # or an edge-vX.Y.Z tag to pin a version
```

It writes `/etc/canrosetta/config.yaml` with a generated **control token**, starts
`canrosetta-edge serve`, and prints a QR payload:

```json
{ "host": "http://<autopi-ip>:8765", "token": "<generated-token>" }
```

Show that as a QR on the AutoPi's config page (or copy the token). That's the last
time you need a shell on the device.

## Step 2 — pair from the phone

In the companion app, **Pair AutoPi** scans that QR: it agrees the shared session
id, verifies the control token, and pins the clocks (Cristian's algorithm). Done.

## Step 3 — updates come from the phone

The app checks the edge version (`GET /api/version?check=1`) against the latest
`edge-v*` release. When one is newer it offers **Update AutoPi → vX.Y.Z**, which
calls `POST /api/update`. The AutoPi pip-installs that release of *this same
package* from the official repo and **re-execs into it** (the systemd unit is
`Restart=always`, so even a plain exit relaunches). A drive later, it's current —
no cable, no SSH.

## Safety

- Updates install **only** `canrosetta-edge` from `inomotech-foss/can-rosetta`
  over HTTPS at a pinned `edge-v*` tag. A non-official `update_repo` is refused
  (`updater.is_official`), and `allow_remote_update: false` disables OTA entirely.
- This updates the edge **software** only. It does not touch the vehicle and does
  not change the strictly-read-only discovery guarantee in [SAFETY.md](../SAFETY.md).
- The update endpoint requires the control token and refuses to run while a
  discovery/logging job is active.
