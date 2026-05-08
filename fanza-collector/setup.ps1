# =============================================================================
# FANZA Collector Lambda セットアップスクリプト（一回だけ実行）
# 実行前提: AWS CLI 設定済み、Docker Desktop 起動済み
# =============================================================================

$ErrorActionPreference = "Stop"
$REGION     = "ap-northeast-1"
$REPO_NAME  = "fanza-collector"
$FUNC_NAME  = "fanza-collector"
$ROLE_NAME  = "fanza-collector-role"

Write-Host "=== [1/8] AWS アカウント ID 取得 ===" -ForegroundColor Cyan
$ACCOUNT_ID = (aws sts get-caller-identity --query Account --output text)
Write-Host "Account ID: $ACCOUNT_ID"

$ECR_URI = "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}"

# ─────────────────────────────────────────────────────────────────────────────
Write-Host "=== [2/8] ECR リポジトリ作成 ===" -ForegroundColor Cyan
$repoExists = aws ecr describe-repositories --repository-names $REPO_NAME --region $REGION 2>&1
if ($LASTEXITCODE -ne 0) {
    aws ecr create-repository `
        --repository-name $REPO_NAME `
        --region $REGION `
        --image-scanning-configuration scanOnPush=true | Out-Null
    Write-Host "リポジトリ作成: $ECR_URI"
} else {
    Write-Host "既存リポジトリを使用: $ECR_URI"
}

# ─────────────────────────────────────────────────────────────────────────────
Write-Host "=== [3/8] Docker ビルド ===" -ForegroundColor Cyan
Set-Location $PSScriptRoot
docker build -t "${REPO_NAME}:latest" .
if ($LASTEXITCODE -ne 0) { throw "Docker build 失敗" }

# ─────────────────────────────────────────────────────────────────────────────
Write-Host "=== [4/8] ECR へ push ===" -ForegroundColor Cyan
aws ecr get-login-password --region $REGION |
    docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

docker tag "${REPO_NAME}:latest" "${ECR_URI}:latest"
docker push "${ECR_URI}:latest"
if ($LASTEXITCODE -ne 0) { throw "ECR push 失敗" }

# ─────────────────────────────────────────────────────────────────────────────
Write-Host "=== [5/8] IAM 実行ロール作成 ===" -ForegroundColor Cyan
$trustPolicy = @'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "lambda.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
'@

$roleExists = aws iam get-role --role-name $ROLE_NAME 2>&1
if ($LASTEXITCODE -ne 0) {
    aws iam create-role `
        --role-name $ROLE_NAME `
        --assume-role-policy-document $trustPolicy | Out-Null
    Write-Host "ロール作成: $ROLE_NAME"
} else {
    Write-Host "既存ロールを使用: $ROLE_NAME"
}

# 基本 Lambda 実行ポリシー
aws iam attach-role-policy `
    --role-name $ROLE_NAME `
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole | Out-Null

# DynamoDB アクセスポリシー
$dynamoPolicy = @"
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:DescribeTable",
      "dynamodb:CreateTable"
    ],
    "Resource": [
      "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/fanza-affiliate-sales",
      "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/fanza-product-metadata",
      "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/fanza-hourly-clicks",
      "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/x-poster-daily-posts"
    ]
  }]
}
"@

$POLICY_NAME = "fanza-collector-lambda-policy"
$policyExists = aws iam list-policies --query "Policies[?PolicyName=='${POLICY_NAME}'].Arn" --output text
if ([string]::IsNullOrWhiteSpace($policyExists)) {
    $policyArn = (aws iam create-policy `
        --policy-name $POLICY_NAME `
        --policy-document $dynamoPolicy `
        --query Policy.Arn --output text)
    Write-Host "ポリシー作成: $policyArn"
} else {
    $policyArn = $policyExists
    Write-Host "既存ポリシーを使用: $policyArn"
}

aws iam attach-role-policy --role-name $ROLE_NAME --policy-arn $policyArn | Out-Null

# ロールが Lambda に伝播するまで待機
Write-Host "IAM ロール伝播待機 (15秒)..."
Start-Sleep -Seconds 15

# ─────────────────────────────────────────────────────────────────────────────
Write-Host "=== [6/8] Lambda 関数 作成/更新 ===" -ForegroundColor Cyan
$ROLE_ARN = "arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
$IMAGE_URI = "${ECR_URI}:latest"

$funcExists = aws lambda get-function --function-name $FUNC_NAME --region $REGION 2>&1
if ($LASTEXITCODE -ne 0) {
    aws lambda create-function `
        --function-name $FUNC_NAME `
        --region $REGION `
        --package-type Image `
        --code "ImageUri=${IMAGE_URI}" `
        --role $ROLE_ARN `
        --timeout 300 `
        --memory-size 2048 | Out-Null
    Write-Host "Lambda 関数作成: $FUNC_NAME"
} else {
    aws lambda update-function-code `
        --function-name $FUNC_NAME `
        --region $REGION `
        --image-uri $IMAGE_URI | Out-Null
    Write-Host "Lambda コード更新: $FUNC_NAME"
}

# ─────────────────────────────────────────────────────────────────────────────
Write-Host "=== [7/8] 環境変数設定 (FANZA_EMAIL / FANZA_PASSWORD) ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "FANZA_EMAIL と FANZA_PASSWORD を入力してください。" -ForegroundColor Yellow
$fanza_email    = Read-Host "FANZA_EMAIL"
$fanza_password = Read-Host "FANZA_PASSWORD (非表示)" -AsSecureString
$fanza_password_plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($fanza_password)
)

aws lambda update-function-configuration `
    --function-name $FUNC_NAME `
    --region $REGION `
    --environment "Variables={FANZA_EMAIL=${fanza_email},FANZA_PASSWORD=${fanza_password_plain}}" | Out-Null
Write-Host "環境変数を設定しました"

# ─────────────────────────────────────────────────────────────────────────────
Write-Host "=== [8/8] EventBridge スケジュール作成 (毎日 23:55 JST) ===" -ForegroundColor Cyan
$RULE_NAME = "fanza-collector-daily"

aws events put-rule `
    --name $RULE_NAME `
    --schedule-expression "cron(55 14 * * ? *)" `
    --state ENABLED `
    --region $REGION | Out-Null

# Lambda にターゲット追加
$LAMBDA_ARN = "arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${FUNC_NAME}"
aws events put-targets `
    --rule $RULE_NAME `
    --region $REGION `
    --targets "Id=1,Arn=${LAMBDA_ARN}" | Out-Null

# EventBridge が Lambda を呼び出す権限
$permExists = aws lambda get-policy --function-name $FUNC_NAME --region $REGION 2>&1
if ($permExists -notmatch "eventbridge-invoke") {
    aws lambda add-permission `
        --function-name $FUNC_NAME `
        --region $REGION `
        --statement-id "eventbridge-invoke" `
        --action "lambda:InvokeFunction" `
        --principal "events.amazonaws.com" `
        --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${RULE_NAME}" | Out-Null
}

Write-Host ""
Write-Host "=== セットアップ完了 ===" -ForegroundColor Green
Write-Host "Lambda: $FUNC_NAME"
Write-Host "ECR:    $IMAGE_URI"
Write-Host "スケジュール: 毎日 23:55 JST (cron 55 14 * * ? *)"
Write-Host ""
Write-Host "手動テスト実行コマンド:"
Write-Host "  aws lambda invoke --function-name $FUNC_NAME --region $REGION /tmp/out.json; cat /tmp/out.json"
