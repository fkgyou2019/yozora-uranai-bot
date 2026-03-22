#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ライターのClaude API応答を解析し、品質チェック後にキューに追加
"""

import re
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

PROJECT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_json(path):
    full = os.path.join(PROJECT_DIR, path)
    if os.path.exists(full):
        with open(full, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def extract_json(text):
    """テキストからJSONを抽出"""
    try:
        return json.loads(text)
    except:
        pass
    match = re.search(r'```json?\s*([\s\S]*?)```', text)
    if match:
        try:
            return json.loads(match.group(1))
        except:
            pass
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group(0))
        except:
            pass
    return None


def main():
    result_file = os.path.join(PROJECT_DIR, "state", "_tmp_claude_result.txt")
    if not os.path.exists(result_file):
        print("Error: Claude result file not found", file=sys.stderr)
        sys.exit(1)

    with open(result_file, 'r', encoding='utf-8') as f:
        text = f.read()

    generated = extract_json(text)
    if generated is None:
        print("Error: Could not extract JSON from writer result", file=sys.stderr)
        print(f"Response: {text[:500]}", file=sys.stderr)
        sys.exit(1)

    # postsキーがあればそこから取得、なければ直接配列として扱う
    posts = generated.get('posts', []) if isinstance(generated, dict) else generated

    # キュー読み込み
    queue = load_json("state/post-queue.json")
    if 'queue' not in queue:
        queue['queue'] = []

    # 品質チェック: 平均スコア7.0未満を除外
    approved = []
    discarded = []
    for post in posts:
        score = post.get('quality_score', {})
        avg = score.get('average', 0) if isinstance(score, dict) else 0
        if avg >= 7.0:
            post['status'] = 'queued'
            approved.append(post)
        else:
            post['status'] = 'discarded'
            discarded.append(post)

    queue['queue'].extend(approved)

    # キュー保存
    queue_path = os.path.join(PROJECT_DIR, "state", "post-queue.json")
    with open(queue_path, 'w', encoding='utf-8') as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)

    print(f"承認: {len(approved)}本, 棄却: {len(discarded)}本")


if __name__ == '__main__':
    main()
