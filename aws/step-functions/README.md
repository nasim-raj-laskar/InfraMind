## Step Functions Workflow (HITL)

This state machine handles the human-in-the-loop (HITL) review process.

![](/assets/SF.png)

Flow:

1. StoreForReview
   - Sends RCA output to Lambda
   - Waits for human decision using task token

2. OnApprove
   - Triggered when RCA is accepted

3. OnReject
   - Triggered when RCA is rejected
   - Feedback is stored for correction loop

4. EscalateTimeout
   - Automatically rejects if no response within 72 hours

Key Feature:
- Uses `waitForTaskToken` for asynchronous human approval