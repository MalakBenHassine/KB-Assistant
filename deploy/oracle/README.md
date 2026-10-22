# Free public demo on Oracle Cloud

Everything here costs nothing: an Oracle Cloud "Always Free" Ampere VM, a
DuckDNS sub-domain, a Let's Encrypt certificate, the offline models, and the
dependencies on the VM itself. The API runs in **read-only demo mode**:
visitors share the demo accounts, can sign in and ask, and cannot upload,
invite or change settings (`KB_DEMO__READ_ONLY`, `api/demo.py`). Conversations
are deleted every night.

## 1. The VM

In the Oracle Cloud console (an account needs a payment card for identity
verification; Always Free resources are not charged):

- **Compute → Instances → Create**: Ubuntu 24.04, shape `VM.Standard.A1.Flex`
  (Ampere, ARM). 4 OCPUs and 24 GB is the free maximum; 2 OCPUs and 12 GB is
  enough. The images of this project are multi-architecture.
- **Networking → the VCN → Security List**: add ingress rules for TCP 80 and
  TCP 443 from `0.0.0.0/0`.
- **DuckDNS** (https://www.duckdns.org): create a sub-domain pointing at the
  VM's public IP. Keep its token private.

Oracle's Ubuntu images also filter traffic on the VM itself (iptables). Allow
HTTP and HTTPS, and drop the rule that rejects forwarded traffic, which k3s
needs for its pods:

```bash
sudo iptables -I INPUT 5 -p tcp -m multiport --dports 80,443 -j ACCEPT
sudo iptables -D FORWARD -j REJECT --reject-with icmp-host-prohibited || true
sudo netfilter-persistent save
```

## 2. k3s, Helm, cert-manager

```bash
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.36.5+k3s1 sh -
mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config && sudo chown $USER: ~/.kube/config && chmod 600 ~/.kube/config
echo 'export KUBECONFIG=$HOME/.kube/config' >> ~/.bashrc && export KUBECONFIG=$HOME/.kube/config
```

Helm 4.3.0 for ARM, checked against its published checksum:

```bash
cd /tmp && curl -sSfLO https://get.helm.sh/helm-v4.3.0-linux-arm64.tar.gz && curl -sSfL https://get.helm.sh/helm-v4.3.0-linux-arm64.tar.gz.sha256sum | sha256sum -c - && tar xzf helm-v4.3.0-linux-arm64.tar.gz && sudo install linux-arm64/helm /usr/local/bin/helm && cd -
```

cert-manager, then the Let's Encrypt issuers:

```bash
helm install cert-manager oci://quay.io/jetstack/charts/cert-manager --version v1.21.2 \
  -n cert-manager --create-namespace --set crds.enabled=true --wait
git clone https://github.com/MalakBenHassine/KB-Assistant.git kb-assistant && cd kb-assistant
kubectl apply -f deploy/oracle/cluster-issuer.yaml
```

## 3. The application

The bootstrap and the dependencies are the same as for the local test VM.
The first install leaves read-only mode **off**, so the demo documents can be
uploaded; the last command turns it on.

```bash
sudo apt-get install -y jq
deploy/vm/bootstrap.sh
helm upgrade --install kb deploy/helm/kb-assistant -n kb -f deploy/oracle/values.yaml   --set ingress.host=<name>.duckdns.org --set config.KB_DEMO__READ_ONLY=false --wait --timeout 15m
kubectl -n kb apply -f deploy/vm/seed-job.yaml && kubectl -n kb wait --for=condition=complete job/kb-seed --timeout=180s
```

Load the demo documents through a port-forward (the API is never public):

```bash
kubectl -n kb port-forward svc/kb-kb-assistant-api 8000:8000 >/dev/null 2>&1 & sleep 3
export KB_SEED_PASSWORD=$(kubectl -n kb get secret kb-demo-seed -o jsonpath='{.data.KB_SEED_PASSWORD}' | base64 -d)
API_URL=http://127.0.0.1:8000 scripts/load-demo-corpus.sh; kill %1
```

Then open the demo to the public, read-only:

```bash
helm upgrade kb deploy/helm/kb-assistant -n kb -f deploy/oracle/values.yaml   --set ingress.host=<name>.duckdns.org --wait
```

## 4. Check

```bash
kubectl -n kb get certificate          # READY True within a minute or two
curl -sI https://<name>.duckdns.org/login | head -1
```

Open `https://<name>.duckdns.org`: a trusted certificate, the demo accounts and
their shared password on the sign-in page.

## Operating it

- **Updates**: `helm upgrade` with the same command; images are pulled again
  (`main` moves with every merge).
- **The nightly reset** is the `retention` CronJob (`olderThanDays: 1`).
- **Idle reclamation**: Oracle may reclaim an Always Free instance that stays
  mostly idle for a week, and a demo with few visitors is mostly idle. Check
  Oracle's current policy; if the instance is reclaimed, these steps rebuild it
  in about fifteen minutes (the data is only the demo corpus).
- **Cost guard**: the offline models make no paid call, whatever visitors do.
  Do not set an OpenAI key on a public demo.
