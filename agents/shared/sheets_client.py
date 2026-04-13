#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Google Sheets レビューダッシュボード クライアント

役割:
  - 生成した投稿をシートに書き込む（nightly-generateから呼ばれる）
  - 承認状態を読み取る（posterから呼ばれる）
  - 再生成後の投稿文を上書き更新する（regenerate_slotから呼ばれる）

シート構造（1日1タブ、タブ名=YYYY-MM-DD）:
  A: スロット番号  B: 投稿時刻  C: ターゲット/方向性  D: 投稿文
  E: スコア       F: チェック詳細  G: 承認✅  H: 再作成🔄  I: 修正メモ  J: ステータス
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

SPREADSHEET_ID = "1b_n18RRelWvYxy2DHJEpn6v40I735ouro8-NZdeUn2A"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# 列番号（1-indexed, gspread用）
COL = {
    "slot":    1,   # A
    "time":    2,   # B
    "target":  3,   # C
    "content": 4,   # D
    "score":   5,   # E
    "detail":  6,   # F
    "approve": 7,   # G  ← チェックボックス
    "regen":   8,   # H  ← チェックボックス
    "memo":    9,   # I
    "status":  10,  # J
}

HEADERS = [
    "スロット", "投稿時刻", "ターゲット/方向性", "投稿文",
    "スコア", "チェック詳細", "承認✅", "再作成🔄", "修正メモ", "ステータス"
]

# スロット番号 → 設定マッピング（2026-04-14確定: ゴールデンタイム最適化7スロット）
SLOT_MAP = {
    1: {"hour": 6,  "time": "06:07"},   # 朝ゴールデン先頭・めざまし型
    2: {"hour": 7,  "time": "07:07"},   # 朝ゴールデン中心・天体根拠型
    3: {"hour": 8,  "time": "08:07"},   # 朝ゴールデン末尾・しいたけ共感型
    4: {"hour": 9,  "time": "09:37"},   # 第2ゴールデン・仕事アドバイス型
    5: {"hour": 12, "time": "12:07"},   # 昼休み・スピ×ラッキー型
    6: {"hour": 18, "time": "18:07"},   # 夕方ゴールデン・哲学深掘り型
    7: {"hour": 20, "time": "20:07"},   # 夜ゴールデン・夜恋愛型
}


def _is_available():
    """GOOGLE_SHEETS_CREDENTIALS が設定されているか確認"""
    return bool(os.environ.get("GOOGLE_SHEETS_CREDENTIALS"))


def _get_client():
    """Google Sheets API クライアントを取得"""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("[SHEETS] gspread / google-auth 未インストール: pip install gspread google-auth")
        return None

    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if not creds_json:
        print("[SHEETS] 環境変数 GOOGLE_SHEETS_CREDENTIALS が未設定")
        return None

    try:
        creds_dict = json.loads(creds_json)
    except json.JSONDecodeError as e:
        print(f"[SHEETS] 認証情報のJSON解析失敗: {e}")
        return None

    try:
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        import gspread
        return gspread.authorize(creds)
    except Exception as e:
        print(f"[SHEETS] 認証失敗: {e}")
        return None


def _today_str():
    return datetime.now(JST).strftime("%Y-%m-%d")


def _get_or_create_sheet(client, date_str: str):
    """日付タブを取得、なければ作成してヘッダー+チェックボックスを設定"""
    import gspread
    ss = client.open_by_key(SPREADSHEET_ID)
    try:
        return ss.worksheet(date_str)
    except gspread.WorksheetNotFound:
        sheet = ss.add_worksheet(title=date_str, rows=20, cols=10)
        sheet.append_row(HEADERS)
        # ヘッダー行のスタイル設定
        sheet.format("A1:J1", {
            "backgroundColor": {"red": 0.15, "green": 0.15, "blue": 0.15},
            "textFormat": {"bold": True,
                           "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}}
        })
        # G列・H列（データ行）にチェックボックス書式を設定
        _apply_checkbox_format(ss, sheet)
        return sheet


def _apply_checkbox_format(ss, sheet):
    """G列（承認）・H列（再作成）にチェックボックスを設定"""
    sheet_id = sheet.id
    requests = []
    for col_idx in [6, 7]:  # G=6, H=7（0-indexed）
        requests.append({
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,   # 2行目から（ヘッダー除く）
                    "endRowIndex": 15,
                    "startColumnIndex": col_idx,
                    "endColumnIndex": col_idx + 1,
                },
                "rule": {
                    "condition": {"type": "BOOLEAN"},
                    "strict": True,
                }
            }
        })
    ss.batch_update({"requests": requests})


