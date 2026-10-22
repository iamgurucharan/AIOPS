# ROBO-SHOP Incident Workbench

A standard-library Python incident dashboard for Robot Shop. The workbench can probe the already deployed Robot Shop LoadBalancer URL, create ServiceNow Incidents automatically, and hold generated Problem/RCA and Knowledge drafts for explicit human approval. Every Kubernetes resource in this deployment package is namespaced `guru`; the workbench does not need Kubernetes API credentials or access to the `robot-shop` namespace.

## Current environment values

- AWS region: `ap-southeast-2`
- AWS account from the supplied `sts get-caller-identity`: `464447071432`
- EKS cluster: `final-project-cluster`
- App namespace: `guru`
- Robot Shop LoadBalancer URL: `http://a29edbfdcb68f43a393309b9987fb200-1520260698.ap-southeast-2.elb.amazonaws.com`

The ELB hostname may change if its Service is recreated. Verify it from the Robot Shop deployment before applying `k8s/configmap.yaml`.

## End-to-end Ubuntu and EKS deployment

Run AWS and Kubernetes commands using the same identity that has the EKS Access Entry. The supplied transcript shows `Guru` can administer this cluster when commands run as `root`, while the `ubuntu` account did not have AWS credentials. Do not assume `sudo` and `ubuntu` share AWS credentials or kubeconfig. The steps below use a root shell because that identity already succeeded in the transcript; alternatively configure an appropriately authorized profile for `ubuntu` and omit `sudo` consistently.

### 1. Prepare the Ubuntu host

SSH to the Ubuntu EC2 host and open a root shell using the already-configured method:

```bash
sudo -i
aws sts get-caller-identity
```

Confirm the identity is the intended AWS account/principal. Do not paste credentials into chat or commit them. Install Docker, AWS CLI, and basic utilities if absent:

```bash
apt-get update
apt-get install -y docker.io awscli curl ca-certificates
systemctl enable --now docker
```

Install `kubectl` matching the EKS control plane version (the provided cluster reported Kubernetes `1.35`):

```bash
KUBECTL_VERSION="$(curl -fsSL https://dl.k8s.io/release/stable.txt)"
curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
chmod 0755 /usr/local/bin/kubectl
kubectl version --client
```

If using a non-amd64 host, choose the corresponding Kubernetes download architecture.

### 2. Configure and verify EKS access

```bash
aws eks update-kubeconfig --region ap-southeast-2 --name final-project-cluster
kubectl get nodes
kubectl get namespace guru
```

If `guru` does not exist, create only that application namespace:

```bash
kubectl create namespace guru
```

If access fails, first check `aws sts get-caller-identity` and which home directory contains the active AWS credentials and kubeconfig. Do not work around this by granting the application pod cluster-admin access; the pod does not require AWS credentials or Kubernetes RBAC.

### 3. Put the project on Ubuntu and verify Robot Shop

Transfer this project to the host with a private Git repository or `scp`. From its project directory, verify the existing app endpoint responds:

```bash
curl -I 'http://a29edbfdcb68f43a393309b9987fb200-1520260698.ap-southeast-2.elb.amazonaws.com'
```

Update `ROBOT_SHOP_URL` in `k8s/configmap.yaml` if the current ELB hostname differs. The workbench performs only an HTTP `HEAD` check against that URL. It does not scrape application pages, deploy into `robot-shop`, or query pods/events there.

### 4. Build and push the image to ECR

The provided AWS identity needs ECR permissions to create/use the repository and push images. Run on the Ubuntu host:

```bash
AWS_REGION=ap-southeast-2
AWS_ACCOUNT_ID=464447071432
ECR_HOST="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
ECR_URI="${ECR_HOST}/robo-shop-workbench:1.0.0"

aws ecr describe-repositories --region "$AWS_REGION" --repository-names robo-shop-workbench >/dev/null 2>&1 || \
  aws ecr create-repository --region "$AWS_REGION" --repository-name robo-shop-workbench

aws ecr get-login-password --region "$AWS_REGION" | \
  docker login --username AWS --password-stdin "$ECR_HOST"

docker build -t "$ECR_URI" .
docker push "$ECR_URI"
```

Set the image in `k8s/deployment.yaml` to the exact `ECR_URI` you pushed. The image contains the Python server, static dashboard, and priority reference data only; it excludes `config.env`, local state, tests, and credentials.

The EKS worker-node IAM role must be able to pull from this ECR repository (commonly `AmazonEC2ContainerRegistryReadOnly`). Grant image-pull access to the node role, not to the application pod; the workbench pod itself needs no AWS identity.

### 5. Configure credentials privately on the Ubuntu host

Create a private env file on the host, not in the repository:

```bash
umask 077
nano /root/robo-shop-workbench-secrets.env
chmod 600 /root/robo-shop-workbench-secrets.env
```

Use these keys and enter actual values in the editor. Keep the username/password values on the same line and do not add `export`:

```dotenv
APP_USERNAME=choose-a-dashboard-login
APP_PASSWORD=replace-with-a-long-unique-random-password
SN_INSTANCE_URL=https://your-instance.service-now.com
SN_USERNAME=your-restricted-integration-user
SN_PASSWORD=your-ServiceNow-password
# Alternatively, use a ServiceNow OAuth client-credentials app instead of username/password.
SN_CLIENT_ID=
SN_CLIENT_SECRET=
LLM_API_KEY=your-LLM-provider-key
```

