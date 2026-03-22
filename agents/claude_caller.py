#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude API 呼び出しスクリプト
使い方: python claude_caller.py <system_prompt_file> <user_prompt_file> [max_tokens]
"""

import json
import urllib.request
import urllib.error
import os
import sys

# UTF-8出力を強制
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: claude_caller.py <sys_prompt_file> <usr_prompt_file> [max_tokens]"}))
        sys.exit(1)

    sys_prompt_file = sys.argv[1]
    usr_prompt_file = sys.argv[2]
    max_tokens = int(sys.argv[3]) if len(sys.argv) > 3 else 4096

    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        print(json.dumps({"error": "ANTHROPIC_API_KEY not set"}))
        sys.exit(1)

    # プロンプトファイルを読み込み
    with open(sys_prompt_file, 'r', encoding='utf-8') as f:
        system_prompt = f.read()
    with open(usr_prompt_file, 'r', encoding='utf-8') as f:
        user_prompt = f.read()

    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}]
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=body,
        headers={
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01'
        },
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            text = result['content'][0]['text']
            print(text)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        print(json.dumps({"error": f"HTTP {e.code}", "detail": error_body}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == '__main__':
    main()
