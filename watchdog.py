import os
import requests
import feedparser
import io
import time
import re
from bs4 import BeautifulSoup
from openai import OpenAI
from pypdf import PdfReader

SEEN_FILE = "seen_reports.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

try:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
except Exception:
    client = None
    print("CRITICAL ERROR: OpenAI Key missing.")

def load_seen():
    if not os.path.exists(SEEN_FILE):
        open(SEEN_FILE, "a").close()
        return set()

    seen = set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                seen.add(line)
    return seen

def save_seen(link):
    with open(SEEN_FILE, "a", encoding="utf-8") as f:
        f.write(f"{link}\n")

def send_telegram(message):
    try:
        token = os.environ["TELEGRAM_TOKEN"]
        chat_id = os.environ["TELEGRAM_CHAT_ID"]
        url = f"https://api.telegram.org/bot{token}/sendMessage"

        if len(message) > 4000:
            parts = [message[i:i+4000] for i in range(0, len(message), 4000)]
        else:
            parts = [message]

        for part in parts:
            requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": part,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": False,
                },
                timeout=20,
            )
    except Exception as e:
        print(f"Telegram Error: {e}")

def get_pdf_text(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        f = io.BytesIO(response.content)
        reader = PdfReader(f)
        text = ""
        for page in reader.pages[:15]:
            extracted = page.extract_text() or ""
            text += extracted + "\n"
        return text.strip()
    except Exception as e:
        print(f"PDF fetch error: {e}")
        return None

def clean_text(text):
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def extract_page_text(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()
        if "pdf" in content_type or url.lower().endswith(".pdf"):
            return get_pdf_text(url)

        soup = BeautifulSoup(response.text, "html.parser")

        # Remove obvious junk
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
            tag.decompose()

        # Prefer article/main containers if present
        container = soup.find("article") or soup.find("main") or soup.body
        if not container:
            return None

        text = clean_text(container.get_text(" ", strip=True))
        if len(text) < 100:
            return None

        return text[:20000]
    except Exception as e:
        print(f"Page fetch error for {url}: {e}")
        return None

def analyze_with_ai(text, title, source_name, link):
    if not client:
        return "Summary failed: OPENAI_API_KEY missing."

    prompt = f"""
You are an expert AI risk and industry analyst.

Source: {source_name}
Title: {title}
URL: {link}

Write a Telegram-ready brief with this exact structure:

*Headline*: one sentence.
*Key Details*: 2-4 sentences explaining what happened.
*Threat / Strategic Analysis*: 2-4 sentences focused on model capability, safety, policy, competition, bio/cyber misuse, governance, or strategic implications.
*Why It Matters*: one concise sentence.

Be concrete. Do not be fluffy. If there is no obvious safety or threat angle, say so clearly.

Article text:
{text[:15000]}
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Summary failed: {e}"

def fetch_rss_entries(source_name, feed_url, limit=5):
    try:
        response = requests.get(feed_url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        feed = feedparser.parse(response.content)

        entries = []
        for entry in feed.entries[:limit]:
            entries.append({
                "source": source_name,
                "title": getattr(entry, "title", "Untitled"),
                "link": getattr(entry, "link", None),
                "summary": getattr(entry, "summary", "") or getattr(entry, "description", ""),
            })
        return entries
    except Exception as e:
        print(f"RSS fetch error for {source_name}: {e}")
        return []

def fetch_anthropic_news(limit=8):
    url = "https://www.anthropic.com/news"
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        items = []
        seen_links = set()

        # Grab all newsroom links that look like article cards/list items
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            text = clean_text(a.get_text(" ", strip=True))

            if not href:
                continue

            if href.startswith("/"):
                href = requests.compat.urljoin(url, href)

            if not href.startswith("https://www.anthropic.com/news/"):
                continue

            if href in seen_links:
                continue

            if len(text) < 12:
                continue

            seen_links.add(href)
            items.append({
                "source": "Anthropic",
                "title": text,
                "link": href,
                "summary": "",
            })

            if len(items) >= limit:
                break

        return items
    except Exception as e:
        print(f"Anthropic scrape error: {e}")
        return []

def fetch_openai_news(limit=8):
    # Official RSS link exposed from OpenAI news footer
    rss_url = "https://openai.com/news/rss.xml"
    return fetch_rss_entries("OpenAI", rss_url, limit=limit)

def fetch_deepmind_news(limit=8):
    rss_url = "https://deepmind.google/blog/rss.xml"
    return fetch_rss_entries("DeepMind", rss_url, limit=limit)

def run_watchdog():
    print("Starting watchdog scan...")
    seen_links = load_seen()

    sources = [
        ("OpenAI", fetch_openai_news),
        ("Anthropic", fetch_anthropic_news),
        ("DeepMind", fetch_deepmind_news),
    ]

    for source_name, fetcher in sources:
        print(f"--- Checking {source_name} ---")
        entries = fetcher()

        if not entries:
            print(f"⚠️ No entries found for {source_name}")
            continue

        print(f"Found {len(entries)} entries for {source_name}")

        for entry in entries[:5]:
            link = entry.get("link")
            title = entry.get("title", "Untitled")
            summary_fallback = entry.get("summary", "")

            if not link or link in seen_links:
                continue

            print(f"NEW: {title}")

            content = extract_page_text(link)

            if not content or len(content) < 100:
                content = summary_fallback

            if not content or len(content) < 50:
                print("Skipping - insufficient content.")
                continue

            summary = analyze_with_ai(content, title, source_name, link)

            final_msg = (
                f"*{source_name} Update*\n"
                f"[Read article]({link})\n\n"
                f"{summary}"
            )

            send_telegram(final_msg)
            save_seen(link)
            seen_links.add(link)
            time.sleep(2)

if __name__ == "__main__":
    run_watchdog()
