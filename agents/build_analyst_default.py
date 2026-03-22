#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""データ不足時のデフォルトフィードバックを生成"""

import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

PROJECT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

default = {
    "analysis_date": "initial",
    "period": "初期段階（データ不足）",
    "performance_summary": {
        "total_posts": 0,
        "note": "投稿データが5件未満のため、デフォルト設定を使用"
    },
    "insights": [
        {
            "finding": "初期段階のためデータ分析なし",
            "evidence": "投稿5件未満",
            "recommendation": "まずは多様なパターンで投稿し、データを蓄積する"
        }
    ],
    "writer_instructions": {
        "推奨パターン": ["コメント誘導型", "短文完結型", "タロットワンオラクル型", "ランキング型"],
        "避けるパターン": [],
        "推奨テーマ": ["恋愛運", "タロット", "星座運勢"],
        "控えるテーマ": [],
        "トーン指示": "persona.jsonに従い、親しみやすく語りかけるトーン",
        "フック指示": "数字インパクト型と呼びかけ型を中心に。20文字以内で好奇心を刺激する",
        "文字数指示": "Threads: 200〜400文字、X: 100〜140文字",
        "その他": "初期は幅広いパターンを試し、反応を見てから絞り込む"
    }
}

output_path = os.path.join(PROJECT_DIR, "state", "analyst-feedback.json")
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(default, f, ensure_ascii=False, indent=2)

print("OK")
