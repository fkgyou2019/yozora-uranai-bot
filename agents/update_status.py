#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
エージェントステータス更新
使い方: python update_status.py <project_dir> <agent_name> <status>
"""

import json
import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

def main():
    if len(sys.argv) < 4:
        print("Usage: update_status.py <project_dir> <agent_name> <status>", file=sys.stderr)
        sys.exit(1)

    project_dir = sys.argv[1]
    agent_name = sys.argv[2]
    status = sys.argv[3]

    status_file = os.path.join(project_dir, "state", "system-status.json")

    with open(status_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if agent_name not in data.get('agents', {}):
        data.setdefault('agents', {})[agent_name] = {"status": "idle", "last_run": None, "error_count": 0}

    data['agents'][agent_name]['status'] = status
    data['agents'][agent_name]['last_run'] = datetime.now().isoformat()

    if status == 'error':
        data['agents'][agent_name]['error_count'] = data['agents'][agent_name].get('error_count', 0) + 1
        data['consecutive_errors'] = data.get('consecutive_errors', 0) + 1
    else:
        data['consecutive_errors'] = 0

    with open(status_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
