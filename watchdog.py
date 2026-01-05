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
    "https://openai.com/index/rss",
    "https://www.anthropic.com/rss", 
    "https://deepmind.google/blog/rss.xml",
    "https://ai.meta.com/blog/rss.xml", # Added Meta AI
]

SEEN_FILE = "seen_reports.txt"
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

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
    token = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    # Chunking for long messages
    if len(message) > 4000:
        parts = [message[i:i+4000] for i in range(0, len(message), 4000)]
        for part in parts:
            requests.post(url, json={"chat_id": chat_id, "text": part, "parse_mode": "Markdown"})
    else:
        requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"})

def get_pdf_text(url):
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        f = io.BytesIO(response.content)
        reader = PdfReader(f)
        text = ""
        # Read first 15 pages (usually contains the executive summary & risks)
        for page in reader.pages[:15]: 
            text += page.extract_text() + "\n"
        return text
    except:
        return None

def find_hidden_pdf(blog_url):
    try:
        response = requests.get(blog_url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Priority: Find a PDF link
        for a in soup.find_all('a', href=True):
            if a['href'].endswith('.pdf'):
                pdf_url = a['href']
                if not pdf_url.startswith('http'):
                    pdf_url = requests.compat.urljoin(blog_url, pdf_url)
                return pdf_url
        
        # Fallback: Just return blog text
        return soup.get_text()[:15000] 
    except:
        return None

def analyze_with_ai(text, title, is_pdf):
    source_type = "Technical PDF Report" if is_pdf else "Blog Post"
    
    prompt = f"""
    You are a Tech Reporter for an elite audience.
    I have a {source_type} titled "{title}".
    
    CRITICAL INSTRUCTION:
    If this text is just generic marketing (e.g., "We hired a new VP" or "Customer success story"), reply with EXACTLY: "SKIP_marketing".
    
    If it is a real technical update or safety report, summarize it using this structure:
    
    1. **The Headline**: One sentence explaining what happened in plain English.
    2. **The Details**: A brief paragraph (3-4 sentences). Explain it as if you are reading it aloud to a smart friend. Simplify the jargon.
    3. **Safety & Risks** (Crucial): Did they mention "red teaming," "biological risks," "sabotage," or "evaluations"? If yes, detail it. If no, say "No specific safety risks mentioned."
    4. **The "So What?"**: Why does this matter?
    
    Keep the tone conversational but serious. Use emojis for bullet points.
    
    TEXT:
    {text[:20000]}
    """

    response = client.chat.completions.create(
        model="gpt-4o-mini", 
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

def run_watchdog():
    print("Checking feeds...")
    seen_links = load_seen()
    
    for feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            # Check only the newest 2 items to save API costs
            for entry in feed.entries[:2]:
                if entry.link not in seen_links:
                    print(f"Investigating: {entry.title}")
                    
                    # 1. Scrape
                    content_data = find_hidden_pdf(entry.link)
                    is_pdf = False
                    
                    if content_data and content_data.startswith("http") and content_data.endswith(".pdf"):
                        content_data = get_pdf_text(content_data)
                        is_pdf = True
                    
                    if content_data:
                        # 2. Analyze
                        summary = analyze_with_ai(content_data, entry.title, is_pdf)
                        
                        # 3. Filter Marketing Fluff
                        if "SKIP_marketing" not in summary:
                            # Put Link at the VERY TOP
                            final_msg = f"🔗 [Read Full Report]({entry.link})\n\n{summary}"
                            send_telegram(final_msg)
                        else:
                            print("Skipped marketing fluff.")
                        
                        # 4. Mark as seen (even if we skipped it, so we don't check again)
                        save_seen(entry.link)
                        time.sleep(2)
        except Exception as e:
            print(f"Error checking feed {feed_url}: {e}")

if __name__ == "__main__":
    run_watchdog()
