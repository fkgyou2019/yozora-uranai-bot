#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
成長トラッカー ダッシュボード生成
tracker.json + growth-archive.json を読み込んで
docs/index.html を生成する（GitHub Pages で公開）。
"""

import json
import os
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TRACKER_FILE  = os.path.join(PROJECT_DIR, "state", "post-growth-tracker.json")
ARCHIVE_FILE  = os.path.join(PROJECT_DIR, "state", "growth-archive.json")
OUTPUT_FILE   = os.path.join(PROJECT_DIR, "docs", "index.html")


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_all_entries():
    """tracker + archive を統合して返す"""
    tracker = load_json(TRACKER_FILE)
    archive = load_json(ARCHIVE_FILE)
    merged = {}
    merged.update(archive)                        # 古い順に先に入れる
    merged.update(tracker.get("snapshots", {}))   # tracker が最新
    last_updated = tracker.get("last_updated", "")
    return merged, last_updated


def build_data(entries):
    colors = [
        "#FF6384","#36A2EB","#FFCE56","#4BC0C0","#9966FF",
        "#FF9F40","#C9CBCF","#7BC8A4","#5B8FF9","#5AD8A6",
        "#5D7092","#F6BD16","#E86452","#6DC8EC","#945FB9",
        "#FF99C3","#1E9493","#FAAD14","#E7869B","#3fb950",
    ]

    growth_datasets   = []   # 成長曲線（追跡中のみ）
    all_rows          = []   # 全テーブル行（追跡中＋過去）
    pattern_vel       = {}
    slot_vel          = {}
    slot_views_map    = {}

    for i, (pid, entry) in enumerate(entries.items()):
        hourly = entry.get("hourly", [])
        if not hourly:
            continue

        pname     = entry.get("pattern_name", "不明") or "不明"
        slot_h    = entry.get("slot_hour", 0)
        day       = entry.get("day_name", "?")
        fl        = entry.get("first_line", "")[:20]
        url       = entry.get("threads_url", "")
        peak_v    = entry.get("peak_velocity", 0)
        peak_h    = entry.get("peak_velocity_hour", "-")
        completed = entry.get("completed", False)
        posted_at = entry.get("posted_at", "")
        latest    = hourly[-1]
        v_latest  = latest.get("views", 0)
        l_latest  = latest.get("likes", 0)
        er_latest = latest.get("er", 0)
        elapsed   = latest.get("h", 0)

        # 追跡中の投稿のみ成長曲線に追加
        if not completed:
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

        # テーブル行（追跡中・完了どちらも）
        posted_dt_str = ""
        try:
            dt = datetime.fromisoformat(posted_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            posted_dt_str = dt.astimezone(JST).strftime("%m/%d %H:%M")
        except Exception:
            posted_dt_str = posted_at[:10]

        status_label = "✅完了" if completed else f"📊+{elapsed}h"
        all_rows.append({
            "fl": fl, "url": url, "pname": pname,
            "slot": f"{slot_h}時/{day}", "posted_at": posted_dt_str,
            "views": v_latest, "likes": l_latest, "er": er_latest,
            "peak_v": peak_v, "peak_h": peak_h,
            "status": status_label, "completed": completed,
            "posted_at_raw": posted_at,
        })

        pattern_vel.setdefault(pname, []).append(peak_v)
        slot_vel.setdefault(slot_h, []).append(peak_v)
        slot_views_map.setdefault(slot_h, []).append(v_latest)

    # ビュー数降順
    all_rows.sort(key=lambda r: r["views"], reverse=True)

    # パターン別
    pat_labels = sorted(pattern_vel, key=lambda k: sum(pattern_vel[k])/len(pattern_vel[k]), reverse=True)
    pat_avg    = [round(sum(pattern_vel[k])/len(pattern_vel[k]),1) for k in pat_labels]
    pat_counts = [len(pattern_vel[k]) for k in pat_labels]

    # 時間帯別
    slot_sorted   = sorted(slot_vel)
    slot_avg_vel  = [round(sum(slot_vel[h])/len(slot_vel[h]),1)      for h in slot_sorted]
    slot_avg_view = [round(sum(slot_views_map[h])/len(slot_views_map[h])) for h in slot_sorted]

    tracking  = sum(1 for e in entries.values() if not e.get("completed"))
    completed = sum(1 for e in entries.values() if e.get("completed"))

    return {
        "growth_datasets": growth_datasets,
        "all_rows": all_rows,
        "pat_labels": pat_labels, "pat_avg": pat_avg, "pat_counts": pat_counts,
        "slot_labels": [f"{h}時" for h in slot_sorted],
        "slot_avg_vel": slot_avg_vel, "slot_avg_view": slot_avg_view,
        "tracking": tracking, "completed_count": completed,
    }


def render_table(rows):
    if not rows:
        return "<p class='empty'>データが蓄積されると表示されます</p>"
    html = """
