#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""アナリストのユーザープロンプトを構築"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding='utf-8')

PROJECT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_json(path):
    full = os.path.join(PROJECT_DIR, path)
    if os.path.exists(full):
        with open(full, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

metrics = load_json("state/metrics.json")
history = load_json("state/post-history.json")
patterns = load_json("knowledge/uranai/post-patterns.json")

recent_posts = history.get("posts", [])[-50:]
today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

prompt = f"""以下のデータを分析し、ライターへのフィードバックをJSON形式で出力してください。

## 分析日
{today}

## メトリクスデータ
{json.dumps(metrics, ensure_ascii=False, indent=2)}

## 投稿履歴（直近50件）
{json.dumps(recent_posts, ensure_ascii=False, indent=2)}

## 投稿パターン一覧
{json.dumps(patterns, ensure_ascii=False, indent=2)}

JSONのみ出力してください。説明文は不要です。"""

output_path = os.path.join(PROJECT_DIR, "state", "_tmp_analyst_user_prompt.txt")
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(prompt)

print("OK")
