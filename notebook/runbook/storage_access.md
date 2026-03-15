# Storage Access Runbook

## Overview
This runbook covers storage-related incidents, persistent volume issues, and data access problems for InfraMind infrastructure on AWS EKS using EBS and EFS volumes.

---

## CRITICAL: Storage Error Quick Reference

| Error | Likely cause | First action |
|---|---|---|
| `FailedMount: timeout` | EBS volume stuck attached to old node | Force detach from AWS console |
| `Multi-Attach error` | EBS (RWO) mounted on 2 nodes | Delete stuck pod, let scheduler reassign |
| `no space left on device` | PV full or EBS volume full | Expand PVC or clean up data |
| `StorageClass not found` | SC deleted or wrong name | `kubectl get storageclass` |
| `Pod stuck ContainerCreating` | PVC not bound | Check PVC status and provisioner logs |
| `Permission denied on /data` | fsGroup mismatch | Check pod securityContext fsGroup |

---

## Issue 1: Pod Stuck in ContainerCreating (Volume Not Mounting)

**Symptoms:**
- Pod stuck in `ContainerCreating` for more than 2 minutes
- `FailedMount` in pod events
- `kubectl describe pod` shows volume attachment pending

**Diagnosis:**
```bash
# Step 1: Check pod events
kubectl describe pod <pod-name> | grep -A 20 Events

# Step 2: Check PVC is Bound
kubectl get pvc -n <namespace>
# STATUS must be "Bound" — if "Pending", provisioning failed

# Step 3: If PVC Pending, check provisioner
kubectl describe pvc <pvc-name>
kubectl get events -n <namespace> --sort-by=.metadata.creationTimestamp | tail -20

# Step 4: Check CSI driver pods are healthy
kubectl get pods -n kube-system -l app=ebs-csi-controller
kubectl get pods -n kube-system -l app=ebs-csi-node

# Step 5: Check VolumeAttachment objects
kubectl get volumeattachment
```

**Root Cause Analysis:**
- EBS volume stuck in "attaching" state from a previous node crash
- CSI driver pod is down or crashing
- Node doesn't have permission to attach EBS (missing IAM role policy)
- Availability zone mismatch — EBS volume in us-east-1a but pod scheduled to us-east-1b

**Immediate Fix:**
```bash
# If EBS volume is stuck attaching — force detach from AWS
aws ec2 describe-volumes --filters Name=status,Values=in-use \
  --query 'Volumes[*].[VolumeId,Attachments[0].State,Attachments[0].InstanceId]'

aws ec2 detach-volume --volume-id <vol-id> --force

# Restart CSI controller to clear stale state
kubectl rollout restart deployment/ebs-csi-controller -n kube-system

# If AZ mismatch, add node affinity to pod spec to match volume's AZ
# Or delete PVC+PV and recreate in the correct AZ
```

---

## Issue 2: Multi-Attach Error (EBS RWO Volume)

**Symptoms:**
- `Multi-Attach error for volume: Volume is already exclusively attached`
- Pod cannot start after node failure or rolling update

**Diagnosis:**
```bash
# Find which node the volume is still attached to
kubectl get volumeattachment -o wide

# Check if the old pod/node is actually gone
kubectl get nodes
kubectl get pod <old-pod> -o wide
```

**Root Cause Analysis:**
- EBS volumes are ReadWriteOnce (RWO) — only one node at a time
- Node crashed without gracefully detaching volumes
- Kubernetes hasn't cleaned up the VolumeAttachment object yet

**Immediate Fix:**
```bash
# Delete the stuck VolumeAttachment (Kubernetes will recreate it correctly)
kubectl delete volumeattachment <attachment-name>

# If node is NotReady/dead, force delete the old pod
kubectl delete pod <stuck-pod> --grace-period=0 --force
```

---

## Issue 3: No Space Left on Device

**Symptoms:**
- `no space left on device` in application or DB logs
- Database write failures
- Application crashes on file write

**Diagnosis:**
```bash
# Check from inside the pod
kubectl exec -it <pod> -- df -h

# Check EBS volume usage in AWS
aws cloudwatch get-metric-statistics \
  --namespace AWS/EBS \
  --metric-name VolumeConsumedReadWriteOps \
  --dimensions Name=VolumeId,Value=<vol-id> \
  --start-time $(date -u -d '1 hour ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 300 --statistics Sum

# Find what's eating the space
kubectl exec -it <pod> -- du -sh /* 2>/dev/null | sort -rh | head -20
```

