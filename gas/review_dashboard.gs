// ============================================================
//  よぞら. 投稿レビューダッシュボード - Google Apps Script
//  スプレッドシートID: 1b_n18RRelWvYxy2DHJEpn6v40I735ouro8-NZdeUn2A
// ============================================================

const CONFIG = {
  SPREADSHEET_ID: "1b_n18RRelWvYxy2DHJEpn6v40I735ouro8-NZdeUn2A",
  GITHUB_REPO:    "fkgyou2019/yozora-uranai-bot",
  // ScriptProperties に登録するキー名（値はユーザーが設定）
  GITHUB_TOKEN_KEY: "GITHUB_TOKEN",
  NOTIFY_HOUR:    6,        // 朝何時に通知するか（JST）
  TIMEZONE:       "Asia/Tokyo",
};

// 列番号（1-based）
const COL = {
  SLOT:    1,   // A: スロット番号
  TIME:    2,   // B: 投稿時刻
  TARGET:  3,   // C: ターゲット/方向性
  CONTENT: 4,   // D: 投稿文
  SCORE:   5,   // E: スコア
  DETAIL:  6,   // F: チェック詳細
  APPROVE: 7,   // G: 承認✅（チェックボックス）
  REGEN:   8,   // H: 再作成🔄（チェックボックス）
  MEMO:    9,   // I: 修正メモ
  STATUS:  10,  // J: ステータス
};

// ============================================================
// メイン: チェックボックス変更を検知（インストール可能トリガー用）
// ============================================================
function onSheetEdit(e) {
  const sheet = e.source.getActiveSheet();
  const range = e.range;
  const row   = range.getRow();
  const col   = range.getColumn();
  const val   = e.value;

  // ヘッダー行はスキップ
  if (row <= 1) return;
  // 今日のタブ以外はスキップ（古いタブを誤操作防止）
  const today = Utilities.formatDate(new Date(), CONFIG.TIMEZONE, "yyyy-MM-dd");
  if (sheet.getName() !== today) return;

  // ── 承認✅ チェックボックス（G列）
  if (col === COL.APPROVE && val === "TRUE") {
    sheet.getRange(row, COL.STATUS).setValue("approved");
    sheet.getRange(row, COL.REGEN).setValue(false);  // 再作成は外す
    // 行を緑に
    sheet.getRange(row, 1, 1, 10).setBackground("#d9ead3");
    SpreadsheetApp.flush();
    console.log(`✅ スロット${sheet.getRange(row, COL.SLOT).getValue()} 承認`);
  }

  // ── 承認✅ を外した（取り消し）
  if (col === COL.APPROVE && val === "FALSE") {
    const status = sheet.getRange(row, COL.STATUS).getValue();
    if (status === "approved") {
      sheet.getRange(row, COL.STATUS).setValue("pending_review");
      sheet.getRange(row, 1, 1, 10).setBackground(null);
      console.log(`↩️ スロット${sheet.getRange(row, COL.SLOT).getValue()} 承認取り消し`);
    }
  }

  // ── 再作成🔄 チェックボックス（H列）
  if (col === COL.REGEN && val === "TRUE") {
    const slotNum = sheet.getRange(row, COL.SLOT).getValue();
    const memo    = sheet.getRange(row, COL.MEMO).getValue() || "";
    const dateStr = sheet.getName();  // タブ名 = 日付（YYYY-MM-DD）

    // ステータスと表示を更新
    sheet.getRange(row, COL.STATUS).setValue("regenerating");
    sheet.getRange(row, COL.CONTENT).setValue("🔄 再生成中...\n（完了後自動更新されます）");
    sheet.getRange(row, 1, 1, 10).setBackground("#fff2cc");
    SpreadsheetApp.flush();
    console.log(`🔄 スロット${slotNum} 再生成リクエスト（memo: ${memo}）`);

    // GitHub Actions に通知
    triggerRegenerate(slotNum, memo, dateStr);
  }
}

