import os
import time
import hashlib
import base64
import requests
import json
import subprocess
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

# --- CONFIGURATION ---
MEMOS_URL = os.getenv("MEMOS_HOST")
MEMOS_TOKEN = os.getenv("MEMOS_ACCESS_TOKEN")
TARGET_USERNAME = os.getenv("X_USERNAME")
MAX_SCROLLS = int(os.getenv("X_MAX_SCROLLS", "50"))
TWITTER_AUTH_TOKEN = os.getenv("X_AUTH_TOKEN")
TWITTER_CT0 = os.getenv("X_CT0")

# Date range for fallback search
START_YEAR = int(os.getenv("X_START_YEAR", "2015"))
START_MONTH = int(os.getenv("X_START_MONTH", "1"))
START_DAY = int(os.getenv("X_START_DAY", "1"))
CHUNK_DAYS = 30  # Search chunk size in days

FILTER_REPLIES = os.getenv("X_FILTER_REPLIES", "true").lower() == "true"
# ---------------------


def fetch_existing_memos():
    """Fetch all existing memos to check for duplicates."""
    print("🔍 Fetching existing memos to prevent duplicates...")
    existing = set()
    page_token = None
    page_count = 0
    max_pages = 100

    headers = {"Authorization": f"Bearer {MEMOS_TOKEN}"}

    try:
        while page_count < max_pages:
            page_count += 1
            url = f"{MEMOS_URL}/api/v1/memos?pageSize=100"
            if page_token:
                url += f"&pageToken={page_token}"

            try:
                response = requests.get(url, headers=headers, timeout=30)
                response.raise_for_status()

                data = response.json()
                memos = data.get("memos", [])

                if not memos:
                    break

                for memo in memos:
                    content = memo.get("content", "")
                    if content:
                        sig = hashlib.md5(content.encode()).hexdigest()
                        existing.add(sig)

                print(f"  📄 Page {page_count}: {len(memos)} memos")

                page_token = data.get("nextPageToken")
                if not page_token:
                    break

                time.sleep(0.5)

            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 400:
                    break
                raise

        print(f"✅ Found {len(existing)} existing memos\n")
        return existing

    except Exception as e:
        print(f"⚠️ Error fetching existing memos: {e}\n")
        return existing


def upload_image_to_memos(img_url, memo_name):
    """Downloads image and uploads to Memos."""
    try:
        if "?" in img_url:
            img_url = img_url.split("?")[0]
        img_url += "?format=jpg&name=large"

        resp = requests.get(img_url, timeout=15)
        resp.raise_for_status()

        filename = img_url.split("/")[-1].split("?")[0]
        if not any(
            filename.endswith(ext)
            for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]
        ):
            filename += ".jpg"

        encoded = base64.b64encode(resp.content).decode("utf-8")

        payload = {
            "filename": filename,
            "content": encoded,
            "type": "image/jpeg",
            "memo": memo_name,
        }

        res = requests.post(
            f"{MEMOS_URL}/api/v1/attachments",
            json=payload,
            headers={"Authorization": f"Bearer {MEMOS_TOKEN}"},
            timeout=30,
        )
        res.raise_for_status()
        return res.json().get("name")

    except Exception as e:
        print(f"⚠️ Failed to upload image: {e}")
        return None


def export_cookies_to_file(context):
    """Export Playwright cookies to a file for yt-dlp."""
    try:
        cookies = context.cookies()
        cookie_file = "twitter_cookies.txt"

        with open(cookie_file, "w") as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write("# This is a generated file! Do not edit.\n\n")

            for cookie in cookies:
                domain = cookie["domain"]
                flag = "TRUE" if domain.startswith(".") else "FALSE"
                secure = "TRUE" if cookie["secure"] else "FALSE"
                expiry = (
                    str(int(cookie.get("expires", 0)))
                    if cookie.get("expires", 0) > 0
                    else "0"
                )
                path = cookie.get("path", "/")
                name = cookie["name"]
                value = cookie["value"]

                f.write(
                    f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n"
                )

        print(f"  📄 Exported cookies for yt-dlp")
        return cookie_file
    except Exception as e:
        print(f"  ⚠️ Failed to export cookies: {e}")
        return None


