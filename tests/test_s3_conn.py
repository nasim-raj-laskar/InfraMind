# test_s3_connection.py — run this from your project root
import sys
sys.path.insert(0, ".")   # so core/, config/ are importable

from dags.ingestion import fetch_logs_from_s3, fetch_logs_by_date

print("── Testing S3 connection ──────────────────────────────")

# Test 1: fetch by last modified (what the DAG uses by default)
logs = fetch_logs_from_s3(
    bucket="inframind-data-hub",
    prefix="raw/",
    since_hours=24,    # last 24h so we catch the files we just uploaded
)
print(f"fetch_logs_from_s3: got {len(logs)} log lines")
for log in logs[:3]:
    print(f"  → {log[:100]}")

print()

# Test 2: fetch by date partition
from datetime import datetime
today = datetime.utcnow().strftime("%Y/%m/%d")
logs2 = fetch_logs_by_date(date=today, bucket="inframind-data-hub")
print(f"fetch_logs_by_date({today}): got {len(logs2)} log lines")
for log in logs2[:3]:
    print(f"  → {log[:100]}")