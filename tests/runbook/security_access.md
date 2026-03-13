# Security & Access Control Runbook

## Overview
This runbook covers security incidents, access control failures, secret management issues, and suspicious activity detection for InfraMind infrastructure.

---

## CRITICAL: Security Incident Priority

| Incident | Severity | Immediate action |
|---|---|---|
| Active data exfiltration detected | P0 | Isolate affected pod/node NOW, then investigate |
| Secret or API key leaked in logs/code | P1 | Rotate credential immediately, then audit |
| Unusual IAM activity from production role | P1 | Revoke session, audit CloudTrail |
| Pod running as root unexpectedly | P2 | Patch securityContext, redeploy |
| Failed auth spike (brute force) | P2 | Block source IP, check for successful auths |
| TLS certificate expiring | P2 | Renew certificate before expiry |
| Dependency with critical CVE | P3 | Patch in next release, check if exploitable |

**When in doubt — isolate first, investigate second. Do not wait for confirmation before isolating.**

---

## Issue 1: Secret or API Key Leaked

**Symptoms:**
- Secret found in Git commit, log output, or environment variable dump
- AWS GuardDuty alert for credential used from unexpected location
- Third-party scanning tool (GitHub secret scanning, truffleHog) flagged

**Diagnosis:**
```bash
# Find where the secret appears
git log --all --full-history -- '*.env' '*.yaml' '*.json'
git log -S "<leaked-secret-prefix>" --source --all

# Check if it's been used by looking at CloudTrail (for AWS keys)
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=AccessKeyId,AttributeValue=<key-id> \
  --start-time $(date -u -d '30 days ago' +%FT%TZ)

# Check pod logs for accidental secret printing
kubectl logs -l app=<service> --since=24h | grep -i "password\|secret\|token\|key" | \
  grep -v "redacted\|\*\*\*\*"
```

**Immediate Fix — Rotate first, investigate second:**
```bash
# AWS Access Key — disable immediately
aws iam update-access-key \
  --access-key-id <key-id> \
  --status Inactive

# Create new key
aws iam create-access-key --user-name <user>

# For Kubernetes Secret — rotate value
kubectl create secret generic <secret-name> \
  --from-literal=key=<new-value> \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart pods to pick up new secret
kubectl rollout restart deployment/<affected-deployment>

# Remove from Git history (requires force push — coordinate with team)
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch path/to/secret-file" \
  --prune-empty --tag-name-filter cat -- --all

# Revoke any active sessions using old credential
aws iam list-access-keys --user-name <user>
```

---

## Issue 2: Pod Running with Excessive Privileges

**Symptoms:**
- Pod running as root (UID 0)
- `privileged: true` in pod spec
- Pod has access to host network or host PID namespace
- Security scanner flagging pod security policy violations

**Diagnosis:**
```bash
# Find pods running as root
kubectl get pods -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}: {.spec.containers[*].securityContext.runAsUser}{"\n"}{end}' | \
  grep ": 0\|: $"

# Find privileged pods
kubectl get pods -A -o json | \
  jq '.items[] | select(.spec.containers[].securityContext.privileged==true) | .metadata.name'

# Check host namespace access
kubectl get pods -A -o json | \
  jq '.items[] | select(.spec.hostNetwork==true or .spec.hostPID==true) | .metadata.name'

# Full security audit of a specific pod
kubectl get pod <pod-name> -o yaml | \
  grep -A 20 "securityContext\|hostNetwork\|hostPID\|privileged"
```

**Root Cause Analysis:**
- Base image runs as root and securityContext not overriding it
- Developer testing with elevated privileges and left in place
- Third-party Helm chart defaults to privileged mode
- DaemonSet legitimately needs host access (node-level agents like Fluent Bit)

**Fix — Apply proper securityContext:**
```yaml
spec:
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    runAsGroup: 1000
    fsGroup: 1000
    seccompProfile:
      type: RuntimeDefault
  containers:
  - name: app
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities:
        drop:
        - ALL
```

```bash
# Apply and rollout
kubectl patch deployment <deployment> --patch-file security-patch.yaml
kubectl rollout restart deployment/<deployment>
```

---

## Issue 3: Unusual IAM Activity / Possible Credential Compromise

**Symptoms:**
- GuardDuty alert: `UnauthorizedAccess:IAMUser/InstanceCredentialExfiltration`
- API calls from unexpected IP addresses or regions
- CloudTrail shows API calls at unusual times
- Resource creation in unexpected regions

**Diagnosis:**
```bash
# Check recent API activity for the compromised role/user
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=UserName,AttributeValue=<username> \
  --start-time $(date -u -d '24 hours ago' +%FT%TZ) \
  --query 'Events[*].[EventTime,EventName,SourceIPAddress,ErrorCode]' \
  --output table

# Check for resource creation (attacker establishing persistence)
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=CreateUser \
  --start-time $(date -u -d '24 hours ago' +%FT%TZ)

aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=AttachUserPolicy \
  --start-time $(date -u -d '24 hours ago' +%FT%TZ)

# Check for new IAM users or roles created
aws iam list-users --query 'Users[*].[UserName,CreateDate]' | \
  sort -k2 | tail -10
```

