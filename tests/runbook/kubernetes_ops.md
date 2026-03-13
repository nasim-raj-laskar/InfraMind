# Kubernetes Cluster Operations Runbook

## Overview
This runbook covers Kubernetes cluster incidents, pod failures, scheduling issues, and control plane problems for InfraMind infrastructure on AWS EKS.

---

## CRITICAL: Pod Status Quick Reference

| Status | Meaning | First action |
|---|---|---|
| `Pending` | Not scheduled yet | Check node resources, taints, PVC binding |
| `ContainerCreating` | Scheduled but not started | Check image pull, volume mount, init containers |
| `CrashLoopBackOff` | Starting then crashing repeatedly | Check logs, liveness probe, exit code |
| `OOMKilled` | Killed by OS due to memory limit | Increase memory limit or fix memory leak |
| `ImagePullBackOff` | Cannot pull container image | Check image name, tag, registry credentials |
| `Evicted` | Removed due to node pressure | Check node disk/memory, increase limits |
| `Terminating` | Stuck during deletion | Check finalizers, force delete |
| `Error` | Exited with non-zero code | Check logs for application error |

---

## Issue 1: Pod Stuck in Pending

**Symptoms:**
- `kubectl get pods` shows `Pending` for more than 2 minutes
- No node assigned in `kubectl get pod -o wide`

**Diagnosis:**
```bash
# Step 1: Check why it's pending
kubectl describe pod <pod-name> | grep -A 20 Events
# Look for: "Insufficient cpu/memory", "no nodes available", "didn't match node affinity"

# Step 2: Check node capacity
kubectl describe nodes | grep -A 5 "Allocated resources"

# Step 3: Check for taints blocking scheduling
kubectl get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints

# Step 4: Check if PVC is unbound (if pod uses storage)
kubectl get pvc -n <namespace>
```

**Root Cause Analysis:**
- All nodes are at CPU/memory capacity — cluster autoscaler should add nodes
- Pod has `nodeSelector` or `nodeAffinity` that no node satisfies
- Pod tolerations don't match node taints
- PVC stuck in Pending prevents pod from scheduling

**Immediate Fix:**
```bash
# Check if cluster autoscaler is working
kubectl logs -n kube-system deployment/cluster-autoscaler | tail -30

# Manually cordon a node and drain to free resources (last resort)
kubectl cordon <node-name>
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data

# Check resource requests vs actual usage
kubectl top pods -n <namespace>
kubectl top nodes
```

---

## Issue 2: CrashLoopBackOff

**Symptoms:**
- Pod status `CrashLoopBackOff`
- Restart count incrementing rapidly
- `kubectl logs` shows application error before crash

**Diagnosis:**
```bash
# Get current logs (may be empty if crash is immediate)
kubectl logs <pod-name> -n <namespace>

# Get logs from PREVIOUS crash (most useful)
kubectl logs <pod-name> -n <namespace> --previous

# Check exit code — tells you WHY it crashed
kubectl describe pod <pod-name> | grep "Exit Code"
# Exit 1 = app error, Exit 137 = OOMKilled, Exit 139 = segfault, Exit 143 = SIGTERM

# Check liveness probe config
kubectl get pod <pod-name> -o yaml | grep -A 15 livenessProbe
```

**Exit Code Reference:**
| Exit Code | Cause | Fix |
|---|---|---|
| `1` | Application error | Check app logs |
| `137` | OOMKilled (memory limit) | Increase memory limit |
| `139` | Segmentation fault | Application bug |
| `143` | SIGTERM (graceful kill) | Check if probe is too aggressive |

**Root Cause Analysis:**
- Missing environment variable or secret that app requires at startup
- Liveness probe threshold too low — killing healthy pods during startup
- Application bug causing panic/crash
- Memory limit too low — OOMKilled immediately

**Immediate Fix:**
```bash
# Temporarily disable liveness probe to see if that's the issue
kubectl patch deployment <deployment> -n <namespace> \
  --type=json -p='[{"op":"remove","path":"/spec/template/spec/containers/0/livenessProbe"}]'

# Increase memory limit temporarily
kubectl set resources deployment <deployment> \
  -c <container-name> --limits=memory=512Mi

# Check if required secrets/configmaps exist
kubectl get secret <secret-name> -n <namespace>
kubectl get configmap <configmap-name> -n <namespace>
```

---

## Issue 3: ImagePullBackOff

**Symptoms:**
- Pod stuck in `ImagePullBackOff` or `ErrImagePull`
- `kubectl describe pod` shows image pull error

**Diagnosis:**
```bash
# See exact error
kubectl describe pod <pod-name> | grep -A 5 "Failed to pull image"

# Common errors:
# "not found" = wrong image name or tag doesn't exist
# "unauthorized" = missing or wrong imagePullSecret
# "TLS handshake timeout" = network issue (see network runbook)
# "no space left on device" = node disk full

# Check if imagePullSecret exists
kubectl get secret <pull-secret-name> -n <namespace>

# Verify the image exists (for ECR)
aws ecr describe-images \
  --repository-name <repo-name> \
  --image-ids imageTag=<tag>
```