def download_video_with_ytdlp(tweet_url, cookie_file=None):
    """Downloads video using yt-dlp and returns the file path."""
    try:
        temp_file = (
            f"twitter_video_{hashlib.md5(tweet_url.encode()).hexdigest()[:8]}.mp4"
        )

        print(f"  📹 Downloading video with yt-dlp...")

        cmd = [
            "yt-dlp",
            "--quiet",
            "--no-warnings",
            "--format",
            "best[ext=mp4]/best",
            "--output",
            temp_file,
        ]

        if cookie_file and os.path.exists(cookie_file):
            cmd.extend(["--cookies", cookie_file])
            print(f"  🔐 Using authentication cookies")

        cmd.append(tweet_url)

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60
        )

        if result.returncode == 0 and os.path.exists(temp_file):
            print(f"  ✅ Video downloaded: {temp_file}")
            return temp_file
        else:
            print(f"  ⚠️ yt-dlp failed: {result.stderr}")
            return None

    except subprocess.TimeoutExpired:
        print(f"  ⚠️ Video download timeout")
        return None
    except Exception as e:
        print(f"  ⚠️ Error downloading video: {e}")
        return None


def upload_video_to_memos(video_path, memo_name):
    """Uploads video file to Memos as attachment."""
    try:
        if not os.path.exists(video_path):
            return None

        file_size = os.path.getsize(video_path)
        print(
            f"  📤 Uploading video ({file_size / 1024 / 1024:.2f} MB)..."
        )

        with open(video_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "filename": os.path.basename(video_path),
            "content": encoded,
            "type": "video/mp4",
            "memo": memo_name,
        }

        res = requests.post(
            f"{MEMOS_URL}/api/v1/attachments",
            json=payload,
            headers={"Authorization": f"Bearer {MEMOS_TOKEN}"},
            timeout=120,
        )
        res.raise_for_status()

        attachment_name = res.json().get("name")
        print(f"  ✅ Video uploaded: {attachment_name}")
        return attachment_name

    except Exception as e:
        print(f"  ⚠️ Failed to upload video: {e}")
        return None
    finally:
        if os.path.exists(video_path):
            try:
                os.remove(video_path)
                print(f"  🗑️ Cleaned up temp file")
            except:
                pass