**Immediate Fix:**
```bash
# Expand the PVC (StorageClass must have allowVolumeExpansion: true)
kubectl patch pvc <pvc-name> -n <namespace> \
  -p '{"spec":{"resources":{"requests":{"storage":"50Gi"}}}}'

# Watch expansion
kubectl get pvc <pvc-name> -w

# Clean up logs immediately if that's the culprit
kubectl exec -it <pod> -- find /var/log -name "*.log" -mtime +7 -delete
```

---

## Issue 4: Permission Denied on Mounted Volume

**Symptoms:**
- `permission denied` when app tries to write to `/data` or mount path
- App starts but immediately crashes with permission error

**Diagnosis:**
```bash
# Check what user the app runs as
kubectl exec -it <pod> -- id

# Check mount point permissions
kubectl exec -it <pod> -- ls -la /data

# Check pod securityContext
kubectl get pod <pod> -o yaml | grep -A 10 securityContext
```

**Root Cause Analysis:**
- `fsGroup` in pod securityContext doesn't match the volume's ownership
- Volume was created with root ownership but app runs as non-root

**Fix:**
```yaml
# Set fsGroup to match the app's GID
spec:
  securityContext:
    fsGroup: 1000          # Kubernetes will chown the volume to this group
    runAsUser: 1000
    runAsGroup: 1000
  containers:
  - name: app
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
```

---

## Issue 5: Data Corruption or Backup Failure

**Symptoms:**
- Application data inconsistencies
- File system errors (`fsck` needed)
- Snapshot creation failing

**Immediate Actions:**
```bash
# FIRST: Stop writes to prevent further corruption
kubectl scale deployment <app> --replicas=0

# Create emergency snapshot BEFORE any recovery attempt
kubectl apply -f - <<EOF
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: emergency-snap-$(date +%Y%m%d-%H%M)
spec:
  source:
    persistentVolumeClaimName: <pvc-name>
  volumeSnapshotClassName: csi-aws-vsc
EOF

# Run fsck on detached volume (must detach first)
aws ec2 detach-volume --volume-id <vol-id>
# Attach to a maintenance EC2 instance
aws ec2 attach-volume --volume-id <vol-id> --instance-id <maint-instance> --device /dev/xvdf
# SSH to maintenance instance and run:
# sudo fsck -y /dev/xvdf
```

---

## Recovery: Restore from Snapshot

```bash
# List available snapshots
kubectl get volumesnapshot -n <namespace>

# Restore into a new PVC
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: restored-data-pvc
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 50Gi
  dataSource:
    name: <snapshot-name>
    kind: VolumeSnapshot
    apiGroup: snapshot.storage.k8s.io
EOF
```

---

## Storage Classes (InfraMind Standard)

```yaml
# Standard gp3 — general purpose
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: gp3-standard
provisioner: ebs.csi.aws.com
parameters:
  type: gp3
  encrypted: "true"
reclaimPolicy: Retain          # IMPORTANT: Retain prevents accidental data loss
allowVolumeExpansion: true
volumeBindingMode: WaitForFirstConsumer   # Ensures AZ match with pod
```

**Always use `reclaimPolicy: Retain`** — `Delete` will destroy the EBS volume when the PVC is deleted. For production data, always Retain and clean up manually.

**Always use `volumeBindingMode: WaitForFirstConsumer`** — prevents the Multi-Attach AZ mismatch issue by waiting to see which node the pod lands on before provisioning the EBS volume.

---

## Monitoring & Alerts

```bash
# Check all PVC statuses
kubectl get pvc -A

# Find any pods with volume issues
kubectl get events -A --field-selector reason=FailedMount

# Check CSI driver health
kubectl get pods -n kube-system | grep csi
```

**Critical Alert Thresholds:**
- EBS volume usage > 80% → expand immediately
- PVC in Pending state > 5 minutes → provisioner issue
- VolumeAttachment stuck > 10 minutes → force detach

---

## Escalation Contacts
- **Storage Team:** storage-team@company.com
- **Data Engineering:** data-team@company.com
- **Infrastructure Team:** infra-oncall@company.com
- **Emergency Hotline:** +1-555-0199
