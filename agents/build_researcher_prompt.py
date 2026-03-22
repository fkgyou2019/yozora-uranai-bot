#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""リサーチャーのユーザープロンプトを構築"""

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

themes = load_json("knowledge/uranai/themes.json")
history = load_json("state/post-history.json")
prev_research = load_json("state/research-results.json")

recent_posts = history.get("posts", [])[-20:]

today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

prompt = f"""以下の情報をもとに、今日のリサーチ結果をJSON形式で出力してください。

## 今日の日付
{today}

## テーマツリー
{json.dumps(themes, ensure_ascii=False, indent=2)}

## 過去の投稿履歴（直近20件）
{json.dumps(recent_posts, ensure_ascii=False, indent=2)}

## 前回のリサーチ結果
{json.dumps(prev_research, ensure_ascii=False, indent=2)}

JSONのみ出力してください。説明文は不要です。"""

output_path = os.path.join(PROJECT_DIR, "state", "_tmp_researcher_user_prompt.txt")
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(prompt)

print("OK")
