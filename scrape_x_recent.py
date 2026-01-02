import os
import time
import hashlib
import base64
import requests
import json
import subprocess
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
MEMOS_URL = os.getenv("MEMOS_HOST")
MEMOS_TOKEN = os.getenv("MEMOS_ACCESS_TOKEN")
TARGET_USERNAME = os.getenv("X_USERNAME")
MAX_SCROLLS = int(os.getenv("X_MAX_SCROLLS"))
TWITTER_AUTH_TOKEN = os.getenv("X_AUTH_TOKEN")
TWITTER_CT0 = os.getenv("X_CT0")
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
                    print(f"  ⚠️ Pagination ended at page {page_count}")
                    break
                raise

        print(f"✅ Found {len(existing)} existing memos\n")
        return existing

    except Exception as e:
        print(f"⚠️ Error fetching existing memos: {e}")
        print("⚠️ Continuing without complete duplicate detection...\n")
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
    """Creates the memo in your self-hosted instance with proper timestamp."""
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
                patch_url,
                json=patch_payload,
                headers=headers,
                timeout=30,
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


def scrape_x():
    with sync_playwright() as p:
        auth_file = "auth.json"

        # Create auth.json from tokens if provided
        if TWITTER_AUTH_TOKEN and TWITTER_CT0 and not os.path.exists(auth_file):
            create_auth_json(TWITTER_AUTH_TOKEN, TWITTER_CT0)

        # Browser login fallback
        if not os.path.exists(auth_file):
            print("⚠️ No login found. Opening REAL browser...")
            print("👉 LOG IN TO X MANUALLY, then wait for home feed to load.")
            print("👉 CLOSE THE BROWSER WINDOW when done.")

            browser = p.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            page.goto("https://x.com/i/flow/login")

            try:
                page.wait_for_url("**/home", timeout=300000)
                print("✅ Login detected! Saving session...")
                time.sleep(5)
            except:
                print("⚠️ Timeout or browser closed")

            context.storage_state(path=auth_file)
            browser.close()
            print("✅ Login saved! Re-run the script.")
            return

        # --- AUTOMATED SCRAPING ---
        print("🕵️ Launching headless browser...")
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            storage_state=auth_file,
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        # Export cookies for video downloads
        cookie_file = export_cookies_to_file(context)

        # Fetch existing memos for duplicate detection
        existing_memos = fetch_existing_memos()

        print(f"🔗 Going to profile: {TARGET_USERNAME}")
        page.goto(
            f"https://x.com/{TARGET_USERNAME}", wait_until="networkidle"
        )

        try:
            page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
            print("✅ Tweets loaded!")
        except Exception as e:
            print(f"❌ Failed to load tweets: {e}")
            page.screenshot(path="error_page.png", full_page=True)
            print("📸 Screenshot saved to error_page.png")
            browser.close()
            return

        seen_tweets = set()
        new_tweets = 0
        duplicate_count = 0
        scroll_attempts = 0

        while scroll_attempts < MAX_SCROLLS:
            tweets = page.locator('article[data-testid="tweet"]').all()
            print(f"👀 Scanning {len(tweets)} visible tweets...")

            found_new = False
            for tweet in tweets:
                try:
                    # Handle reposts/quotes: collect ALL tweetText elements
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

                    # If multiple texts (repost/quote), combine them
                    if len(texts) > 1:
                        text = f"{texts[0]}\n\n---\n\n{texts[1]}"
                        print(f"  🔁 Detected repost/quote")
                    else:
                        text = texts[0]

                    # Check for duplicate
                    content_sig = hashlib.md5(text.encode()).hexdigest()
                    if content_sig in existing_memos:
                        duplicate_count += 1
                        continue

                    # Extract timestamp from <time> element
                    time_element = tweet.locator("time").first
                    timestamp = None
                    if time_element.count() > 0:
                        timestamp = parse_twitter_timestamp(time_element)

                    # Extract images
                    imgs_el = tweet.locator('img[src*="pbs.twimg.com/media"]')
                    images = [
                        imgs_el.nth(i).get_attribute("src")
                        for i in range(imgs_el.count())
                    ]

                    # Check for video
                    video_url = None
                    video_element = tweet.locator("video")
                    if video_element.count() > 0:
                        # Get tweet URL for yt-dlp
                        tweet_url = extract_tweet_url(tweet)
                        if tweet_url:
                            video_url = tweet_url
                            print(f"  🎬 Detected video in tweet")

                    # Create unique signature
                    sig = hashlib.md5(
                        f"{text}{timestamp}{len(images)}{bool(video_url)}".encode()
                    ).hexdigest()

                    if sig not in seen_tweets:
                        seen_tweets.add(sig)
                        existing_memos.add(content_sig)
                        found_new = True
                        new_tweets += 1
                        print(
                            f"📥 New [{new_tweets}]: {text[:50].replace(chr(10), ' ')}..."
                        )
                        if timestamp:
                            print(f"  📅 Date: {timestamp}")
                        create_memo(
                            text, timestamp, images, video_url, cookie_file
                        )
                        time.sleep(0.5)

                except Exception as e:
                    print(f"⚠️ Error processing tweet: {e}")
                    continue

            if not found_new:
                print("🛑 No new tweets in this scroll.")

            page.evaluate("window.scrollBy(0, window.innerHeight)")
            print("⬇️ Scrolling...")
            time.sleep(3)
            scroll_attempts += 1

        print(f"\n✅ Finished! Imported {new_tweets} new tweets, skipped {duplicate_count} duplicates.")
        
        # Cleanup
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
                print("🗑️ Cleaned up cookie file")
            except:
                pass

        browser.close()


if __name__ == "__main__":
    if not MEMOS_TOKEN:
        print("❌ MEMOS_TOKEN not set in .env")
    elif not TARGET_USERNAME:
        print("❌ TWITTER_USERNAME not set in .env")
    else:
        scrape_x()