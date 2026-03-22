#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude APIの応答テキストからJSONを抽出して保存
使い方: python parse_json_result.py <input_file> <output_file>
"""

import re
import json
import sys
import os

sys.stdout.reconfigure(encoding='utf-8')

def extract_json(text):
    """テキストからJSONを抽出"""
    # 1. 直接JSONとして解析
    try:
        return json.loads(text)
    except:
        pass

    # 2. コードブロック内のJSON
    match = re.search(r'```json?\s*([\s\S]*?)```', text)
    if match:
        try:
            return json.loads(match.group(1))
        except:
            pass

    # 3. JSON部分を直接探す（最も外側の {} を探す）
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group(0))
        except:
            pass

    # 4. 配列形式のJSON
    match = re.search(r'\[[\s\S]*\]', text)
    if match:
        try:
            return json.loads(match.group(0))
        except:
            pass

    return None


def main():
    if len(sys.argv) < 3:
        print("Usage: parse_json_result.py <input_file> <output_file>", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    # 入力ファイルを読み込み
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found", file=sys.stderr)
        sys.exit(1)

    with open(input_file, 'r', encoding='utf-8') as f:
        text = f.read()

    data = extract_json(text)
    if data is None:
        print(f"Error: Could not extract JSON from response", file=sys.stderr)
        print(f"Response text: {text[:500]}", file=sys.stderr)
        sys.exit(1)

    # JSON保存
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("OK")


if __name__ == '__main__':
    main()
