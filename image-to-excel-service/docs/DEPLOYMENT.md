# Deployment Guide: Image-to-Excel Service

Follow these steps to publish your infrastructure to AWS.

## Step 0: IAM User Permissions (Creating your Deploy User)

Since you are creating a new user to deploy this infrastructure, that user needs permissions to create the resources (CloudFormation, S3, Lambda, IAM Roles).

**Recommended for Dev/Personal Accounts:**
Attach the **`AdministratorAccess`** policy.
*Why?* Deployment tools like SAM create IAM roles dynamically. Restricting permissions often leads to "User is not authorized to perform: iam:CreateRole" errors, which blocks deployment.

**Strict/Granular Alternative:**
If you cannot use Admin access, attach these specific AWS Managed Policies:
1.  `AWSCloudFormationFullAccess`
2.  `AmazonS3FullAccess`
3.  `AWSLambda_FullAccess`
4.  `IAMFullAccess` (Required to create the Lambda Execution Role)
5.  `AmazonBedrockFullAccess`

## Prerequisites

1.  **AWS Account**: You must have an active AWS account.
2.  **AWS CLI**: Installed and configured (`aws configure`).
3.  **AWS SAM CLI**: Installed. [Install Guide](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
4.  **Python 3.12**: Installed.

## Step 1: Enable Bedrock Model Access (CRITICAL)

By default, Bedrock models are disabled. You must enable them manually.

1.  Log in to the **AWS Console**.
2.  Go to the **Amazon Bedrock** service.
3.  On the left sidebar, click **Model access** (at the bottom).
4.  Click the orange **Enable specific models** button (or "Modify model access").
5.  Look for **Meta** (Facebook) and check the box next to **Llama 3.2 11B Vision Instruct**.
    *   *Note: If Llama 3.2 is not listed, check the "US East (N. Virgina)" region.*
6.  Click **Next** / **Submit**. Access is usually granted instantly.

## Step 2: Build the Application

Open your terminal (Command Prompt or PowerShell) and navigate to the `src` folder:

```bash
cd i:/repos/axle-mike/image-to-excel-service/src
```

Run the build command. This packages your source code and installs dependencies (pandas, openpyxl) into a format Lambda can use.

```bash
sam build
```

*If you see "Build Succeeded", proceed to the next step.*

## Step 3: Deploy to AWS

Run the deploy command with the `--guided` flag. This will ask you a series of questions to configure the deployment.

```bash
sam deploy --guided
```

### Configuration Prompts:
- **Stack Name**: `image-to-excel-stack` (or any name you like).
- **AWS Region**: `us-east-1` (Recommended, as this is where Bedrock models are most available).
- **Confirm changes before deploy**: `Y`
- **Allow SAM CLI IAM role creation**: `Y` (This allows SAM to create the permissions for Lambda).
- **Disable rollback**: `N`
- **Save arguments to configuration file**: `Y`
- **SAM configuration file**: `samconfig.toml` (Press Enter).
- **SAM configuration environment**: `default` (Press Enter).

SAM will now create the S3 Buckets, the Lambda function, and connect everything. This takes about 2-3 minutes.

## Step 4: Test It

1.  Go to the **AWS Console** -> **S3**.
2.  Find the bucket named `image-to-excel-input-<your-account-id>`.
3.  Upload an image (e.g., `invoice.jpg`).
4.  Wait ~10-30 seconds.
5.  Go to the bucket named `image-to-excel-output-<your-account-id>`.
6.  You should see `invoice.xlsx`. Download and open it!

## Troubleshooting

- **"AccessDeniedException"**:
    - Did you complete **Step 1**?
    - Check the CloudWatch Logs: Go to Lambda -> Functions -> ImageProcessorFunction -> Monitor -> View CloudWatch Logs.
- **"Module not found: pandas"**:
    - Ensure you ran `sam build` before `sam deploy`. SAM installs the `requirements.txt` during the build phase.
