# Network Policies Runbook

## Overview
This runbook covers network-related incidents, connectivity issues, and security policy troubleshooting for InfraMind infrastructure running on AWS EKS.

---

## CRITICAL: Diagnosing Connection Errors

Before touching any network policy, identify the **exact error type**:

| Error | Layer | First check |
|---|---|---|
| `connection refused` | Application/Transport | Target pod running? Port correct? SG allowing traffic? |
| `connection timed out` | Network | NetworkPolicy blocking? Node SG? Route table? |
| `no route to host` | Network | Subnet routing, missing VPC peering |
| `TLS handshake timeout` | TLS | Outbound 443 blocked, proxy misconfigured |
| `name resolution failed` | DNS | CoreDNS healthy? VPC DNS enabled? |

---

## Issue 1: Pod Cannot Reach RDS / Internal Services

**Symptoms:**
- `connection refused` from app pod to `rds.cluster-inframind.internal`
- Works from some namespaces but not others

**Diagnosis:**
```bash
# Step 1: Can the pod reach the host at all?
kubectl exec -it <pod> -- nc -zv rds.cluster-inframind.internal 3306

# Step 2: Is a NetworkPolicy blocking egress?
kubectl get networkpolicy -n <namespace>
kubectl describe networkpolicy <policy-name> -n <namespace>

# Step 3: Is the node's security group allowing the traffic?
# Get node for this pod
kubectl get pod <pod> -o wide
# Check the node's SG allows outbound to RDS SG on 3306
aws ec2 describe-security-groups --group-ids <node-sg-id>

# Step 4: Is DNS resolving?
kubectl exec -it <pod> -- nslookup rds.cluster-inframind.internal
```

**Root Cause Analysis:**
- NetworkPolicy with `policyTypes: Egress` blocking outbound traffic
- Node security group missing outbound rule to RDS security group
- Missing egress rule for port 53 (DNS) — DNS itself is being blocked
- VPC peering or route table not set up between EKS and RDS subnets

**Immediate Fix:**
```bash
# Quick test: apply emergency allow-all to the affected namespace
kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: emergency-allow-all
  namespace: <affected-namespace>
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - {}
  egress:
  - {}
EOF

# If that fixes it, the problem is a NetworkPolicy — audit and fix properly
# Remember to delete this after resolving
kubectl delete networkpolicy emergency-allow-all -n <affected-namespace>
```

---

## Issue 2: TLS Handshake Timeout (Kubelet / Container Registry)

**Symptoms:**
- `TLS handshake timeout` in Kubelet or app logs
- `Error response from daemon: Get https://registry-1.docker.io/v2/: net/http: TLS handshake timeout`
- Image pulls failing

**Diagnosis:**
```bash
# Step 1: Can the node reach the internet on port 443?
kubectl run tls-test --image=busybox --rm -it -- \
  nc -zv registry-1.docker.io 443

# Step 2: Check node outbound SG rules
aws ec2 describe-security-groups \
  --group-ids <node-sg-id> \
  --query 'SecurityGroups[0].IpPermissionsEgress'

# Step 3: Is there an HTTP proxy required?
kubectl get configmap -n kube-system | grep proxy

# Step 4: Check NAT Gateway exists for private subnets
aws ec2 describe-nat-gateways \
  --filter Name=state,Values=available
```

**Root Cause Analysis:**
- Node security group missing outbound rule for port 443
- Private subnet EKS nodes have no NAT Gateway for internet access
- HTTP proxy required but not configured in container runtime
- VPC endpoint for ECR not set up (for AWS ECR images)

**Immediate Fix:**
```bash
# Allow outbound HTTPS from node SG
aws ec2 authorize-security-group-egress \
  --group-id <node-sg-id> \
  --protocol tcp --port 443 \
  --cidr 0.0.0.0/0

# For ECR images — use VPC endpoint instead of NAT (cheaper + more reliable)
aws ec2 create-vpc-endpoint \
  --vpc-id <vpc-id> \
  --service-name com.amazonaws.<region>.ecr.dkr \
  --vpc-endpoint-type Interface \
  --subnet-ids <subnet-ids> \
  --security-group-ids <sg-id>
```

