#!/usr/bin/env python3
"""
FANZA アフィリエイト 収益データ収集スクリプト
毎日 23:55 JST に GitHub Actions から実行

テーブル構成:
  fanza-affiliate-sales    … 日別×商品別集計 (PK=sale_date, SK=product_id)
  fanza-product-metadata   … 作品メタデータキャッシュ (PK=product_id)

データ粒度について:
  FANZA アフィリエイトダッシュボードは個別売上タイムスタンプを公開していない。
  利用可能な最小粒度は「日別×商品別集計」のため、それに合わせた設計とする。
"""

import csv
import io
import os
import re
import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

import boto3
from botocore.exceptions import ClientError
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ─────────────────────────────────────────
JST = timezone(timedelta(hours=9))
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-1")
TABLE_SALES   = "fanza-affiliate-sales"
TABLE_META    = "fanza-product-metadata"
TABLE_CLICKS  = "fanza-hourly-clicks"
TABLE_POSTS   = "x-poster-daily-posts"

DAY_NAMES = ["月", "火", "水", "木", "金", "土", "日"]

# ─────────────────────────────────────────
# DynamoDB ユーティリティ
# ─────────────────────────────────────────

def get_dynamo():
    return boto3.resource("dynamodb", region_name=AWS_REGION)


def ensure_tables(dynamo):
    """テーブルが存在しなければ作成する（ListTables を使わない実装）"""
    tables = [
        (
            TABLE_SALES,
            [
                {"AttributeName": "sale_date",  "KeyType": "HASH"},
                {"AttributeName": "product_id", "KeyType": "RANGE"},
            ],
            [
                {"AttributeName": "sale_date",  "AttributeType": "S"},
                {"AttributeName": "product_id", "AttributeType": "S"},
            ],
        ),
        (
            TABLE_META,
            [{"AttributeName": "product_id", "KeyType": "HASH"}],
            [{"AttributeName": "product_id", "AttributeType": "S"}],
        ),
        (
            TABLE_CLICKS,
            [{"AttributeName": "date", "KeyType": "HASH"}],
            [{"AttributeName": "date", "AttributeType": "S"}],
        ),
    ]
    client = dynamo.meta.client
    for name, key_schema, attr_defs in tables:
        try:
            dynamo.create_table(
                TableName=name,
                KeySchema=key_schema,
                AttributeDefinitions=attr_defs,
                BillingMode="PAY_PER_REQUEST",
            )
            client.get_waiter("table_exists").wait(TableName=name)
            print(f"[INFO] Created table: {name}")
        except client.exceptions.ResourceInUseException:
            print(f"[INFO] Table exists: {name}")


def get_product_cache(dynamo, product_id: str) -> dict | None:
    try:
        resp = dynamo.Table(TABLE_META).get_item(Key={"product_id": product_id})
        return resp.get("Item")
    except ClientError:
        return None


def save_product_cache(dynamo, product_id: str, meta: dict):
    now_str = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
    try:
        dynamo.Table(TABLE_META).put_item(Item={
            "product_id": product_id,
            "cached_at":  now_str,
            **{k: v for k, v in meta.items() if v},
        })
    except ClientError as e:
        print(f"[WARN] Cache save failed ({product_id}): {e}")


def find_x_post(dynamo, product_id: str) -> dict | None:
    """folder_name に product_id を含む最古の投稿記録を返す"""
    try:
        resp = dynamo.Table(TABLE_POSTS).scan(
            FilterExpression="contains(folder_name, :pid)",
            ExpressionAttributeValues={":pid": product_id},
            ProjectionExpression="account_id, folder_name, posted_at, tweet_id",
        )
        items = resp.get("Items", [])
        if items:
            items.sort(key=lambda x: x.get("posted_at", ""))
            return items[0]
    except ClientError as e:
        print(f"[WARN] DynamoDB scan failed: {e}")
    return None


def save_sale(dynamo, record: dict):
    try:
        dynamo.Table(TABLE_SALES).put_item(Item=record)
        print(f"[INFO] Saved: {record['sale_date']}  {record.get('product_name', '')[:30]}")
    except ClientError as e:
        print(f"[ERROR] Save failed: {e}")


# ─────────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────────