**Immediate Fix:**
```bash
# Revoke all active sessions for the compromised role immediately
aws iam attach-role-policy \
  --role-name <role> \
  --policy-arn arn:aws:iam::aws:policy/AWSDenyAll
# This denies everything while you investigate

# Disable compromised access key
aws iam update-access-key \
  --access-key-id <key-id> \
  --status Inactive

# Delete any IAM resources created by attacker
aws iam list-users | grep -E "CreateDate.*$(date +%Y-%m-%d)" # Created today

# Isolate affected EC2 instance/pod (block all traffic via SG)
aws ec2 modify-instance-attribute \
  --instance-id <instance-id> \
  --groups <isolation-sg-id>   # SG with no rules = no traffic
```

---

## Issue 4: TLS Certificate Expiry

**Symptoms:**
- `SSL_ERROR_RX_RECORD_TOO_LONG` or `certificate has expired` in client errors
- Monitoring alert: certificate expires in < 30 days
- `kubectl describe certificate` shows not ready

**Diagnosis:**
```bash
# Check all cert-manager certificates
kubectl get certificates -A
kubectl describe certificate <cert-name> -n <namespace>

# Check expiry date directly
echo | openssl s_client -connect <your-domain>:443 2>/dev/null | \
  openssl x509 -noout -dates

# Check cert-manager is running
kubectl get pods -n cert-manager

# Check certificate renewal logs
kubectl logs -n cert-manager deployment/cert-manager | \
  grep -i "renew\|error\|fail" | tail -30
```

**Root Cause Analysis:**
- cert-manager pod crashed and stopped renewing certificates
- ACME challenge failing (DNS-01 or HTTP-01) — cert-manager can't prove domain ownership
- Let's Encrypt rate limit hit — too many renewals in a week
- Certificate was manually managed (not cert-manager) and rotation was missed

**Immediate Fix:**
```bash
# Force manual certificate renewal
kubectl annotate certificate <cert-name> -n <namespace> \
  cert-manager.io/force-renewal="$(date)"

# Check ACME challenge status
kubectl get challenge -A
kubectl describe challenge <challenge-name>

# If HTTP-01 challenge failing — check ingress is reachable
curl http://<domain>/.well-known/acme-challenge/test

# Emergency: use existing cert from another source while fixing
kubectl create secret tls <cert-secret> \
  --cert=path/to/cert.pem \
  --key=path/to/key.pem \
  -n <namespace>
```

---

## Issue 5: Brute Force / Unusual Authentication Spike

**Symptoms:**
- Spike in `401 Unauthorized` or `403 Forbidden` responses
- Single IP making thousands of requests
- GuardDuty: `UnauthorizedAccess:EC2/SSHBruteForce`

**Diagnosis:**
```bash
# Find top source IPs in ingress logs
kubectl logs -n ingress-nginx deployment/ingress-nginx-controller --since=1h | \
  grep "401\|403" | \
  awk '{print $1}' | sort | uniq -c | sort -rn | head -20

# Check if any succeeded after many failures
kubectl logs -n ingress-nginx deployment/ingress-nginx-controller --since=1h | \
  grep "<suspicious-ip>" | grep -v "401\|403" | head -10

# Check GuardDuty findings
aws guardduty list-findings \
  --detector-id <detector-id> \
  --finding-criteria '{"Criterion":{"service.action.actionType":{"Eq":["NETWORK_CONNECTION"]}}}'
```

**Immediate Fix:**
```bash
# Block IP at ingress level
kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: block-bad-actor
  namespace: production
spec:
  podSelector:
    matchLabels:
      app: frontend
  policyTypes:
  - Ingress
  ingress:
  - from:
    - ipBlock:
        cidr: 0.0.0.0/0
        except:
        - <bad-ip>/32
EOF

# Or add to nginx deny list
kubectl annotate ingress <ingress> \
  nginx.ingress.kubernetes.io/deny-list="<bad-ip>"

# If AWS WAF is in use — add IP to block list
aws wafv2 update-ip-set \
  --id <ip-set-id> \
  --name inframind-blocked-ips \
  --scope REGIONAL \
  --addresses "<bad-ip>/32" \
  --lock-token <lock-token>
```

---

## Security Monitoring Commands
```bash
# Check GuardDuty findings
aws guardduty list-findings \
  --detector-id <detector-id> \
  --finding-criteria '{"Criterion":{"service.archived":{"Eq":["false"]}}}'

# Check AWS Config for compliance
aws configservice describe-compliance-by-config-rule \
  --compliance-types NON_COMPLIANT

# Audit Kubernetes RBAC — who can do what
kubectl auth can-i --list --as=system:serviceaccount:<namespace>:<sa>

# Check for overly permissive ClusterRoleBindings
kubectl get clusterrolebindings -o json | \
  jq '.items[] | select(.roleRef.name=="cluster-admin") | .subjects'

# All secrets in a namespace (audit access)
kubectl get secrets -n <namespace>
```

---

## Escalation Contacts
- **Security Team:** security-oncall@company.com
- **Infrastructure Team:** infra-oncall@company.com
- **Legal/Compliance (data breach):** legal@company.com
- **AWS Security (active attack):** Open P1 case in AWS Support Console
- **Severity 1 Incidents:** +1-555-0123