# ============================================================
# 公開API
# ============================================================

def write_posts_to_sheet(posts: list, date_str: str = None) -> bool:
    """
    生成した投稿リストをシートに書き込む。
    既存のデータ行はクリアして上書き。

    posts の形式:
    [
      {
        "slot": 1,
        "time": "08:07",
        "target": "①②③全員",
        "direction": "A",
        "content": "投稿本文",
        "agent_score": 8.2,
        "check_detail": "品質OK",
        "scheduled_hour": 8,    # poster.pyのqueue形式に合わせる
        "time_slot": "朝（8〜9時台）"
      },
      ...
    ]
    """
    if not _is_available():
        print("[SHEETS] 認証情報なし → Sheets書き込みをスキップ")
        return False
    if date_str is None:
        date_str = _today_str()

    client = _get_client()
    if client is None:
        return False

    print(f"[SHEETS] {date_str} に {len(posts)}件書き込み開始")
    sheet = _get_or_create_sheet(client, date_str)

    # 既存データ行をクリア（ヘッダー保持）
    all_rows = sheet.get_all_values()
    if len(all_rows) > 1:
        sheet.delete_rows(2, len(all_rows))

    rows = []
    for p in sorted(posts, key=lambda x: x.get("slot", 0)):
        rows.append([
            p.get("slot", ""),
            p.get("time", ""),
            f"{p.get('target', '')} / 方向{p.get('direction', '')}",
            p.get("content", ""),
            p.get("agent_score", ""),
            p.get("check_detail", "合格"),
            False,              # 承認✅
            False,              # 再作成🔄
            "",                 # 修正メモ
            "pending_review",   # ステータス
        ])

    if rows:
        sheet.append_rows(rows, value_input_option="USER_ENTERED")

    print(f"[SHEETS] ✅ 書き込み完了: {len(rows)}件 → {date_str}")
    return True


def is_slot_approved(slot_num: int, date_str: str = None) -> bool:
    """
    指定スロットが承認済みか確認する。
    GOOGLE_SHEETS_CREDENTIALS 未設定の場合は True を返す（フォールバック: 制限なし）。
    """
    if not _is_available():
        return True  # Sheets未設定 → 従来通り投稿許可

    if date_str is None:
        date_str = _today_str()

    client = _get_client()
    if client is None:
        return True  # 認証失敗時もフォールバック

    try:
        import gspread
        ss = client.open_by_key(SPREADSHEET_ID)
        sheet = ss.worksheet(date_str)
        rows = sheet.get_all_values()

        for row in rows[1:]:  # ヘッダースキップ
            if len(row) < 10:
                continue
            if str(row[COL["slot"] - 1]) == str(slot_num):
                status = row[COL["status"] - 1]
                approve_val = row[COL["approve"] - 1]
                # ステータスが approved OR チェックボックスがTRUE
                return status == "approved" or approve_val in ("TRUE", "true")

        print(f"[SHEETS] スロット{slot_num} が {date_str} シートに見つからない → スキップ")
        return False

    except gspread.WorksheetNotFound:
        print(f"[SHEETS] シート {date_str} が存在しない → フォールバック許可")
        return True
    except Exception as e:
        print(f"[SHEETS] 承認確認エラー（フォールバック許可）: {e}")
        return True


