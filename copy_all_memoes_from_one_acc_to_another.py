import os
import time
import hashlib
import base64
import requests
import json
import logging
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

# --- CONFIGURATION ---
SOURCE_MEMOS_URL = os.getenv("MIGRATION_SOURCE_HOST")
SOURCE_MEMOS_TOKEN = os.getenv("MIGRATION_SOURCE_TOKEN")
DEST_MEMOS_URL = os.getenv("MIGRATION_DEST_HOST")
DEST_MEMOS_TOKEN = os.getenv("MIGRATION_DEST_TOKEN")
SOURCE_HANDLE = os.getenv("MIGRATION_ADD_PREFIX_HANDLE", "")
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "100"))
RATE_LIMIT_DELAY = float(os.getenv("RATE_LIMIT_DELAY", "0.1"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
# Optional: Filter memos by handle (e.g. "myhandle"). 
# If set, only memos starting with "@{MIGRATION_FILTER_HANDLE}:" will be copied, 
# and the prefix will be removed.
FILTER_HANDLE = os.getenv("MIGRATION_FILTER_HANDLE")
# Fixed: DRY_RUN logic was inverted
DRY_RUN = False
# ---------------------

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def create_session_with_retries() -> requests.Session:
    """Create a requests session with automatic retries."""
    session = requests.Session()
    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PATCH"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def fetch_all_memos(
    source_url: str, source_token: str, session: requests.Session
) -> List[Dict[str, Any]]:
    """Fetch all memos from source instance with pagination."""
    logger.info("🔍 Fetching all memos from source instance...")
    all_memos = []
    page_token = None
    page_count = 0
    max_pages = 1000

    headers = {"Authorization": f"Bearer {source_token}"}

    try:
        while page_count < max_pages:
            page_count += 1

            # Fixed: Use params dict for proper URL encoding of pageToken
            params = {"pageSize": PAGE_SIZE}
            if page_token:
                params["pageToken"] = page_token

            response = session.get(
                f"{source_url.rstrip('/')}/api/v1/memos",
                params=params,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()

            data = response.json()
            memos = data.get("memos", [])

            if not memos:
                break

            all_memos.extend(memos)
            logger.info(
                f"  📄 Page {page_count}: {len(memos)} memos "
                f"(total: {len(all_memos)})"
            )

            page_token = data.get("nextPageToken")
            if not page_token:
                break

            time.sleep(RATE_LIMIT_DELAY)

        logger.info(f"✅ Found {len(all_memos)} total memos\n")
        return all_memos

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error fetching memos: {e}")
        return []


def fetch_memo_attachments(
    memo_name: str,
    source_url: str,
    source_token: str,
    session: requests.Session,
) -> List[Dict[str, Any]]:
    """Fetch all attachments for a specific memo."""
    try:
        headers = {"Authorization": f"Bearer {source_token}"}
        memo_id = memo_name.split("/")[-1]
        url = f"{source_url.rstrip('/')}/api/v1/memos/{memo_id}/attachments"

        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json()
        attachments = data.get("attachments", [])
        if attachments:
            logger.info(f"    📎 Found {len(attachments)} attachments")
        return attachments

    except requests.exceptions.RequestException as e:
        logger.warning(f"    ⚠️ Failed to fetch attachments: {e}")
        return []


def download_attachment(
    attachment: Dict[str, Any],
    source_url: str,
    source_token: str,
    session: requests.Session,
) -> Optional[Dict[str, Any]]:
    """Download attachment from source instance."""
    try:
        attachment_name = attachment.get("name")
        if not attachment_name:
            logger.warning("    ⚠️ Attachment has no name")
            return None

        # Extract attachment ID
        attachment_id = (
            attachment_name.split("/")[-1]
            if "/" in attachment_name
            else attachment_name
        )
        filename = attachment.get("filename", attachment_id)

        headers = {"Authorization": f"Bearer {source_token}"}

        # Try API endpoint first
        api_url = f"{source_url.rstrip('/')}/api/v1/attachments/{attachment_id}"
        response = session.get(api_url, headers=headers, timeout=60)

        # Check if response is JSON with base64 content
        if (
            response.status_code == 200
            and "application/json" in response.headers.get("Content-Type", "")
        ):
            try:
                json_data = response.json()
                content_field = json_data.get("content", "")
                if content_field:
                    file_content = base64.b64decode(content_field)
                    logger.info(
                        f"    📥 Downloaded (API): {filename} "
                        f"({len(file_content) / 1024:.2f} KB)"
                    )
                    return {
                        "filename": filename,
                        "content": file_content,
                        "type": attachment.get(
                            "type", "application/octet-stream"
                        ),
                    }
            except (json.JSONDecodeError, base64.binascii.Error) as e:
                logger.debug(f"    Failed to parse JSON response: {e}")

        # Fallback to file serving endpoint
        file_url = (
            f"{source_url.rstrip('/')}/file/{attachment_name}/{filename}"
        )
        response = session.get(file_url, headers=headers, timeout=60)
        response.raise_for_status()

        content = response.content
        logger.info(
            f"    📥 Downloaded (file): {filename} "
            f"({len(content) / 1024:.2f} KB)"
        )
        return {
            "filename": filename,
            "content": content,
            "type": attachment.get("type", "application/octet-stream"),
        }

    except requests.exceptions.RequestException as e:
        logger.warning(f"    ⚠️ Failed to download attachment: {e}")
        return None


def create_memo_in_dest(
    memo: Dict[str, Any],
    dest_url: str,
    dest_token: str,
    session: requests.Session,
    source_handle: str = "",
) -> Optional[str]:
    """Create memo in destination instance."""
    if DRY_RUN:
        logger.info("  [DRY RUN] Would create memo")
        return "dry-run-memo-name"

    try:
        content = memo.get("content", "")
        if source_handle:
            content = f"@{source_handle}:\n{content}"

        payload = {
            "content": content,
            "visibility": memo.get("visibility", "PRIVATE"),
        }

        headers = {
            "Authorization": f"Bearer {dest_token}",
            "Content-Type": "application/json",
        }

        response = session.post(
            f"{dest_url.rstrip('/')}/api/v1/memos",
            json=payload,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()
        new_memo_name = data.get("name")
        logger.info(f"  ✅ Memo created: {new_memo_name}")

        return new_memo_name

    except requests.exceptions.RequestException as e:
        logger.error(f"  ❌ Failed to create memo: {e}")
        if hasattr(e.response, "text"):
            logger.debug(f"  Response: {e.response.text}")
        return None


def update_memo_timestamp(
    dest_memo_name: str,
    source_timestamp: str,
    dest_url: str,
    dest_token: str,
    session: requests.Session,
) -> bool:
    """Update the timestamp of the newly created memo."""
    if DRY_RUN:
        logger.info("    [DRY RUN] Would update timestamp")
        return True

    if not source_timestamp:
        return False

    try:
        headers = {
            "Authorization": f"Bearer {dest_token}",
            "Content-Type": "application/json",
        }

        patch_payload = {"createTime": source_timestamp}
        patch_response = session.patch(
            f"{dest_url.rstrip('/')}/api/v1/{dest_memo_name}",
            json=patch_payload,
            headers=headers,
            timeout=30,
        )

        if patch_response.status_code == 200:
            logger.info("    📅 Timestamp updated")
            return True
        else:
            logger.warning(
                f"    ⚠️ Timestamp update failed: "
                f"{patch_response.status_code}"
            )
            return False

    except requests.exceptions.RequestException as e:
        logger.warning(f"    ⚠️ Error updating timestamp: {e}")
        return False


def upload_attachment_to_dest(
    attachment_data: Dict[str, Any],
    dest_memo_name: str,
    dest_url: str,
    dest_token: str,
    session: requests.Session,
) -> Optional[str]:
    """Upload attachment to destination memo."""
    if DRY_RUN:
        logger.info(
            f"    [DRY RUN] Would upload: {attachment_data['filename']}"
        )
        return "dry-run-attachment-name"

    try:
        if not attachment_data:
            return None

        encoded = base64.b64encode(attachment_data["content"]).decode("utf-8")

        payload = {
            "filename": attachment_data["filename"],
            "content": encoded,
            "type": attachment_data["type"],
            "memo": dest_memo_name,
        }

        headers = {"Authorization": f"Bearer {dest_token}"}

        response = session.post(
            f"{dest_url.rstrip('/')}/api/v1/attachments",
            json=payload,
            headers=headers,
            timeout=120,
        )
        response.raise_for_status()

        attachment_name = response.json().get("name")
        logger.info(f"    📤 Uploaded: {attachment_data['filename']}")
        return attachment_name

    except requests.exceptions.RequestException as e:
        logger.warning(f"    ⚠️ Failed to upload attachment: {e}")
        if hasattr(e, "response") and hasattr(e.response, "text"):
            logger.debug(f"    Response: {e.response.text}")
        return None


def migrate_memo(
    memo: Dict[str, Any],
    source_url: str,
    source_token: str,
    dest_url: str,
    dest_token: str,
    session: requests.Session,
    source_handle: str = "",
) -> bool:
    """Migrate a single memo with all its attachments."""
    memo_name = memo.get("name")
    if not memo_name:
        logger.warning("Memo has no name, skipping")
        return False

    logger.info(f"\n📝 Migrating memo: {memo_name}")

    # --- FILTERING LOGIC ---
    if FILTER_HANDLE:
        content = memo.get("content", "")
        prefix = f"@{FILTER_HANDLE}:"
        
        if content.strip().startswith(prefix):
            # Strip the prefix and leading whitespace/newlines
            new_content = content.replace(prefix, "", 1).strip()
            
            # Update memo content for migration
            # We create a copy to avoid mutating the original dict if it matters
            memo = memo.copy()
            memo["content"] = new_content
            
            logger.info(f"  ✨ Filter match! Stripped handle '{prefix}'")
            # Clear source_handle so we don't double-prepend or prepend unwanted stuff
            source_handle = ""
            
        else:
            logger.info(f"  ⏭️  Skipping: Content does not start with '{prefix}'")
            return False
    # -----------------------

    # Get attachments from source
    attachments = fetch_memo_attachments(
        memo_name, source_url, source_token, session
    )

    # Download attachments
    attachment_files = []
    for attachment in attachments:
        attachment_data = download_attachment(
            attachment, source_url, source_token, session
        )
        if attachment_data:
            attachment_files.append(attachment_data)
        time.sleep(RATE_LIMIT_DELAY)

    # Create memo in destination
    dest_memo_name = create_memo_in_dest(
        memo, dest_url, dest_token, session, source_handle
    )
    if not dest_memo_name:
        return False

    # Update timestamp
    source_timestamp = memo.get("createTime")
    update_memo_timestamp(
        dest_memo_name, source_timestamp, dest_url, dest_token, session
    )

    # Upload attachments
    success = True
    for attachment_data in attachment_files:
        result = upload_attachment_to_dest(
            attachment_data, dest_memo_name, dest_url, dest_token, session
        )
        if not result:
            success = False
        time.sleep(RATE_LIMIT_DELAY)

    return success


def validate_config() -> bool:
    """Validate required configuration."""
    if not all(
        [
            SOURCE_MEMOS_TOKEN,
            DEST_MEMOS_TOKEN,
            SOURCE_MEMOS_URL,
            DEST_MEMOS_URL,
        ]
    ):
        logger.error("❌ Missing required environment variables:")
        logger.error(
            "   SOURCE_MEMOS_TOKEN, DEST_MEMOS_TOKEN, "
            "SOURCE_MEMOS_URL, DEST_MEMOS_URL"
        )
        return False

    return True


def main():
    """Main migration function."""
    # Validate configuration
    if not validate_config():
        return

    if DRY_RUN:
        logger.info("\n⚠️  DRY RUN MODE - No changes will be made\n")

    logger.info(f"\n{'='*60}")
    logger.info("🚀 MEMOS MIGRATION TOOL")
    logger.info(f"{'='*60}")
    logger.info(f"Source: {SOURCE_MEMOS_URL}")
    logger.info(f"Destination: {DEST_MEMOS_URL}")
    logger.info(f"Source Handle: {SOURCE_HANDLE if SOURCE_HANDLE else '(none)'}")
    logger.info(f"{'='*60}\n")

    # Create session with retries
    session = create_session_with_retries()

    try:
        # Fetch all memos from source
        all_memos = fetch_all_memos(SOURCE_MEMOS_URL, SOURCE_MEMOS_TOKEN, session)

        if not all_memos:
            logger.error("❌ No memos to migrate")
            return

        # Migrate each memo
        success_count = 0
        fail_count = 0
        start_time = time.time()

        for idx, memo in enumerate(all_memos, 1):
            logger.info(f"\n{'='*60}")
            logger.info(f"📊 Progress: {idx}/{len(all_memos)}")
            logger.info(f"{'='*60}")

            try:
                if migrate_memo(
                    memo,
                    SOURCE_MEMOS_URL,
                    SOURCE_MEMOS_TOKEN,
                    DEST_MEMOS_URL,
                    DEST_MEMOS_TOKEN,
                    session,
                    SOURCE_HANDLE,
                ):
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"❌ Failed to migrate memo: {e}", exc_info=True)
                fail_count += 1

            # Show progress stats every 10 memos
            if idx % 10 == 0 or idx == len(all_memos):
                elapsed = time.time() - start_time
                rate = (idx / elapsed * 60) if elapsed > 0 else 0
                remaining = len(all_memos) - idx
                eta = (remaining / (rate / 60)) if rate > 0 else 0

                logger.info(
                    f"\n📈 Rate: {rate:.1f} memos/min, ETA: {eta/60:.1f} min"
                )

        # Summary
        elapsed = time.time() - start_time
        logger.info(f"\n{'='*60}")
        logger.info("🎉 MIGRATION COMPLETE!")
        logger.info(f"{'='*60}")
        logger.info(f"✅ Successfully migrated: {success_count}")
        logger.info(f"❌ Failed: {fail_count}")
        logger.info(f"📊 Total processed: {len(all_memos)}")
        logger.info(f"⏱️  Total time: {elapsed/60:.1f} minutes")
        logger.info(f"{'='*60}\n")

    finally:
        session.close()


if __name__ == "__main__":
    main()
