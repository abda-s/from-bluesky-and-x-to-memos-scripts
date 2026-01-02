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
MAX_SCROLLS = int(os.getenv("X_MAX_SCROLLS", "10"))
TWITTER_AUTH_TOKEN = os.getenv("X_AUTH_TOKEN")
TWITTER_CT0 = os.getenv("X_CT0")

# Date range configuration
START_YEAR = int(os.getenv("X_START_YEAR"))
START_MONTH = int(os.getenv("X_START_MONTH"))
START_DAY = int(os.getenv("X_START_DAY"))
END_YEAR = int(os.getenv("X_END_YEAR"))
END_MONTH = int(os.getenv("X_END_MONTH"))
END_DAY = int(os.getenv("X_END_DAY"))

CHUNK_DAYS = 5  # Scrape in 5-day chunks

# Twitter search filter - Set to "false" to disable
FILTER_REPLIES = os.getenv("X_FILTER_REPLIES", "true").lower() == "true"
# ---------------------

def fetch_existing_memos():
    """Fetch all existing memos to check for duplicates."""
    print("🔍 Fetching existing memos to prevent duplicates...")
    existing = set()
    page_token = None
    page_count = 0
    max_pages = 150  # Safety limit

    headers = {"Authorization": f"Bearer {MEMOS_TOKEN}"}

    try:
        while page_count < max_pages:
            page_count += 1
            url = f"{MEMOS_URL}/api/v1/memos?pageSize=150"
            if page_token:
                url += f"&pageToken={page_token}"

            try:
                response = requests.get(url, headers=headers, timeout=30)
                response.raise_for_status()

                data = response.json()
                memos = data.get("memos", [])

                if not memos:
                    print(f"  📄 No more memos on page {page_count}")
                    break

                for memo in memos:
                    content = memo.get("content", "")
                    if content:
                        sig = hashlib.md5(content.encode()).hexdigest()
                        existing.add(sig)

                print(f"  📄 Fetched page {page_count} ({len(memos)} memos)")

                page_token = data.get("nextPageToken")
                if not page_token:
                    break

                time.sleep(0.5)

            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 400:
                    print(f"  ⚠️ Pagination ended at page {page_count} (possibly last page)")
                    break
                raise

        print(f"✅ Found {len(existing)} existing memos\n")
        return existing

    except Exception as e:
        print(f"⚠️ Error fetching existing memos: {e}")
        print("⚠️ Continuing without complete duplicate detection...\n")
        return existing  # Return what we have so far

def upload_image_to_memos(img_url, memo_name):
    """Downloads image and uploads to Memos."""
    try:
        if '?' in img_url:
            img_url = img_url.split('?')[0]
        img_url += "?format=jpg&name=large"

        resp = requests.get(img_url, timeout=15)
        resp.raise_for_status()

        filename = img_url.split("/")[-1].split("?")[0]
        if not any(filename.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
            filename += ".jpg"

        encoded = base64.b64encode(resp.content).decode("utf-8")

        payload = {
            "filename": filename,
            "content": encoded,
            "type": "image/jpeg",
            "memo": memo_name
        }

        res = requests.post(
            f"{MEMOS_URL}/api/v1/attachments",
            json=payload,
            headers={"Authorization": f"Bearer {MEMOS_TOKEN}"},
            timeout=30
        )
        res.raise_for_status()
        return res.json().get("name")

    except requests.RequestException as e:
        print(f"⚠️ Failed to upload image {img_url}: {e}")
        return None
    except Exception as e:
        print(f"⚠️ Unexpected error uploading image: {e}")
        return None

def export_cookies_to_file(context):
    """Export Playwright cookies to a file for yt-dlp."""
    try:
        cookies = context.cookies()
        cookie_file = "twitter_cookies.txt"

        with open(cookie_file, 'w') as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write("# This is a generated file! Do not edit.\n\n")

            for cookie in cookies:
                domain = cookie['domain']
                flag = "TRUE" if domain.startswith('.') else "FALSE"
                secure = "TRUE" if cookie['secure'] else "FALSE"
                expiry = str(int(cookie.get('expires', 0))) if cookie.get('expires', 0) > 0 else "0"
                path = cookie.get('path', '/')
                name = cookie['name']
                value = cookie['value']

                f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n")

        print(f"  📄 Exported cookies for yt-dlp")
        return cookie_file
    except Exception as e:
        print(f"  ⚠️ Failed to export cookies: {e}")
        return None

def download_video_with_ytdlp(tweet_url, cookie_file=None):
    """Downloads video using yt-dlp and returns the file path."""
    try:
        temp_file = f"twitter_video_{hashlib.md5(tweet_url.encode()).hexdigest()[:8]}.mp4"

        print(f"  📹 Downloading video with yt-dlp...")

        cmd = [
            'yt-dlp',
            '--quiet',
            '--no-warnings',
            '--format', 'best[ext=mp4]/best',
            '--output', temp_file,
        ]

        if cookie_file and os.path.exists(cookie_file):
            cmd.extend(['--cookies', cookie_file])
            print(f"  🔐 Using authentication cookies")

        cmd.append(tweet_url)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

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
            print(f"  ⚠️ Video file not found: {video_path}")
            return None

        file_size = os.path.getsize(video_path)
        print(f"  📤 Uploading video ({file_size / 1024 / 1024:.2f} MB)...")

        with open(video_path, 'rb') as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "filename": os.path.basename(video_path),
            "content": encoded,
            "type": "video/mp4",
            "memo": memo_name
        }

        res = requests.post(
            f"{MEMOS_URL}/api/v1/attachments",
            json=payload,
            headers={"Authorization": f"Bearer {MEMOS_TOKEN}"},
            timeout=120
        )
        res.raise_for_status()

        attachment_name = res.json().get("name")
        print(f"  ✅ Video uploaded: {attachment_name}")
        return attachment_name

    except requests.RequestException as e:
        print(f"  ⚠️ Failed to upload video: {e}")
        return None
    except Exception as e:
        print(f"  ⚠️ Unexpected error uploading video: {e}")
        return None
    finally:
        if os.path.exists(video_path):
            try:
                os.remove(video_path)
                print(f"  🗑️ Cleaned up temp file")
            except:
                pass