**Root Cause Analysis:**
- Image tag doesn't exist (typo, deleted, or not yet pushed)
- `imagePullSecret` missing from namespace or deployment spec
- ECR repository permissions not granted to node IAM role
- Node disk full — no space to store pulled image layers

**Immediate Fix:**
```bash
# For ECR — ensure node role has ECR pull permissions
aws iam attach-role-policy \
  --role-name <eks-node-role> \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly

# Create imagePullSecret for private registry
kubectl create secret docker-registry regcred \
  --docker-server=<registry-url> \
  --docker-username=<user> \
  --docker-password=<password> \
  -n <namespace>

# Check node disk usage
kubectl describe node <node-name> | grep -A 5 "Conditions"
# If DiskPressure=True, clean up unused images
kubectl debug node/<node-name> -it --image=busybox -- chroot /host crictl rmi --prune
```

---

## Issue 4: OOMKilled (Out of Memory)

**Symptoms:**
- Pod restarts with exit code `137`
- `kubectl describe pod` shows `OOMKilled: true`
- Happens under load or at specific times

**Diagnosis:**
```bash
# Confirm OOMKill
kubectl describe pod <pod-name> | grep -i oom

# Check current memory usage
kubectl top pods -n <namespace>

# Check memory limit vs request
kubectl get pod <pod-name> -o yaml | grep -A 5 resources

# Check node memory pressure
kubectl describe node <node-name> | grep -A 3 "MemoryPressure"
```

**Root Cause Analysis:**
- Memory limit set too low for the workload
- Memory leak in application — usage grows until killed
- Sudden traffic spike causes memory spike
- JVM/Node.js not respecting container memory limits

**Fix:**
```bash
# Increase memory limit
kubectl set resources deployment <deployment> \
  -c <container-name> \
  --requests=memory=256Mi \
  --limits=memory=512Mi

# For JVM apps — set heap explicitly to stay within container limits
# Add to env: JAVA_OPTS="-Xms128m -Xmx384m"
kubectl set env deployment/<deployment> JAVA_OPTS="-Xms128m -Xmx384m"
```

---

## Issue 5: Node Not Ready

**Symptoms:**
- `kubectl get nodes` shows `NotReady`
- Pods on that node being evicted
- Cluster autoscaler may be creating replacement nodes

**Diagnosis:**
```bash
# Check node conditions
kubectl describe node <node-name> | grep -A 20 Conditions

# Check kubelet status (if you have node access)
systemctl status kubelet

# Check common NotReady causes
kubectl get events --field-selector involvedObject.name=<node-name>

# Check if node is in AWS console (may be terminated)
aws ec2 describe-instances \
  --filters "Name=private-dns-name,Values=<node-internal-dns>"
```

**Root Cause Analysis:**
- Kubelet crashed or lost connection to API server
- Node ran out of disk space (DiskPressure)
- EC2 instance terminated by AWS (spot interruption, hardware failure)
- Network partition between node and control plane

**Immediate Fix:**
```bash
# Cordon immediately to stop new pods scheduling there
kubectl cordon <node-name>

# Evict existing pods safely
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data --timeout=60s

# If spot interruption — check autoscaler is replacing it
kubectl logs -n kube-system deployment/cluster-autoscaler | grep "scale up"

# Force delete NotReady node if EC2 instance is gone
kubectl delete node <node-name>
```

---

## Issue 6: Deployment Rollout Stuck

**Symptoms:**
- `kubectl rollout status deployment/<name>` hangs
- New pods created but old ones not terminating
- Partial rollout — some old, some new pods running

**Diagnosis:**
```bash
# Check rollout status
kubectl rollout status deployment/<deployment> -n <namespace>

# Check replica sets
kubectl get rs -n <namespace>

# Check if new pods are healthy
kubectl get pods -n <namespace> -l app=<app>

# Check minReadySeconds and progressDeadlineSeconds
kubectl describe deployment <deployment> | grep -E "MinReady|Progress"
```

**Immediate Fix:**
```bash
# Roll back if new version is broken
kubectl rollout undo deployment/<deployment> -n <namespace>

# Check rollout history
kubectl rollout history deployment/<deployment>

# Roll back to specific revision
kubectl rollout undo deployment/<deployment> --to-revision=2
```

---

## Useful EKS Cluster Health Commands
```bash
# Overall cluster health
kubectl get componentstatuses
kubectl get nodes
kubectl top nodes

# All failing pods across cluster
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded

# Recent cluster events (errors only)
kubectl get events -A --sort-by=.metadata.creationTimestamp | grep -i "error\|fail\|kill" | tail -30

# Check control plane (EKS managed)
aws eks describe-cluster --name inframind-cluster \
  --query 'cluster.status'
```

---

## Escalation Contacts
- **Platform Engineering:** platform-team@company.com
- **Infrastructure Team:** infra-oncall@company.com
- **Severity 1 Incidents:** +1-555-0123
