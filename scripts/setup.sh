#!/bin/bash
# ==================================================
# 初期セットアップスクリプト
# ==================================================

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd -W)"
echo "=========================================="
echo "AI × 占い自動運用システム セットアップ"
echo "=========================================="

# --- Python チェック ---
echo ""
echo "[1/4] Python環境チェック..."
PYTHON="python"
command -v python3 &> /dev/null && PYTHON="python3"

if ! command -v $PYTHON &> /dev/null; then
    echo "[ERROR] Pythonがインストールされていません"
    echo "  Windows: winget install Python.Python.3.12"
    echo "  Mac: brew install python3"
    exit 1
fi

echo "  Python: $($PYTHON --version)"

# --- pip パッケージ（依存なし、標準ライブラリのみ使用）---
echo ""
echo "[2/4] 依存パッケージチェック..."
echo "  このシステムはPython標準ライブラリのみ使用するため、追加パッケージは不要です。"

# --- API Keys チェック ---
echo ""
echo "[3/4] APIキー設定チェック..."
if [ -f "$PROJECT_DIR/config/api-keys.env" ]; then
    echo "  config/api-keys.env: 存在します"
    # キーが設定されているかチェック
    if grep -q "xxxxx" "$PROJECT_DIR/config/api-keys.env"; then
        echo "  [WARNING] api-keys.env にデフォルト値が残っています。実際のAPIキーに置き換えてください。"
    fi
else
    echo "  [WARNING] config/api-keys.env が見つかりません"
    echo "  以下のコマンドでテンプレートをコピーしてください:"
    echo "    cp config/api-keys.env.example config/api-keys.env"
    echo "  その後、実際のAPIキーを設定してください。"
fi

# --- ディレクトリ構造チェック ---
echo ""
echo "[4/4] ディレクトリ構造チェック..."
DIRS=("agents" "knowledge/uranai" "state" "prompts" "config" "scripts" "docs")
ALL_OK=true
for dir in "${DIRS[@]}"; do
    if [ -d "$PROJECT_DIR/$dir" ]; then
        echo "  $dir/: OK"
    else
        echo "  $dir/: MISSING"
        ALL_OK=false
    fi
done

# --- 結果サマリー ---
echo ""
echo "=========================================="
if [ "$ALL_OK" = true ]; then
    echo "セットアップ状態: OK"
else
    echo "セットアップ状態: 一部不完全"
fi
echo ""
echo "次のステップ:"
echo "  1. config/api-keys.env にAPIキーを設定"
echo "     - ANTHROPIC_API_KEY (Claude API)"
echo "     - THREADS_ACCESS_TOKEN, THREADS_USER_ID (Threads API)"
echo "     - X_API_KEY 等 (X API)"
echo "  2. テスト実行:"
echo "     bash scripts/daily-run.sh"
echo "  3. cron設定（自動実行）:"
echo "     crontab -e で以下を追加:"
echo "     0 6 * * * bash $PROJECT_DIR/scripts/daily-run.sh"
echo "     0 8,10,12,14,16,18,20,22,0 * * * bash $PROJECT_DIR/scripts/post-scheduler.sh"
echo "=========================================="
