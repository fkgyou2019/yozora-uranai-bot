#!/usr/bin/env python3
"""
FANZA アフィリエイト 収益データ収集
Lambda コンテナ版 (ap-northeast-1 東京 = 日本IPで実行)

DynamoDB テーブル:
  fanza-affiliate-sales    PK=sale_date  SK=product_id
  fanza-product-metadata   PK=product_id
  fanza-hourly-clicks      PK=date
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

JST = timezone(timedelta(hours=9))
AWS_REGION    = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-1")
TABLE_SALES   = "fanza-affiliate-sales"
TABLE_META    = "fanza-product-metadata"
TABLE_CLICKS  = "fanza-hourly-clicks"
TABLE_POSTS   = "x-poster-daily-posts"
DAY_NAMES     = ["月", "火", "水", "木", "金", "土", "日"]


# ── DynamoDB ────────────────────────────────────────────────────

def get_dynamo():
    return boto3.resource("dynamodb", region_name=AWS_REGION)


def ensure_tables(dynamo):
    specs = [
        (TABLE_SALES,
         [{"AttributeName": "sale_date",  "KeyType": "HASH"},
          {"AttributeName": "product_id", "KeyType": "RANGE"}],
         [{"AttributeName": "sale_date",  "AttributeType": "S"},
          {"AttributeName": "product_id", "AttributeType": "S"}]),
        (TABLE_META,
         [{"AttributeName": "product_id", "KeyType": "HASH"}],
         [{"AttributeName": "product_id", "AttributeType": "S"}]),
        (TABLE_CLICKS,
         [{"AttributeName": "date", "KeyType": "HASH"}],
         [{"AttributeName": "date", "AttributeType": "S"}]),
    ]
    client = dynamo.meta.client
    for name, key_schema, attr_defs in specs:
        try:
            dynamo.create_table(TableName=name, KeySchema=key_schema,
                                AttributeDefinitions=attr_defs,
                                BillingMode="PAY_PER_REQUEST")
            client.get_waiter("table_exists").wait(TableName=name)
            print(f"[INFO] Created table: {name}")
        except client.exceptions.ResourceInUseException:
            print(f"[INFO] Table exists: {name}")


def get_product_cache(dynamo, product_id):
    try:
        return dynamo.Table(TABLE_META).get_item(Key={"product_id": product_id}).get("Item")
    except ClientError:
        return None


def save_product_cache(dynamo, product_id, meta):
    try:
        dynamo.Table(TABLE_META).put_item(Item={
            "product_id": product_id,
            "cached_at": datetime.now(JST).strftime("%Y/%m/%d %H:%M"),
            **{k: v for k, v in meta.items() if v},
        })
    except ClientError as e:
        print(f"[WARN] Cache save failed: {e}")


def find_x_post(dynamo, product_id):
    try:
        items = dynamo.Table(TABLE_POSTS).scan(
            FilterExpression="contains(folder_name, :pid)",
            ExpressionAttributeValues={":pid": product_id},
            ProjectionExpression="account_id, folder_name, posted_at, tweet_id",
        ).get("Items", [])
        if items:
            return sorted(items, key=lambda x: x.get("posted_at", ""))[0]
    except ClientError as e:
        print(f"[WARN] DynamoDB scan: {e}")
    return None


def save_sale(dynamo, record):
    try:
        dynamo.Table(TABLE_SALES).put_item(Item=record)
        print(f"[INFO] Saved: {record['sale_date']}  {record.get('product_name','')[:30]}")
    except ClientError as e:
        print(f"[ERROR] Save failed: {e}")


def save_hourly_clicks(dynamo, date_str, clicks):
    if not clicks:
        return
    try:
        dynamo.Table(TABLE_CLICKS).put_item(Item={
            "date": date_str,
            "clicks_by_hour": {h: Decimal(str(v)) for h, v in clicks.items()},
            "total_clicks": Decimal(str(sum(clicks.values()))),
            "scraped_at": datetime.now(JST).strftime("%Y/%m/%d %H:%M"),
        })
        print(f"[INFO] Saved hourly clicks: total={sum(clicks.values())}")
    except ClientError as e:
        print(f"[ERROR] Failed to save hourly clicks: {e}")


# ── ヘルパー ─────────────────────────────────────────────────────

def extract_product_id(url):
    for pat in [r"cid=([A-Za-z0-9_-]+)", r"/cid/([A-Za-z0-9_-]+)"]:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return ""


def to_decimal(text):
    cleaned = re.sub(r"[^\d.]", "", text or "0") or "0"
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0")


# ── Playwright ───────────────────────────────────────────────────

async def save_screenshot(page, name):
    try:
        await page.screenshot(path=f"/tmp/{name}.png", full_page=False)
        print(f"[DEBUG] Screenshot: /tmp/{name}.png")
    except Exception as e:
        print(f"[WARN] Screenshot failed: {e}")


async def login_fanza(page):
    email    = os.environ["FANZA_EMAIL"]
    password = os.environ["FANZA_PASSWORD"]

    await page.goto("https://affiliate.dmm.com/", wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PWTimeout:
        pass

    print(f"[INFO] Pre-login URL: {page.url[:80]}")
    await save_screenshot(page, "01_pre_login")

    if "login" in page.url or "accounts.dmm.com" in page.url:
        for sel in ["input[name='login_id']", "input[type='email']",
                    "input[id='login_id']", "input[placeholder*='メール']"]:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.fill(email)
                print(f"[INFO] Email filled: {sel}")
                break

        for sel in ["input[name='password']", "input[type='password']"]:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.fill(password)
                print(f"[INFO] Password filled: {sel}")
                break

        await save_screenshot(page, "02_form_filled")

        for sel in ["button[type='submit']", "input[type='submit']",
                    "button:has-text('ログイン')"]:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click()
                print(f"[INFO] Submit: {sel}")
                break

        try:
            await page.wait_for_load_state("networkidle", timeout=25_000)
        except PWTimeout:
            pass
        print(f"[INFO] Post-submit URL: {page.url[:80]}")
        await save_screenshot(page, "03_post_login")

    if "affiliate.dmm.com" not in page.url:
        await page.goto("https://affiliate.dmm.com/", wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeout:
            pass

    print(f"[INFO] Login complete: {page.url[:80]}")
    await save_screenshot(page, "04_after_login")


async def scrape_hourly_clicks(page, target_date):
    await page.goto("https://affiliate.dmm.com/affiliate/report/",
                    wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PWTimeout:
        pass

    for sel in ["a:has-text('FANZA')", ".tab-fanza a"]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.click()
            await page.wait_for_timeout(2_000)
            break

    for sel in ["a:has-text('今日')", "button:has-text('今日')"]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.click()
            await page.wait_for_timeout(2_000)
            break

    await page.wait_for_timeout(3_000)
    await save_screenshot(page, "05_report_top")

    raw = await page.evaluate("""
        () => {
            try {
                const charts = window.Highcharts && window.Highcharts.charts;
                if (!charts) return null;
                for (const c of charts) {
                    if (!c) continue;
                    const s = c.series.find(s => s.name && s.name.includes('クリック'));
                    if (s) return s.data.map(p => p && p.y !== undefined ? p.y : 0);
                }
                for (const c of charts) {
                    if (!c || !c.series || !c.series[0]) continue;
                    return c.series[0].data.map(p => p && p.y !== undefined ? p.y : 0);
                }
            } catch(e) { return null; }
            return null;
        }
    """)

    if not raw:
        print("[WARN] Highcharts data not found")
        return {}

    result = {str(i).zfill(2): int(v or 0) for i, v in enumerate(raw[:24])}
    print(f"[INFO] Hourly clicks: {len(result)} hours, total={sum(result.values())}")
    return result


async def collect_product_urls(page, target_date):
    urls = {}
    page_num = 1
    while True:
        for row in await page.locator("table tbody tr").all():
            link = row.locator("a[href*='dmm.co.jp'], a[href*='dmm.com']")
            if await link.count() > 0:
                href = (await link.first.get_attribute("href")) or ""
                name = (await link.first.inner_text()).strip()
                if href and name:
                    urls[name] = href
        nxt = page.locator("a:has-text('次へ'), .pagination .next a")
        if await nxt.count() > 0:
            await nxt.first.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PWTimeout:
                pass
            page_num += 1
        else:
            break
    print(f"[INFO] Product URLs: {len(urls)} ({page_num} pages)")
    return urls


async def download_goods_report_csv(page, target_date):
    date_str = target_date.strftime("%Y/%m/%d")

    await page.goto("https://affiliate.dmm.com/affiliate/report/",
                    wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PWTimeout:
        pass

    for sel in ["a:has-text('商品別レポート')", "a[href*='goods']"]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PWTimeout:
                pass
            break

    for fname, sel in [("date_from", "input[name='date_from']"),
                        ("date_to",   "input[name='date_to']")]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.triple_click()
            await loc.first.fill(date_str)

    for sel in ["button:has-text('集計')", "button:has-text('検索')"]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PWTimeout:
                pass
            break

    for sel in ["a:has-text('FANZA')", ".tab-fanza a"]:
        loc = page.locator(sel)
        if await loc.count() > 0:
            await loc.first.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PWTimeout:
                pass
            break

    print(f"[INFO] Report page: {page.url[:70]}")
    await save_screenshot(page, "06_goods_report")

    csv_btn = page.locator("a:has-text('CSVデータをダウンロード'), a:has-text('CSV')")
    if await csv_btn.count() == 0:
        print("[WARN] CSV download button not found")
        return ""

    async with page.expect_download(timeout=30_000) as dl_info:
        await csv_btn.first.click()
    download = await dl_info.value
    await download.save_as("/tmp/fanza_report.csv")

    for enc in ["shift_jis", "utf-8-sig", "utf-8"]:
        try:
            with open("/tmp/fanza_report.csv", encoding=enc) as f:
                content = f.read()
            print(f"[INFO] CSV ({enc}): {len(content)} chars")
            return content
        except UnicodeDecodeError:
            continue
    return ""


def parse_goods_csv(csv_text, target_date):
    if not csv_text.strip():
        return []
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    if not rows:
        return []
    print(f"[INFO] CSV columns: {list(rows[0].keys())}")

    date_str    = target_date.strftime("%Y/%m/%d")
    day_of_week = DAY_NAMES[target_date.weekday()]
    sales = []

    for row in rows:
        if any(v in ("合計", "期間内合計") for v in row.values() if v):
            continue

        def get(keys):
            for k in keys:
                for col in row:
                    if k in col:
                        return (row[col] or "").strip()
            return ""

        product_name = get(["商品名", "作品名", "販売商品"])
        if not product_name:
            continue

        sales.append({
            "_date":            date_str,
            "_day_of_week":     day_of_week,
            "_service":         get(["サービス"]),
            "_product_name":    product_name,
            "_sale_price":      to_decimal(get(["価格", "販売金額"])),
            "_commission_type": get(["報酬体系", "体系"]),
            "_sale_count":      int(to_decimal(get(["報酬件数", "件数"]))),
            "_revenue":         to_decimal(get(["獲得報酬", "報酬金額", "報酬"])),
        })

    print(f"[INFO] Parsed {len(sales)} products")
    return sales


async def scrape_product_page(browser, product_url):
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

        async def get_vals(label):
            for sel in [
                f"tr:has(td:text-is('{label}')) td:not(:first-child) a",
                f"tr:has(th:text-is('{label}')) td a",
            ]:
                els = await page.locator(sel).all()
                if els:
                    return [(await e.inner_text()).strip() for e in els
                            if (await e.inner_text()).strip()]
            for sel in [f"tr:has(td:text-is('{label}')) td:last-child"]:
                els = await page.locator(sel).all()
                if els:
                    t = (await els[0].inner_text()).strip()
                    return [t] if t else []
            return []

        meta["product_genre"] = await get_vals("ジャンル")
        meta["actress_name"]  = await get_vals("出演者")
        maker   = await get_vals("メーカー")
        meta["maker_name"]   = maker[0] if maker else ""
        release = await get_vals("発売日")
        meta["release_date"] = release[0] if release else ""
    except Exception as e:
        print(f"[WARN] Product page error: {e}")
    finally:
        await page.close()
    return meta


# ── メイン ───────────────────────────────────────────────────────

async def main():
    dynamo = get_dynamo()
    ensure_tables(dynamo)

    now_jst     = datetime.now(JST)
    target_date = now_jst
    print(f"[INFO] Run: {now_jst.strftime('%Y/%m/%d %H:%M')} JST")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            # Lambda 環境向けフラグ
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--no-zygote",
            ],
            executable_path=os.environ.get("CHROMIUM_PATH"),  # 未設定時は自動検出
        )
        context = await browser.new_context(
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1280, "height": 800},
            accept_downloads=True,
        )
        page = await context.new_page()

        await login_fanza(page)

        hourly = await scrape_hourly_clicks(page, target_date)
        save_hourly_clicks(dynamo, target_date.strftime("%Y/%m/%d"), hourly)

        product_urls = await collect_product_urls(page, target_date)
        csv_text     = await download_goods_report_csv(page, target_date)
        sales        = parse_goods_csv(csv_text, target_date)

        if not sales:
            print("[INFO] No sales today.")
            await browser.close()
            return

        for sale in sales:
            pname = sale["_product_name"]
            purl  = product_urls.get(pname, "")
            if not purl:
                for k, v in product_urls.items():
                    if pname[:10] in k or k[:10] in pname:
                        purl = v
                        break

            pid = extract_product_id(purl) if purl else ""
            if not pid:
                pid = re.sub(r"[^\w]", "_", pname[:20])

            meta = None
            if purl:
                meta = get_product_cache(dynamo, pid)
                if not meta:
                    meta = await scrape_product_page(browser, purl)
                    save_product_cache(dynamo, pid, meta)

            post_url = post_datetime = account_id = ""
            post_info = find_x_post(dynamo, pid)
            if post_info:
                tid         = post_info.get("tweet_id", "")
                account_id  = post_info.get("account_id", "")
                post_datetime = post_info.get("posted_at", "")
                if tid:
                    post_url = f"https://x.com/i/web/status/{tid}"

            record = {
                "sale_date":       sale["_date"],
                "product_id":      pid,
                "product_name":    pname,
                "product_url":     purl,
                "service_type":    sale["_service"],
                "sale_price":      sale["_sale_price"],
                "commission_type": sale["_commission_type"],
                "sale_count":      sale["_sale_count"],
                "revenue":         sale["_revenue"],
                "day_of_week":     sale["_day_of_week"],
                "post_url":        post_url,
                "post_datetime":   post_datetime,
                "account_id":      account_id,
            }
            if meta:
                for f in ["product_genre", "actress_name", "maker_name", "release_date"]:
                    if meta.get(f):
                        record[f] = meta[f]

            save_sale(dynamo, record)

        await browser.close()
    print(f"[INFO] Done. {len(sales)} products.")


# ── Lambda エントリーポイント ─────────────────────────────────────

def handler(event, context):
    """AWS Lambda から呼び出されるハンドラー"""
    asyncio.run(main())
    return {"statusCode": 200, "body": "Done"}


if __name__ == "__main__":
    asyncio.run(main())
