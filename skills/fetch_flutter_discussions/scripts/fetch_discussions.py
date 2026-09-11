#!/usr/bin/env python3
"""
Fetch Flutter community discussions from GitHub, Stack Overflow, and Reddit.
Uses only standard library modules (no pip dependencies required).
"""

import argparse
import base64
import datetime
import gzip
import html
import json
import os
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


USER_AGENT = "FlutterDiscussionFetcher/1.0 (Research tool; contact@example.com)"

# scripts/ -> fetch_flutter_discussions/ -> skills/ -> repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

# Everything this script writes is a regenerable dump, so it belongs in the
# repo's gitignored tmp/ tree rather than the committed working tree. Relative
# output paths resolve against this directory, not the current one.
DEFAULT_OUTPUT_DIR = os.path.join(_REPO_ROOT, "tmp", "fetch_flutter_discussions")


def resolve_output(path, output_dir):
    """Absolute paths are honoured; bare names land in the output directory."""
    if not path:
        return None
    if os.path.isabs(path):
        target = path
    else:
        target = os.path.join(output_dir, path)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    return target


def get_ssl_context():
    """Create SSL context with fallback paths for macOS and Linux certificates."""
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs():
        for ca_path in [
            "/etc/ssl/cert.pem",
            "/etc/pki/tls/certs/ca-bundle.crt",
            "/etc/ssl/certs/ca-certificates.crt",
            "/etc/ssl/ca-bundle.pem",
        ]:
            if os.path.exists(ca_path):
                try:
                    ctx.load_verify_locations(ca_path)
                    break
                except Exception:
                    pass
    return ctx


def make_request(url, headers=None, data=None):
    """Perform HTTP request handling gzip compression and SSL context."""
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, data=data, headers=req_headers)
    ssl_context = get_ssl_context()
    try:
        with urllib.request.urlopen(req, context=ssl_context, timeout=15) as response:
            encoding = response.info().get("Content-Encoding")
            raw = response.read()
            if encoding == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8")
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"HTTP {e.code} for {url}: {e.reason}\n")
        return None
    except Exception as e:
        sys.stderr.write(f"Request failed for {url}: {e}\n")
        return None


def fetch_github_issues(repo="flutter/flutter", days=180, max_items=50, token=None, query=None):
    """
    Fetch issues and pull requests from GitHub Search API.
    """
    cutoff_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    if query:
        search_q = f"repo:{repo} {query}"
    else:
        search_q = f"repo:{repo} created:>={cutoff_date}"

    headers = {
        "Accept": "application/vnd.github.v3+json",
    }
    if token:
        headers["Authorization"] = f"token {token}"

    results = []
    page = 1
    per_page = min(max_items, 100)

    while len(results) < max_items:
        params = {
            "q": search_q,
            "sort": "updated" if query else "created",
            "order": "desc",
            "per_page": per_page,
            "page": page,
        }
        url = f"https://api.github.com/search/issues?{urllib.parse.urlencode(params)}"
        raw_text = make_request(url, headers=headers)
        if not raw_text:
            break

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            break

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            results.append({
                "source": "github",
                "id": str(item.get("id")),
                "title": item.get("title"),
                "url": item.get("html_url"),
                "author": item.get("user", {}).get("login") if item.get("user") else None,
                "created_at": item.get("created_at"),
                "comments_count": item.get("comments", 0),
                "state": item.get("state"),
                "score": item.get("score", 0),
                "body": (item.get("body") or "")[:500],
                "labels": [label.get("name") for label in item.get("labels", []) if isinstance(label, dict)],
            })
            if len(results) >= max_items:
                break

        page += 1
        time.sleep(1)

    return results