def create_memo(text, timestamp, images, video_url=None, cookie_file=None):
    """Creates the memo in your self-hosted instance with proper timestamp."""
    payload = {"content": text, "visibility": "PRIVATE"}
    headers = {
        "Authorization": f"Bearer {MEMOS_TOKEN}",
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(
            f"{MEMOS_URL}/api/v1/memos",
            json=payload,
            headers=headers,
            timeout=30
        )
        resp.raise_for_status()

        data = resp.json()
        memo_name = data.get("name")
        print(f"✅ Memo created: {memo_name}")

        if timestamp:
            patch_url = f"{MEMOS_URL}/api/v1/{memo_name}"
            patch_payload = {"createTime": timestamp}
            patch_response = requests.patch(
                patch_url,
                json=patch_payload,
                headers=headers,
                timeout=30
            )

            if patch_response.status_code == 200:
                print(f"  📅 Timestamp updated to {timestamp}")
            else:
                print(f"  ⚠️ Timestamp update failed: {patch_response.status_code}")

        for img_url in images:
            attachment = upload_image_to_memos(img_url, memo_name)
            if attachment:
                print(f"  📎 Attached: {attachment}")

        if video_url:
            video_path = download_video_with_ytdlp(video_url, cookie_file)
            if video_path:
                upload_video_to_memos(video_path, memo_name)

    except requests.RequestException as e:
        print(f"❌ Failed to create memo: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

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
                "sameSite": "None"
            },
            {
                "name": "ct0",
                "value": ct0_token,
                "domain": ".x.com",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax"
            }
        ],
        "origins": []
    }
    with open('auth.json', 'w') as f:
        json.dump(auth_data, f)
    print("✅ Created auth.json from tokens!")

def parse_twitter_timestamp(time_element):
    """Extract Twitter timestamp in ISO format."""
    try:
        datetime_attr = time_element.get_attribute("datetime")
        if datetime_attr:
            return datetime_attr
        return None
    except Exception as e:
        print(f"  ⚠️ Failed to parse timestamp: {e}")
        return None

def extract_tweet_url(tweet_element):
    """Extract the tweet URL from the tweet element."""
    try:
        link = tweet_element.locator('a[href*="/status/"]').first
        if link.count() > 0:
            href = link.get_attribute("href")
            if href:
                if href.startswith('/'):
                    return f"https://x.com{href}"
                return href
    except Exception as e:
        print(f"  ⚠️ Failed to extract tweet URL: {e}")
    return None

def is_tweet_in_date_range(timestamp_str, start_date, end_date):
    """Verify tweet is actually within the expected date range."""
    try:
        tweet_date = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        tweet_date = tweet_date.replace(tzinfo=None)
        return start_date <= tweet_date < end_date
    except Exception as e:
        print(f"  ⚠️ Error verifying date: {e}")
        return True

def generate_date_ranges(start_date, end_date, chunk_days=4):
    """Generate list of (start, end) date tuples in chunks."""
    ranges = []
    current = start_date

    while current < end_date:
        chunk_end = min(current + timedelta(days=chunk_days), end_date)
        ranges.append((current, chunk_end))
        current = chunk_end

    return ranges

