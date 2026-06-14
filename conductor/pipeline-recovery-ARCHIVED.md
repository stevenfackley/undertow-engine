# Pipeline Recovery Plan

## Objective
Restore the CI/CD pipeline and production deployment for the `undertow-engine` project.

## Root Cause Analysis
1.  **CI Failure (`test.yml`):** `main.py` was missing critical imports for `slowapi` (`Limiter`, `_rate_limit_exceeded_handler`, etc.), leading to a `NameError` and 500 Internal Server Errors on the `/api/v1/generate` endpoint.
2.  **Deployment Failure (`deploy-prod.yml`):** The SSM deploy step failed with `InvalidInstanceId`. This indicates the instance ID stored in the GitHub secret `PROD_EC2_INSTANCE_ID` is either incorrect, the instance is powered off, or it's not managed by SSM (e.g., missing IAM role/agent).

## Proposed Changes

### 1. Code Fixes
-   **`main.py`:** Restore missing imports for `slowapi` and `limits`. (Already applied to local filesystem).

### 2. Verification
-   **Linting:** Run `ruff check .` to ensure no other obvious errors exist.
-   **CI Run:** Commit and push the fix to trigger the GitHub Actions `Test` workflow.

### 3. Deployment Investigation
-   **Instance ID:** Compare the `PROD_EC2_INSTANCE_ID` secret (if accessible or if I can find a reference in logs) with the current state of AWS.
-   **IAM Role:** Ensure the EC2 instance itself has a profile with the `AmazonSSMManagedInstanceCore` policy.

## Implementation Steps

### Phase 1: Restore CI
1.  Verify `main.py` has the correct imports (Done).
2.  Run `ruff check .` locally to verify syntax and imports.
3.  Commit the fix with a descriptive message: `fix: restore missing slowapi imports in main.py`.

### Phase 2: Fix Production Deployment
1.  Check `infra/outputs.tf` to see if the instance ID is exported.
2.  If the instance is managed by Terraform in another repo, I will ask the user to verify the `PROD_EC2_INSTANCE_ID` secret.
3.  Add a diagnostic step to `deploy-prod.yml` to list managed instances if the command fails, to help debug.

## Verification & Testing
1.  **GitHub Actions:** Monitor the `Test` workflow for success.
2.  **Deployment:** Monitor the `Deploy – Production` workflow. If it still fails with `InvalidInstanceId`, I will provide specific CLI commands for the user to run to verify their instance state.