---

## Issue 3: Pod-to-Pod Communication Failures

**Symptoms:**
- `connection refused` between microservices
- Intermittent connectivity — works sometimes, fails others
- Service mesh errors (Istio/Envoy)

**Diagnosis:**
```bash
# Test direct pod-to-pod (bypasses Service)
kubectl exec -it <source-pod> -- \
  nc -zv <target-pod-ip> <port>

# Test via Service (uses kube-proxy/DNS)
kubectl exec -it <source-pod> -- \
  nc -zv <service-name>.<namespace>.svc.cluster.local <port>

# Check if NetworkPolicy exists that might block
kubectl get networkpolicy -A

# Check endpoints are healthy
kubectl get endpoints <service-name>

# Check if Istio sidecar is interfering
kubectl logs <pod> -c istio-proxy | tail -50
```

**Root Cause Analysis:**
- NetworkPolicy blocking ingress on target pod
- Service selector not matching pod labels
- Istio mTLS policy mismatch between namespaces
- Pod readiness probe failing — pod excluded from endpoints

---

## Issue 4: DNS Resolution Failures

**Symptoms:**
- `no such host` errors
- `nslookup` fails from pods
- Intermittent DNS timeouts

**Diagnosis:**
```bash
# Check CoreDNS pods are running
kubectl get pods -n kube-system -l k8s-app=kube-dns

# Check CoreDNS logs for errors
kubectl logs -n kube-system -l k8s-app=kube-dns --tail=50

# Test from a pod
kubectl exec -it <pod> -- dig kubernetes.default.svc.cluster.local

# Check CoreDNS ConfigMap for misconfig
kubectl get configmap coredns -n kube-system -o yaml

# Verify VPC DNS is enabled (required for .internal hostnames)
aws ec2 describe-vpc-attribute \
  --vpc-id <vpc-id> --attribute enableDnsSupport
aws ec2 describe-vpc-attribute \
  --vpc-id <vpc-id> --attribute enableDnsHostnames
```

**Root Cause Analysis:**
- CoreDNS pods crashing or OOMKilled — scale up or increase memory limit
- NetworkPolicy blocking UDP/TCP 53 from pods to CoreDNS
- VPC `enableDnsSupport` or `enableDnsHostnames` disabled
- Route53 private hosted zone missing for `.internal` domain

**Immediate Fix:**
```bash
# Restart CoreDNS
kubectl rollout restart deployment/coredns -n kube-system

# If CoreDNS is OOMKilled, increase memory
kubectl patch deployment coredns -n kube-system \
  -p '{"spec":{"template":{"spec":{"containers":[{"name":"coredns","resources":{"limits":{"memory":"256Mi"}}}]}}}}'
```

---

## Issue 5: Ingress / Load Balancer Problems

**Symptoms:**
- 502/503 errors from external traffic
- SSL/TLS certificate errors
- Health checks failing

**Diagnosis:**
```bash
# Check ingress controller
kubectl get pods -n ingress-nginx
kubectl logs -n ingress-nginx deployment/ingress-nginx-controller --tail=100

# Check backend service is healthy
kubectl get endpoints <service-name>
kubectl describe ingress <ingress-name>

# Test backend directly
kubectl port-forward service/<service-name> 8080:80
curl http://localhost:8080/health
```

---

## Standard NetworkPolicy Templates

### Allow specific service egress to RDS
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-db-egress
  namespace: production
spec:
  podSelector:
    matchLabels:
      app: backend
  policyTypes:
  - Egress
  egress:
  - ports:
    - protocol: TCP
      port: 3306
  - ports:           # Always include DNS egress or nothing resolves
    - protocol: UDP
      port: 53
    - protocol: TCP
      port: 53
```

### Deny all, allow only frontend→backend
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-frontend-to-backend
  namespace: production
spec:
  podSelector:
    matchLabels:
      app: backend
  policyTypes:
  - Ingress
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app: frontend
    ports:
    - protocol: TCP
      port: 8080
```

---

## Escalation Contacts
- **Network Team:** network-team@company.com
- **Security Team:** security-oncall@company.com
- **Platform Engineering:** platform-team@company.com
