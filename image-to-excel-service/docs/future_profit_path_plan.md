# FUTURE PHASE: THE PROFIT PATH (Hybrid Architecture)

This document preserves the architectural plan for migrating to the "Profit Path" in the future to maximize margins.

## The Objective
Move away from AWS S3 (storage) and AWS Bedrock (compute) to cheaper alternatives to reduce the cost per image from ~$0.0006 to ~$0.00002.

## The Stack
1.  **Storage**: Cloudflare R2
    - Replaces Amazon S3.
    - **Why**: Zero Egress Fees. AWS charges ~$0.09/GB to send data out to the internet (e.g., to Google's API). Cloudflare R2 charges $0.
    - **Compatibility**: R2 is S3-compatible, meaning the code changes are minimal (just changing the endpoint URL in boto3).

2.  **AI Model**: Google Gemini 2.0 Flash
    - Replaces AWS Bedrock (Llama 3.2).
    - **Why**: "State of the Art" performance on handwriting and tables at a fraction of the cost.
    - **Integration**: Requires an API Key from Google AI Studio.

## Implementation Steps for Migration

### Step 1: External Accounts
1.  Set up a Cloudflare R2 bucket.
2.  Get a Google AI Studio API Key.

### Step 2: Code Updates (Lambda)
1.  **Dependencies**: Update `requirements.txt` to include `google-generativeai`.
2.  **Environment Variables**: Add `GOOGLE_API_KEY` and `R2_ENDPOINT_URL`.
3.  **Download Logic**: Update the image download function to point to R2 instead of AWS S3.
4.  **AI Logic**: Replace the `bedrock_runtime.invoke_model()` call with the Google GenAI client call:
    ```python
    model = genai.GenerativeModel('gemini-2.0-flash-exp')
    response = model.generate_content(["Extract data...", img])
    ```

### Step 3: Deployment
1.  Deploy the updated Lambda.
2.  Update the frontend/upload mechanism to upload files to Cloudflare R2 instead of S3.

## Why we waited
We started with AWS Bedrock to keep the system simple (single provider, no extra accounts, no API keys to manage). This migration introduces complexity (managing credit cards on 3 platforms) in exchange for higher margins.
