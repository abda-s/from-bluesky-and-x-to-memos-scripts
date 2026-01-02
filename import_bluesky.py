import requests
from atproto import Client, models
import time
import os
from dotenv import load_dotenv
import base64
import mimetypes

# Load configuration from .env file
load_dotenv()

# --- CONFIGURATION ---
MEMOS_URL = os.getenv("MEMOS_HOST")
MEMOS_TOKEN = os.getenv("MEMOS_ACCESS_TOKEN")
BSKY_HANDLE = os.getenv("BLUESKY_HANDLE")
BSKY_PASSWORD = os.getenv("BLUESKY_PASSWORD")
PAGINATION_DELAY = 1.0
MAX_RETRIES = 3
# ---------------------

def get_bsky_posts(client):
    """Fetch ALL posts from Bluesky with robust pagination and proper image extraction."""
    posts = []
    cursor = None
    page_count = 0
    retry_count = 0

    user_did = client.me.did
    print(f"📋 User DID: {user_did}\n")

    try:
        while True:
            page_count += 1
            print(
                f"📥 Fetching page {page_count} (cursor: {cursor[:20] if cursor else 'None'}...)..."
            )

            try:
                feed = client.get_author_feed(
                    actor=BSKY_HANDLE, limit=100, cursor=cursor
                )
                retry_count = 0

            except Exception as e:
                retry_count += 1
                print(f"⚠️  Error fetching page {page_count}: {e}")

                if retry_count >= MAX_RETRIES:
                    print(
                        f"❌ Max retries ({MAX_RETRIES}) reached. Stopping pagination."
                    )
                    break

                print(
                    f"🔄 Retrying in 5 seconds... (attempt {retry_count}/{MAX_RETRIES})"
                )
                time.sleep(5)
                continue

            posts_in_page = 0

            for item in feed.feed:
                try:
                    if getattr(item, "reason", None):
                        continue

                    parent_uri = None
                    record = item.post.record

                    # Check if this is a self-reply
                    if hasattr(record, "reply") and record.reply:
                        parent_uri = record.reply.parent.uri
                        parent_author_did = parent_uri.split("/")[2]

                        if parent_author_did != user_did:
                            continue

                    post_data = {
                        "content": record.text,
                        "created_at": record.created_at,
                        "uri": item.post.uri,
                        "parent_uri": parent_uri,
                        "images": [],
                        "is_self_reply": bool(parent_uri),
                    }

                    # Extract images from embed with proper type checking
                    if hasattr(record, "embed") and record.embed:
                        embed = record.embed
                        images = []

                        # Check for direct image embed
                        if isinstance(embed, models.AppBskyEmbedImages.Main):
                            images = embed.images
                        # Check for record with media
                        elif isinstance(embed, models.AppBskyEmbedRecordWithMedia.Main):
                            if hasattr(embed, "media") and isinstance(
                                embed.media, models.AppBskyEmbedImages.Main
                            ):
                                images = embed.media.images

                        for img in images:
                            if hasattr(img, "image") and hasattr(img.image, "ref"):
                                # FIX: Access the link attribute directly, don't convert to string
                                cid_link = img.image.ref.link
                                if cid_link:
                                    image_url = f"https://cdn.bsky.app/img/feed_fullsize/plain/{user_did}/{cid_link}@jpeg"
                                    post_data["images"].append(
                                        {
                                            "url": image_url,
                                            "alt": getattr(img, "alt", ""),
                                        }
                                    )
                                    print(
                                        f"   📷 Found image: {image_url[-40:]}"
                                    )  # Debug print

                    posts.append(post_data)
                    posts_in_page += 1

                except Exception as e:
                    print(f"   ⚠️  Skipping post due to error: {e}")
                    continue

            print(f"   ✓ Found {posts_in_page} posts on this page")
            print(f"   📊 Total collected so far: {len(posts)} posts")

            cursor = feed.cursor
            if not cursor:
                print(f"\n✅ Reached end of feed. No more pages to fetch.")
                break

            print(f"   ⏳ Waiting {PAGINATION_DELAY}s before next page...\n")
            time.sleep(PAGINATION_DELAY)

    except KeyboardInterrupt:
        print(f"\n⚠️  Interrupted by user. Collected {len(posts)} posts so far.")
    except Exception as e:
        print(f"\n❌ Unexpected error during pagination: {e}")
        print(f"   Collected {len(posts)} posts before error.")

    print(
        f"\n✅ Successfully fetched {len(posts)} posts from Bluesky across {page_count} pages.\n"
    )
    return posts