// ============================================================
// GitHub Actions repository_dispatch を発火（再生成リクエスト）
// ============================================================
function triggerRegenerate(slotNum, memo, dateStr) {
  const token = PropertiesService.getScriptProperties()
                  .getProperty(CONFIG.GITHUB_TOKEN_KEY);
  if (!token) {
    SpreadsheetApp.getUi().alert(
      "エラー",
      "スクリプトプロパティに GITHUB_TOKEN が設定されていません。\n" +
      "「ファイル → プロジェクトの設定 → スクリプト プロパティ」から設定してください。",
      SpreadsheetApp.getUi().ButtonSet.OK
    );
    return;
  }

  const url = `https://api.github.com/repos/${CONFIG.GITHUB_REPO}/dispatches`;
  const payload = {
    event_type: "regenerate-slot",
    client_payload: {
      slot: String(slotNum),
      memo: memo,
      date: dateStr
    }
  };

  const options = {
    method:             "post",
    contentType:        "application/json",
    headers: {
      "Authorization":          `Bearer ${token}`,
      "Accept":                 "application/vnd.github+json",
      "X-GitHub-Api-Version":   "2022-11-28",
    },
    payload:            JSON.stringify(payload),
    muteHttpExceptions: true,
  };

  const response = UrlFetchApp.fetch(url, options);
  const code     = response.getResponseCode();

  if (code === 204) {
    console.log(`✅ スロット${slotNum} 再生成 webhook 送信成功`);
  } else {
    console.error(`❌ webhook 失敗: HTTP ${code}\n${response.getContentText()}`);
    SpreadsheetApp.getUi().alert(
      "再生成リクエスト失敗",
      `HTTP ${code}\n${response.getContentText()}`,
      SpreadsheetApp.getUi().ButtonSet.OK
    );
  }
}

// ============================================================
// 朝6時 メール通知（時間ベーストリガーで自動実行）
// ============================================================
function sendMorningNotification() {
  const today = Utilities.formatDate(new Date(), CONFIG.TIMEZONE, "yyyy-MM-dd");
  const ss    = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);
  const sheet = ss.getSheetByName(today);
  if (!sheet) return;  // 今日のシートがなければ何もしない

  const rows  = sheet.getDataRange().getValues();
  const posts = rows.slice(1);  // ヘッダー除く
  if (posts.length === 0) return;

  const sheetUrl = `https://docs.google.com/spreadsheets/d/${CONFIG.SPREADSHEET_ID}/edit#gid=${sheet.getSheetId()}`;
  const email    = Session.getActiveUser().getEmail();

  // 投稿一覧サマリー
  const summary = posts.map(r =>
    `  スロット${r[0]}（${r[1]}）スコア${r[4]}  ${String(r[3]).substring(0, 30)}...`
  ).join("\n");

  const subject = `【よぞら.】${today} 投稿候補${posts.length}件 ✅レビュー待ち`;
  const body = [
    `${today} の投稿候補 ${posts.length}件 が準備できました。`,
    ``,
    `⏰ 07:50 までに承認してください。`,
    `   未承認のスロットは当日スキップされます。`,
    ``,
    `▼ レビュー用シート`,
    sheetUrl,
    ``,
    `【本日の投稿候補】`,
    summary,
    ``,
    `承認 → G列✅チェック`,
    `再作成 → H列🔄チェック（I列に修正メモも書けます）`,
  ].join("\n");

  GmailApp.sendEmail(email, subject, body);
  console.log(`📧 朝の通知メール送信完了: ${email}`);
}

// ============================================================
// 初期セットアップ（一度だけ手動で実行してください）
// ============================================================
function setupTriggers() {
  // 既存のトリガーを削除（重複防止）
  ScriptApp.getProjectTriggers().forEach(t => {
    const fn = t.getHandlerFunction();
    if (fn === "onSheetEdit" || fn === "sendMorningNotification") {
      ScriptApp.deleteTrigger(t);
    }
  });

  // 1. シート編集トリガー（インストール可能）
  ScriptApp.newTrigger("onSheetEdit")
    .forSpreadsheet(CONFIG.SPREADSHEET_ID)
    .onEdit()
    .create();

  // 2. 毎日 6:00 通知トリガー
  ScriptApp.newTrigger("sendMorningNotification")
    .timeBased()
    .atHour(CONFIG.NOTIFY_HOUR)
    .everyDays(1)
    .inTimezone(CONFIG.TIMEZONE)
    .create();

  const ui = SpreadsheetApp.getUi();
  ui.alert(
    "セットアップ完了",
    "✅ トリガーを設定しました。\n\n" +
    "1. 編集トリガー（承認/再作成チェックボックス対応）\n" +
    "2. 毎日 6:00 通知トリガー\n\n" +
    "次に「スクリプトプロパティ」に GITHUB_TOKEN を設定してください。",
    ui.ButtonSet.OK
  );
}

// ============================================================
// スクリプトプロパティ設定確認（デバッグ用）
// ============================================================
function checkSettings() {
  const token = PropertiesService.getScriptProperties()
                  .getProperty(CONFIG.GITHUB_TOKEN_KEY);
  const triggers = ScriptApp.getProjectTriggers().map(t => t.getHandlerFunction());

  const ui = SpreadsheetApp.getUi();
  ui.alert(
    "設定確認",
    `GITHUB_TOKEN: ${token ? "✅ 設定済み" : "❌ 未設定"}\n` +
    `トリガー: ${triggers.join(", ") || "なし"}`,
    ui.ButtonSet.OK
  );
}
