# Storage Access Runbook

## Overview
This runbook covers storage-related incidents, persistent volume issues, and data access problems for InfraMind infrastructure.

## Common Storage Issues

### Persistent Volume Mount Failures
**Symptoms:**
- Pods stuck in `ContainerCreating` state
- `FailedMount` events in pod descriptions
- `no space left on device` errors

**Immediate Actions:**
1. Check PV status: `kubectl get pv,pvc -A`
2. Describe failing pod: `kubectl describe pod pod-name`
3. Verify storage class: `kubectl get storageclass`

**Root Cause Analysis:**
- Storage provisioner issues
- Insufficient storage capacity
- Permission/access control problems
- Node storage exhaustion

### Volume Attachment Problems
**Symptoms:**
- Pods cannot start due to volume attachment failures
- `Multi-Attach error` for volumes
- Storage driver errors in node logs

**Immediate Actions:**
1. Check volume attachments: `kubectl get volumeattachment`
2. Verify node capacity: `kubectl describe node node-name`
3. Check CSI driver status: `kubectl get pods -n kube-system -l app=csi-driver`

### Data Corruption or Loss
**Symptoms:**
- Application data inconsistencies
- File system errors
- Backup restoration failures

**Immediate Actions:**
1. Stop affected applications immediately
2. Create emergency snapshot: `kubectl create volumesnapshot emergency-snap --source-pvc=data-pvc`
3. Run file system check: `fsck /dev/disk-device`

## Storage Troubleshooting

### PVC Debugging
```bash
# Check PVC status and events
kubectl describe pvc pvc-name

# Verify storage class configuration
kubectl describe storageclass storage-class-name

# Check provisioner logs
kubectl logs -n kube-system -l app=storage-provisioner
```

### Volume Snapshot Operations
```yaml
# Create volume snapshot
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: data-snapshot
spec:
  source:
    persistentVolumeClaimName: data-pvc
  volumeSnapshotClassName: csi-snapclass
```

### Emergency Storage Expansion
```bash
# Patch PVC to increase size
kubectl patch pvc data-pvc -p '{"spec":{"resources":{"requests":{"storage":"100Gi"}}}}'

# Verify expansion
kubectl get pvc data-pvc -w
```

## Recovery Procedures

### Restore from Snapshot
```yaml
# Create PVC from snapshot
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
    name: data-snapshot
    kind: VolumeSnapshot
    apiGroup: snapshot.storage.k8s.io
```

### Migrate Data Between Volumes
```bash
# Create migration job
kubectl create job data-migration --image=busybox -- sh -c "cp -r /source/* /destination/"

# Mount both volumes to migration pod
kubectl patch job data-migration -p '{"spec":{"template":{"spec":{"volumes":[{"name":"source","persistentVolumeClaim":{"claimName":"old-pvc"}},{"name":"dest","persistentVolumeClaim":{"claimName":"new-pvc"}}]}}}}'
```

### Storage Class Migration
```bash
# Create new PVC with different storage class
kubectl apply -f new-storage-class-pvc.yaml

# Use data migration job to copy data
kubectl create job storage-migration --image=alpine -- sh -c "cp -r /old-data/* /new-data/"
```

## Performance Optimization

### Storage Performance Testing
```bash
# Run I/O performance test
kubectl run storage-test --image=busybox --rm -it -- sh
dd if=/dev/zero of=/data/testfile bs=1M count=1000 oflag=direct

# Check IOPS performance
fio --name=random-write --ioengine=libaio --rw=randwrite --bs=4k --size=1G --numjobs=1 --iodepth=1 --runtime=60 --time_based --group_reporting
```

### Storage Monitoring
```bash
# Check node storage usage
kubectl top nodes

# Monitor PVC usage
kubectl get pvc -A -o custom-columns=NAME:.metadata.name,CAPACITY:.spec.resources.requests.storage,USED:.status.capacity.storage

# Check storage driver metrics
kubectl get --raw /metrics | grep storage
```

## Backup and Disaster Recovery

### Automated Backup Strategy
```yaml
# Scheduled volume snapshots
apiVersion: batch/v1
kind: CronJob
metadata:
  name: daily-backup
spec:
  schedule: "0 2 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: backup
            image: backup-tool:latest
            command: ["create-snapshot"]
```

### Cross-Region Backup
```bash
# Export snapshot to object storage
kubectl create job backup-export --image=backup-tool -- export-snapshot snapshot-name s3://backup-bucket/

# Verify backup integrity
kubectl create job backup-verify --image=backup-tool -- verify-backup s3://backup-bucket/snapshot-name
```

## Storage Security

### Access Control
```yaml
# Pod Security Context for storage access
apiVersion: v1
kind: Pod
spec:
  securityContext:
    runAsUser: 1000
    runAsGroup: 1000
    fsGroup: 1000
  containers:
  - name: app
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
```

### Encryption at Rest
```yaml
# Storage class with encryption
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: encrypted-storage
provisioner: kubernetes.io/aws-ebs
parameters:
  type: gp3
  encrypted: "true"
  kmsKeyId: "arn:aws:kms:region:account:key/key-id"
```

## Monitoring and Alerts

### Key Storage Metrics
- PVC usage percentage
- Volume attachment failures
- Storage provisioning errors
- Backup success rates

### Critical Alerts
- Storage usage > 85%
- PVC mount failures
- Snapshot creation failures
- Data corruption detected

## Escalation Contacts
- **Storage Team:** storage-team@company.com
- **Data Engineering:** data-team@company.com
- **Infrastructure Team:** infra-oncall@company.com
- **Emergency Hotline:** +1-555-0199