def wait_for_page_load(page, timeout=30):
    """Wait for Twitter page to stabilize with multiple fallback strategies."""
    strategies = [
        ('article[data-testid="tweet"]', "tweets"),
        ('div[data-testid="primaryColumn"]', "main column"),
        ('main[role="main"]', "main content"),
    ]
    
    for selector, name in strategies:
        try:
            page.wait_for_selector(selector, timeout=timeout * 1000, state="visible")
            print(f"✅ Page loaded (detected: {name})")
            time.sleep(2)  # Additional stabilization
            return True
        except Exception:
            continue
    
    return False

def scrape_date_range(page, start_date, end_date, existing_memos, cookie_file=None):
    """Scrape tweets from a specific date range."""
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')

    search_query = f"from:{TARGET_USERNAME} since:{start_str} until:{end_str}"

    if FILTER_REPLIES:
        search_query += " -filter:replies"

    search_url = f"https://x.com/search?q={requests.utils.quote(search_query)}&src=typed_query&f=live"

    print(f"\n{'='*60}")
    print(f"📅 SCRAPING: {start_str} → {end_str}")
    print(f"🔗 Query: {search_query}")
    if FILTER_REPLIES:
        print(f"🔍 Filter: -filter:replies (native)")
    print(f"{'='*60}\n")

    # Retry navigation with better error handling
    max_retries = 3
    page_loaded = False
    
    for attempt in range(max_retries):
        try:
            print(f"🌐 Loading page (attempt {attempt + 1}/{max_retries})...")
            page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            
            # More flexible waiting strategy
            if wait_for_page_load(page, timeout=30):
                page_loaded = True
                break
            else:
                print(f"⚠️ Page elements not found on attempt {attempt + 1}")
                
        except Exception as e:
            print(f"⚠️ Navigation attempt {attempt + 1} failed: {e}")
            
        if attempt < max_retries - 1:
            wait_time = (attempt + 1) * 5
            print(f"🔄 Retrying in {wait_time}s...")
            time.sleep(wait_time)

    if not page_loaded:
        print(f"❌ Failed to load page after {max_retries} attempts")
        
        # Check for error messages
        try:
            error_text = page.locator('text=/something went wrong/i').first
            if error_text.count() > 0:
                print("⚠️ Twitter returned 'Something went wrong' message")
        except:
            pass
            
        return 0, 0, 0

    # Check if there are actually any tweets
    tweets = page.locator('article[data-testid="tweet"]').all()
    if len(tweets) == 0:
        print("⚠️ No tweets found in this date range")
        
        # Check for "no results" message
        try:
            no_results = page.locator('text=/no results/i, text=/try searching/i').first
            if no_results.count() > 0:
                print("📭 Twitter confirms no tweets in this range")
        except:
            pass
            
        return 0, 0, 0

    seen_tweets = set()
    new_tweets = 0
    duplicate_count = 0
    date_mismatch_count = 0
    scroll_attempts = 0
    no_new_scrolls = 0

    while scroll_attempts < MAX_SCROLLS:
        tweets = page.locator('article[data-testid="tweet"]').all()
        print(f"👀 Scanning {len(tweets)} visible tweets...")

        found_new = False
        for tweet in tweets:
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
                    continue

                if len(texts) > 1:
                    text = f"{texts[0]}\n\n---\n\n{texts[1]}"
                else:
                    text = texts[0]

                time_element = tweet.locator('time').first
                timestamp = None
                if time_element.count() > 0:
                    timestamp = parse_twitter_timestamp(time_element)

                if timestamp:
                    if not is_tweet_in_date_range(timestamp, start_date, end_date):
                        date_mismatch_count += 1
                        continue

                content_sig = hashlib.md5(text.encode()).hexdigest()
                if content_sig in existing_memos:
                    duplicate_count += 1
                    continue

                imgs_el = tweet.locator('img[src*="pbs.twimg.com/media"]')
                images = [
                    imgs_el.nth(i).get_attribute("src")
                    for i in range(imgs_el.count())
                ]

                video_url = None
                video_element = tweet.locator('video')
                if video_element.count() > 0:
                    tweet_url = extract_tweet_url(tweet)
                    if tweet_url:
                        video_url = tweet_url
                        print(f"  🎬 Detected video in tweet")

                sig = hashlib.md5(
                    f"{text}{timestamp}{len(images)}{bool(video_url)}".encode()
                ).hexdigest()

                if sig not in seen_tweets:
                    seen_tweets.add(sig)
                    existing_memos.add(content_sig)
                    found_new = True
                    new_tweets += 1
                    print(f"📥 New [{new_tweets}]: {text[:50].replace(chr(10), ' ')}...")
                    if timestamp:
                        print(f"  📅 Date: {timestamp}")
                    create_memo(text, timestamp, images, video_url, cookie_file)
                    time.sleep(0.5)

            except Exception as e:
                print(f"⚠️ Error processing tweet: {e}")
                continue

        if not found_new:
            no_new_scrolls += 1
            print(f"🛑 No new tweets in this scroll ({no_new_scrolls}/3)")
            if no_new_scrolls >= 3:
                print("🏁 Stopping - no new tweets for 3 scrolls")
                break
        else:
            no_new_scrolls = 0

        # Check if we've reached the end
        try:
            end_message = page.locator('text=/you\'re all caught up/i, text=/nothing more to load/i').first
            if end_message.count() > 0:
                print("🏁 Reached end of timeline")
                break
        except:
            pass

        page.evaluate("window.scrollBy(0, window.innerHeight)")
        print("⬇️ Scrolling...")
        time.sleep(3)
        scroll_attempts += 1

    print(f"\n📊 Date Range Summary:")
    print(f"  ✅ New tweets: {new_tweets}")
    print(f"  ⏭️  Duplicates skipped: {duplicate_count}")
    if date_mismatch_count > 0:
        print(f"  🚫 Date mismatches: {date_mismatch_count}")

    return new_tweets, duplicate_count, 0