def create_memo(
    text, timestamp, images, video_url=None, cookie_file=None
):
    """Creates the memo with proper timestamp and attachments."""
    payload = {"content": text, "visibility": "PRIVATE"}
    headers = {
        "Authorization": f"Bearer {MEMOS_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            f"{MEMOS_URL}/api/v1/memos",
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()

        data = resp.json()
        memo_name = data.get("name")
        print(f"✅ Memo created: {memo_name}")

        if timestamp:
            patch_url = f"{MEMOS_URL}/api/v1/{memo_name}"
            patch_payload = {"createTime": timestamp}
            patch_response = requests.patch(
                patch_url, json=patch_payload, headers=headers, timeout=30
            )

            if patch_response.status_code == 200:
                print(f"  📅 Timestamp: {timestamp}")

        for img_url in images:
            attachment = upload_image_to_memos(img_url, memo_name)
            if attachment:
                print(f"  📎 Image attached")

        if video_url:
            video_path = download_video_with_ytdlp(video_url, cookie_file)
            if video_path:
                upload_video_to_memos(video_path, memo_name)

    except Exception as e:
        print(f"❌ Failed to create memo: {e}")


def create_auth_json(auth_token, ct0_token):
    """Creates auth.json from tokens."""
    auth_data = {
        "cookies": [
            {
                "name": "auth_token",
                "value": auth_token,
                "domain": ".x.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            },
            {
                "name": "ct0",
                "value": ct0_token,
                "domain": ".x.com",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            },
        ],
        "origins": [],
    }
    with open("auth.json", "w") as f:
        json.dump(auth_data, f)
    print("✅ Created auth.json from tokens!")


def parse_twitter_timestamp(time_element):
    """Extract Twitter timestamp in ISO format."""
    try:
        datetime_attr = time_element.get_attribute("datetime")
        return datetime_attr if datetime_attr else None
    except Exception:
        return None


def extract_tweet_url(tweet_element):
    """Extract the tweet URL from the tweet element."""
    try:
        link = tweet_element.locator('a[href*="/status/"]').first
        if link.count() > 0:
            href = link.get_attribute("href")
            if href:
                if href.startswith("/"):
                    return f"https://x.com{href}"
                return href
    except Exception:
        pass
    return None


def process_tweet(tweet, existing_memos, cookie_file=None):
    """Process a single tweet element and return its data."""
    try:
        text_elements = tweet.locator('div[data-testid="tweetText"]')
        text_count = text_elements.count()

        texts = []
        for i in range(text_count):
            try:
                txt = text_elements.nth(i).inner_text()
                if txt:
                    texts.append(txt)
            except:
                continue

        if not texts:
            return None

        if len(texts) > 1:
            text = f"{texts[0]}\n\n---\n\n{texts[1]}"
        else:
            text = texts[0]

        # Check for duplicate
        content_sig = hashlib.md5(text.encode()).hexdigest()
        if content_sig in existing_memos:
            return None

        time_element = tweet.locator("time").first
        timestamp = None
        if time_element.count() > 0:
            timestamp = parse_twitter_timestamp(time_element)

        imgs_el = tweet.locator('img[src*="pbs.twimg.com/media"]')
        images = [
            imgs_el.nth(i).get_attribute("src")
            for i in range(imgs_el.count())
        ]

        video_url = None
        video_element = tweet.locator("video")
        if video_element.count() > 0:
            tweet_url = extract_tweet_url(tweet)
            if tweet_url:
                video_url = tweet_url

        sig = hashlib.md5(
            f"{text}{timestamp}{len(images)}{bool(video_url)}".encode()
        ).hexdigest()

        return {
            "text": text,
            "timestamp": timestamp,
            "images": images,
            "video_url": video_url,
            "sig": sig,
            "content_sig": content_sig,
        }

    except Exception as e:
        print(f"⚠️ Error processing tweet: {e}")
        return None


def scrape_profile_timeline(
    page, existing_memos, cookie_file=None, max_scrolls=50
):
    """Fast scrolling method for recent tweets."""
    print(f"\n{'='*60}")
    print(f"🔄 PHASE 1: PROFILE TIMELINE SCROLLING")
    print(f"{'='*60}\n")

    print(f"🔗 Loading profile: https://x.com/{TARGET_USERNAME}")
    page.goto(
        f"https://x.com/{TARGET_USERNAME}", wait_until="networkidle"
    )

    try:
        page.wait_for_selector(
            'article[data-testid="tweet"]', timeout=15000
        )
        print("✅ Profile loaded!")
    except Exception as e:
        print(f"❌ Failed to load profile: {e}")
        return 0, None

    seen_tweets = set()
    new_tweets = 0
    duplicate_count = 0
    scroll_attempts = 0
    no_new_scrolls = 0
    oldest_timestamp = None

    while scroll_attempts < max_scrolls:
        tweets = page.locator('article[data-testid="tweet"]').all()
        print(f"👀 Scanning {len(tweets)} visible tweets...")

        found_new = False
        for tweet in tweets:
            tweet_data = process_tweet(tweet, existing_memos, cookie_file)

            if not tweet_data:
                duplicate_count += 1
                continue

            if tweet_data["sig"] in seen_tweets:
                continue

            seen_tweets.add(tweet_data["sig"])
            existing_memos.add(tweet_data["content_sig"])
            found_new = True
            new_tweets += 1

            # Track oldest timestamp
            if tweet_data["timestamp"]:
                tweet_date = datetime.fromisoformat(
                    tweet_data["timestamp"].replace("Z", "+00:00")
                )
                if (
                    oldest_timestamp is None
                    or tweet_date < oldest_timestamp
                ):
                    oldest_timestamp = tweet_date

            print(
                f"📥 New [{new_tweets}]: {tweet_data['text'][:50].replace(chr(10), ' ')}..."
            )
            if tweet_data["timestamp"]:
                print(f"  📅 Date: {tweet_data['timestamp']}")

            create_memo(
                tweet_data["text"],
                tweet_data["timestamp"],
                tweet_data["images"],
                tweet_data["video_url"],
                cookie_file,
            )
            time.sleep(0.5)

        if not found_new:
            no_new_scrolls += 1
            print(f"🛑 No new tweets ({no_new_scrolls}/3)")
            if no_new_scrolls >= 3:
                print("🏁 Timeline scrolling complete")
                break
        else:
            no_new_scrolls = 0

        page.evaluate("window.scrollBy(0, window.innerHeight)")
        print("⬇️ Scrolling...")
        time.sleep(3)
        scroll_attempts += 1

    print(f"\n📊 Phase 1 Summary:")
    print(f"  ✅ New tweets: {new_tweets}")
    print(f"  ⏭️  Duplicates skipped: {duplicate_count}")
    if oldest_timestamp:
        print(
            f"  📅 Oldest tweet: {oldest_timestamp.strftime('%Y-%m-%d')}"
        )

    return new_tweets, oldest_timestamp


def scrape_date_range(
    page, start_date, end_date, existing_memos, cookie_file=None
):
    """Scrape tweets from a specific date range using search."""
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    search_query = (
        f"from:{TARGET_USERNAME} since:{start_str} until:{end_str}"
    )

    if FILTER_REPLIES:
        search_query += " -filter:replies"

    search_url = f"https://x.com/search?q={requests.utils.quote(search_query)}&src=typed_query&f=live"

    print(f"\n📅 Scraping: {start_str} → {end_str}")

    max_retries = 3
    for attempt in range(max_retries):
        try:
            page.goto(
                search_url, wait_until="domcontentloaded", timeout=60000
            )
            page.wait_for_selector(
                'article[data-testid="tweet"]', timeout=15000
            )
            break
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"⚠️ Retry {attempt + 1}/{max_retries}...")
                time.sleep(5)
            else:
                print(f"❌ Failed to load search results")
                return 0, 0

    seen_tweets = set()
    new_tweets = 0
    duplicate_count = 0
    scroll_attempts = 0
    no_new_scrolls = 0

    while scroll_attempts < MAX_SCROLLS:
        tweets = page.locator('article[data-testid="tweet"]').all()

        found_new = False
        for tweet in tweets:
            tweet_data = process_tweet(tweet, existing_memos, cookie_file)

            if not tweet_data:
                duplicate_count += 1
                continue

            if tweet_data["sig"] in seen_tweets:
                continue

            seen_tweets.add(tweet_data["sig"])
            existing_memos.add(tweet_data["content_sig"])
            found_new = True
            new_tweets += 1

            print(
                f"📥 New [{new_tweets}]: {tweet_data['text'][:50].replace(chr(10), ' ')}..."
            )

            create_memo(
                tweet_data["text"],
                tweet_data["timestamp"],
                tweet_data["images"],
                tweet_data["video_url"],
                cookie_file,
            )
            time.sleep(0.5)

        if not found_new:
            no_new_scrolls += 1
            if no_new_scrolls >= 3:
                break
        else:
            no_new_scrolls = 0

        page.evaluate("window.scrollBy(0, window.innerHeight)")
        time.sleep(3)
        scroll_attempts += 1

    return new_tweets, duplicate_count


def generate_date_ranges(start_date, end_date, chunk_days=5):
    """Generate list of (start, end) date tuples in chunks."""
    ranges = []
    current = start_date

    while current < end_date:
        chunk_end = min(current + timedelta(days=chunk_days), end_date)
        ranges.append((current, chunk_end))
        current = chunk_end

    return ranges


def scrape_historical(
    page, oldest_date, existing_memos, cookie_file=None
):
    """Scrape historical tweets using date range search."""
    print(f"\n{'='*60}")
    print(f"🔄 PHASE 2: HISTORICAL SEARCH")
    print(f"{'='*60}\n")

    start_date = datetime(START_YEAR, START_MONTH, START_DAY)

    if oldest_date:
        end_date = oldest_date.replace(tzinfo=None)
        print(
            f"📅 Searching from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
        )
    else:
        print("⚠️ No oldest date found, skipping historical search")
        return 0, 0

    if start_date >= end_date:
        print("✅ No historical tweets to search")
        return 0, 0

    date_ranges = generate_date_ranges(start_date, end_date, CHUNK_DAYS)
    print(f"📆 Generated {len(date_ranges)} date ranges\n")

    total_new = 0
    total_duplicates = 0

    for idx, (range_start, range_end) in enumerate(date_ranges, 1):
        print(f"\n🔄 Progress: {idx}/{len(date_ranges)}")
        try:
            new_count, dup_count = scrape_date_range(
                page, range_start, range_end, existing_memos, cookie_file
            )
            total_new += new_count
            total_duplicates += dup_count

        except Exception as e:
            print(f"❌ Error scraping range: {e}")

        if idx < len(date_ranges):
            time.sleep(5)

    print(f"\n📊 Phase 2 Summary:")
    print(f"  ✅ New tweets: {total_new}")
    print(f"  ⏭️  Duplicates skipped: {total_duplicates}")

    return total_new, total_duplicates


def main():
    if not MEMOS_TOKEN:
        print("❌ MEMOS_TOKEN not set in .env")
        return
    if not TARGET_USERNAME:
        print("❌ TWITTER_USERNAME not set in .env")
        return

    # Create auth
    if TWITTER_AUTH_TOKEN and TWITTER_CT0:
        create_auth_json(TWITTER_AUTH_TOKEN, TWITTER_CT0)
    else:
        print(
            "❌ TWITTER_AUTH_TOKEN and TWITTER_CT0 must be set in .env"
        )
        return

    if not os.path.exists("auth.json"):
        print("❌ Auth file not found")
        return

    # Fetch existing memos
    existing_memos = fetch_existing_memos()

    print("🕵️ Launching headless browser...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            storage_state="auth.json",
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        context.set_default_timeout(60000)
        page = context.new_page()

        cookie_file = export_cookies_to_file(context)

        # Phase 1: Fast timeline scrolling
        timeline_count, oldest_timestamp = scrape_profile_timeline(
            page, existing_memos, cookie_file, MAX_SCROLLS
        )

        # Phase 2: Historical search if needed
        historical_count = 0
        if oldest_timestamp:
            historical_count, _ = scrape_historical(
                page, oldest_timestamp, existing_memos, cookie_file
            )

        # Cleanup
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
                print("\n🗑️ Cleaned up cookie file")
            except:
                pass

        print(f"\n{'='*60}")
        print(f"🎉 SCRAPING COMPLETE!")
        print(f"{'='*60}")
        print(f"✅ Timeline tweets: {timeline_count}")
        print(f"✅ Historical tweets: {historical_count}")
        print(f"✅ Total imported: {timeline_count + historical_count}")
        print(f"{'='*60}\n")

        browser.close()


if __name__ == "__main__":
    main()
