# Deploying the CAN-Rosetta server to the pegasus cluster

These manifests deploy the CAN-Rosetta **server** (the FastAPI service built by
`.github/workflows/build-image.yml` and published to
`ghcr.io/inomotech-foss/can-rosetta-server`) to the pegasus Kubernetes cluster,
behind Microsoft Entra (Azure AD) login.

They mirror how `inomotech-foss/paperplane` is deployed in the pegasus GitOps
repo at <https://codeberg.org/inomotech/pegasus-cluster>.

## How pegasus works (what we matched)

Discovered from the pegasus-cluster repo:

| Concern            | Pegasus pattern                                                                 |
| ------------------ | ------------------------------------------------------------------------------- |
| GitOps engine      | **Rancher Fleet** (not Flux, not ArgoCD). Each app is a `fleet.yaml` bundle.     |
| Templating         | Fleet bundles wrap either a Helm chart or a **Kustomize** overlay.               |
| Ingress            | **Gateway API + Envoy Gateway** (`GatewayClass: pegasus`), two shared Gateways `pegasus-a` / `pegasus-b` in namespace `inomo-envoy-gateway`. Apps attach an `HTTPRoute`. |
| TLS                | **cert-manager** `ClusterIssuer: letsencrypt` (DNS-01 via Cloudflare), wildcard `*.svc.inomo.tech` terminated at the Gateway. |
| Secrets            | **OpenBao** (Vault fork) via the **Secrets Store CSI driver** + a per-app `SecretProviderClass` (provider `openbao`). Not SealedSecrets/SOPS/external-secrets. |
| Namespaces         | `inomo-` prefix (we use `inomo-canrosetta`).                                     |
| Entra login        | Paperplane uses its **app-native OIDC** (issuer `https://login.microsoftonline.com/<TENANT>/v2.0`), client id/secret mounted from OpenBao. |

## Files here

| File                          | Purpose                                                                 |
| ----------------------------- | ----------------------------------------------------------------------- |
| `fleet.yaml`                  | Rancher Fleet bundle; applies the Kustomize overlay into `inomo-canrosetta`. |
| `kustomization.yaml`          | Kustomize entry point; pins the image.                                   |
| `namespace.yaml`              | `inomo-canrosetta` namespace.                                            |
| `deployment.yaml`             | Non-root, read-only-rootfs Deployment (2 replicas) with `/healthz` probes. |
| `service.yaml`                | ClusterIP Service on port 80 -> container 8000.                          |
| `httproute.yaml`             | Gateway API `HTTPRoute` attaching to `pegasus-a`/`pegasus-b`, host `canrosetta.svc.inomo.tech`. |
| `securitypolicy.yaml`         | Envoy Gateway `SecurityPolicy` enforcing Entra OIDC on the route.        |
| `secretproviderclass.yaml`    | OpenBao `SecretProviderClass`; syncs the OIDC client secret into a k8s Secret. |
| `oidc-secret.example.yaml`    | Example plain Secret shape (portability; not applied on pegasus).        |

## Entra login enforcement — design note (read this)

Paperplane enforces Entra with its **own built-in OIDC**. The CAN-Rosetta server
is a small FastAPI service with **no built-in OIDC**, so we cannot copy that
verbatim. Instead we enforce Entra one layer out, at the **same ingress stack
pegasus already runs (Envoy Gateway)**, using Envoy Gateway's native
`SecurityPolicy` OIDC (`securitypolicy.yaml`). It targets the `HTTPRoute`, so
every request must complete the Entra OIDC flow before reaching the pod — same
identity provider, same gateway, zero application changes.

There is **no oauth2-proxy, no ingress-nginx `auth-url` annotations, and no
Traefik forwardAuth** anywhere in pegasus, so we deliberately did not introduce
any of those. If this ever moves to a cluster without Envoy Gateway, the portable
equivalent is an oauth2-proxy Deployment fronting the Service configured with the
same Entra issuer; the client secret shape in `oidc-secret.example.yaml` still
applies.

### Values to fill in

- `securitypolicy.yaml`: `<ENTRA_TENANT_ID>` and `<ENTRA_CLIENT_ID>` — from the
  Azure/Entra app registration. These are **identifiers, not secrets**.
- The client **secret** is never committed. Store it in OpenBao and let the CSI
  driver sync it into the `canrosetta-oidc` Secret (key `client-secret`):
  ```
  bao kv put kv/oidc/canrosetta client_secret='<value from Entra portal>'
  ```
  and bind the CSI role `pegasus-canrosetta` to read `kv/data/oidc/canrosetta`.
- In the Entra app registration, add the redirect URI
  `https://canrosetta.svc.inomo.tech/oauth2/callback`.

## Registering this in the pegasus-cluster GitOps repo

We cannot push to codeberg from here. To wire it up, an operator adds a Fleet
bundle in `pegasus-cluster` that points at these manifests. Two options:

**A. Reference this repo directly** — add `apps/can-rosetta/fleet.yaml` in
pegasus-cluster containing a Fleet bundle whose source is this repo's
`deploy/pegasus` path. (Fleet's `GitRepo` in the Fleet controller lists the
paths; add `deploy/pegasus` from `github.com/inomotech-foss/can-rosetta` as a
path/bundle.)

**B. Vendor the manifests** — copy the contents of this directory into
`pegasus-cluster/apps/can-rosetta/` (including `fleet.yaml`, which is already a
valid Fleet bundle). Fleet auto-detects the `kustomization.yaml` and applies it.

Either way, keep the `dependsOn` entries in `fleet.yaml` so the OpenBao CSI
provider is ready before this app starts, and pin the image in
`kustomization.yaml` to an immutable digest published by the build workflow (the
image is also tagged `sha-<commit>`), rather than `latest`.

## Local validation

```
kustomize build deploy/pegasus | kubectl apply --dry-run=client -f -
```

Note: `SecurityPolicy` (Envoy Gateway) and `SecretProviderClass` (Secrets Store
CSI) are CRDs, so a full server-side dry-run needs those CRDs installed on the
target cluster; `kustomize build` alone validates structure and kustomization.

## Assumptions / caveats

- Host `canrosetta.svc.inomo.tech` follows the pegasus internal-service naming
  (`*.svc.inomo.tech`, wildcard TLS already provisioned). Change if a public
  `*.pegasus.inomo.tech` host is wanted instead.
- The Gateway names (`pegasus-a`/`pegasus-b`, ns `inomo-envoy-gateway`) and the
  cert-manager wildcard were taken from the pegasus repo at time of writing; if
  the cluster's gateway names change, update `httproute.yaml`.
- `SecretProviderClass` uses `apiVersion secrets-store.csi.x-k8s.io/v1`; pegasus's
  paperplane example used `v1alpha1`. Both are served by the driver; switch if
  the cluster only serves the alpha version.
