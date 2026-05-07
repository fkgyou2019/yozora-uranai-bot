#!/usr/bin/env python3
"""
FANZA アフィリエイト 収益データ収集スクリプト
毎日 23:55 JST に GitHub Actions から実行

テーブル構成:
  fanza-affiliate-sales    … 日別売上レコード (PK=sale_date, SK=sale_timestamp)
  fanza-product-metadata   … 作品メタデータキャッシュ (PK=product_id)
"""

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
TABLE_SALES = "fanza-affiliate-sales"
TABLE_META  = "fanza-product-metadata"
TABLE_POSTS = "x-poster-daily-posts"

DAY_NAMES = ["月", "火", "水", "木", "金", "土", "日"]

# ─────────────────────────────────────────
# DynamoDB ユーティリティ
# ─────────────────────────────────────────

def get_dynamo():
    return boto3.resource("dynamodb", region_name=AWS_REGION)


def ensure_tables(dynamo):
    existing = {t.name for t in dynamo.tables.all()}

    if TABLE_SALES not in existing:
        dynamo.create_table(
            TableName=TABLE_SALES,
            KeySchema=[
                {"AttributeName": "sale_date",      "KeyType": "HASH"},
                {"AttributeName": "sale_timestamp",  "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "sale_date",      "AttributeType": "S"},
                {"AttributeName": "sale_timestamp",  "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"[INFO] Created table: {TABLE_SALES}")

    if TABLE_META not in existing:
        dynamo.create_table(
            TableName=TABLE_META,
            KeySchema=[
                {"AttributeName": "product_id", "KeyType": "HASH"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "product_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"[INFO] Created table: {TABLE_META}")

    client = dynamo.meta.client
    for name in [TABLE_SALES, TABLE_META]:
        if name not in existing:
            client.get_waiter("table_exists").wait(TableName=name)
            print(f"[INFO] Table ready: {name}")


def get_product_cache(dynamo, product_id):
    try:
        resp = dynamo.Table(TABLE_META).get_item(Key={"product_id": product_id})
        return resp.get("Item")
    except ClientError:
        return None


def save_product_cache(dynamo, product_id, meta):
    now_str = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
    try:
        dynamo.Table(TABLE_META).put_item(Item={
            "product_id": product_id,
            "cached_at":  now_str,
            **{k: v for k, v in meta.items() if v},
        })
    except ClientError as e:
        print(f"[WARN] Cache save failed ({product_id}): {e}")


def find_x_post(dynamo, product_id):
    """folder_name に product_id を含む最古のポスト記録を返す"""
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


def save_sale(dynamo, record):
    try:
        dynamo.Table(TABLE_SALES).put_item(Item=record)
        print(f"[INFO] Saved: {record['sale_timestamp']}  {record.get('product_name','')[:30]}")
    except ClientError as e:
        print(f"[ERROR] Save failed: {e}")


# ─────────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────────

def extract_product_id(url: str) -> str:
    """FANZA URL から cid を抽出"""
    m = re.search(r"cid=([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    # /detail/=/cid=XXXXX/ 形式
    m = re.search(r"/cid/([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    return ""


def to_decimal(text: str) -> Decimal:
    cleaned = re.sub(r"[^\d.]", "", text or "0") or "0"
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0")


def compute_days_since_post(sale_ts: str, post_iso: str):
    if not post_iso:
        return None
    try:
        sale_dt = datetime.strptime(sale_ts, "%Y/%m/%d %H:%M").replace(tzinfo=JST)
        post_dt = datetime.fromisoformat(post_iso)
        if post_dt.tzinfo is None:
            post_dt = post_dt.replace(tzinfo=JST)
        return max(0, (sale_dt - post_dt).days)
    except Exception:
        return None


# ─────────────────────────────────────────
# Playwright: ログイン
# ─────────────────────────────────────────

async def login_fanza(page):
    email    = os.environ["FANZA_EMAIL"]
    password = os.environ["FANZA_PASSWORD"]

    await page.goto("https://www.dmm.com/my/-/login/", wait_until="domcontentloaded")
    await page.wait_for_load_state("networkidle", timeout=20_000)

    await page.fill("input[name='login_id']", email)
    await page.fill("input[name='password']", password)
    await page.click("input[type='submit'], button[type='submit']")
    await page.wait_for_load_state("networkidle", timeout=20_000)

    print(f"[INFO] Logged in. URL={page.url[:60]}")

    # アフィリエイトトップへ
    await page.goto("https://affiliate.dmm.com/affiliate/", wait_until="domcontentloaded")
    await page.wait_for_load_state("networkidle", timeout=20_000)


# ─────────────────────────────────────────
# Playwright: 成果レポート収集
# ─────────────────────────────────────────

async def collect_today_sales(page, target_date: datetime) -> list[dict]:
    date_str = target_date.strftime("%Y/%m/%d")

    # 成果レポートページへ移動
    # ※ FANZA アフィリエイト UI 変更時はこの URL を更新すること
    report_urls = [
        "https://affiliate.dmm.com/affiliate/report/",
        "https://affiliate.dmm.com/report/",
    ]
    for url in report_urls:
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle", timeout=15_000)
        if page.url != url and "login" not in page.url:
            break

    # 日付フィルタ設定
    for sel in ["input[name='date_from']", "input[id='date_from']", "input[placeholder*='開始']"]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.fill(date_str)
            break

    for sel in ["input[name='date_to']", "input[id='date_to']", "input[placeholder*='終了']"]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.fill(date_str)
            break

    for sel in ["button:has-text('検索')", "input[type='submit']", "button[type='submit']"]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.click()
            await page.wait_for_load_state("networkidle", timeout=15_000)
            break

    sales = []
    page_num = 1

    while True:
        # テーブル行を取得 (セレクタは FANZA の実 UI に合わせて要調整)
        row_selectors = [
            "table tbody tr",
            ".affiliate-report tbody tr",
            ".result-table tbody tr",
            "#report_detail tbody tr",
        ]
        rows = []
        for sel in row_selectors:
            rows = await page.locator(sel).all()
            if rows:
                break

        if not rows:
            print(f"[INFO] No rows (page {page_num})")
            break

        for row in rows:
            sale = await parse_row(row, target_date)
            if sale:
                sales.append(sale)

        # 次ページ
        next_loc = page.locator("a:has-text('次へ'), .pagination .next a, a[rel='next']")
        if await next_loc.count() > 0:
            await next_loc.first.click()
            await page.wait_for_load_state("networkidle", timeout=15_000)
            page_num += 1
        else:
            break

    print(f"[INFO] Collected {len(sales)} sales (page count: {page_num})")
    return sales


async def parse_row(row, target_date: datetime) -> dict | None:
    cells = await row.locator("td").all()
    if len(cells) < 4:
        return None

    texts = [(await c.inner_text()).strip() for c in cells]

    # 1列目: タイムスタンプ
    ts_text = texts[0].replace("-", "/")
    if not re.search(r"\d{4}/\d{2}/\d{2}", ts_text):
        return None
    ts_text = ts_text[:16]  # "YYYY/MM/DD HH:MM"

    # 作品名・URL
    product_name = ""
    product_url  = ""
    link_loc = row.locator("a[href*='dmm.co.jp'], a[href*='dmm.com'], a[href*='fanza.com']")
    if await link_loc.count() > 0:
        product_url  = (await link_loc.first.get_attribute("href")) or ""
        product_name = (await link_loc.first.inner_text()).strip()
    if not product_name and len(texts) > 1:
        product_name = texts[1]

    # 数値列 (¥ / % を除去)
    nums = [(i, to_decimal(t)) for i, t in enumerate(texts) if re.search(r"[\d,]+", t) and i > 0]

    # 報酬率 (%) は % 含む列
    commission_rate = Decimal("0")
    for i, t in enumerate(texts):
        if "%" in t:
            commission_rate = to_decimal(t)
            break

    # 価格を推定: 100円以上の数値列から最大2つ
    price_nums = sorted([(v, i) for i, v in nums if v >= Decimal("100")], reverse=True)
    original_price = price_nums[0][0] if len(price_nums) >= 1 else Decimal("0")
    sale_price     = price_nums[1][0] if len(price_nums) >= 2 else original_price

    # 収益: 最後の数値列 (通常最右)
    revenue = Decimal("0")
    for i, v in reversed(nums):
        if v > 0:
            revenue = v
            break

    is_sale   = sale_price < original_price
    sale_type = "rental" if any("レンタル" in t for t in texts) else "purchase"

    try:
        dt = datetime.strptime(ts_text, "%Y/%m/%d %H:%M").replace(tzinfo=JST)
    except ValueError:
        dt = target_date

    return {
        "_ts":          ts_text,
        "_date":        ts_text[:10],
        "product_id":   extract_product_id(product_url),
        "product_name": product_name,
        "product_url":  product_url,
        "sale_type":    sale_type,
        "original_price": original_price,
        "sale_price":     sale_price,
        "revenue":        revenue,
        "commission_rate": commission_rate,
        "is_sale":        is_sale,
        "day_of_week":    DAY_NAMES[dt.weekday()],
    }


# ─────────────────────────────────────────
# Playwright: 作品ページ スクレイピング
# ─────────────────────────────────────────

async def scrape_product_page(browser, product_url: str) -> dict:
    meta = {"product_genre": [], "actress_name": [], "maker_name": "", "release_date": ""}
    if not product_url:
        return meta

    page = await browser.new_page()
    try:
        # al.dmm.co.jp (アフィリエイトリンク) はリダイレクト先を追う
        await page.goto(product_url, wait_until="domcontentloaded", timeout=20_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeout:
            pass

        actual_url = page.url
        print(f"[INFO] Product page: {actual_url[:70]}")

        # 作品詳細テーブル行セレクタ (FANZA/DMM 共通パターン)
        # ※ UI 変更時は以下のセレクタを更新すること
        async def get_table_values(label: str) -> list[str]:
            results = []
            for sel in [
                f"tr:has(td:text('{label}')) td:not(:first-child) a",
                f"tr:has(th:text('{label}')) td a",
                f".pd-info tr:has(td:text('{label}')) td a",
            ]:
                els = await page.locator(sel).all()
                if els:
                    for el in els:
                        t = (await el.inner_text()).strip()
                        if t:
                            results.append(t)
                    break
            # リンクなしテキストのフォールバック
            if not results:
                for sel in [
                    f"tr:has(td:text('{label}')) td:last-child",
                    f"tr:has(th:text('{label}')) td:last-child",
                ]:
                    els = await page.locator(sel).all()
                    if els:
                        t = (await els[0].inner_text()).strip()
                        if t:
                            results.append(t)
                        break
            return results

        meta["product_genre"]  = await get_table_values("ジャンル")
        meta["actress_name"]   = await get_table_values("出演者")
        meta["maker_name"]     = (await get_table_values("メーカー") or [""])[0]
        meta["release_date"]   = (await get_table_values("発売日")   or [""])[0]

    except Exception as e:
        print(f"[WARN] Product scrape error: {e}")
    finally:
        await page.close()

    return meta


# ─────────────────────────────────────────
# メイン処理
# ─────────────────────────────────────────

async def main():
    dynamo = get_dynamo()
    ensure_tables(dynamo)

    now_jst     = datetime.now(JST)
    target_date = now_jst
    print(f"[INFO] Run: {now_jst.strftime('%Y/%m/%d %H:%M')} JST  →  target={target_date.strftime('%Y/%m/%d')}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        await login_fanza(page)
        sales = await collect_today_sales(page, target_date)

        if not sales:
            print("[INFO] No sales today. Exit.")
            await browser.close()
            return

        processed = 0
        for sale in sales:
            product_id = sale["product_id"]

            # 作品メタデータ (キャッシュ優先)
            meta = None
            if product_id:
                meta = get_product_cache(dynamo, product_id)
                if meta:
                    print(f"[INFO] Cache hit: {product_id}")
                else:
                    meta = await scrape_product_page(browser, sale["product_url"])
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

            # 派生フィールド計算
            days_since  = compute_days_since_post(sale["_ts"], post_datetime)

            op = sale["original_price"]
            sp = sale["sale_price"]
            discount_rate = (
                Decimal(str(round(float(op - sp) / float(op), 4)))
                if op > 0 and sp < op else None
            )

            # 保存レコード
            record: dict = {
                "sale_date":       sale["_date"],
                "sale_timestamp":  sale["_ts"],
                "product_id":      product_id or "unknown",
                "product_name":    sale["product_name"],
                "product_url":     sale["product_url"],
                "sale_type":       sale["sale_type"],
                "original_price":  sale["original_price"],
                "sale_price":      sale["sale_price"],
                "revenue":         sale["revenue"],
                "commission_rate": sale["commission_rate"],
                "is_sale":         sale["is_sale"],
                "day_of_week":     sale["day_of_week"],
                "post_url":        post_url,
                "post_datetime":   post_datetime,
                "account_id":      account_id,
            }
            if days_since is not None:
                record["days_since_post"] = days_since
            if discount_rate is not None:
                record["discount_rate"] = discount_rate
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
            processed += 1

        await browser.close()

    print(f"[INFO] Done. {processed}/{len(sales)} records saved.")


if __name__ == "__main__":
    asyncio.run(main())
