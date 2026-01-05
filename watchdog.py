import os
import requests
import feedparser
import io
import time
from bs4 import BeautifulSoup
from openai import OpenAI
from pypdf import PdfReader

# --- CONFIGURATION ---
FEEDS = [
    # (Name, URL)
    ("OpenAI", "https://openai.com/index/rss"),
    ("Anthropic", "https://www.anthropic.com/rss"),
    ("DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("Meta AI", "https://ai.meta.com/blog/rss.xml"),
]

SEEN_FILE = "seen_reports.txt"

# Initialize OpenAI
try:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
except:
    print("CRITICAL ERROR: OpenAI Key missing.")

def load_seen():
    if not os.path.exists(SEEN_FILE):
        open(SEEN_FILE, 'a').close()
        return []
    with open(SEEN_FILE, "r") as f:
        return f.read().splitlines()

def save_seen(link):
    with open(SEEN_FILE, "a") as f:
        f.write(f"{link}\n")

def send_telegram(message):
    try:
        token = os.environ["TELEGRAM_TOKEN"]
        chat_id = os.environ["TELEGRAM_CHAT_ID"]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        
        # Split long messages if needed
        if len(message) > 4000:
            parts = [message[i:i+4000] for i in range(0, len(message), 4000)]
            for part in parts:
                requests.post(url, json={"chat_id": chat_id, "text": part, "parse_mode": "Markdown"})
        else:
            requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"})
    except Exception as e:
        print(f"Telegram Error: {e}")

def get_pdf_text(url):
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        f = io.BytesIO(response.content)
        reader = PdfReader(f)
        text = ""
        for page in reader.pages[:15]: 
            text += page.extract_text() + "\n"
        return text
    except:
        return None

def fetch_feed_stealth(url):
    """
    Fetches RSS feed using browser headers to bypass Cloudflare blocks.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        return feedparser.parse(response.content)
    except Exception as e:
        print(f"Feed fetch error: {e}")
        return None

def find_hidden_pdf(blog_url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(blog_url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            return None # Scraper blocked
            
        soup = BeautifulSoup(response.content, 'html.parser')
        
        for a in soup.find_all('a', href=True):
            if a['href'].endswith('.pdf'):
                pdf_url = a['href']
                if not pdf_url.startswith('http'):
                    pdf_url = requests.compat.urljoin(blog_url, pdf_url)
                return pdf_url
        
        text = soup.get_text()
        if len(text) < 100: return None
        return text[:15000] 
    except:
        return None

def analyze_with_ai(text, title, source_name):
    print(f"Generating summary for {source_name}...")
    prompt = f"""
    You are an Expert Tech Analyst.
    Source: {source_name} - "{title}".
    
    Summarize this for a Telegram update.
    1. **Headline**: One sentence summary.
    2. **Key Details**: 2-3 sentences on the tech/policy.
    3. **Risk Analysis**: Explicitly look for "evaluations," "safety," "biological/chemical" risks, or "sabotage." If none, say "No specific safety risks reported."
    
    Text:
    {text[:15000]}
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Summary failed: {e}"

def run_watchdog():
    print("Starting stealth scan...")
    seen_links = load_seen()
    
    for name, feed_url in FEEDS:
        print(f"--- Checking {name} ---")
        feed = fetch_feed_stealth(feed_url)
        
        if not feed or len(feed.entries) == 0:
            print(f"⚠️  {name} feed looks empty or blocked.")
            continue
            
        print(f"Found {len(feed.entries)} entries.")
        
        # Check top 5 entries
        for entry in feed.entries[:5]:
            if entry.link not in seen_links:
                print(f"NEW: {entry.title}")
                
                # 1. Try to scrape
                content_data = find_hidden_pdf(entry.link)
                is_pdf = False
                
                # 2. Check PDF
                if content_data and content_data.startswith("http") and content_data.endswith(".pdf"):
                    content_data = get_pdf_text(content_data)
                    is_pdf = True
                
                # 3. Fallback to RSS summary if scraping failed
                if not content_data:
                    print("Scraping blocked. Using RSS fallback.")
                    content_data = getattr(entry, 'summary', '') or getattr(entry, 'description', '')
                
                # 4. Process if we have ANY data
                if content_data and len(content_data) > 50:
                    summary = analyze_with_ai(content_data, entry.title, name)
                    final_msg = f"🔗 [Read {name} Report]({entry.link})\n\n{summary}"
                    send_telegram(final_msg)
                    save_seen(entry.link)
                    time.sleep(2) # Be polite
                else:
                    print("Skipping - No content could be extracted.")

if __name__ == "__main__":
    run_watchdog()
