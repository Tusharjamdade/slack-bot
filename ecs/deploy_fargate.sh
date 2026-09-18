#!/usr/bin/env bash
# ==============================================================================
# AWS ECS Fargate Deployment Script for Slack AI Agent
# ==============================================================================
set -euo pipefail

# Configuration - update these values or pass via environment variables
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
ECR_REPO_NAME="${ECR_REPO_NAME:-slack-ai-agent}"
ECS_CLUSTER_NAME="${ECS_CLUSTER_NAME:-slack-agent-cluster}"
ECS_SERVICE_NAME="${ECS_SERVICE_NAME:-slack-ai-agent-service}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}"

echo "================================================================="
echo " Deploying Slack AI Agent to AWS ECS Fargate"
echo " AWS Region:       $AWS_REGION"
echo " AWS Account ID:   $AWS_ACCOUNT_ID"
echo " ECR Repository:   $ECR_REPO_NAME"
echo " Image Target:     $ECR_URI:$IMAGE_TAG"
echo " ECS Cluster:      $ECS_CLUSTER_NAME"
echo " ECS Service:      $ECS_SERVICE_NAME"
echo "================================================================="

# 1. Ensure ECR repository exists
echo "==> Ensuring ECR repository exists..."
aws ecr describe-repositories --repository-names "$ECR_REPO_NAME" --region "$AWS_REGION" >/dev/null 2>&1 || \
aws ecr create-repository \
    --repository-name "$ECR_REPO_NAME" \
    --image-scanning-configuration scanOnPush=true \
    --region "$AWS_REGION"

# 2. Authenticate Docker with ECR
echo "==> Authenticating Docker to Amazon ECR..."
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# 3. Build Docker Image (platform linux/amd64 for ECS Fargate)
echo "==> Building Docker image for linux/amd64..."
docker build --platform linux/amd64 -t "$ECR_REPO_NAME:$IMAGE_TAG" -f Dockerfile .

# 4. Tag and Push Image to ECR
echo "==> Tagging and pushing Docker image to ECR..."
docker tag "$ECR_REPO_NAME:$IMAGE_TAG" "$ECR_URI:$IMAGE_TAG"
docker push "$ECR_URI:$IMAGE_TAG"

# 5. Register new Task Definition revision with substituted values
echo "==> Registering updated ECS Task Definition..."
RENDERED_TASK_DEF=$(sed \
    -e "s/<AWS_ACCOUNT_ID>/$AWS_ACCOUNT_ID/g" \
    -e "s/<AWS_REGION>/$AWS_REGION/g" \
    ecs/task-definition.json)

TASK_DEF_ARN=$(echo "$RENDERED_TASK_DEF" | aws ecs register-task-definition \
    --cli-input-json "$RENDERED_TASK_DEF" \
    --region "$AWS_REGION" \
    --query "taskDefinition.taskDefinitionArn" \
    --output text)

echo "==> Registered Task Definition: $TASK_DEF_ARN"

# 6. Update ECS Fargate Service
echo "==> Updating ECS Fargate Service to deploy new revision..."
aws ecs update-service \
    --cluster "$ECS_CLUSTER_NAME" \
    --service "$ECS_SERVICE_NAME" \
    --task-definition "$TASK_DEF_ARN" \
    --force-new-deployment \
    --region "$AWS_REGION"

echo "==> Waiting for ECS Service deployment to stabilize..."
aws ecs wait services-stable \
    --cluster "$ECS_CLUSTER_NAME" \
    --services "$ECS_SERVICE_NAME" \
    --region "$AWS_REGION"

echo "================================================================="
echo " Deployment to AWS ECS Fargate completed successfully!"
echo " Service is live and running."
echo "================================================================="