def upload_attachment_to_memo(image_url, memo_name):
    """Download image from URL and upload to Memos as attachment linked to memo."""
    temp_file = None
    try:
        print(f"  📎 Downloading image...")
        img_response = requests.get(image_url, timeout=30)

        if img_response.status_code != 200:
            print(
                f"  ⚠️  Failed to download image: HTTP {img_response.status_code}"
            )
            return None

        content_type = img_response.headers.get("Content-Type", "image/jpeg")
        ext = content_type.split("/")[-1] if "/" in content_type else "jpg"
        temp_file = f"bluesky_import_{os.urandom(4).hex()}.{ext}"

        # Save temporarily
        with open(temp_file, "wb") as f:
            f.write(img_response.content)

        file_size = os.path.getsize(temp_file)
        print(f"  ✓ Downloaded {file_size} bytes")

        # Upload to Memos
        mime_type, _ = mimetypes.guess_type(temp_file)
        if not mime_type:
            mime_type = content_type

        with open(temp_file, "rb") as f:
            encoded_content = base64.b64encode(f.read()).decode("utf-8")

            payload = {
                "filename": os.path.basename(temp_file),
                "content": encoded_content,
                "type": mime_type,
                "memo": memo_name,
            }

            headers = {
                "Authorization": f"Bearer {MEMOS_TOKEN}",
                "Content-Type": "application/json",
            }

            upload_url = f"{MEMOS_URL}/api/v1/attachments"
            print(f"  📤 Uploading to Memos...")

            response = requests.post(upload_url, json=payload, headers=headers)

            if response.status_code == 200:
                data = response.json()
                print(f"  ✅ Attachment uploaded: {data.get('name')}")
                return data.get("name")
            else:
                print(f"  ❌ Upload failed: HTTP {response.status_code}")
                print(f"  Response: {response.text[:200]}")
                return None

    except Exception as e:
        print(f"  ❌ Error processing image: {e}")
        return None

    finally:
        # Clean up temp file in all cases
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass


def post_to_memos(post_data):
    """Post a root memo to Memos with optional images."""
    url = f"{MEMOS_URL}/api/v1/memos"
    headers = {
        "Authorization": f"Bearer {MEMOS_TOKEN}",
        "Content-Type": "application/json",
    }

    content = post_data["content"]

    payload = {
        "content": content,
        "visibility": "PRIVATE",
    }

    try:
        print(f"📤 Creating memo...")
        response = requests.post(url, json=payload, headers=headers)

        if response.status_code == 200:
            memo_data = response.json()
            memo_name = memo_data.get("name") or f"memos/{memo_data.get('id')}"
            print(f"✅ Memo created: {memo_name}")

            # Backdate the memo
            patch_url = f"{MEMOS_URL}/api/v1/{memo_name}"
            patch_payload = {"createTime": post_data["created_at"]}
            patch_response = requests.patch(patch_url, json=patch_payload, headers=headers)

            if patch_response.status_code == 200:
                print(f"✅ Timestamp updated")
            else:
                print(f"⚠️  Timestamp update failed")

            # Upload images
            if post_data["images"]:
                print(f"  📎 Processing {len(post_data['images'])} image(s)...")
                for img in post_data["images"]:
                    upload_attachment_to_memo(img["url"], memo_name)

            return memo_name
        else:
            print(f"❌ Failed ({response.status_code}): {response.text[:100]}")
            return None

    except Exception as e:
        print(f"❌ Error posting to Memos: {e}")
        return None


