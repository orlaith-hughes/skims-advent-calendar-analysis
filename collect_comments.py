

import csv
import os
import sys
import time
from pathlib import Path

import requests

API = "https://www.googleapis.com/youtube/v3"

# --- Settings you can tweak -------------------------------------------------
SEARCH_QUERIES = [
    "skims advent calendar",
    "skims advent calendar unboxing",
    "skims advent calendar review",
    "skims accessories advent calendar",
]
PUBLISHED_AFTER = "2026-10-29T00:00:00Z"   # calendar launch date - filters out the 2025 panty calendar
VIDEOS_PER_QUERY = 15
MAX_COMMENTS_PER_VIDEO = 500
MIN_COMMENTS_ON_VIDEO = 10                # skip videos with almost no discussion

# Add specific video IDs here if you find good ones the search misses
# (the ID is the bit after "v=" in a YouTube URL)
EXTRA_VIDEO_IDS: list[str] = []
# -----------------------------------------------------------------------------

OUT_DIR = Path(__file__).parent / "data"
OUT_FILE = OUT_DIR / "comments_raw.csv"


def load_api_key() -> str:
    key = os.environ.get("YOUTUBE_API_KEY")
    env_file = Path(__file__).parent / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip().startswith("YOUTUBE_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("No API key found. Create a .env file containing YOUTUBE_API_KEY=your_key")
    return key


def get(endpoint: str, params: dict, key: str) -> dict:
    params = {**params, "key": key}
    r = requests.get(f"{API}/{endpoint}", params=params, timeout=30)
    if r.status_code == 403 and "commentsDisabled" in r.text:
        return {"items": [], "_disabled": True}
    r.raise_for_status()
    return r.json()


def search_videos(query: str, key: str) -> list[str]:
    data = get("search", {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": VIDEOS_PER_QUERY,
        "publishedAfter": PUBLISHED_AFTER,
        "order": "relevance",
        "relevanceLanguage": "en",
    }, key)
    return [item["id"]["videoId"] for item in data.get("items", [])]


def video_details(video_ids: list[str], key: str) -> dict:
    """Return {video_id: {title, channel, published, comment_count, views}}."""
    details = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        data = get("videos", {"part": "snippet,statistics", "id": ",".join(batch)}, key)
        for v in data.get("items", []):
            stats = v.get("statistics", {})
            details[v["id"]] = {
                "video_title": v["snippet"]["title"],
                "channel": v["snippet"]["channelTitle"],
                "video_published": v["snippet"]["publishedAt"],
                "video_views": int(stats.get("viewCount", 0)),
                "video_comment_count": int(stats.get("commentCount", 0)),
            }
    return details


def is_relevant(title: str) -> bool:
    t = title.lower()
    return "skims" in t and ("advent" in t or "calendar" in t)


def fetch_comments(video_id: str, key: str) -> list[dict]:
    rows, page_token = [], None
    while len(rows) < MAX_COMMENTS_PER_VIDEO:
        params = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": 100,
            "textFormat": "plainText",
            "order": "relevance",
        }
        if page_token:
            params["pageToken"] = page_token
        data = get("commentThreads", params, key)
        if data.get("_disabled"):
            break
        for item in data.get("items", []):
            c = item["snippet"]["topLevelComment"]["snippet"]
            rows.append({
                "video_id": video_id,
                "comment_id": item["id"],
                "text": c["textDisplay"],
                "likes": c.get("likeCount", 0),
                "reply_count": item["snippet"].get("totalReplyCount", 0),
                "published_at": c["publishedAt"],
            })
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.1)
    return rows


def main():
    key = load_api_key()
    OUT_DIR.mkdir(exist_ok=True)

    print("Searching for videos...")
    ids = list(EXTRA_VIDEO_IDS)
    for q in SEARCH_QUERIES:
        found = search_videos(q, key)
        print(f"  '{q}': {len(found)} videos")
        ids.extend(found)
    ids = list(dict.fromkeys(ids))  # dedupe, keep order

    details = video_details(ids, key)
    keep = [
        vid for vid, d in details.items()
        if (vid in EXTRA_VIDEO_IDS or is_relevant(d["video_title"]))
        and d["video_comment_count"] >= MIN_COMMENTS_ON_VIDEO
    ]
    print(f"\n{len(keep)} relevant videos with {MIN_COMMENTS_ON_VIDEO}+ comments:")
    for vid in keep:
        d = details[vid]
        print(f"  [{d['video_comment_count']:>5} comments] {d['video_title'][:70]}  ({d['channel']})")

    all_rows = []
    for vid in keep:
        rows = fetch_comments(vid, key)
        for r in rows:
            r.update(details[vid])
        all_rows.extend(rows)
        print(f"  pulled {len(rows):>4} from {details[vid]['video_title'][:50]}")

    if not all_rows:
        sys.exit("No comments collected. Try adding video IDs to EXTRA_VIDEO_IDS.")

    fields = ["comment_id", "video_id", "video_title", "channel", "video_published",
              "video_views", "video_comment_count", "text", "likes", "reply_count", "published_at"]
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(all_rows)
    print(f"\nSaved {len(all_rows)} comments from {len(keep)} videos to {OUT_FILE}")


if __name__ == "__main__":
    main()
