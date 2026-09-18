# ==============================================================================
# AWS ECS Fargate Deployment PowerShell Script for Slack AI Agent
# ==============================================================================
param (
    [string]$AwsRegion = "us-east-1",
    [string]$EcrRepoName = "slack-ai-agent",
    [string]$EcsClusterName = "slack-agent-cluster",
    [string]$EcsServiceName = "slack-ai-agent-service",
    [string]$ImageTag = "latest"
)

$ErrorActionPreference = "Stop"

Write-Host "==> Fetching AWS Account ID..." -ForegroundColor Cyan
$AwsAccountId = (aws sts get-caller-identity --query Account --output text).Trim()

$EcrUri = "${AwsAccountId}.dkr.ecr.${AwsRegion}.amazonaws.com/${EcrRepoName}"

Write-Host "=================================================================" -ForegroundColor Green
Write-Host " Deploying Slack AI Agent to AWS ECS Fargate" -ForegroundColor Green
Write-Host " AWS Region:       $AwsRegion"
Write-Host " AWS Account ID:   $AwsAccountId"
Write-Host " ECR Repository:   $EcrRepoName"
Write-Host " Target Image:     ${EcrUri}:${ImageTag}"
Write-Host " ECS Cluster:      $EcsClusterName"
Write-Host " ECS Service:      $EcsServiceName"
Write-Host "=================================================================" -ForegroundColor Green

# 1. Ensure ECR Repo exists
Write-Host "==> Checking if ECR repository exists..." -ForegroundColor Cyan
try {
    aws ecr describe-repositories --repository-names $EcrRepoName --region $AwsRegion | Out-Null
} catch {
    Write-Host "==> Creating ECR repository '$EcrRepoName'..." -ForegroundColor Yellow
    aws ecr create-repository --repository-name $EcrRepoName --image-scanning-configuration scanOnPush=true --region $AwsRegion | Out-Null
}

# 2. Authenticate Docker with ECR
Write-Host "==> Authenticating Docker with AWS ECR..." -ForegroundColor Cyan
$loginPassword = aws ecr get-login-password --region $AwsRegion
$loginPassword | docker login --username AWS --password-stdin "${AwsAccountId}.dkr.ecr.${AwsRegion}.amazonaws.com"

# 3. Build Docker Image for linux/amd64 (required for ECS Fargate)
Write-Host "==> Building Docker image for linux/amd64..." -ForegroundColor Cyan
docker build --platform linux/amd64 -t "${EcrRepoName}:${ImageTag}" -f Dockerfile .

# 4. Tag and Push Image to ECR
Write-Host "==> Tagging and pushing image to ECR..." -ForegroundColor Cyan
docker tag "${EcrRepoName}:${ImageTag}" "${EcrUri}:${ImageTag}"
docker push "${EcrUri}:${ImageTag}"

# 5. Render Task Definition template
Write-Host "==> Rendering and registering ECS Task Definition..." -ForegroundColor Cyan
$taskDefContent = Get-Content -Path "ecs/task-definition.json" -Raw
$taskDefContent = $taskDefContent.Replace("<AWS_ACCOUNT_ID>", $AwsAccountId).Replace("<AWS_REGION>", $AwsRegion)

$tempTaskDefFile = [System.IO.Path]::GetTempFileName() + ".json"
Set-Content -Path $tempTaskDefFile -Value $taskDefContent

$taskDefArn = (aws ecs register-task-definition --cli-input-json "file://$tempTaskDefFile" --region $AwsRegion --query "taskDefinition.taskDefinitionArn" --output text).Trim()
Remove-Item -Path $tempTaskDefFile -Force

Write-Host "==> Registered Task Definition ARN: $taskDefArn" -ForegroundColor Green

# 6. Update ECS Fargate Service
Write-Host "==> Updating ECS Fargate Service with new deployment..." -ForegroundColor Cyan
aws ecs update-service `
    --cluster $EcsClusterName `
    --service $EcsServiceName `
    --task-definition $taskDefArn `
    --force-new-deployment `
    --region $AwsRegion | Out-Null

Write-Host "==> Waiting for ECS Service to reach steady state..." -ForegroundColor Cyan
aws ecs wait services-stable `
    --cluster $EcsClusterName `
    --services $EcsServiceName `
    --region $AwsRegion

Write-Host "=================================================================" -ForegroundColor Green
Write-Host " Deployment to AWS ECS Fargate completed successfully!" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green