def post_reply_as_comment(post_data, parent_memo_name, parent_comment_name=None):
    """Post a reply as a comment on a memo or as a nested comment."""
    memo_id = parent_memo_name.split("/")[-1]
    comment_url = f"{MEMOS_URL}/api/v1/memos/{memo_id}/comments"

    headers = {
        "Authorization": f"Bearer {MEMOS_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {"content": post_data["content"]}

    # If this is a nested reply, specify parent comment
    if parent_comment_name:
        parent_id = parent_comment_name.split("/")[-1]
        payload["parentId"] = parent_id

    try:
        print(f"  📤 Creating comment...")
        response = requests.post(comment_url, json=payload, headers=headers)

        if response.status_code == 200:
            comment_data = response.json()
            comment_name = comment_data.get("name")
            print(f"  ✅ Comment created: {comment_name}")

            # Backdate the comment
            patch_url = f"{MEMOS_URL}/api/v1/{comment_name}"
            patch_payload = {"createTime": post_data["created_at"]}
            patch_response = requests.patch(patch_url, json=patch_payload, headers=headers)

            if patch_response.status_code == 200:
                print(f"  ✅ Comment timestamp updated")
            else:
                print(f"  ⚠️  Comment timestamp update failed")

            # Upload images for comment
            if post_data["images"]:
                print(f"  📎 Processing {len(post_data['images'])} image(s) for comment...")
                for img in post_data["images"]:
                    upload_attachment_to_memo(img["url"], parent_memo_name)

            return comment_name
        else:
            print(f"  ❌ Failed ({response.status_code}): {response.text[:100]}")
            return None

    except Exception as e:
        print(f"  ❌ Error posting comment: {e}")
        return None


def main():
    """Main execution function."""
    if not all([MEMOS_URL, MEMOS_TOKEN, BSKY_HANDLE, BSKY_PASSWORD]):
        print("❌ Missing configuration! Please set all environment variables.")
        return

    try:
        print(f"🔵 Logging into Bluesky as {BSKY_HANDLE}...")
        client = Client()
        client.login(BSKY_HANDLE, BSKY_PASSWORD)
        print(f"✅ Successfully logged in!\n")
    except Exception as e:
        print(f"❌ Login failed: {e}")
        return

    print("=" * 60)
    print("STEP 1: FETCHING ALL POSTS FROM BLUESKY")
    print("=" * 60)

    posts = get_bsky_posts(client)

    if not posts:
        print("No posts to import. Exiting.")
        return

    # Separate root posts and reply posts
    root_posts = []
    reply_posts = []
    uri_to_post = {post["uri"]: post for post in posts}

    for post in posts:
        if post.get("is_self_reply"):
            reply_posts.append(post)
        else:
            root_posts.append(post)

    # Sort chronologically (oldest first)
    root_posts.sort(key=lambda x: x["created_at"])
    reply_posts.sort(key=lambda x: x["created_at"])

    regular = len(root_posts)
    replies = len(reply_posts)

    print("=" * 60)
    print("STEP 2: IMPORTING TO MEMOS")
    print("=" * 60)
    print(f"📊 Post breakdown:")
    print(f"   • Root posts: {regular}")
    print(f"   • Self-replies: {replies}")
    print(f"   • TOTAL: {len(posts)} posts")
    print(f"\n🚀 Starting import (oldest first)...\n")

    uri_to_memo_map = {}
    uri_to_comment_map = {}

    # Import root posts first
    for i, post in enumerate(root_posts, 1):
        print(f"[{i}/{len(root_posts)}] ", end="")
        memo_name = post_to_memos(post)
        if memo_name:
            uri_to_memo_map[post["uri"]] = memo_name
        time.sleep(0.5)

    # Import replies as comments
    if reply_posts:
        print(f"\n📬 Processing {len(reply_posts)} replies as nested comments...\n")
        for i, post in enumerate(reply_posts, 1):
            print(f"[{i}/{len(reply_posts)}] ", end="")
            parent_uri = post.get("parent_uri")

            if not parent_uri:
                print("⚠️  No parent found, skipping")
                continue

            # Check if parent is a root post or another reply
            parent_memo_name = uri_to_memo_map.get(parent_uri)
            parent_comment_name = uri_to_comment_map.get(parent_uri)

            if parent_memo_name:
                # Reply to root post
                comment_name = post_reply_as_comment(post, parent_memo_name)
                if comment_name:
                    uri_to_comment_map[post["uri"]] = comment_name
            elif parent_comment_name:
                # Reply to another comment (nested)
                # Extract memo_id from parent_comment_name (format: memos/123/comments/456)
                memo_id = parent_comment_name.split("/")[1]
                memo_name = f"memos/{memo_id}"
                comment_name = post_reply_as_comment(
                    post, memo_name, parent_comment_name
                )
                if comment_name:
                    uri_to_comment_map[post["uri"]] = comment_name
            else:
                print(
                    f"⚠️  Parent not found (might be a reply to an excluded post), skipping"
                )
                continue

            time.sleep(0.5)

    print("\n" + "=" * 60)
    print("🎉 IMPORT COMPLETE!")
    print("=" * 60)
    print(f"✅ Successfully imported {len(root_posts)} root posts")
    if reply_posts:
        print(f"✅ Created {len(reply_posts)} nested comments for self-replies")


if __name__ == "__main__":
    main()