<table id="mainTable">
  <thead>
    <tr>
      <th>#</th><th>投稿日時</th><th>フック（1行目）</th>
      <th>パターン</th><th>時/曜</th>
      <th>閲覧</th><th>いいね</th><th>ER%</th>
      <th>ピーク速度</th><th>ピーク時間</th><th>ステータス</th>
    </tr>
  </thead>
  <tbody>
"""
    for i, r in enumerate(rows, 1):
        cls = "completed" if r["completed"] else "tracking"
        if r["url"]:
            hook_cell = f'<a href="{r["url"]}" target="_blank" class="post-link">{r["fl"]} 🔗</a>'
        else:
            hook_cell = r["fl"]
        html += f"""
    <tr class="{cls}">
      <td>{i}</td>
      <td class="date">{r['posted_at']}</td>
      <td class="hook">{hook_cell}</td>
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


def generate_html(data, last_updated, now_str):
    growth_json   = json.dumps(data["growth_datasets"], ensure_ascii=False)
    pat_lbl_json  = json.dumps(data["pat_labels"], ensure_ascii=False)
    pat_avg_json  = json.dumps(data["pat_avg"])
    pat_cnt_json  = json.dumps(data["pat_counts"])
    slot_lbl_json = json.dumps(data["slot_labels"], ensure_ascii=False)
    slot_vel_json = json.dumps(data["slot_avg_vel"])
    slot_vw_json  = json.dumps(data["slot_avg_view"])
    table_html    = render_table(data["all_rows"])
    tracking      = data["tracking"]
    completed     = data["completed_count"]

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>夜空の占い｜成長トラッカー</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg:#0d1117; --surface:#161b22; --border:#30363d;
      --text:#e6edf3; --muted:#8b949e; --accent:#58a6ff;
      --green:#3fb950; --yellow:#d29922; --red:#f85149; --purple:#a371f7;
    }}
    *{{box-sizing:border-box;margin:0;padding:0;}}
    body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;line-height:1.6;}}
    header{{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 20px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;position:sticky;top:0;z-index:100;}}
    header h1{{font-size:16px;font-weight:700;}} header h1 span{{color:var(--accent);}}
    .meta{{color:var(--muted);font-size:11px;}}
    .badges{{display:flex;gap:6px;flex-wrap:wrap;}}
    .badge{{padding:3px 9px;border-radius:20px;font-size:11px;font-weight:600;}}
    .badge-blue{{background:#1a2940;color:var(--accent);border:1px solid #388bfd;}}
    .badge-green{{background:#1f3a2a;color:var(--green);border:1px solid #2ea043;}}
    .badge-purple{{background:#2d1f4a;color:var(--purple);border:1px solid #8957e5;}}
    main{{padding:18px;max-width:1500px;margin:0 auto;}}
    .grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:16px;}}
    @media(max-width:900px){{.grid-2{{grid-template-columns:1fr;}}}}
    .card{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:18px;margin-bottom:18px;}}
    .card h2{{font-size:14px;font-weight:700;margin-bottom:14px;color:var(--accent);border-bottom:1px solid var(--border);padding-bottom:8px;}}
    .chart-wrap{{position:relative;height:300px;}}
    /* テーブル */
    .table-wrap{{overflow-x:auto;}}
    table{{width:100%;border-collapse:collapse;font-size:12px;min-width:800px;}}
    th{{background:#21262d;color:var(--muted);font-weight:600;padding:7px 10px;text-align:left;border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none;}}
    th:hover{{color:var(--text);}}
    td{{padding:7px 10px;border-bottom:1px solid #21262d;}}
    tr:last-child td{{border-bottom:none;}}
    tr:hover td{{background:#1c2128;}}
    .num{{text-align:right;font-variant-numeric:tabular-nums;}}
    .vel{{color:var(--yellow);font-weight:700;}}
    .date{{color:var(--muted);white-space:nowrap;font-size:11px;}}
    .hook{{max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
    .post-link{{color:var(--accent);text-decoration:none;}}
    .post-link:hover{{text-decoration:underline;}}
    .status-tracking{{color:var(--accent);font-size:11px;}}
    .status-completed{{color:var(--muted);font-size:11px;}}
    .completed td{{opacity:0.6;}}
    /* フィルタ */
    .filter-bar{{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;align-items:center;}}
    .filter-bar input{{background:#21262d;border:1px solid var(--border);color:var(--text);padding:5px 10px;border-radius:6px;font-size:12px;width:180px;}}
    .filter-bar select{{background:#21262d;border:1px solid var(--border);color:var(--text);padding:5px 10px;border-radius:6px;font-size:12px;}}
    .filter-label{{color:var(--muted);font-size:12px;}}
    /* タブ */
    .tabs{{display:flex;gap:0;margin-bottom:0;border-bottom:1px solid var(--border);}}
    .tab{{padding:8px 16px;font-size:13px;font-weight:600;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px;}}
    .tab.active{{color:var(--accent);border-bottom-color:var(--accent);}}
    .tab-content{{display:none;padding-top:16px;}}
    .tab-content.active{{display:block;}}
    .empty{{color:var(--muted);text-align:center;padding:40px;font-size:13px;}}
    .footer{{text-align:center;color:var(--muted);font-size:11px;margin-top:28px;padding-bottom:12px;}}
  </style>
</head>
<body>
<header>
  <div>
    <h1>🌙 夜空の占い ｜ <span>成長トラッカー</span></h1>
    <div class="meta">投稿後72hの伸びを毎時記録 ／ 過去データも全保存</div>
  </div>
  <div>
    <div class="badges">
      <span class="badge badge-blue">📊 追跡中 {tracking}件</span>
      <span class="badge badge-green">✅ 完了 {completed}件</span>
      <span class="badge badge-purple">🕐 毎時30分更新</span>
    </div>
    <div class="meta" style="margin-top:5px;text-align:right">最終更新: {last_updated}</div>
  </div>
</header>

<main>

<!-- ========== 投稿一覧テーブル ========== -->
<div class="card">
  <h2>📋 投稿別パフォーマンス（全期間 ／ 閲覧数降順）</h2>

  <div class="filter-bar">
    <span class="filter-label">絞り込み:</span>
    <input type="text" id="searchInput" placeholder="フック・パターンで検索..." oninput="filterTable()">
    <select id="statusFilter" onchange="filterTable()">
      <option value="all">すべて</option>
      <option value="tracking">追跡中のみ</option>
      <option value="completed">完了済みのみ</option>
    </select>
    <select id="patternFilter" onchange="filterTable()">
      <option value="all">全パターン</option>
    </select>
    <span class="filter-label" id="rowCount"></span>
  </div>

  <div class="table-wrap">
    {table_html}
  </div>
</div>

<!-- ========== チャートタブ ========== -->
<div class="card">
  <h2>📈 分析チャート</h2>
  <div class="tabs">
    <div class="tab active" onclick="showTab('growth')">成長曲線</div>
    <div class="tab" onclick="showTab('pattern')">パターン別速度</div>
    <div class="tab" onclick="showTab('slot')">時間帯別閲覧</div>
  </div>

  <div id="tab-growth" class="tab-content active">
    <div class="chart-wrap"><canvas id="growthChart"></canvas></div>
    <p style="color:var(--muted);font-size:11px;margin-top:8px">追跡中の投稿のみ表示。X軸=経過時間(h)、Y軸=累計閲覧数</p>
  </div>
  <div id="tab-pattern" class="tab-content">
    <div class="chart-wrap"><canvas id="patternChart"></canvas></div>
  </div>
  <div id="tab-slot" class="tab-content">
    <div class="chart-wrap"><canvas id="slotChart"></canvas></div>
  </div>
</div>

</main>

<div class="footer">毎時30分にGitHub Actionsが自動更新 ／ 過去データはgrowth-archive.jsonとgrowth-all-time.csvに永続保存</div>

<script>
// ---- タブ切り替え ----
function showTab(name) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
}}

// ---- テーブルフィルタ ----
const table = document.getElementById('mainTable');
const patternFilter = document.getElementById('patternFilter');

// パターン一覧を動的生成
if (table) {{
  const patterns = new Set();
  table.querySelectorAll('tbody tr').forEach(tr => {{
    const cells = tr.querySelectorAll('td');
    if (cells[3]) patterns.add(cells[3].textContent.trim());
  }});
  patterns.forEach(p => {{
    const opt = document.createElement('option');
    opt.value = p; opt.textContent = p;
    patternFilter.appendChild(opt);
  }});
}}

function filterTable() {{
  const q       = document.getElementById('searchInput').value.toLowerCase();
  const status  = document.getElementById('statusFilter').value;
  const pattern = document.getElementById('patternFilter').value;
  let visible   = 0;

  if (!table) return;
  table.querySelectorAll('tbody tr').forEach(tr => {{
    const text    = tr.textContent.toLowerCase();
    const isCom   = tr.classList.contains('completed');
    const pname   = tr.querySelectorAll('td')[3]?.textContent.trim() || '';

    const matchQ  = !q || text.includes(q);
    const matchSt = status === 'all' || (status === 'completed' && isCom) || (status === 'tracking' && !isCom);
    const matchPt = pattern === 'all' || pname === pattern;

    if (matchQ && matchSt && matchPt) {{
      tr.style.display = '';
      visible++;
    }} else {{
      tr.style.display = 'none';
    }}
  }});
  document.getElementById('rowCount').textContent = visible + '件表示中';
}}

// 初期カウント表示
window.addEventListener('DOMContentLoaded', filterTable);

// ---- 成長曲線チャート ----
const growthDatasets = {growth_json};
if (growthDatasets.length > 0) {{
  new Chart(document.getElementById('growthChart'), {{
    type: 'line',
    data: {{ datasets: growthDatasets }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      parsing: {{ xAxisKey: 'x', yAxisKey: 'y' }},
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ labels: {{ color: '#8b949e', font: {{ size: 11 }}, boxWidth: 12 }}, position: 'bottom' }},
        tooltip: {{ backgroundColor: '#161b22', borderColor: '#30363d', borderWidth: 1, titleColor: '#e6edf3', bodyColor: '#8b949e' }}
      }},
      scales: {{
        x: {{ type: 'linear', title: {{ display: true, text: '経過時間（h）', color: '#8b949e' }}, ticks: {{ color: '#8b949e' }}, grid: {{ color: '#21262d' }} }},
        y: {{ title: {{ display: true, text: '閲覧数', color: '#8b949e' }}, ticks: {{ color: '#8b949e' }}, grid: {{ color: '#21262d' }} }}
      }}
    }}
  }});
}} else {{
  document.getElementById('growthChart').parentElement.innerHTML = '<p class="empty">追跡中の投稿が蓄積されると成長曲線が表示されます</p>';
}}

// ---- パターン別棒グラフ ----
const patLabels = {pat_lbl_json};
const patAvg    = {pat_avg_json};
const patCounts = {pat_cnt_json};
if (patLabels.length > 0) {{
  new Chart(document.getElementById('patternChart'), {{
    type: 'bar',
    data: {{
      labels: patLabels,
      datasets: [{{ label: '平均ピーク速度（閲覧/h）', data: patAvg,
        backgroundColor: ['#58a6ff','#3fb950','#d29922','#f85149','#a371f7','#79c0ff'],
        borderRadius: 4 }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false, indexAxis: 'y',
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{ backgroundColor: '#161b22', borderColor: '#30363d', borderWidth: 1,
          callbacks: {{ label: (ctx) => `${{ctx.raw}}/h（${{patCounts[ctx.dataIndex]}}件）` }} }}
      }},
      scales: {{
        x: {{ ticks: {{ color: '#8b949e' }}, grid: {{ color: '#21262d' }} }},
        y: {{ ticks: {{ color: '#e6edf3' }}, grid: {{ color: '#21262d' }} }}
      }}
    }}
  }});
}} else {{
  document.getElementById('patternChart').parentElement.innerHTML = '<p class="empty">データが蓄積されると表示されます</p>';
}}

// ---- 時間帯別棒グラフ ----
const slotLabels  = {slot_lbl_json};
const slotViews   = {slot_vw_json};
if (slotLabels.length > 0) {{
  new Chart(document.getElementById('slotChart'), {{
    type: 'bar',
    data: {{
      labels: slotLabels,
      datasets: [{{ label: '平均閲覧数', data: slotViews,
        backgroundColor: '#388bfd88', borderColor: '#58a6ff', borderWidth: 1, borderRadius: 4 }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }}, tooltip: {{ backgroundColor: '#161b22', borderColor: '#30363d', borderWidth: 1 }} }},
      scales: {{
        x: {{ ticks: {{ color: '#8b949e' }}, grid: {{ color: '#21262d' }} }},
        y: {{ ticks: {{ color: '#8b949e' }}, grid: {{ color: '#21262d' }} }}
      }}
    }}
  }});
}} else {{
  document.getElementById('slotChart').parentElement.innerHTML = '<p class="empty">データが蓄積されると表示されます</p>';
}}
</script>
</body>
</html>"""


def main():
    now = datetime.now(JST)
    now_str = now.strftime("%Y-%m-%d %H:%M JST")

    entries, last_updated = load_all_entries()
    data = build_data(entries)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    html = generate_html(data, last_updated or now_str, now_str)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"ダッシュボード生成完了: {OUTPUT_FILE}")
    print(f"  追跡中: {data['tracking']}件 / 完了・アーカイブ: {data['completed_count']}件")
    print(f"  全テーブル行: {len(data['all_rows'])}件")


if __name__ == "__main__":
    main()