def fetch_stackoverflow_questions(tag="flutter", days=180, max_items=50, api_key=None, query=None):
    """
    Fetch questions from Stack Exchange API.
    """
    from_date = int((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).timestamp())
    to_date = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    results = []
    page = 1
    page_size = min(max_items, 100)

    while len(results) < max_items:
        if query:
            params = {
                "page": page,
                "pagesize": page_size,
                "order": "desc",
                "sort": "relevance",
                "q": query,
                "tagged": tag,
                "site": "stackoverflow",
            }
            url = f"https://api.stackexchange.com/2.3/search/advanced?{urllib.parse.urlencode(params)}"
        else:
            params = {
                "page": page,
                "pagesize": page_size,
                "fromdate": from_date,
                "todate": to_date,
                "order": "desc",
                "sort": "activity",
                "tagged": tag,
                "site": "stackoverflow",
                "filter": "default",
            }
            url = f"https://api.stackexchange.com/2.3/questions?{urllib.parse.urlencode(params)}"
        raw_text = make_request(url)
        if not raw_text:
            break

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            break

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            created_ts = item.get("creation_date")
            created_iso = datetime.datetime.fromtimestamp(created_ts, tz=datetime.timezone.utc).isoformat() if created_ts else None
            results.append({
                "source": "stackoverflow",
                "id": str(item.get("question_id")),
                "title": item.get("title"),
                "url": item.get("link"),
                "author": item.get("owner", {}).get("display_name") if item.get("owner") else None,
                "created_at": created_iso,
                "comments_count": item.get("answer_count", 0),
                "score": item.get("score", 0),
                "view_count": item.get("view_count", 0),
                "is_answered": item.get("is_answered", False),
                "tags": item.get("tags", []),
            })
            if len(results) >= max_items:
                break

        if not data.get("has_more", False):
            break

        page += 1
        backoff = data.get("backoff", 1)
        time.sleep(backoff)

    return results


def get_reddit_oauth_token(client_id, client_secret):
    """Obtain application-only OAuth token for Reddit API."""
    auth_str = f"{client_id}:{client_secret}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    url = "https://www.reddit.com/api/v1/access_token"
    headers = {
        "Authorization": f"Basic {b64_auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    raw = make_request(url, headers=headers, data=data)
    if raw:
        try:
            res = json.loads(raw)
            return res.get("access_token")
        except Exception:
            return None
    return None


def fetch_reddit_posts(subreddit="FlutterDev", days=180, max_items=50, client_id=None, client_secret=None, query=None):
    """
    Fetch posts from Reddit using OAuth or public RSS feed fallback.
    """
    results = []
    token = None
    if client_id and client_secret:
        token = get_reddit_oauth_token(client_id, client_secret)

    if token:
        # Authenticated OAuth path
        cutoff_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).timestamp()
        after = None
        while len(results) < max_items:
            headers = {"Authorization": f"Bearer {token}"}
            params = {"limit": min(max_items, 100)}
            if after:
                params["after"] = after
            if query:
                params["q"] = query
                params["restrict_sr"] = 1
                params["sort"] = "relevance"
                url = f"https://oauth.reddit.com/r/{subreddit}/search?{urllib.parse.urlencode(params)}"
            else:
                url = f"https://oauth.reddit.com/r/{subreddit}/new?{urllib.parse.urlencode(params)}"

            raw = make_request(url, headers=headers)
            if not raw:
                break
            try:
                data = json.loads(raw)
            except Exception:
                break

            children = data.get("data", {}).get("children", [])
            if not children:
                break

            stop = False
            for child in children:
                post = child.get("data", {})
                created_utc = post.get("created_utc", 0)
                if created_utc < cutoff_ts:
                    stop = True
                    break

                created_iso = datetime.datetime.fromtimestamp(created_utc, tz=datetime.timezone.utc).isoformat()
                results.append({
                    "source": f"reddit/r/{subreddit}",
                    "id": post.get("id"),
                    "title": post.get("title"),
                    "url": f"https://reddit.com{post.get('permalink')}" if post.get("permalink") else post.get("url"),
                    "author": post.get("author"),
                    "created_at": created_iso,
                    "comments_count": post.get("num_comments", 0),
                    "score": post.get("score", 0),
                    "body": (post.get("selftext") or "")[:500],
                })
                if len(results) >= max_items:
                    stop = True
                    break

            if stop:
                break
            after = data.get("data", {}).get("after")
            if not after:
                break
            time.sleep(1)
    else:
        # Fallback to RSS feed
        url = f"https://www.reddit.com/r/{subreddit}/new.rss"
        raw_xml = make_request(url)
        if raw_xml:
            try:
                root = ET.fromstring(raw_xml)
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                for entry in root.findall("atom:entry", ns):
                    title_elem = entry.find("atom:title", ns)
                    link_elem = entry.find("atom:link", ns)
                    author_elem = entry.find("atom:author/atom:name", ns)
                    updated_elem = entry.find("atom:updated", ns)
                    id_elem = entry.find("atom:id", ns)

                    title = title_elem.text if title_elem is not None else ""

                    results.append({
                        "source": f"reddit/r/{subreddit}",
                        "id": id_elem.text if id_elem is not None else "",
                        "title": title,
                        "url": link_elem.attrib.get("href") if link_elem is not None else "",
                        "author": author_elem.text if author_elem is not None else "",
                        "created_at": updated_elem.text if updated_elem is not None else "",
                        "comments_count": 0,
                        "score": 0,
                        "body": "",
                    })
                    if len(results) >= max_items:
                        break
            except Exception as e:
                sys.stderr.write(f"Failed to parse Reddit RSS feed: {e}\n")

    return results


