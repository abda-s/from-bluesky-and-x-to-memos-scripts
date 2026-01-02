import os
import hashlib
import requests
from dotenv import load_dotenv
from collections import defaultdict
import logging
import time
from typing import List, Dict, Any, Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime

load_dotenv()

# --- CONFIGURATION ---
MEMOS_URL = os.getenv("MEMOS_URL")
MEMOS_TOKEN = os.getenv("MEMOS_TOKEN")
DRY_RUN = False  # Set to False to actually delete duplicates
MAX_RETRIES = 3
PAGE_SIZE = 100
RATE_LIMIT_DELAY = 0.1

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)



def extract_date(timestamp_str):
    """Extract just the date (YYYY-MM-DD) from ISO timestamp."""
    try:
        if not timestamp_str:
            return "unknown"
        dt = datetime.fromisoformat(
            timestamp_str.replace('Z', '+00:00')
        )
        return dt.strftime('%Y-%m-%d')
    except Exception as e:
        return "unknown"


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


def find_duplicates(memos):
    """Group memos by content hash AND date to find duplicates."""
    print("🔎 Analyzing memos for duplicates (by content + date)...\n")

    # Group memos by content hash + date
    content_date_groups = defaultdict(list)

    for memo in memos:
        content = memo.get("content", "") or ""
        resources = memo.get("resources", []) or []
        
        # Skip only if BOTH content and resources are empty
        if not content and not resources:
            continue

        # Create resource signature to include in hash
        # We sort resources to ensure order doesn't affect hash
        resource_sigs = []
        for res in resources:
            res_name = res.get("filename") or res.get("name") or "unknown"
            res_type = res.get("type", "unknown")
            resource_sigs.append(f"{res_name}:{res_type}")
        
        resource_string = "|".join(sorted(resource_sigs))
        
        # Combine content + resources for uniqueness
        unique_string = f"{content}||{resource_string}"

        # Get date from createTime
        create_time = memo.get("createTime", "")
        date = extract_date(create_time)
        
        # Create composite key: content_hash + date
        content_hash = hashlib.md5(unique_string.encode()).hexdigest()
        composite_key = f"{content_hash}_{date}"
        
        content_date_groups[composite_key].append(memo)

    # Filter to only groups with duplicates
    duplicates = {
        key: memo_list
        for key, memo_list in content_date_groups.items()
        if len(memo_list) > 1
    }

    if not duplicates:
        print("✨ No duplicates found!\n")
        return {}

    print(
        f"⚠️  Found {len(duplicates)} sets of duplicate "
        f"content on the same date\n"
    )

    # Display duplicate sets (show first 15 in detail)
    total_to_delete = 0
    display_limit = 15
    
    for idx, (composite_key, memo_list) in enumerate(
        list(duplicates.items())[:display_limit], 1
    ):
        # Extract date from composite key
        # Extract date from composite key
        date = composite_key.split('_')[-1]
        
        memo_content = memo_list[0].get("content", "")
        memo_resources = memo_list[0].get("resources", [])
        
        preview = memo_content[:60].replace("\n", " ") if memo_content else "[No Text]"
        if memo_resources:
            preview += f" (+{len(memo_resources)} attachments)"
        
        print(f"Set {idx}: {len(memo_list)} copies on {date}")
        print(f"  Content: {preview}...")
        print(f"  Will keep: {memo_list[0]['name']} (oldest)")
        print(f"  Will delete: {len(memo_list) - 1} duplicate(s)")
        
        # Show all timestamps in this group
        for memo in memo_list:
            create_time = memo.get("createTime", "N/A")
            print(f"    • {memo['name']}: {create_time}")
        print()
        
        total_to_delete += len(memo_list) - 1

    # Count remaining
    for memo_list in list(duplicates.values())[display_limit:]:
        total_to_delete += len(memo_list) - 1

    if len(duplicates) > display_limit:
        print(
            f"... and {len(duplicates) - display_limit} more "
            f"duplicate sets\n"
        )

    print(f"📊 Total duplicates to delete: {total_to_delete}\n")
    return duplicates


def delete_duplicates(duplicates):
    """Delete duplicate memos, keeping the oldest one in each set."""
    if not duplicates:
        return

    mode = "DRY RUN - No actual deletions" if DRY_RUN else "LIVE MODE"
    print(f"{'='*60}")
    print(f"⚠️  {mode}")
    print(f"{'='*60}\n")

    if not DRY_RUN:
        print(
            f"This will permanently delete {sum(len(m) - 1 for m in duplicates.values())} "
            f"duplicate memos."
        )
        confirm = input("❗ Continue? (yes/no): ")
        if confirm.lower() != "yes":
            print("❌ Deletion cancelled.\n")
            return

    headers = {"Authorization": f"Bearer {MEMOS_TOKEN}"}
    deleted_count = 0
    failed_count = 0

    for idx, (composite_key, memo_list) in enumerate(
        duplicates.items(), 1
    ):
        # Sort by create time to keep the oldest
        memo_list.sort(key=lambda x: x.get("createTime", ""))

        date = composite_key.split('_')[-1]
        
        # Keep first (oldest), delete the rest
        for memo in memo_list[1:]:
            memo_name = memo.get("name")
            content = memo.get("content", "")
            preview = content[:50].replace("\n", " ") if content else "[No Text]"

            if DRY_RUN:
                print(f"[DRY RUN] Would delete: {memo_name}")
                print(f"  Date: {date}")
                print(f"  Content: {preview}...")
                deleted_count += 1
            else:
                try:
                    response = requests.delete(
                        f"{MEMOS_URL}/api/v1/{memo_name}",
                        headers=headers,
                        timeout=30,
                    )
                    response.raise_for_status()
                    print(f"✅ Deleted: {memo_name}")
                    print(f"  Date: {date}")
                    print(f"  Content: {preview}...")
                    deleted_count += 1
                except Exception as e:
                    print(f"❌ Failed to delete {memo_name}: {e}")
                    failed_count += 1

        # Progress indicator
        if idx % 10 == 0:
            print(
                f"\n  Progress: {idx}/{len(duplicates)} "
                f"sets processed\n"
            )

    print(f"\n{'='*60}")
    print(f"📊 SUMMARY")
    print(f"{'='*60}")
    if DRY_RUN:
        print(f"Would delete: {deleted_count} duplicate memos")
    else:
        print(f"✅ Deleted: {deleted_count} memos")
        if failed_count > 0:
            print(f"❌ Failed: {failed_count} memos")
    print(f"{'='*60}\n")


def main():
    if not MEMOS_TOKEN:
        print("❌ MEMOS_TOKEN not set in .env")
        return

    print(f"\n{'='*60}")
    print(f"🗑️  MEMOS DUPLICATE CLEANER")
    print(f"{'='*60}")
    print(f"Instance: {MEMOS_URL}")
    print(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    print(f"Strategy: Same content + same date")
    print(f"{'='*60}\n")

    # Create session with retries
    session = create_session_with_retries()
    
    try:
        # Fetch all memos
        memos = fetch_all_memos(MEMOS_URL, MEMOS_TOKEN, session)
        if not memos:
            print("❌ No memos fetched. Cannot continue.\n")
            return

        # Find duplicates
        duplicates = find_duplicates(memos)

        # Delete duplicates (or show what would be deleted)
        if duplicates:
            delete_duplicates(duplicates)

            if DRY_RUN:
                print("💡 To actually delete duplicates, set DRY_RUN = False")
                print("   in the script configuration.\n")
    finally:
        session.close()


if __name__ == "__main__":
    main()