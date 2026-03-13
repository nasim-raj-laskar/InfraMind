# AWS Cloud Infrastructure Runbook

## Overview
This runbook covers AWS infrastructure incidents including EC2, IAM, VPC, EKS, and service limits for InfraMind. Region: `ap-south-1` (Mumbai).

---

## CRITICAL: AWS Error Quick Reference

| Error | Service | First suspect |
|---|---|---|
| `UnauthorizedOperation` / `AccessDenied` | Any | IAM policy missing permission |
| `RequestExpired` | Any | System clock skew > 5 minutes |
| `Throttling` / `RequestLimitExceeded` | Any | API rate limit hit |
| `InsufficientInstanceCapacity` | EC2/EKS | AWS availability zone out of capacity |
| `VcpuLimitExceeded` | EC2/EKS | Account-level vCPU limit reached |
| `InvalidClientTokenId` | Any | Wrong AWS region or invalid credentials |
| `NoCredentialProviders` | SDK/CLI | IAM role not attached or IRSA misconfigured |

---

## Issue 1: IAM AccessDenied / UnauthorizedOperation

**Symptoms:**
- `AccessDenied` or `UnauthorizedOperation` in application logs
- AWS CLI returns `An error occurred (AccessDenied)`
- Pod cannot access AWS service (S3, SQS, RDS, etc.)

**Diagnosis:**
```bash
# Step 1: Identify exact action being denied
# The error message always contains the action: e.g., "s3:PutObject", "sqs:ReceiveMessage"

# Step 2: Check what identity is being used
# For IRSA (pod-level IAM):
kubectl get serviceaccount <sa-name> -n <namespace> -o yaml | grep eks.amazonaws.com

# For node-level IAM:
aws sts get-caller-identity   # Run from inside the pod

# Step 3: Simulate the policy
aws iam simulate-principal-policy \
  --policy-source-arn <role-arn> \
  --action-names s3:PutObject \
  --resource-arns arn:aws:s3:::inframind-bucket/*
# EvalDecision: "allowed" or "explicitDeny" or "implicitDeny"

# Step 4: Check if there's an SCP (Service Control Policy) blocking it
aws organizations list-policies-for-target \
  --target-id <account-id> \
  --filter SERVICE_CONTROL_POLICY
```

**Root Cause Analysis:**
- IAM role missing required action (e.g., `sqs:DeleteMessage` added but `sqs:ReceiveMessage` forgotten)
- IRSA annotation missing on ServiceAccount — pod using node role instead of intended role
- Resource ARN in policy uses wrong account ID or region
- S3 bucket policy denying access even if IAM allows it (explicit deny wins)
- AWS Organizations SCP blocking the action at account level

**Immediate Fix:**
```bash
# Add missing permission to IAM role
aws iam put-role-policy \
  --role-name <role-name> \
  --policy-name inframind-inline-policy \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": ["sqs:ReceiveMessage","sqs:DeleteMessage","sqs:GetQueueAttributes"],
      "Resource": "arn:aws:sqs:ap-south-1:<account>:inframind-*"
    }]
  }'

# For IRSA — annotate ServiceAccount correctly
kubectl annotate serviceaccount <sa-name> -n <namespace> \
  eks.amazonaws.com/role-arn=arn:aws:iam::<account>:role/<role-name> \
  --overwrite

# Restart pods to pick up new IRSA token
kubectl rollout restart deployment/<deployment>
```

---

## Issue 2: EKS Node Scaling Failure / InsufficientInstanceCapacity

**Symptoms:**
- Cluster autoscaler failing to add nodes
- `InsufficientInstanceCapacity` in autoscaler logs
- Pods stuck in Pending with no new nodes being added

**Diagnosis:**
```bash
# Check autoscaler logs
kubectl logs -n kube-system deployment/cluster-autoscaler | \
  grep -E "error|scale|capacity" | tail -30

# Check what instance types are being requested
kubectl logs -n kube-system deployment/cluster-autoscaler | \
  grep "InsufficientInstanceCapacity"

# Check current node group config
aws eks describe-nodegroup \
  --cluster-name inframind-cluster \
  --nodegroup-name <nodegroup-name>

# Check account-level vCPU limits
aws service-quotas get-service-quota \
  --service-code ec2 \
  --quota-code L-1216C47A   # Running On-Demand Standard instances vCPUs
```

**Root Cause Analysis:**
- Specific instance type unavailable in AZ — AWS capacity issue
- Account vCPU limit reached — need to request quota increase
- Node group configured for single AZ — no capacity there
- Spot instance interruption causing nodes to terminate faster than added

**Immediate Fix:**
```bash
# Add multiple instance types to node group (fallback options)
aws eks update-nodegroup-config \
  --cluster-name inframind-cluster \
  --nodegroup-name <nodegroup> \
  --scaling-config minSize=2,maxSize=20,desiredSize=5

# For immediate capacity — switch to on-demand temporarily
# Edit node group launch template to use On-Demand instead of Spot

# Request quota increase (not immediate — do proactively)
aws service-quotas request-service-quota-increase \
  --service-code ec2 \
  --quota-code L-1216C47A \
  --desired-value 500

# Spread across AZs — update node group subnets
aws eks update-nodegroup-config \
  --cluster-name inframind-cluster \
  --nodegroup-name <nodegroup> \
  --subnets subnet-az1 subnet-az2 subnet-az3
```

---

## Issue 3: AWS API Throttling

**Symptoms:**
- `ThrottlingException: Rate exceeded`
- `RequestLimitExceeded` in logs
- AWS SDK retries exhausted
- Affects multiple services simultaneously