def save_to_sqlite(db_path, items):
    """Save records to SQLite database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS discussions (
        id TEXT,
        source TEXT,
        title TEXT,
        url TEXT,
        author TEXT,
        created_at TEXT,
        comments_count INTEGER,
        score INTEGER,
        content_preview TEXT,
        metadata_json TEXT,
        PRIMARY KEY (source, id)
    )
    """)

    for item in items:
        cursor.execute("""
        INSERT OR REPLACE INTO discussions
        (id, source, title, url, author, created_at, comments_count, score, content_preview, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item.get("id"),
            item.get("source"),
            item.get("title"),
            item.get("url"),
            item.get("author"),
            item.get("created_at"),
            item.get("comments_count", 0),
            item.get("score", 0),
            item.get("body", ""),
            json.dumps(item),
        ))

def categorize_item(item):
    """Categorize a discussion item into a topic based on keywords, tags, and labels."""
    title = (item.get("title") or "").lower()
    body = (item.get("body") or "").lower()
    labels = [l.lower() for l in item.get("labels", [])]
    tags = [t.lower() for t in item.get("tags", [])]
    all_text = f"{title} {body} {' '.join(labels)} {' '.join(tags)}"

    if any(k in all_text for k in ["focusorder", "focustraversal", "traversalpolicy", "tab order", "focus traversal", "nextfocus", "previousfocus", "directional focus", "focus scope", "focusnode"]):
        return "Focus Order and Traversal"
    if any(k in all_text for k in ["a11y", "accessibility", "semantics", "screen reader", "talkback", "voiceover"]):
        return "Accessibility and Semantics"
    if any(k in all_text for k in ["textinputaction", "textfield", "textformfield", "autofill", "ime", "keyboard navigation", "focus on submit"]):
        return "Forms and Keyboard Input"
    if any(k in all_text for k in ["menuanchor", "dropdown", "dialog", "bottomsheet", "drawer", "modal"]):
        return "Menus, Dialogs, and Overlays"
    if any(k in all_text for k in ["impeller", "skia", "shader", "memory leak", "fps", "performance", "render"]):
        return "Engine and Performance"
    if any(k in all_text for k in ["go_router", "routing", "navigator", "bloc", "riverpod", "provider", "state management"]):
        return "Routing and State Management"
    if any(k in all_text for k in ["gradle", "xcode", "cocoapods", "flutter_tools", "build", "pubspec", "analyzer", "linter"]):
        return "Tooling and Build"
    if any(k in all_text for k in ["platform channel", "ffi", "camera", "webview", "plugin", "package"]):
        return "Platform Integration and Plugins"
    if any(k in all_text for k in ["widget", "layout", "animation", "scroll", "sliver"]):
        return "Framework and Widgets"
    return "General Topics"


def save_to_markdown(md_path, items):
    """Save discussion sources grouped by topic into a markdown file."""
    grouped = {}
    for item in items:
        topic = categorize_item(item)
        grouped.setdefault(topic, []).append(item)

    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    lines = [
        "# Flutter discussions by topic",
        f"\nReport generated on {now_str}. Total items: {len(items)}.\n",
    ]

    for topic in sorted(grouped.keys()):
        topic_items = grouped[topic]
        lines.append(f"## {topic}\n")
        for it in topic_items:
            source = it.get("source", "unknown")
            raw_title = html.unescape(it.get("title", "Untitled"))
            title = raw_title.replace("[", "\\[").replace("]", "\\]")
            url = it.get("url", "#")
            author = it.get("author") or "anonymous"
            created = (it.get("created_at") or "")[:10]
            comments = it.get("comments_count", 0)

            lines.append(f"* [{title}]({url})")
            lines.append(f"  Source: `{source}` | Author: {author} | Date: {created} | Comments: {comments}")
            if it.get("body"):
                raw_snippet = html.unescape(it["body"]).strip().replace("\n", " ")[:150]
                lines.append(f"  > {raw_snippet}...")
            lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def fetch_twitter_posts(query="#Flutter OR #FlutterDev lang:en -is:retweet", days=7, max_items=50, bearer_token=None):
    """
    Fetch recent tweets from Twitter/X API v2 search endpoint.
    Requires a valid bearer token (v2 search requires a paid basic/pro tier or elevated developer access).
    """
    if not bearer_token:
        sys.stderr.write("Twitter/X bearer token not configured. Set TWITTER_BEARER_TOKEN, X_BEARER_TOKEN, or pass --twitter-bearer-token.\n")
        return []

    # Twitter recent search is constrained to the past 7 days on standard v2 endpoints
    search_days = min(days, 7)
    start_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=search_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    headers = {
        "Authorization": f"Bearer {bearer_token}",
    }

    results = []
    next_token = None

    while len(results) < max_items:
        params = {
            "query": query,
            "max_results": min(max(max_items - len(results), 10), 100),
            "start_time": start_time,
            "tweet.fields": "created_at,public_metrics,author_id",
            "expansions": "author_id",
            "user.fields": "username,name",
        }
        if next_token:
            params["next_token"] = next_token

        url = f"https://api.twitter.com/2/tweets/search/recent?{urllib.parse.urlencode(params)}"
        raw_text = make_request(url, headers=headers)
        if not raw_text:
            break

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            break

        users = {u["id"]: u.get("username") for u in data.get("includes", {}).get("users", [])}
        tweets = data.get("data", [])
        if not tweets:
            break

        for tweet in tweets:
            tid = tweet.get("id")
            author_id = tweet.get("author_id")
            username = users.get(author_id, "unknown")
            metrics = tweet.get("public_metrics", {})
            text_content = tweet.get("text") or ""
            first_line = text_content.strip().split("\n")[0]
            title = first_line[:100] if len(first_line) > 100 else first_line

            results.append({
                "source": "twitter/x",
                "id": str(tid),
                "title": title,
                "url": f"https://x.com/{username}/status/{tid}",
                "author": f"@{username}",
                "created_at": tweet.get("created_at"),
                "comments_count": metrics.get("reply_count", 0),
                "score": metrics.get("like_count", 0),
                "body": text_content[:500],
            })
            if len(results) >= max_items:
                break

        next_token = data.get("meta", {}).get("next_token")
        if not next_token:
            break
        time.sleep(1)

    return results


def main():
    parser = argparse.ArgumentParser(description="Fetch Flutter community discussions from public APIs.")
    parser.add_argument(
        "--source",
        choices=["all", "github", "stackoverflow", "reddit", "twitter"],
        default="all",
        help="Source to query (default: all)",
    )
    parser.add_argument("--query", help="Search query or keyword to filter fetched discussions")
    parser.add_argument("--days", type=int, default=180, help="Days to look back (default: 180)")
    parser.add_argument("--max-items", type=int, default=50, help="Maximum items to fetch per source (default: 50)")
    parser.add_argument("--github-repo", default="flutter/flutter", help="GitHub repository (default: flutter/flutter)")
    parser.add_argument("--github-token", default=os.getenv("GITHUB_TOKEN"), help="GitHub API token for higher rate limits")
    parser.add_argument("--stackexchange-key", default=os.getenv("STACKEXCHANGE_KEY"), help="Stack Exchange API key")
    parser.add_argument("--reddit-sub", default="FlutterDev", help="Subreddit name (default: FlutterDev)")
    parser.add_argument("--reddit-client-id", default=os.getenv("REDDIT_CLIENT_ID"), help="Reddit API client ID")
    parser.add_argument("--reddit-client-secret", default=os.getenv("REDDIT_CLIENT_SECRET"), help="Reddit API client secret")
    parser.add_argument("--twitter-bearer-token", default=os.getenv("TWITTER_BEARER_TOKEN") or os.getenv("X_BEARER_TOKEN"), help="Twitter/X API v2 bearer token")
    parser.add_argument("--twitter-query", default="#Flutter OR #FlutterDev lang:en -is:retweet", help="Search query for Twitter/X (default: #Flutter OR #FlutterDev lang:en -is:retweet)")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help="Directory that relative output paths resolve against "
                             "(default: the repo's gitignored tmp/fetch_flutter_discussions)")
    parser.add_argument("--output-json", help="Path to write JSON output")
    parser.add_argument("--output-sqlite", help="Path to write SQLite database output")
    parser.add_argument("--output-markdown", help="Path to write topic-grouped markdown report")

    args = parser.parse_args()

    # Resolve before fetching so a bad path fails immediately rather than
    # after several minutes of API calls.
    args.output_json = resolve_output(args.output_json, args.output_dir)
    args.output_sqlite = resolve_output(args.output_sqlite, args.output_dir)
    args.output_markdown = resolve_output(args.output_markdown, args.output_dir)

    all_results = []

    if args.source in ["all", "github"]:
        print(f"Fetching GitHub issues from {args.github_repo} (past {args.days} days)...")
        gh_items = fetch_github_issues(
            repo=args.github_repo,
            days=args.days,
            max_items=args.max_items,
            token=args.github_token,
            query=args.query,
        )
        print(f"Found {len(gh_items)} GitHub items.")
        all_results.extend(gh_items)

    if args.source in ["all", "stackoverflow"]:
        print(f"Fetching Stack Overflow questions tagged 'flutter' (past {args.days} days)...")
        so_items = fetch_stackoverflow_questions(
            tag="flutter",
            days=args.days,
            max_items=args.max_items,
            api_key=args.stackexchange_key,
            query=args.query,
        )
        print(f"Found {len(so_items)} Stack Overflow items.")
        all_results.extend(so_items)

    if args.source in ["all", "reddit"]:
        print(f"Fetching Reddit posts from r/{args.reddit_sub} (past {args.days} days)...")
        rd_items = fetch_reddit_posts(
            subreddit=args.reddit_sub,
            days=args.days,
            max_items=args.max_items,
            client_id=args.reddit_client_id,
            client_secret=args.reddit_client_secret,
            query=args.query,
        )
        print(f"Found {len(rd_items)} Reddit items.")
        all_results.extend(rd_items)

    if args.source in ["all", "twitter"]:
        if args.twitter_bearer_token:
            print(f"Fetching Twitter/X posts (past {min(args.days, 7)} days)...")
            tw_items = fetch_twitter_posts(
                query=args.twitter_query,
                days=args.days,
                max_items=args.max_items,
                bearer_token=args.twitter_bearer_token,
            )
            print(f"Found {len(tw_items)} Twitter/X items.")
            all_results.extend(tw_items)
        elif args.source == "twitter":
            print("Skipping Twitter/X: no bearer token provided (set TWITTER_BEARER_TOKEN or pass --twitter-bearer-token).")

    print(f"\nTotal items fetched across all sources: {len(all_results)}")

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=2, ensure_ascii=False)
        print(f"Saved JSON results to {args.output_json}")

    if args.output_sqlite:
        save_to_sqlite(args.output_sqlite, all_results)
        print(f"Saved SQLite records to {args.output_sqlite}")

    if args.output_markdown:
        save_to_markdown(args.output_markdown, all_results)
        print(f"Saved Markdown report to {args.output_markdown}")

    if not args.output_json and not args.output_sqlite and not args.output_markdown:
        for item in all_results[:5]:
            print(f"- [{item['source']}] {item['title']} ({item['url']})")


if __name__ == "__main__":
    main()
