# ☸️ Entebus Server Kubernetes Deployment Guide

This guide explains how to deploy **Entebus Server** on Kubernetes using Kustomize overlays for **dev** and **prod**, install NGINX Ingress, and configure a Cloudflare Origin Certificate for encrypted HTTPS traffic to your cluster.

## 🧩 What Each Overlay Does

### `base`

- Creates namespace: `entebus`
- Deploys application: `entebus-server`
- Creates service: `entebus-server-svc` (ClusterIP `80 -> 8080`)
- Creates ingress: `entebus-ingress`
- Provides default ConfigMap + Secret values (must be overridden for production)

### `overlays/dev`

- Includes `base`
- Deploys local dependencies inside cluster:
	- PostGIS (`postgis`)
	- Redis (`redis`)
	- MinIO (`minio`)
	- OpenObserve (`openobserve`)
- Uses app image tag: `develop`
- Rewrites ingress host to: `dev-api.entebus.com`
- Changes namespace label `environment` to `dev`
- Rewrites ingress host to: `dev-api.entebus.com` and adds hosts for developer UIs:
   - `dev-minio.entebus.com` (MinIO console)
   - `dev-openobserve.entebus.com` (OpenObserve UI)

### `overlays/prod`

- Includes only `base`
- Intended for production where DB/object storage/observability are provided externally or separately

## ✅ Prerequisites

- A working Kubernetes cluster (MicroK8s/K8s)
- `kubectl` configured to target the cluster
- `helm` installed
- DNS configured in Cloudflare for your API domain
- TLS files from Cloudflare Origin CA (`origin.crt`, `origin.key`)

Optional checks:

```bash
# Export KUBECONFIG as needed
export KUBECONFIG="$HOME/.kube/entebus-k8-dev-kubeconfig.yaml"

kubectl version --client
kubectl cluster-info
helm version
```

## 🔍 Render Kustomize Manifests (Validation)

Run these before applying to ensure all manifests compile correctly.

```bash
kubectl kustomize k8s/overlays/prod
kubectl kustomize k8s/overlays/dev
```

If either command fails, fix the corresponding YAML/patch before deployment.

## 🚀 Deploy Dev Overlay

Apply the dev overlay:

```bash
kubectl apply -k k8s/overlays/dev
```

Watch resources:

```bash
kubectl get all -n entebus
kubectl get ingress -n entebus
kubectl get configmap,secret -n entebus
```

## 🌐 Install Ingress NGINX Controller

Install/upgrade ingress controller:

```bash
helm upgrade --install ingress-nginx ingress-nginx \
	--repo https://kubernetes.github.io/ingress-nginx \
	--namespace ingress-nginx --create-namespace
```

Verify controller is healthy:

```bash
kubectl get pods -n ingress-nginx
kubectl get svc -n ingress-nginx
```

## 🔐 Cloudflare Origin Certificate Setup

Use this when running Cloudflare in front of your cluster, so traffic from Cloudflare to ingress is encrypted.

### 1 Generate certificate in Cloudflare

In Cloudflare dashboard:

1. Go to **SSL/TLS → Origin Server**
2. Click **Create Certificate**
3. Choose:
 	 - **Private Key Type:** RSA (2048)
 	 - **Hostnames:** add all required domains (example: `api.entebus.com`, `dev-api.entebus.com`, `dev-minio.entebus.com`, `dev-openobserve.entebus.com`, `minio.entebus.com`, `openobserve.entebus.com`)
	 - **Validity:** 15 years
4. Download/save files in PEM format:
	 - `origin.crt`
	 - `origin.key`

### 2 Create Kubernetes TLS secret

From the project root (or directory containing the certificate files):

```bash
kubectl create secret tls cloudflare-origin-cert \
	--namespace entebus \
	--cert=origin.crt \
	--key=origin.key
```

Verify secret:

```bash
kubectl get secret cloudflare-origin-cert -n entebus
```

### 3 Ensure ingress points to secret

The ingress already references:

- Secret name: `cloudflare-origin-cert`
- TLS host in base: `api.entebus.com`
- TLS host in dev overlay: `dev-api.entebus.com` (patched)

Confirm final ingress manifest:

```bash
kubectl get ingress entebus-ingress -n entebus -o yaml
```

## 🧪 Post-Deployment Verification

### Cluster resources

```bash
kubectl get ns
kubectl get all -n entebus
kubectl get ingress -n entebus
kubectl get service -A
```

### Ingress/DNS checks

- Confirm your domain points to the ingress external IP (or node IP in MicroK8s setup)
- In Cloudflare SSL/TLS mode, use **Full (strict)** after origin cert is configured
- Test endpoint over HTTPS

### App checks

- Access API docs at: `https://api.entebus.com/docs` or `https://dev-api.entebus.com/docs`
- Confirm backend pod logs:

```bash
kubectl logs -n entebus deploy/entebus-server --tail=200
```

## 🔁 Recommended Deployment Sequence

Use this sequence for safer rollout:

1. Render manifests (`kubectl kustomize ...`) for both overlays
2. Ensure ingress-nginx controller exists
3. Ensure `cloudflare-origin-cert` TLS secret exists in `entebus`
4. Apply overlay (`kubectl apply -k ...`)
5. Verify pods, services, ingress, and logs
6. Validate HTTPS from public domain

## ⚠️ Production Notes

- Do not keep default credentials from `base/config.yaml` for production
- Replace image tag from `latest` with immutable release tags
- For production, keep dependent services highly available and outside the single-node dev pattern
- Consider using external secret manager / sealed secrets / CSI driver
- Add persistence strategy beyond `hostPath` (e.g., PVC + StorageClass)

## 🛠️ Useful Troubleshooting Commands

```bash
kubectl describe ingress entebus-ingress -n entebus
kubectl describe pod -n entebus -l app=entebus-server
kubectl get events -n entebus --sort-by=.lastTimestamp
kubectl logs -n ingress-nginx deploy/ingress-nginx-controller --tail=200
```

If ingress returns 404/503:

- Check service name/port mapping (`entebus-server-svc:80`)
- Confirm backend pod is Ready
- Confirm ingress class is `nginx`
- Confirm DNS points to ingress endpoint