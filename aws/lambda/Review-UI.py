import boto3, json, os
from decimal import Decimal

dynamo = boto3.resource('dynamodb')
table  = dynamo.Table('rca_reviews')


#  Convert DynamoDB types (Decimal → float)
def convert(item):
    if isinstance(item, list):
        return [convert(i) for i in item]
    elif isinstance(item, dict):
        return {k: convert(v) for k, v in item.items()}
    elif isinstance(item, Decimal):
        return float(item)
    else:
        return item


#  Common response helper
def response(body, status=200):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body)
    }


def lambda_handler(event, context):
    try:
        path   = event.get("rawPath") or event.get("path", "/")
        method = event.get("requestContext", {}).get("http", {}).get("method") \
                 or event.get("httpMethod", "GET")

        # ─── GET /queue ───
        if path.endswith('/queue') and method == 'GET':
            items = table.scan(
                FilterExpression='#s = :s',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':s': 'pending'}
            ).get('Items', [])

            items = convert(items)  # 🔥 FIX

            return response(items)

        # ─── GET /rca/{id} ───
        if '/rca/' in path and method == 'GET':
            iid = path.split('/')[-1]

            record = table.get_item(
                Key={'incident_id': iid}
            ).get('Item')

            if not record:
                return response({"error": "Not found"}, 404)

            record = convert(record)  # 🔥 FIX

            # parse ai_critic safely
            try:
                record['ai_critic'] = json.loads(record.get('ai_critic', '{}'))
            except:
                record['ai_critic'] = {}

            return response(record)

        # ─── POST /approve ───
        if path.endswith('/approve') and method == 'POST':
            body = json.loads(event.get('body', '{}'))

            record = table.get_item(
                Key={'incident_id': body.get('incident_id')}
            ).get('Item')

            if not record:
                return response({"error": "Not found"}, 404)

            sfn = boto3.client('stepfunctions')

            sfn.send_task_success(
                taskToken=record['task_token'],
                output=json.dumps({
                    'incident_id': body.get('incident_id'),
                    'rater_id': body.get('rater_id')
                })
            )

            return response({'status': 'approved'})

        # ─── POST /reject ───
        if path.endswith('/reject') and method == 'POST':
            body = json.loads(event.get('body', '{}'))

            record = table.get_item(
                Key={'incident_id': body.get('incident_id')}
            ).get('Item')

            if not record:
                return response({"error": "Not found"}, 404)

            sfn = boto3.client('stepfunctions')

            sfn.send_task_failure(
                taskToken=record['task_token'],
                error='HumanRejection',
                cause=json.dumps({
                    'incident_id': body.get('incident_id'),
                    'human_feedback': body.get('human_feedback')
                })
            )

            return response({'status': 'rejected'})

        # ─── Default UI ───
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "text/html",
                "Access-Control-Allow-Origin": "*"
            },
            "body": get_ui_html()
        }

    except Exception as e:
        return response({
            "error": str(e),
            "event": event
        }, 500)