`SN_*` and `LLM_API_KEY` may be omitted if you want the app in demo/fallback mode. ServiceNow accepts either Basic Auth (`SN_USERNAME` + `SN_PASSWORD`) or OAuth client credentials (`SN_CLIENT_ID` + `SN_CLIENT_SECRET`). OAuth requests use `${SN_INSTANCE_URL}/oauth_token.do` unless `SN_OAUTH_TOKEN_URL` is set. The ServiceNow integration account needs create rights for Incident (`incident`), Problem (`problem`), and Knowledge (`kb_knowledge`) tables/fields used by the app. A 401 means authentication was rejected or disabled; verify the secret values and REST authentication policy, or configure OAuth. A 403 means table/field permissions are missing. The LLM endpoint/model are configured in `k8s/configmap.yaml`. Review your organization's data policy before sending incident descriptions or endpoint evidence to an external LLM.

Create/update the Secret only in `guru`:

```bash
kubectl -n guru create secret generic robo-shop-workbench-secrets \
  --from-env-file=/root/robo-shop-workbench-secrets.env \
  --dry-run=client -o yaml | kubectl apply -f -
```

Kubernetes Secret values are base64-encoded, not inherently encrypted. Restrict Kubernetes API access and enable EKS secret encryption at rest through the cluster's KMS configuration. Never put live secret values in YAML committed to source control.

### 6. Configure public HTTPS access

The supplied `k8s/ingress.yaml` creates an internet-facing AWS Application Load Balancer with HTTPS only. Before applying it, edit these placeholders:

- `incidents.example.com`: a DNS name you control.
- `REPLACE_WITH_ACM_CERTIFICATE_ARN`: an ACM certificate ARN in `ap-southeast-2`, valid for that DNS name.
- `REPLACE_WITH_YOUR_PUBLIC_IP_CIDR`: your current public egress IP in CIDR form, e.g. `198.51.100.23/32`; replace the example with your real address.

The EKS cluster must already have the AWS Load Balancer Controller installed and configured. The controller is a cluster add-on; this application package creates no resources outside `guru`. If the controller is absent, ask the cluster administrator to install/enable it as a platform prerequisite before creating the Ingress. Do not remove the CIDR allowlist just to make the endpoint work; use an approved trusted network range.

After editing the placeholders, create the deployment resources, all in `guru`:

```bash
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/ingress.yaml
```

These files declare only namespaced ConfigMap, Service, Deployment, and Ingress objects. No PVC, ClusterRole, cluster-wide binding, or access to Robot Shop's namespace is created. Incident records use Pod-local ephemeral storage and are lost when the Pod is recreated.

### 7. Wait for rollout and connect DNS

```bash
kubectl -n guru rollout status deployment/robo-shop-workbench --timeout=180s
kubectl -n guru get pods,svc,ingress
kubectl -n guru describe ingress robo-shop-workbench
```

Get the ALB hostname from `kubectl -n guru get ingress robo-shop-workbench`. Add a DNS A/AAAA Alias (Route 53) or provider-supported ALIAS/CNAME for your chosen host pointing to that ALB. Visit `https://incidents.example.com`; the browser will request the dashboard Basic Auth credentials configured in the Secret. The Ingress permits only the configured source CIDR.

For a private smoke-test without public Ingress, omit applying the Ingress and use:

```bash
kubectl -n guru port-forward service/robo-shop-workbench 8080:80
```

Then open `http://127.0.0.1:8080` locally. Do not expose this plain-HTTP port-forward through a public network.

## Updating and operations

Build and push a new immutable image tag, update `k8s/deployment.yaml`, and apply it:

```bash
kubectl apply -f k8s/deployment.yaml
kubectl -n guru rollout status deployment/robo-shop-workbench
```

Check logs with `kubectl -n guru logs deployment/robo-shop-workbench`. Incident state lives in an `emptyDir` volume and is lost if the Pod is rescheduled or recreated. The deployment uses one replica and a `Recreate` rollout. For production/high availability or retained incident history, use a managed database rather than adding a PVC, and add authenticated per-user approval/audit identity; the current Basic Auth gate is a shared responder login, not a full identity system.

The dashboard probes Robot Shop on each new incident and records the HTTP result as evidence. It cannot infer pod-level failures from an HTTP probe alone; deeper telemetry would require a separately approved read-only monitoring integration. ServiceNow incident creation happens on submit; Problem and KB creation remain approval-gated, and KB records are submitted as drafts.

## Local development

```powershell
C:/Users/gurchara/AppData/Local/Programs/Python/Python312/python.exe server.py
```

Open `http://127.0.0.1:8080`. Local mode uses demo records and rule-based analysis if integrations are not configured.

## Priority reference

Categories, impact/urgency definitions, and the priority matrix are in `data/incident_reference.json`. Confirm the matrix matches the priority rules in your ServiceNow instance.

## Credentials and values needed for a live deployment

Do not send passwords, API keys, AWS access keys, or private keys in chat. Enter them directly into the Ubuntu-only Secret env file. The remaining non-secret values you need are: a valid Robot Shop ELB URL (the one from the supplied output is prefilled), the ServiceNow instance URL, the approved DNS name, ACM certificate ARN, and the trusted public CIDR allowed to reach the dashboard. AWS credentials should be supplied locally through the existing authorized AWS CLI identity/profile; the AWS identity needs EKS access plus ECR push permissions. The cluster must have the AWS Load Balancer Controller available for the HTTPS Ingress.
