import requests
import os
from dotenv import load_dotenv
from datetime import datetime
import time
import logging
from typing import List, Dict, Any, Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Load configuration from .env file
load_dotenv()

# --- CONFIGURATION ---
MEMOS_URL = os.getenv("MEMOS_URL")
MEMOS_TOKEN = os.getenv("MEMOS_TOKEN")
# Set the cutoff date (delete everything BEFORE this date)
CUTOFF_DATE = os.getenv("CUTOFF_DATE")
DRY_RUN = False  # Set to False to actually delete
PAGE_SIZE = 100
RATE_LIMIT_DELAY = 0.1
MAX_RETRIES = 3

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


def delete_memo(memo_name, memo_id, session):
    """Delete a single memo using the persistent session."""
    headers = {"Authorization": f"Bearer {MEMOS_TOKEN}"}
    
    # Try with name first (newer API)
    if memo_name:
        url = f"{MEMOS_URL}/api/v1/{memo_name}"
    else:
        url = f"{MEMOS_URL}/api/v1/memos/{memo_id}"
    
    try:
        response = session.delete(url, headers=headers)
        return response.status_code == 200
    except Exception as e:
        print(f"  ⚠️  Error: {e}")
        return False


def main():
    """Main execution function."""
    if not all([MEMOS_URL, MEMOS_TOKEN]):
        print("❌ Missing configuration! Set MEMOS_URL and MEMOS_TOKEN.")
        return

    print("=" * 60)
    print("MEMOS CLEANUP SCRIPT")
    print("=" * 60)
    print(f"📅 Cutoff date: {CUTOFF_DATE}")
    print(f"🗑️  Will delete all memos BEFORE this date")
    print(f"🔒 Dry run: {'ON (no deletion)' if DRY_RUN else 'OFF (WILL DELETE)'}")
    print("=" * 60 + "\n")
    
    # Parse cutoff date
    try:
        cutoff_dt = datetime.fromisoformat(
            CUTOFF_DATE.replace("Z", "+00:00")
        )
    except Exception as e:
        print(f"❌ Invalid cutoff date format: {e}")
        return
    
    # Create session
    session = create_session_with_retries()
    
    try:
        # Fetch all memos
        print("📥 Fetching all memos...\n")
        memos = fetch_all_memos(MEMOS_URL, MEMOS_TOKEN, session)
        
        if not memos:
            print("No memos found. Exiting.")
            return
    
        # Filter memos before cutoff date
        memos_to_delete = []
        
        for memo in memos:
            create_time = memo.get("createTime") or memo.get("createdTs")
            
            if not create_time:
                continue
            
            # Handle both ISO string and Unix timestamp
            try:
                if isinstance(create_time, str):
                    memo_dt = datetime.fromisoformat(
                        create_time.replace("Z", "+00:00")
                    )
                else:
                    memo_dt = datetime.fromtimestamp(create_time)
                
                if memo_dt < cutoff_dt:
                    memos_to_delete.append({
                        "name": memo.get("name"),
                        "id": memo.get("id") or memo.get("uid"),
                        "content": memo.get("content", "")[:50],
                        "date": memo_dt.strftime("%Y-%m-%d %H:%M:%S")
                    })
            except Exception as e:
                print(f"⚠️  Skipping memo due to date parsing error: {e}")
                continue
        
        # Show summary
        print(f"📊 Found {len(memos_to_delete)} memos to delete:\n")
        
        for i, memo in enumerate(memos_to_delete[:10], 1):
            print(f"  {i}. [{memo['date']}] {memo['content']}...")
        
        if len(memos_to_delete) > 10:
            print(f"  ... and {len(memos_to_delete) - 10} more")
        
        print("\n" + "=" * 60)
        
        if not memos_to_delete:
            print("✅ No memos to delete!")
            return
        
        # Delete memos
        deleted_count = 0
        failed_count = 0
        
        for i, memo in enumerate(memos_to_delete, 1):
            print(f"[{i}/{len(memos_to_delete)}] ", end="")
            
            if DRY_RUN:
                print(f"Would delete: [{memo['date']}] {memo['content']}...")
                deleted_count += 1
            else:
                if delete_memo(memo['name'], memo['id'], session):
                    print(f"✅ Deleted: [{memo['date']}]")
                    deleted_count += 1
                else:
                    print(f"❌ Failed: [{memo['date']}]")
                    failed_count += 1
        
        # Final summary
        print("\n" + "=" * 60)
        print("CLEANUP COMPLETE")
        print("=" * 60)
        
        if DRY_RUN:
            print(f"🔍 Dry run: {deleted_count} memos would be deleted")
            print(
                "\n💡 Set DRY_RUN = False to actually delete these memos"
            )
        else:
            print(f"✅ Successfully deleted: {deleted_count}")
            if failed_count > 0:
                print(f"❌ Failed to delete: {failed_count}")
            
    finally:
        session.close()


if __name__ == "__main__":
    main()
