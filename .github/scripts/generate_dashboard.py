#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
成長トラッカー ダッシュボード生成
state/post-growth-tracker.json を読み込んで
docs/index.html を生成する（GitHub Pages で公開）。
"""

import json
import os
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TRACKER_FILE = os.path.join(PROJECT_DIR, "state", "post-growth-tracker.json")
OUTPUT_FILE = os.path.join(PROJECT_DIR, "docs", "index.html")


def load_tracker():
    if not os.path.exists(TRACKER_FILE):
        return {"snapshots": {}, "last_updated": None}
    with open(TRACKER_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def build_chart_data(tracker):
    """Chart.js 用データを組み立てる"""
    snapshots = tracker.get("snapshots", {})
    now = datetime.now(JST)

    # カラーパレット（最大20本）
    colors = [
        "#FF6384", "#36A2EB", "#FFCE56", "#4BC0C0", "#9966FF",
        "#FF9F40", "#FF6384", "#C9CBCF", "#7BC8A4", "#E7869B",
        "#5B8FF9", "#5AD8A6", "#5D7092", "#F6BD16", "#E86452",
        "#6DC8EC", "#945FB9", "#FF99C3", "#1E9493", "#FAAD14",
    ]

    # --- 成長曲線データ ---
    growth_datasets = []
    table_rows = []
    pattern_vel = {}   # {pattern_name: [peak_velocity, ...]}
    slot_vel = {}      # {slot_hour: [peak_velocity, ...]}
    slot_views = {}    # {slot_hour: [views, ...]}

    for i, (pid, entry) in enumerate(snapshots.items()):
        hourly = entry.get("hourly", [])
        if not hourly:
            continue

        pname = entry.get("pattern_name", "不明") or "不明"
        slot_h = entry.get("slot_hour", 0)
        day = entry.get("day_name", "?")
        fl = entry.get("first_line", "")[:18]
        peak_v = entry.get("peak_velocity", 0)
        peak_h = entry.get("peak_velocity_hour", "-")
        latest = hourly[-1]
        v_latest = latest.get("views", 0)
        l_latest = latest.get("likes", 0)
        er_latest = latest.get("er", 0)
        elapsed = latest.get("h", 0)
        completed = entry.get("completed", False)

        # 成長曲線
        data_points = [{"x": s["h"], "y": s["views"]} for s in hourly]
        color = colors[i % len(colors)]
        label = f"{fl}（{slot_h}時/{day}）"
        growth_datasets.append({
            "label": label,
            "data": data_points,
            "borderColor": color,
            "backgroundColor": color + "22",
            "fill": False,
            "tension": 0.3,
            "pointRadius": 3,
        })

        # テーブル行
        status = "完了" if completed else f"+{elapsed}h追跡中"
        table_rows.append({
            "fl": fl,
            "pname": pname,
            "slot": f"{slot_h}時/{day}",
            "views": v_latest,
            "likes": l_latest,
            "er": er_latest,
            "peak_v": peak_v,
            "peak_h": peak_h,
            "status": status,
            "completed": completed,
        })

        # パターン別集計
        pattern_vel.setdefault(pname, []).append(peak_v)

        # 時間帯別集計
        slot_vel.setdefault(slot_h, []).append(peak_v)
        slot_views.setdefault(slot_h, []).append(v_latest)

    # テーブルをビュー数降順ソート
    table_rows.sort(key=lambda r: r["views"], reverse=True)

    # --- パターン別棒グラフ ---
    pattern_labels = sorted(pattern_vel.keys(),
                            key=lambda k: sum(pattern_vel[k]) / len(pattern_vel[k]),
                            reverse=True)
    pattern_avg = [round(sum(pattern_vel[k]) / len(pattern_vel[k]), 1)
                   for k in pattern_labels]
    pattern_counts = [len(pattern_vel[k]) for k in pattern_labels]

    # --- 時間帯別棒グラフ ---
    slot_labels_sorted = sorted(slot_vel.keys())
    slot_avg_vel = [round(sum(slot_vel[h]) / len(slot_vel[h]), 1)
                    for h in slot_labels_sorted]
    slot_avg_views = [round(sum(slot_views[h]) / len(slot_views[h]))
                      for h in slot_labels_sorted]

    return {
        "growth_datasets": growth_datasets,
        "table_rows": table_rows,
        "pattern_labels": pattern_labels,
        "pattern_avg": pattern_avg,
        "pattern_counts": pattern_counts,
        "slot_labels": [f"{h}時" for h in slot_labels_sorted],
        "slot_avg_vel": slot_avg_vel,
        "slot_avg_views": slot_avg_views,
        "last_updated": tracker.get("last_updated", ""),
        "total_tracking": sum(1 for e in snapshots.values() if not e.get("completed")),
        "total_completed": sum(1 for e in snapshots.values() if e.get("completed")),
    }


def render_table(rows):
    if not rows:
        return "<p style='color:#888'>データなし</p>"
    html = """
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>フック（1行目）</th>
          <th>パターン</th>
          <th>投稿時刻</th>
          <th>閲覧数</th>
          <th>いいね</th>
          <th>ER%</th>
          <th>ピーク速度</th>
          <th>ピーク時間</th>
          <th>ステータス</th>
        </tr>
      </thead>
      <tbody>
    """
    for i, r in enumerate(rows, 1):
        cls = "completed" if r["completed"] else "tracking"
        html += f"""
        <tr class="{cls}">
          <td>{i}</td>
          <td class="hook">{r['fl']}</td>
          <td>{r['pname']}</td>
          <td>{r['slot']}</td>
          <td class="num">{r['views']:,}</td>
          <td class="num">{r['likes']}</td>
          <td class="num">{r['er']:.1f}%</td>
          <td class="num vel">{r['peak_v']:.1f}/h</td>
          <td class="num">+{r['peak_h']}h</td>
          <td class="status-{cls}">{r['status']}</td>
        </tr>"""
    html += "\n  </tbody>\n</table>"
    return html


def generate_html(data, now_str):
    growth_json = json.dumps(data["growth_datasets"], ensure_ascii=False)
    pattern_labels_json = json.dumps(data["pattern_labels"], ensure_ascii=False)
    pattern_avg_json = json.dumps(data["pattern_avg"])
    pattern_counts_json = json.dumps(data["pattern_counts"])
    slot_labels_json = json.dumps(data["slot_labels"], ensure_ascii=False)
    slot_vel_json = json.dumps(data["slot_avg_vel"])
    slot_views_json = json.dumps(data["slot_avg_views"])
    table_html = render_table(data["table_rows"])
    last_updated = data["last_updated"] or "—"
    tracking = data["total_tracking"]
    completed = data["total_completed"]

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>夜空の占い | 成長トラッカー</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg: #0d1117;
      --surface: #161b22;
      --border: #30363d;
      --text: #e6edf3;
      --muted: #8b949e;
      --accent: #58a6ff;
      --green: #3fb950;
      --yellow: #d29922;
      --red: #f85149;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.6;
    }}
    header {{
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 16px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
    }}
    header h1 {{ font-size: 18px; font-weight: 700; }}
    header h1 span {{ color: var(--accent); }}
    .meta {{ color: var(--muted); font-size: 12px; }}
    .badges {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .badge {{
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 600;
    }}
    .badge-green {{ background: #1f3a2a; color: var(--green); border: 1px solid #2ea043; }}
    .badge-blue {{ background: #1a2940; color: var(--accent); border: 1px solid #388bfd; }}
    main {{ padding: 24px; max-width: 1400px; margin: 0 auto; }}
    .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
    @media (max-width: 900px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 20px;
    }}
    .card h2 {{
      font-size: 15px;
      font-weight: 700;
      margin-bottom: 16px;
      color: var(--accent);
      border-bottom: 1px solid var(--border);
      padding-bottom: 10px;
    }}
    .chart-wrap {{ position: relative; height: 320px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th {{
      background: #21262d;
      color: var(--muted);
      font-weight: 600;
      padding: 8px 12px;
      text-align: left;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }}
    td {{
      padding: 8px 12px;
      border-bottom: 1px solid #21262d;
    }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #1c2128; }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .vel {{ color: var(--yellow); font-weight: 700; }}
    .hook {{ color: var(--text); max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .status-tracking {{ color: var(--accent); font-size: 12px; }}
    .status-completed {{ color: var(--muted); font-size: 12px; }}
    .completed td {{ opacity: 0.65; }}
    .refresh-note {{
      text-align: center;
      color: var(--muted);
      font-size: 12px;
      margin-top: 32px;
      padding-bottom: 16px;
    }}
    .empty {{ color: var(--muted); text-align: center; padding: 40px; }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>🌙 夜空の占い | <span>成長トラッカー</span></h1>
      <div class="meta">投稿後72時間の伸びをモニタリング</div>
    </div>
    <div>
      <div class="badges">
        <span class="badge badge-blue">📊 追跡中 {tracking}件</span>
        <span class="badge badge-green">✅ 完了 {completed}件</span>
      </div>
      <div class="meta" style="margin-top:6px;text-align:right">最終更新: {last_updated}</div>
    </div>
  </header>

  <main>

    <!-- 投稿一覧テーブル -->
    <div class="card">
      <h2>📋 投稿別パフォーマンス（閲覧数降順）</h2>
      {table_html}
    </div>

    <!-- 成長曲線 -->
    <div class="card">
      <h2>📈 成長曲線（投稿後 経過時間 × 閲覧数）</h2>
      <div class="chart-wrap">
        <canvas id="growthChart"></canvas>
      </div>
    </div>

    <div class="grid-2">
      <!-- パターン別ピーク速度 -->
      <div class="card">
        <h2>⚡ パターン別 平均ピーク速度（閲覧/h）</h2>
        <div class="chart-wrap" style="height:260px">
          <canvas id="patternChart"></canvas>
        </div>
      </div>

      <!-- 時間帯別パフォーマンス -->
      <div class="card">
        <h2>🕐 投稿時刻別 平均閲覧数</h2>
        <div class="chart-wrap" style="height:260px">
          <canvas id="slotChart"></canvas>
        </div>
      </div>
    </div>

  </main>

  <div class="refresh-note">このページは毎時30分に自動更新されます（GitHub Actions）</div>

  <script>
    // ---- 成長曲線チャート ----
    const growthDatasets = {growth_json};
    if (growthDatasets.length > 0) {{
      new Chart(document.getElementById('growthChart'), {{
        type: 'line',
        data: {{ datasets: growthDatasets }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          parsing: {{ xAxisKey: 'x', yAxisKey: 'y' }},
          interaction: {{ mode: 'index', intersect: false }},
          plugins: {{
            legend: {{
              labels: {{ color: '#8b949e', font: {{ size: 11 }} }},
              position: 'bottom',
            }},
            tooltip: {{
              backgroundColor: '#161b22',
              borderColor: '#30363d',
              borderWidth: 1,
              titleColor: '#e6edf3',
              bodyColor: '#8b949e',
            }}
          }},
          scales: {{
            x: {{
              type: 'linear',
              title: {{ display: true, text: '経過時間（h）', color: '#8b949e' }},
              ticks: {{ color: '#8b949e' }},
              grid: {{ color: '#21262d' }},
            }},
            y: {{
              title: {{ display: true, text: '閲覧数', color: '#8b949e' }},
              ticks: {{ color: '#8b949e' }},
              grid: {{ color: '#21262d' }},
            }}
          }}
        }}
      }});
    }} else {{
      document.getElementById('growthChart').parentElement.innerHTML =
        '<p class="empty">データが蓄積されると成長曲線が表示されます</p>';
    }}

    // ---- パターン別棒グラフ ----
    const patLabels = {pattern_labels_json};
    const patAvg = {pattern_avg_json};
    const patCounts = {pattern_counts_json};
    if (patLabels.length > 0) {{
      new Chart(document.getElementById('patternChart'), {{
        type: 'bar',
        data: {{
          labels: patLabels,
          datasets: [{{
            label: '平均ピーク速度（閲覧/h）',
            data: patAvg,
            backgroundColor: ['#58a6ff','#3fb950','#d29922','#f85149','#a371f7','#79c0ff'],
            borderRadius: 4,
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          indexAxis: 'y',
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{
              backgroundColor: '#161b22',
              borderColor: '#30363d',
              borderWidth: 1,
              callbacks: {{
                label: (ctx) => `${{ctx.raw}}/h（${{patCounts[ctx.dataIndex]}}件）`
              }}
            }}
          }},
          scales: {{
            x: {{ ticks: {{ color: '#8b949e' }}, grid: {{ color: '#21262d' }} }},
            y: {{ ticks: {{ color: '#e6edf3' }}, grid: {{ color: '#21262d' }} }},
          }}
        }}
      }});
    }} else {{
      document.getElementById('patternChart').parentElement.innerHTML =
        '<p class="empty">データが蓄積されると表示されます</p>';
    }}

    // ---- 時間帯別棒グラフ ----
    const slotLabels = {slot_labels_json};
    const slotViews = {slot_views_json};
    if (slotLabels.length > 0) {{
      new Chart(document.getElementById('slotChart'), {{
        type: 'bar',
        data: {{
          labels: slotLabels,
          datasets: [{{
            label: '平均閲覧数',
            data: slotViews,
            backgroundColor: '#388bfd88',
            borderColor: '#58a6ff',
            borderWidth: 1,
            borderRadius: 4,
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{
              backgroundColor: '#161b22',
              borderColor: '#30363d',
              borderWidth: 1,
            }}
          }},
          scales: {{
            x: {{ ticks: {{ color: '#8b949e' }}, grid: {{ color: '#21262d' }} }},
            y: {{ ticks: {{ color: '#8b949e' }}, grid: {{ color: '#21262d' }} }},
          }}
        }}
      }});
    }} else {{
      document.getElementById('slotChart').parentElement.innerHTML =
        '<p class="empty">データが蓄積されると表示されます</p>';
    }}
  </script>
</body>
</html>"""


def main():
    now = datetime.now(JST)
    now_str = now.strftime("%Y-%m-%d %H:%M JST")

    tracker = load_tracker()
    data = build_chart_data(tracker)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    html = generate_html(data, now_str)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"ダッシュボード生成完了: {OUTPUT_FILE}")
    print(f"  追跡中: {data['total_tracking']}件 / 完了: {data['total_completed']}件")


if __name__ == "__main__":
    main()