def get_approved_content(slot_num: int, date_str: str = None) -> str | None:
    """
    承認済みスロットの投稿文を取得する。
    再生成で内容が更新されている場合はそちらを返す。
    未承認またはSheets未設定の場合は None。
    """
    if not _is_available():
        return None

    if date_str is None:
        date_str = _today_str()

    client = _get_client()
    if client is None:
        return None

    try:
        import gspread
        ss = client.open_by_key(SPREADSHEET_ID)
        sheet = ss.worksheet(date_str)
        rows = sheet.get_all_values()

        for row in rows[1:]:
            if len(row) < 10:
                continue
            if str(row[COL["slot"] - 1]) == str(slot_num):
                status = row[COL["status"] - 1]
                approve_val = row[COL["approve"] - 1]
                if status == "approved" or approve_val in ("TRUE", "true"):
                    return row[COL["content"] - 1]
        return None

    except Exception:
        return None


def update_post_content(slot_num: int, new_content: str, new_score: float,
                        check_detail: str = "", date_str: str = None) -> bool:
    """
    再生成後の投稿文をシートに上書き更新する。
    - 再作成チェックを外す
    - 承認チェックを外す（再確認を促す）
    - ステータスを pending_review に戻す
    - 行の背景色をリセット
    """
    if not _is_available():
        return False
    if date_str is None:
        date_str = _today_str()

    client = _get_client()
    if client is None:
        return False

    try:
        import gspread
        ss = client.open_by_key(SPREADSHEET_ID)
        sheet = ss.worksheet(date_str)
        rows = sheet.get_all_values()

        for i, row in enumerate(rows[1:], start=2):  # 1-indexed
            if not row:
                continue
            if str(row[COL["slot"] - 1]) == str(slot_num):
                sheet.update_cell(i, COL["content"],  new_content)
                sheet.update_cell(i, COL["score"],    round(new_score, 1))
                sheet.update_cell(i, COL["detail"],   check_detail)
                sheet.update_cell(i, COL["approve"],  False)
                sheet.update_cell(i, COL["regen"],    False)
                sheet.update_cell(i, COL["status"],   "pending_review")
                # 背景色リセット
                sheet.format(f"A{i}:J{i}", {"backgroundColor": {"red": 1, "green": 1, "blue": 1}})
                print(f"[SHEETS] ✅ スロット{slot_num} 投稿文更新・レビュー待ちに戻しました")
                return True

        print(f"[SHEETS] ⚠️ スロット{slot_num} が見つかりません（{date_str}）")
        return False

    except Exception as e:
        print(f"[SHEETS] 更新エラー: {e}")
        return False


def get_current_slot_num() -> int | None:
    """
    現在時刻から実行中のスロット番号（1〜7）を返す。
    一致するスロットがない場合は None。
    スロット: 06:07/07:07/08:07/09:37/12:07/18:07/20:07 JST
    """
    now = datetime.now(JST)
    h, m = now.hour, now.minute
    if h == 6:  return 1
    if h == 7:  return 2
    if h == 8:  return 3
    if h == 9:  return 4   # 09:37
    if h == 10: return 4   # 遅延対応
    if h == 12: return 5
    if h == 13: return 5   # 遅延対応
    if h == 18: return 6
    if h == 19: return 6   # 遅延対応
    if h == 20: return 7
    if h == 21: return 7   # 遅延対応
    return None


if __name__ == "__main__":
    # 動作テスト
    print(f"[TEST] Sheets利用可能: {_is_available()}")
    print(f"[TEST] 現在スロット: {get_current_slot_num()}")
    if _is_available():
        test_posts = [
            {"slot": 1, "time": "08:07", "target": "①②③全員", "direction": "A",
             "content": "テスト投稿1\n#占い", "agent_score": 8.5, "check_detail": "品質OK"},
            {"slot": 2, "time": "10:07", "target": "①②③全員", "direction": "B",
             "content": "テスト投稿2\n#星占い", "agent_score": 7.8, "check_detail": "品質OK"},
        ]
        write_posts_to_sheet(test_posts)