def get_ui_html():
    return """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>InfraMind Review</title>

<style>
body {
  font-family: system-ui;
  max-width: 900px;
  margin: 40px auto;
  background: #f5f7fb;
}

.card {
  background: white;
  padding: 20px;
  margin: 12px 0;
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.08);
}

.btn {
  padding: 8px 14px;
  border-radius: 6px;
  border: none;
  cursor: pointer;
  margin-top: 10px;
}

.approve { background: #22c55e; color: white; }
.reject  { background: #ef4444; color: white; }

.badge {
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: bold;
}

.score {
  background: #111;
  color: #22c55e;
}

pre {
  background: #111;
  color: #0f0;
  padding: 10px;
  border-radius: 6px;
  white-space: pre-wrap;
}

details {
  margin-top: 10px;
}

textarea, input, select {
  width: 100%;
  padding: 8px;
  margin-top: 6px;
  margin-bottom: 10px;
  border-radius: 6px;
  border: 1px solid #ccc;
}

#error {
  color: red;
  margin-bottom: 10px;
}
</style>
</head>

<body>

<h2>InfraMind — Pending Reviews</h2>

<div id="error"></div>
<div id="queue">Loading...</div>
<div id="detail" style="display:none;"></div>

<script>
const API = 'API_GATEWAY_URL_HERE'; // REPLACE WITH YOUR API GATEWAY URL
let currentId = null;
const rater = prompt("Enter SRE ID") || "unknown";

function safeParse(data) {
  if (!data) return null;
  if (typeof data === "object") return data;
  try { return JSON.parse(data); } catch { return null; }
}

function showError(msg) {
  document.getElementById("error").innerText = msg;
}

// ─── LOAD QUEUE ───
async function loadQueue() {
  try {
    const res = await fetch(API + "/queue");
    if (!res.ok) throw new Error("Queue API failed");

    const items = await res.json();

    if (!items.length) {
      document.getElementById("queue").innerHTML =
        '<div class="card">No pending reviews</div>';
      return;
    }

    document.getElementById("queue").innerHTML =
      items.map(i => `
        <div class="card">
          <b>${i.incident_id}</b>
          <span style="margin-left:10px;color:#666;">
            ${i.log_service || ''} • ${i.severity || ''}
          </span>
          <button style="float:right"
            onclick="loadRCA('${i.incident_id}')">
            Review
          </button>
        </div>
      `).join("");

  } catch (e) {
    showError("Queue failed: " + e.message);
  }
}

// ─── LOAD RCA ───
async function loadRCA(id) {
  currentId = id;

  try {
    const res = await fetch(API + "/rca/" + id);
    if (!res.ok) throw new Error("RCA API failed");

    const record = await res.json();

    const rca = safeParse(record.rca_output) || {};
    const critic = safeParse(record.ai_critic);

    const score = critic?.score ?? "-";
    const reasoning = critic?.reasoning ?? "No critic data";

    let logDisplay = record.raw_log || "";
    const parsedLog = safeParse(logDisplay);
    if (parsedLog) logDisplay = JSON.stringify(parsedLog, null, 2);

    document.getElementById("queue").style.display = "none";
    document.getElementById("detail").style.display = "block";

    document.getElementById("detail").innerHTML = `
      <div class="card">

        <h3>${rca.summary || "No summary"}</h3>

        <span class="badge score">Score: ${score}</span>

        <p><b>Severity:</b> ${rca.severity || "-"}</p>
        <p><b>Service:</b> ${rca.log_service || "-"}</p>

        <h4>Root Cause</h4>
        <p>${rca.root_cause || "-"}</p>

        <h4>Immediate Fix</h4>
        <p>${rca.immediate_fix || "-"}</p>

        <details>
          <summary>AI Critic</summary>
          <p>${reasoning}</p>
        </details>

        <details>
          <summary>Raw Log</summary>
          <pre>${logDisplay}</pre>
        </details>

        <hr/>

        <button class="btn approve" onclick="approve()">Approve</button>
        <button class="btn reject" onclick="toggleReject()">Reject</button>

        <div id="rejectBox" style="display:none;">
          <h4>Rejection Feedback</h4>

          <textarea id="reason" placeholder="Reason"></textarea>
          <input id="correctedRootCause" placeholder="Correct root cause"/>

          <select id="feedbackType">
            <option value="wrong_rc">Wrong root cause</option>
            <option value="wrong_fix">Wrong fix</option>
            <option value="hallucination">Hallucination</option>
            <option value="incomplete">Incomplete</option>
          </select>

          <button class="btn reject" onclick="submitReject()">Submit</button>
        </div>

      </div>
    `;

  } catch (e) {
    showError("RCA failed: " + e.message);
  }
}

// ─── TOGGLE REJECT FORM ───
function toggleReject() {
  const box = document.getElementById("rejectBox");
  box.style.display = box.style.display === "none" ? "block" : "none";
}

// ─── ACTIONS ───
async function approve() {
  await fetch(API + "/approve", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ incident_id: currentId, rater_id: rater })
  });
  location.reload();
}

async function submitReject() {
  const reason = document.getElementById("reason").value;
  if (!reason.trim()) return alert("Reason required");

  await fetch(API + "/reject", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({
      incident_id: currentId,
      human_feedback: {
        reason,
        corrected_root_cause: document.getElementById("correctedRootCause").value,
        feedback_type: document.getElementById("feedbackType").value
      }
    })
  });

  location.reload();
}

loadQueue();
</script>

</body>
</html>"""