def scrape_x():
    with sync_playwright() as p:
        auth_file = 'auth.json'

        if TWITTER_AUTH_TOKEN and TWITTER_CT0:
            create_auth_json(TWITTER_AUTH_TOKEN, TWITTER_CT0)
        else:
            print("❌ TWITTER_AUTH_TOKEN and TWITTER_CT0 must be set in .env")
            return

        if not os.path.exists(auth_file):
            print(f"❌ Auth file not found at {auth_file}")
            print("⚠️ Please verify your TWITTER_AUTH_TOKEN and TWITTER_CT0 are correct")
            return

        start_date = datetime(START_YEAR, START_MONTH, START_DAY)
        end_date = datetime(END_YEAR, END_MONTH, END_DAY)

        # Validate date range
        if start_date >= end_date:
            print("❌ Start date must be before end date")
            return

        print(f"\n{'='*60}")
        print(f"📅 SCRAPING CONFIGURATION")
        print(f"{'='*60}")
        print(f"Start: {start_date.strftime('%Y-%m-%d')}")
        print(f"End: {end_date.strftime('%Y-%m-%d')}")
        print(f"Chunk size: {CHUNK_DAYS} days")
        print(f"Mode: ORIGINAL POSTS ONLY")
        if FILTER_REPLIES:
            print(f"🔍 Twitter filter: -filter:replies (native)")
        print(f"{'='*60}\n")

        date_ranges = generate_date_ranges(start_date, end_date, CHUNK_DAYS)
        print(f"📆 Generated {len(date_ranges)} date ranges to scrape\n")

        existing_memos = fetch_existing_memos()

        print("🕵️ Launching headless browser...")
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox'
            ]
        )
        context = browser.new_context(
            storage_state=auth_file,
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        # Set longer default timeout
        context.set_default_timeout(10000)
        page = context.new_page()

        cookie_file = export_cookies_to_file(context)

        total_new = 0
        total_duplicates = 0
        failed_ranges = []

        for idx, (range_start, range_end) in enumerate(date_ranges, 1):
            print(f"\n🔄 Progress: {idx}/{len(date_ranges)} ranges")
            try:
                new_count, dup_count, _ = scrape_date_range(
                    page, range_start, range_end, existing_memos, cookie_file
                )
                total_new += new_count
                total_duplicates += dup_count
                
                if new_count == 0 and dup_count == 0:
                    failed_ranges.append(f"{range_start.strftime('%Y-%m-%d')} → {range_end.strftime('%Y-%m-%d')}")
                    
            except Exception as e:
                print(f"❌ Error scraping range: {e}")
                failed_ranges.append(f"{range_start.strftime('%Y-%m-%d')} → {range_end.strftime('%Y-%m-%d')}")

            if idx < len(date_ranges):
                print(f"\n⏳ Waiting 5 seconds before next range...")
                time.sleep(5)

        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
                print(f"🗑️ Cleaned up cookie file")
            except:
                pass

        print(f"\n{'='*60}")
        print(f"🎉 SCRAPING COMPLETE!")
        print(f"{'='*60}")
        print(f"✅ Total new tweets imported: {total_new}")
        print(f"⏭️  Total duplicates skipped: {total_duplicates}")
        print(f"📅 Date range: {start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}")
        print(f"📦 Processed {len(date_ranges)} chunks ({CHUNK_DAYS} days each)")
        
        if failed_ranges:
            print(f"\n⚠️ Failed/empty ranges ({len(failed_ranges)}):")
            for fr in failed_ranges:
                print(f"  - {fr}")

        browser.close()

if __name__ == "__main__":
    if not MEMOS_TOKEN:
        print("❌ MEMOS_TOKEN not set in .env")
    elif not TARGET_USERNAME:
        print("❌ TWITTER_USERNAME not set in .env")
    else:
        scrape_x()
