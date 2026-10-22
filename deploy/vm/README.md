# Single-node k3s test environment

The Helm chart on one Ubuntu VM, with PostgreSQL (pgvector), Redis and
SeaweedFS running in the same cluster. It exercises the real deployment path
(Pod Security, NetworkPolicies, the migration hook, the ingress) before a cloud
VM exists. The models are the offline ones; nothing here costs money.

Requirements on the VM: k3s, Helm 4, `kubectl` pointing at the cluster
(`KUBECONFIG`), `openssl`, `jq`, and this repository cloned.

```bash
deploy/vm/bootstrap.sh
helm upgrade --install kb deploy/helm/kb-assistant -n kb -f deploy/vm/values.yaml --wait --timeout 10m
kubectl -n kb apply -f deploy/vm/seed-job.yaml && kubectl -n kb wait --for=condition=complete job/kb-seed --timeout=180s
```

`bootstrap.sh` creates the namespaces (the application's enforces the
*restricted* Pod Security Standard), random credentials, the Secrets, the JWT
signing key and the dependencies. Run again, it changes nothing.

## Demo documents

The API is not exposed outside the cluster, by design; reach it through a
port-forward to load the 31 evaluation documents:

```bash
kubectl -n kb port-forward svc/kb-kb-assistant-api 8000:8000 >/dev/null &
export KB_SEED_PASSWORD=$(kubectl -n kb get secret kb-demo-seed -o jsonpath='{.data.KB_SEED_PASSWORD}' | base64 -d)
API_URL=http://127.0.0.1:8000 scripts/load-demo-corpus.sh
kill %1
```

## Open it

https://kb.192.168.184.128.nip.io:8443 (replace the address with your VM's
in `values.yaml`). Traefik serves its self-signed certificate: accept it once.
HTTPS is required because the session cookies are `Secure`. Sign in as
`carol@acme.example` with the seed password printed by:

```bash
kubectl -n kb get secret kb-demo-seed -o jsonpath='{.data.KB_SEED_PASSWORD}' | base64 -d; echo
```

## Notes

- k3s's Traefik listens on 8080/8443 here because another service holds 80/443
  on this VM (a `HelmChartConfig` in `/var/lib/rancher/k3s/server/manifests/`).
- Upgrading: `helm upgrade` again; the images are pulled at every rollout
  (`pullPolicy: Always` on the moving `main` tag).
- Removing everything: `helm uninstall kb -n kb`, then
  `kubectl delete namespace kb kb-deps` (this deletes the data volumes too).
