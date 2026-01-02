# from-bluesky-and-x-to-memos-scripts

A collection of Python scripts to migrate your content from **[Bluesky](https://bsky.app)** and **[Twitter/X](https://x.com)** into your self-hosted **[Memos](https://usememos.com)** instance.

## 🚀 Setup & Installation

1.  **Clone or Download** this repository.

2.  **Create and Activate a Virtual Environment** (Recommended):
    Create an isolated environment to install dependencies.
    ```bash
    # Windows
    python -m venv venv
    .\venv\Scripts\activate

    # Mac/Linux
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Install Dependencies**:
    This installs all required libraries (including `yt-dlp` for video downloading).
    ```bash
    pip install -r requirements.txt
    python -m playwright install chromium
    ```

4.  **System Connections**:
    *   **FFmpeg**: Required by `yt-dlp` to merge video and audio.
        *   **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/download.html), extract, and add the `bin` folder to your System PATH.
        *   **Mac**: `brew install ffmpeg`
        *   **Linux**: `sudo apt install ffmpeg`

5.  **Configure Environment**:
    - Copy `.env.example` to `.env`:
        ```bash
        cp .env.example .env
    - Open `.env` and fill in your details (see below).

## 🔑 Authentication Guide

### 1. Memos Instance
*   **`MEMOS_HOST`**: The full URL to your Memos instance (e.g., `http://192.168.1.5:5230`).
*   **`MEMOS_ACCESS_TOKEN`**:
    *   Go to your Memos **Settings**.
    *   Click on **Access Tokens**.
    *   Create a new token (e.g., "Migration Script") and copy it.

### 2. Bluesky
*   **`BLUESKY_HANDLE`**: Your full handle (e.g., `user.bsky.social`).
*   **`BLUESKY_PASSWORD`**:
    *   **Do NOT** use your main login password.
    *   Go to **Settings** -> **Privacy & Security** -> **App Passwords**.
    *   Click **Add App Password**, name it, and copy the code.

### 3. Twitter / X
*   **`X_USERNAME`**: Your username (without the `@`).
*   **`X_AUTH_TOKEN`** & **`X_CT0`**:
    *   These are **cookies** required to scrape data as a logged-in user.
    *   Login to [x.com](https://x.com) in your browser (Chrome/Edge recommended).
    *   Press `F12` to open Developer Tools.
    *   Go to the **Application** tab (sometimes under ">>" overflow menu).
    *   In the sidebar, expand **Cookies** and click on `https://x.com` or `https://twitter.com`.
    *   Find the row named `auth_token` -> Copys its value to `.env`.
    *   Find the row named `ct0` -> Copy its value to `.env`.

---

## 🐦 Twitter / X Scrapers
We provide three scripts, but **`scrape_x_hybrid.py` is the best one** and the only one you likely need.

### 🏆 `scrape_x_hybrid.py` (Recommended)
**Use this one.** It automates the entire process by combining the best of both worlds:
1.  **Fast Scroll**: It starts by rapidly scrolling through your profile to get the most recent ~3,000 posts (much faster).
2.  **Chunked Search**: Once scrolling hits the limit (X blocks infinite scroll after ~1 year), it automatically switches to "Chunk Mode" to find older posts that scrolling missed.
*   **Why it's the best**: It gives you the speed of scrolling + the completeness of the advanced search.

---
### Other Scrapers (For specific needs)

#### `scrape_x_search.py`
*   **What it is**: The "Deep Search" component of the hybrid script.
*   **When to use**: If you *only* want to scrape a specific old year (e.g., just 2018) without scrolling through everything else.

#### `scrape_x_recent.py`
*   **What it is**: The "Fast Scroll" component of the hybrid script.
*   **When to use**: If you only want to quickly backup your tweets from the last few months.

---

## ☁️ Bluesky Migration
### `import_bluesky.py`
**Use this for:** Migrating all your Bluesky posts.
*   **How it works**: Uses the official Bluesky API (AT Protocol). It's much faster and more reliable than the X scrapers because we have official API access.

---

## 🛠️ Management & Cleanup Tools

### `migrate_memos.py`
**Use this for:** Moving memos between two accounts or two different Memos instances.
*   **Features**:
    *   Copies content, resources (images/videos), and original creation dates.
    *   **Filter & Strip**: Can look for memos starting with `@{handle}:`, remove that handle, and copy just the clean content. (Set `MIGRATION_FILTER_HANDLE` in `.env`).

### `cleanup_duplicates.py`
**Use this for:** Cleaning up accidental duplicates.
*   **Smart Detection**: Identifies duplicates by hashing checking both **Content** + **Attachments**. (e.g., two posts with no text but the same image are flagged as duplicates).
*   **Safety**: Runs in `DRY_RUN` mode by default (configure in script).

### `cleanup_old_memos.py`
**Use this for:** Bulk deleting old content.
*   **Config**: Deletes everything created **BEFORE** the `CLEANUP_CUTOFF_DATE` set in your `.env`.