**Diagnosis:**
```bash
# Check CloudTrail for throttled API calls
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=ThrottlingException \
  --start-time $(date -u -d '1 hour ago' +%FT%TZ) \
  --max-results 20

# Identify which service and which call is being throttled
# Look at error: "Rate exceeded for API: DescribeInstances"

# Check if it's one service calling AWS too frequently
kubectl logs -l app=<service> --since=30m | grep "Throttling" | \
  awk '{print $NF}' | sort | uniq -c | sort -rn
```

**Root Cause Analysis:**
- Application calling AWS API in a tight loop (e.g., polling instead of using events/SNS)
- Too many pods all calling the same AWS API simultaneously
- CloudWatch metrics/logs agent calling API too frequently
- Missing pagination — fetching all results in one call hitting limits

**Immediate Fix:**
```bash
# Add exponential backoff — most AWS SDKs have built-in retry with backoff
# Ensure SDK retry config is set (Python boto3 example):
# config = Config(retries={'max_attempts': 10, 'mode': 'adaptive'})

# For EC2 DescribeInstances throttling — switch to EventBridge for state changes
# instead of polling

# Temporarily reduce number of pods making AWS API calls
kubectl scale deployment <aws-heavy-service> --replicas=2

# Use AWS resource tagging and filter instead of listing all resources
```

---

## Issue 4: VPC / Subnet Connectivity Issues

**Symptoms:**
- Pods cannot reach internet (for external APIs, image pulls)
- Cross-AZ traffic failing
- VPC peering not working

**Diagnosis:**
```bash
# Check NAT Gateway status (for private subnet internet access)
aws ec2 describe-nat-gateways \
  --filter Name=state,Values=available \
  --query 'NatGateways[*].[NatGatewayId,State,SubnetId]'

# Check route tables for private subnets
aws ec2 describe-route-tables \
  --filters Name=tag:Name,Values=inframind-private-rt \
  --query 'RouteTables[0].Routes'
# Should have: 0.0.0.0/0 → nat-xxxxxx

# Check VPC flow logs for dropped packets
aws logs filter-log-events \
  --log-group-name /aws/vpc/flowlogs/inframind-vpc \
  --filter-pattern "REJECT" \
  --start-time $(date -s '30 minutes ago' +%s000) | tail -20

# Verify VPC peering is active (if connecting to another VPC)
aws ec2 describe-vpc-peering-connections \
  --filters Name=status-code,Values=active
```

**Root Cause Analysis:**
- NAT Gateway deleted or in wrong subnet — private pods lose internet
- Route table missing or wrong — traffic has no path
- Security group blocking traffic (see network runbook)
- VPC peering route missing from route table — accepted peering but forgot to add routes

**Immediate Fix:**
```bash
# If NAT Gateway missing — create one
aws ec2 create-nat-gateway \
  --subnet-id <public-subnet-id> \   # Must be PUBLIC subnet
  --allocation-id <elastic-ip-id>

# Add route to route table
aws ec2 create-route \
  --route-table-id <private-rt-id> \
  --destination-cidr-block 0.0.0.0/0 \
  --nat-gateway-id <nat-gw-id>
```

---

## Issue 5: AWS Credentials Expired / NoCredentialProviders

**Symptoms:**
- `NoCredentialProviders: no valid providers in chain`
- `ExpiredTokenException: The security token included in the request is expired`
- Happens on pods that were running fine but suddenly fail

**Diagnosis:**
```bash
# Check IRSA token expiry
kubectl exec -it <pod> -- \
  cat /var/run/secrets/eks.amazonaws.com/serviceaccount/token | \
  cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool | grep exp

# Check if IRSA is configured at all
kubectl get pod <pod> -o yaml | grep -A 5 "serviceAccountName"
kubectl get serviceaccount <sa> -n <namespace> -o yaml

# Test credential resolution from inside the pod
kubectl exec -it <pod> -- aws sts get-caller-identity
```

**Root Cause Analysis:**
- IRSA not configured — pod using expired instance profile credentials
- ServiceAccount annotation missing the IAM role ARN
- Pod predates IRSA setup — old pods may not have token mounted
- Token webhook (EKS pod identity) not running in kube-system

**Immediate Fix:**
```bash
# Restart pod to get fresh IRSA token (tokens auto-rotate but pod must restart)
kubectl delete pod <pod-name>

# Verify EKS pod identity webhook is running
kubectl get pods -n kube-system | grep pod-identity

# Re-annotate ServiceAccount and restart
kubectl annotate serviceaccount <sa> -n <namespace> \
  eks.amazonaws.com/role-arn=arn:aws:iam::<account>:role/<role> --overwrite
kubectl rollout restart deployment/<deployment>
```

---

## AWS Health & Cost Monitoring
```bash
# Check AWS service health
aws health describe-events \
  --filter eventStatusCodes=open \
  --region ap-south-1

# Check EKS cluster health
aws eks describe-cluster \
  --name inframind-cluster \
  --query 'cluster.[status,health]'

# List all running EC2 instances (EKS nodes)
aws ec2 describe-instances \
  --filters "Name=tag:eks:cluster-name,Values=inframind-cluster" \
             "Name=instance-state-name,Values=running" \
  --query 'Reservations[*].Instances[*].[InstanceId,InstanceType,State.Name]'

# Check current month spend
aws ce get-cost-and-usage \
  --time-period Start=$(date +%Y-%m-01),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics BlendedCost
```

---

## Escalation Contacts
- **Infrastructure Team:** infra-oncall@company.com
- **Cloud Architecture:** cloud-arch@company.com
- **AWS Support:** (open case in AWS console for P1/P2)
- **Severity 1 Incidents:** +1-555-0123