def extract_product_id(url: str) -> str:
    """FANZA/DMM URL から cid (作品ID) を抽出"""
    for pattern in [r"cid=([A-Za-z0-9_-]+)", r"/cid/([A-Za-z0-9_-]+)", r"/([a-z]{2,6}\d{3,8})/"]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return ""


def to_decimal(text: str) -> Decimal:
    cleaned = re.sub(r"[^\d.]", "", text or "0") or "0"
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0")


# ─────────────────────────────────────────
# Playwright: ログイン
# ─────────────────────────────────────────

async def save_screenshot(page, name: str):
    path = f"/tmp/{name}.png"
    try:
        await page.screenshot(path=path, full_page=False)
        print(f"[DEBUG] Screenshot saved: {path}")
    except Exception as e:
        print(f"[WARN] Screenshot failed: {e}")


async def login_fanza(page):
    email    = os.environ["FANZA_EMAIL"]
    password = os.environ["FANZA_PASSWORD"]

    # アフィリエイトダッシュボードへ移動 → 未ログイン時は accounts.dmm.com へリダイレクト
    await page.goto("https://affiliate.dmm.com/", wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PWTimeout:
        pass

    print(f"[INFO] Pre-login URL: {page.url[:80]}")
    await save_screenshot(page, "01_pre_login")

    # ログインページにいる場合はフォーム入力
    if "login" in page.url or "accounts.dmm.com" in page.url:
        # メールアドレス入力 (複数セレクタをフォールバック)
        email_filled = False
        for sel in [
            "input[name='login_id']",
            "input[type='email']",
            "input[id='login_id']",
            "input[placeholder*='メール']",
            "input[placeholder*='ID']",
        ]:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.fill(email)
                email_filled = True
                print(f"[INFO] Email filled with selector: {sel}")
                break
        if not email_filled:
            print("[WARN] Email input not found")

        # パスワード入力
        pass_filled = False
        for sel in [
            "input[name='password']",
            "input[type='password']",
            "input[id='password']",
        ]:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.fill(password)
                pass_filled = True
                print(f"[INFO] Password filled with selector: {sel}")
                break
        if not pass_filled:
            print("[WARN] Password input not found")

        await save_screenshot(page, "02_form_filled")

        # 送信
        for sel in [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('ログイン')",
            "button:has-text('Login')",
            "input[value*='ログイン']",
        ]:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click()
                print(f"[INFO] Submit clicked: {sel}")
                break

        try:
            await page.wait_for_load_state("networkidle", timeout=25_000)
        except PWTimeout:
            pass

        print(f"[INFO] Post-submit URL: {page.url[:80]}")
        await save_screenshot(page, "03_post_login")

    # アフィリエイトドメインにいることを確認
    if "affiliate.dmm.com" not in page.url:
        await page.goto("https://affiliate.dmm.com/", wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeout:
            pass
        print(f"[INFO] Re-navigated to affiliate: {page.url[:80]}")
        await save_screenshot(page, "04_affiliate_top")

    print(f"[INFO] Login complete. URL={page.url[:80]}")


# ─────────────────────────────────────────
# Playwright: 商品別レポート CSV ダウンロード
# ─────────────────────────────────────────

async def download_goods_report_csv(page, target_date: datetime) -> str:
    """
    商品別レポート → FANZA タブ → CSV ダウンロード
    戻り値: CSV 文字列（空の場合は ""）
    """
    date_str = target_date.strftime("%Y/%m/%d")

    # アフィリエイトレポートページへ移動
    await page.goto("https://affiliate.dmm.com/affiliate/report/", wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PWTimeout:
        pass

    # 「商品別レポート」タブをクリック
    for sel in [
        "a:has-text('商品別レポート')",
        "li:has-text('商品別レポート') a",
        "a[href*='goods']",
    ]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PWTimeout:
                pass
            break

    print(f"[INFO] Navigated to: {page.url[:70]}")

    # 集計期間を今日に設定 (すでに今日になっている場合もある)
    for sel in ["input[name='date_from']", "input[id='date_from']"]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.triple_click()
            await loc.first.fill(date_str)
            break
    for sel in ["input[name='date_to']", "input[id='date_to']"]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.triple_click()
            await loc.first.fill(date_str)
            break

    # 検索実行
    for sel in ["button:has-text('集計')", "button:has-text('検索')", "input[type='submit']"]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PWTimeout:
                pass
            break

    # FANZA タブを選択（アクティブでなければクリック）
    fanza_tab = page.locator("a:has-text('FANZA'), .tab-fanza a, li:has-text('FANZA') a")
    if await fanza_tab.count() > 0:
        await fanza_tab.first.click()
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeout:
            pass

    # CSV ダウンロード
    csv_btn = page.locator("a:has-text('CSVデータをダウンロード'), a:has-text('CSV'), button:has-text('CSV')")
    if await csv_btn.count() == 0:
        print("[WARN] CSV download button not found")
        return ""

    async with page.expect_download(timeout=30_000) as dl_info:
        await csv_btn.first.click()
    download = await dl_info.value

    save_path = "/tmp/fanza_report.csv"
    await download.save_as(save_path)

    # エンコーディングを試行 (Shift-JIS / UTF-8)
    for enc in ["shift_jis", "utf-8-sig", "utf-8"]:
        try:
            with open(save_path, encoding=enc) as f:
                content = f.read()
            print(f"[INFO] CSV downloaded ({enc}): {len(content)} chars")
            return content
        except UnicodeDecodeError:
            continue

    print("[WARN] CSV encoding detection failed")
    return ""


# ─────────────────────────────────────────
# CSV パース
# ─────────────────────────────────────────

def parse_goods_csv(csv_text: str, target_date: datetime) -> list[dict]:
    """
    商品別レポート CSV をパースして辞書リストを返す

    想定カラム例:
      サービス, 商品名, 価格, 詳細情報, 報酬体系, 報酬件数, 獲得報酬
    ※ 実際のカラム名は FANZA 仕様に依存。ヘッダー行を確認してマッピングする。
    """
    if not csv_text.strip():
        return []

    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)
    if not rows:
        print("[INFO] CSV has no data rows")
        return []

    print(f"[INFO] CSV columns: {list(rows[0].keys())}")

    date_str    = target_date.strftime("%Y/%m/%d")
    day_of_week = DAY_NAMES[target_date.weekday()]
    sales = []

    for row in rows:
        # 合計行をスキップ
        values = list(row.values())
        if any(v in ("合計", "期間内合計", "総合計") for v in values if v):
            continue

        # カラム名の揺れに対応するマッピング
        def get(keys):
            for k in keys:
                for col in row:
                    if k in col:
                        return (row[col] or "").strip()
            return ""

        service       = get(["サービス"])
        product_name  = get(["商品名", "作品名", "販売商品"])
        price_text    = get(["価格", "販売金額"])
        commission_type = get(["報酬体系", "体系"])
        count_text    = get(["報酬件数", "件数"])
        revenue_text  = get(["獲得報酬", "報酬金額", "報酬"])

        if not product_name:
            continue

        sale_price    = to_decimal(price_text)
        sale_count    = int(to_decimal(count_text))
        revenue       = to_decimal(revenue_text)

        sales.append({
            "_date":            date_str,
            "_day_of_week":     day_of_week,
            "_service":         service,
            "_product_name":    product_name,
            "_sale_price":      sale_price,
            "_commission_type": commission_type,
            "_sale_count":      sale_count,
            "_revenue":         revenue,
        })

    print(f"[INFO] Parsed {len(sales)} products from CSV")
    return sales


# ─────────────────────────────────────────
# Playwright: 作品ページ スクレイピング
# ─────────────────────────────────────────

async def scrape_product_page(browser, product_url: str) -> dict:
    meta = {"product_genre": [], "actress_name": [], "maker_name": "", "release_date": ""}
    if not product_url:
        return meta

    page = await browser.new_page()
    try:
        await page.goto(product_url, wait_until="domcontentloaded", timeout=20_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeout:
            pass

        async def get_table_values(label: str) -> list[str]:
            for sel in [
                f"tr:has(td:text-is('{label}')) td:not(:first-child) a",
                f"tr:has(th:text-is('{label}')) td a",
                f"tr:has(td:has-text('{label}')) td:last-child a",
            ]:
                els = await page.locator(sel).all()
                if els:
                    return [(await e.inner_text()).strip() for e in els if (await e.inner_text()).strip()]
            # リンクなし行
            for sel in [
                f"tr:has(td:text-is('{label}')) td:last-child",
                f"tr:has(th:text-is('{label}')) td:last-child",
            ]:
                els = await page.locator(sel).all()
                if els:
                    t = (await els[0].inner_text()).strip()
                    return [t] if t else []
            return []

        meta["product_genre"] = await get_table_values("ジャンル")
        meta["actress_name"]  = await get_table_values("出演者")
        maker = await get_table_values("メーカー")
        meta["maker_name"]    = maker[0] if maker else ""
        release = await get_table_values("発売日")
        meta["release_date"]  = release[0] if release else ""

    except Exception as e:
        print(f"[WARN] Product scrape error ({product_url[:50]}): {e}")
    finally:
        await page.close()

    return meta


# ─────────────────────────────────────────
# 商品別レポートからの製品 URL 取得
# ─────────────────────────────────────────

async def collect_product_urls(page, target_date: datetime) -> dict[str, str]:
    """
    商品別レポートのテーブルから 商品名 → 商品URL のマッピングを取得する
    CSV には URL が含まれないため、HTML テーブルから補完する
    """
    urls: dict[str, str] = {}

    page_num = 1
    while True:
        rows = await page.locator("table tbody tr").all()
        for row in rows:
            link = row.locator("a[href*='dmm.co.jp'], a[href*='dmm.com'], a[href*='fanza.com']")
            if await link.count() > 0:
                href = (await link.first.get_attribute("href")) or ""
                name = (await link.first.inner_text()).strip()
                if href and name:
                    urls[name] = href

        next_btn = page.locator("a:has-text('次へ'), .pagination .next a")
        if await next_btn.count() > 0:
            await next_btn.first.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PWTimeout:
                pass
            page_num += 1
        else:
            break

    print(f"[INFO] Collected {len(urls)} product URLs from HTML table ({page_num} pages)")
    return urls


# ─────────────────────────────────────────
# Playwright: 時間別クリック数 (レポートトップ Highcharts)
# ─────────────────────────────────────────

async def scrape_hourly_clicks(page, target_date: datetime) -> dict[str, int]:
    """
    レポートトップの時間別クリック数グラフ（Highcharts）から
    0〜23時のクリック数を抽出して返す。
    戻り値: {"00": 795, "01": 680, ..., "23": 0}
    """
    date_str = target_date.strftime("%Y/%m/%d")

    # レポートトップへ移動（今日タブ）
    await page.goto("https://affiliate.dmm.com/affiliate/report/", wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PWTimeout:
        pass

    # FANZA タブを選択
    fanza_tab = page.locator("a:has-text('FANZA'), .tab-fanza a")
    if await fanza_tab.count() > 0:
        await fanza_tab.first.click()
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PWTimeout:
            pass

    # 「今日」タブが選択されていない場合はクリック
    today_tab = page.locator("a:has-text('今日'), button:has-text('今日')")
    if await today_tab.count() > 0:
        await today_tab.first.click()
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except PWTimeout:
            pass

    # Highcharts のレンダリングを待つ
    await page.wait_for_timeout(3_000)
    await save_screenshot(page, "05_report_top")

    # Highcharts から クリック数 series のデータを抽出
    raw = await page.evaluate("""
        () => {
            try {
                const charts = window.Highcharts && window.Highcharts.charts;
                if (!charts) return null;
                for (const chart of charts) {
                    if (!chart) continue;
                    // クリック数 series を探す
                    const series = chart.series.find(s =>
                        s.name && (s.name.includes('クリック') || s.name.toLowerCase().includes('click'))
                    );
                    if (series) {
                        return series.data.map(p => (p && p.y !== undefined) ? p.y : 0);
                    }
                }
                // フォールバック: 最初の series を使用
                for (const chart of charts) {
                    if (!chart || !chart.series || !chart.series[0]) continue;
                    return chart.series[0].data.map(p => (p && p.y !== undefined) ? p.y : 0);
                }
            } catch(e) {
                return null;
            }
            return null;
        }
    """)

    if not raw:
        print("[WARN] Highcharts data not found")
        return {}

    # 最大 24 要素 (0〜23時) に正規化
    result: dict[str, int] = {}
    for i, val in enumerate(raw[:24]):
        result[str(i).zfill(2)] = int(val or 0)

    total = sum(result.values())
    print(f"[INFO] Hourly clicks extracted: {len(result)} hours, total={total}")
    return result


def save_hourly_clicks(dynamo, date_str: str, clicks: dict[str, int]):
    if not clicks:
        return
    now_str = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
    total   = sum(clicks.values())
    try:
        dynamo.Table(TABLE_CLICKS).put_item(Item={
            "date":          date_str,
            "clicks_by_hour": {h: Decimal(str(v)) for h, v in clicks.items()},
            "total_clicks":   Decimal(str(total)),
            "scraped_at":     now_str,
        })
        print(f"[INFO] Saved hourly clicks: date={date_str}, total={total}")
    except ClientError as e:
        print(f"[ERROR] Failed to save hourly clicks: {e}")


# ─────────────────────────────────────────
# メイン処理
# ─────────────────────────────────────────

async def main():
    dynamo = get_dynamo()
    ensure_tables(dynamo)

    now_jst     = datetime.now(JST)
    target_date = now_jst
    print(f"[INFO] Run: {now_jst.strftime('%Y/%m/%d %H:%M')} JST  target={target_date.strftime('%Y/%m/%d')}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1280, "height": 800},
            accept_downloads=True,
        )
        page = await context.new_page()

        # ログイン
        await login_fanza(page)

        # 時間別クリック数を収集（レポートトップ Highcharts）
        hourly_clicks = await scrape_hourly_clicks(page, target_date)
        save_hourly_clicks(dynamo, target_date.strftime("%Y/%m/%d"), hourly_clicks)

        # HTML テーブルから商品 URL を先に取得（CSV に URL が含まれないため）
        product_urls = await collect_product_urls(page, target_date)

        # CSV ダウンロード & パース
        csv_text = await download_goods_report_csv(page, target_date)
        sales    = parse_goods_csv(csv_text, target_date)

        if not sales:
            print("[INFO] No sales today. Exit.")
            await browser.close()
            return

        # 各商品を処理
        for sale in sales:
            product_name = sale["_product_name"]

            # 商品 URL (HTML テーブルから取得済み、または部分一致で探す)
            product_url = product_urls.get(product_name, "")
            if not product_url:
                # 部分一致フォールバック
                for k, v in product_urls.items():
                    if product_name[:10] in k or k[:10] in product_name:
                        product_url = v
                        break

            product_id = extract_product_id(product_url) if product_url else ""
            if not product_id:
                product_id = re.sub(r"[^\w]", "_", product_name[:20])

            # 作品メタデータ（キャッシュ優先）
            meta = None
            if product_url:
                meta = get_product_cache(dynamo, product_id)
                if meta:
                    print(f"[INFO] Cache hit: {product_id}")
                else:
                    meta = await scrape_product_page(browser, product_url)
                    save_product_cache(dynamo, product_id, meta)

            # X 投稿との紐付け
            post_url      = ""
            post_datetime = ""
            account_id    = ""
            if product_id:
                post_info = find_x_post(dynamo, product_id)
                if post_info:
                    tweet_id      = post_info.get("tweet_id", "")
                    account_id    = post_info.get("account_id", "")
                    post_datetime = post_info.get("posted_at", "")
                    if tweet_id:
                        post_url = f"https://x.com/i/web/status/{tweet_id}"

            # 保存レコード
            record: dict = {
                "sale_date":        sale["_date"],
                "product_id":       product_id,
                "product_name":     product_name,
                "product_url":      product_url,
                "service_type":     sale["_service"],
                "sale_price":       sale["_sale_price"],
                "commission_type":  sale["_commission_type"],
                "sale_count":       sale["_sale_count"],
                "revenue":          sale["_revenue"],
                "day_of_week":      sale["_day_of_week"],
                "post_url":         post_url,
                "post_datetime":    post_datetime,
                "account_id":       account_id,
            }
            if meta:
                if meta.get("product_genre"):
                    record["product_genre"] = meta["product_genre"]
                if meta.get("actress_name"):
                    record["actress_name"] = meta["actress_name"]
                if meta.get("maker_name"):
                    record["maker_name"] = meta["maker_name"]
                if meta.get("release_date"):
                    record["release_date"] = meta["release_date"]

            save_sale(dynamo, record)

        await browser.close()

    print(f"[INFO] Done. {len(sales)} products processed.")


if __name__ == "__main__":
    asyncio.run(main())
