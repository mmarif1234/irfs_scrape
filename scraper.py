"""
IRFS Allied Lines Scraper
Downloads all Allied Lines rate filings from Florida OIR (2015-2026).

Prerequisites:
  pip3 install -r requirements.txt
  python3 -m playwright install chromium
  export ANTHROPIC_API_KEY=sk-ant-...
  credentials.json in this folder (see GMAIL_SETUP.md)
  ProtonVPN connected to a US server
"""

import os
import re
import sys
import csv
import time
import base64
import json
import logging
import traceback
from pathlib import Path
from datetime import datetime

import requests
import anthropic
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ─── LOGGING ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("scraper.log"),
    ],
)
log = logging.getLogger(__name__)

# ─── CONFIG ───────────────────────────────────────────────────────────────────

EMAIL = "mohammadmustafa.arif1998@gmail.com"
DOWNLOAD_DIR = Path("./downloads")
LOG_FILE = Path("./log.csv")
SEARCH_URL = "https://irfssearch.floir.gov/"

DATE_FROM = "01/01/2015"
DATE_TO = "12/31/2026"

DELAY_BETWEEN_FILINGS = 4.0   # seconds
DELAY_BETWEEN_PAGES   = 3.0
DELAY_AFTER_SUBMIT    = 2.0

GMAIL_POLL_INTERVAL = 15      # seconds between Gmail checks
GMAIL_POLL_TIMEOUT  = 300     # seconds to wait for each email (5 min)

CREDENTIALS_FILE = Path("./credentials.json")
TOKEN_FILE       = Path("./token.json")
GMAIL_SCOPES     = ["https://www.googleapis.com/auth/gmail.readonly"]

LOG_HEADERS = ["fileLogNumber", "companyName", "finalAction",
               "dateClosed", "downloadStatus", "notes", "timestamp"]

# ─── GMAIL ────────────────────────────────────────────────────────────────────

def get_gmail_service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            log.info("📧 Gmail authorization required (one-time)...")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
        log.info("✅ Gmail authorized and token saved.")

    return build("gmail", "v1", credentials=creds)


def decode_body(part):
    """Recursively decode a Gmail message part to text."""
    if part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
    for sub in part.get("parts", []):
        result = decode_body(sub)
        if result:
            return result
    return ""


def extract_download_url(html: str) -> str | None:
    """Pull the IRFS download page URL from the email body."""
    patterns = [
        r'href="(https?://irfssearch\.[^"]+(?:Download|download|Request)[^"]*?)"',
        r'(https?://irfssearch\.(?:floir|fldfs)\.(?:gov|com)[^\s"<>]+)',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            return m.group(1).replace("&amp;", "&")
    return None


def wait_for_oir_email(service, file_log_number: str, submitted_at: float) -> str:
    """
    Poll Gmail until an OIR email arrives for this filing.
    Returns the download page URL.
    """
    deadline = time.time() + GMAIL_POLL_TIMEOUT
    after_ts = int(submitted_at)
    log.info(f"  📬 Waiting for email ({file_log_number})...")

    while time.time() < deadline:
        time.sleep(GMAIL_POLL_INTERVAL)
        try:
            results = service.users().messages().list(
                userId="me",
                q=f"from:NOREPLY-IRFS.Admin@floir.com after:{after_ts}",
                maxResults=20,
            ).execute()

            for msg in results.get("messages", []):
                detail = service.users().messages().get(
                    userId="me", id=msg["id"], format="full"
                ).execute()
                body = decode_body(detail["payload"])
                if file_log_number in body:
                    url = extract_download_url(body)
                    if url:
                        log.info(f"  ✅ Email received for {file_log_number}")
                        return url
        except Exception as e:
            log.warning(f"  ⚠️  Gmail poll error: {e}")

    raise TimeoutError(f"No email received for {file_log_number} within {GMAIL_POLL_TIMEOUT}s")


# ─── CAPTCHA SOLVER ───────────────────────────────────────────────────────────

def solve_captcha(page, client: anthropic.Anthropic) -> str:
    """Screenshot the captcha image, send to Claude, return the numeric answer."""
    # Try to find the captcha image element
    for selector in [
        "img[src*='captcha' i]",
        "img[src*='Captcha']",
        "img[alt*='captcha' i]",
    ]:
        try:
            el = page.locator(selector).first
            el.wait_for(timeout=5000)
            img_bytes = el.screenshot()
            break
        except Exception:
            img_bytes = None

    if not img_bytes:
        # Fallback: screenshot the area around the answer input
        try:
            answer_input = page.locator("input[placeholder*='answer' i]").first
            # Get bounding box and expand upward to capture the image above
            box = answer_input.bounding_box()
            img_bytes = page.screenshot(
                clip={"x": box["x"] - 50, "y": box["y"] - 80,
                      "width": 250, "height": 100}
            )
        except Exception:
            img_bytes = page.screenshot()

    b64 = base64.standard_b64encode(img_bytes).decode()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=50,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64",
                                              "media_type": "image/png",
                                              "data": b64}},
                {"type": "text",
                 "text": ("This is a math captcha showing a simple equation like '20 + 5 = ?'. "
                          "Read the numbers and operator carefully and reply with ONLY the "
                          "numeric answer. No words, no punctuation — just the number.")}
            ]
        }]
    )
    answer = re.sub(r"[^0-9]", "", response.content[0].text.strip())
    log.info(f"  🔢 Captcha answer: {answer}")
    return answer


# ─── CSV LOG ──────────────────────────────────────────────────────────────────

def load_done_filings() -> set:
    """Return set of fileLogNumbers already successfully downloaded."""
    done = set()
    if not LOG_FILE.exists():
        return done
    with LOG_FILE.open() as f:
        for row in csv.DictReader(f):
            if row.get("downloadStatus") == "done":
                done.add(row["fileLogNumber"])
    return done


def append_log(record: dict):
    file_exists = LOG_FILE.exists()
    with LOG_FILE.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(record)


# ─── PDF DOWNLOADER ───────────────────────────────────────────────────────────

def download_pdf(download_page_url: str, file_log_number: str,
                 context) -> bool:
    """Open the IRFS download page in the browser and save the PDF."""
    file_path = DOWNLOAD_DIR / f"{file_log_number}.pdf"
    page = context.new_page()
    try:
        page.goto(download_page_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(1)

        # Find the Download link
        try:
            download_link = page.locator("a:has-text('Download')").first
            href = download_link.get_attribute("href")
        except Exception:
            # Try any link with 'download' in the URL
            links = page.locator("a").all()
            href = None
            for lnk in links:
                h = lnk.get_attribute("href") or ""
                if "download" in h.lower() or h.endswith(".pdf"):
                    href = h
                    break

        if not href:
            log.warning(f"  ⚠️  No download link found on page for {file_log_number}")
            return False

        # Make absolute URL
        if not href.startswith("http"):
            from urllib.parse import urljoin
            href = urljoin(download_page_url, href)

        # Grab cookies from the browser context for the requests download
        cookies = {c["name"]: c["value"] for c in context.cookies()}
        resp = requests.get(href, cookies=cookies, timeout=60, stream=True)
        resp.raise_for_status()

        with open(file_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        size_kb = file_path.stat().st_size // 1024
        log.info(f"  💾 Saved: {file_log_number}.pdf ({size_kb} KB)")
        return True

    except Exception as e:
        log.warning(f"  ❌ Download failed for {file_log_number}: {e}")
        return False
    finally:
        page.close()


# ─── SEARCH & ITERATE ─────────────────────────────────────────────────────────

def set_up_search(page):
    """Fill in the Advanced Search form."""
    log.info("🔍 Opening IRFS Advanced Search...")
    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(1)

    # Click Advanced Search tab
    page.click("text=Advanced Search")
    time.sleep(1)

    # Select Property & Casualty
    try:
        page.click("label:has-text('Property & Casualty')")
    except Exception:
        try:
            page.check("input[value='PC']")
        except Exception:
            pass
    time.sleep(0.5)

    # Enable Line of Business checkbox and pick Allied Lines
    try:
        lob_cb = page.locator("tr", has_text="Line of Business").first.locator("input[type='checkbox']").first
        if not lob_cb.is_checked():
            lob_cb.check()
        time.sleep(0.3)
        page.select_option("select", label=re.compile(r"Allied Lines", re.IGNORECASE))
        time.sleep(0.3)
    except Exception as e:
        log.warning(f"  ⚠️  Could not set Line of Business: {e}")

    # Enable Date Filed checkbox and set range
    try:
        date_row = page.locator("tr", has_text="Date Filed").first
        date_cb = date_row.locator("input[type='checkbox']").first
        if not date_cb.is_checked():
            date_cb.check()
        time.sleep(0.3)
        inputs = date_row.locator("input[type='text']").all()
        inputs[0].fill(DATE_FROM)
        inputs[1].fill(DATE_TO)
        time.sleep(0.3)
    except Exception as e:
        log.warning(f"  ⚠️  Could not set Date Filed: {e}")

    # Submit
    log.info("⏳ Submitting search...")
    try:
        page.click("input[type='submit'][value*='Search']")
    except Exception:
        page.click("button:has-text('Search')")

    page.wait_for_selector("text=rows returned", timeout=30000)
    time.sleep(1)

    # Try to maximize rows per page
    try:
        rpp = page.locator("input[id*='rows'], input[name*='rows']").first
        rpp.fill("100")
        rpp.press("Enter")
        time.sleep(2)
    except Exception:
        pass

    total_text = page.locator("text=/Total of \\d+ rows/").text_content()
    total = int(re.search(r"(\d+)", total_text).group(1))
    log.info(f"\n📊 Found {total} filings total.\n")
    return total


def collect_rows(page) -> list[dict]:
    """Collect filing info from all visible result rows."""
    filings = []
    rows = page.locator("table tbody tr").all()
    for row in rows:
        try:
            cells = row.locator("td").all()
            if len(cells) < 3:
                continue
            file_log = cells[1].text_content().strip()
            if not re.match(r"\d{2}-\d{6}", file_log):
                continue
            filings.append({
                "fileLogNumber": file_log,
                "companyName":   cells[2].text_content().strip(),
                "dateClosed":    cells[3].text_content().strip() if len(cells) > 3 else "",
                "finalAction":   cells[4].text_content().strip() if len(cells) > 4 else "",
            })
        except Exception:
            continue
    return filings


# ─── PROCESS ONE FILING ───────────────────────────────────────────────────────

def process_filing(page, filing: dict, anthropic_client, gmail_service, context) -> str:
    """
    Click a filing, submit the document request, wait for email, download PDF.
    Returns 'done', 'failed', or 'error'.
    """
    fln = filing["fileLogNumber"]

    # Click the row to expand it
    try:
        row = page.locator(f"td:text-is('{fln}')").first.locator("..").first
        row.locator("td").first.click()
        time.sleep(1.5)
    except Exception as e:
        log.warning(f"  ⚠️  Could not click row: {e}")

    # Click the filing actions / document request icon
    clicked = False
    for selector in [
        "img[title*='request' i]",
        "img[src*='copy' i]",
        "button[title*='Document' i]",
        ".filing-doc-icon",
        "img[title*='filing' i]",
    ]:
        try:
            btn = page.locator(selector).last
            btn.click(timeout=4000)
            clicked = True
            break
        except Exception:
            continue

    if not clicked:
        # Try clicking the icon in the expanded section at the bottom
        try:
            page.locator("td[colspan]").last.locator("img, button").first.click(timeout=4000)
            clicked = True
        except Exception:
            raise RuntimeError("Could not find filing actions button")

    time.sleep(1.5)

    # ── Modal: select request type ────────────────────────────────────────────
    try:
        page.select_option("select", label=re.compile(r"Single request.*entire filing", re.IGNORECASE))
        time.sleep(0.3)
    except Exception:
        pass  # May already be selected

    # Fill email
    email_input = page.locator("input[type='email'], input[name*='email' i]").first
    email_input.fill(EMAIL)

    # Solve captcha (retry once if wrong)
    for attempt in range(2):
        answer = solve_captcha(page, anthropic_client)
        captcha_input = page.locator("input[placeholder*='answer' i], input[id*='captcha' i]").first
        captcha_input.fill(answer)

        submitted_at = time.time()
        page.click("button:has-text('Submit'), input[value='Submit']")
        time.sleep(DELAY_AFTER_SUBMIT)

        # Check for captcha error message
        try:
            err = page.locator("text=/incorrect|wrong|invalid/i").first
            err.wait_for(timeout=2000)
            log.warning(f"  ⚠️  Captcha wrong, retrying...")
            # Re-fill email (modal may have reset)
            email_input.fill(EMAIL)
            continue
        except Exception:
            break  # No error = success

    # Close modal
    try:
        page.click("button:has-text('Close')", timeout=2000)
    except Exception:
        pass

    # ── Wait for Gmail and download ────────────────────────────────────────────
    download_url = wait_for_oir_email(gmail_service, fln, submitted_at)
    success = download_pdf(download_url, fln, context)
    return "done" if success else "failed"


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    log.info("🚀 IRFS Allied Lines Scraper starting...\n")

    # Preflight checks
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("❌  ANTHROPIC_API_KEY not set.  Run: export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)
    if not CREDENTIALS_FILE.exists():
        log.error("❌  credentials.json not found. Follow GMAIL_SETUP.md first.")
        sys.exit(1)

    DOWNLOAD_DIR.mkdir(exist_ok=True)

    anthropic_client = anthropic.Anthropic(api_key=api_key)
    gmail_service    = get_gmail_service()
    done_filings     = load_done_filings()
    log.info(f"📋 {len(done_filings)} filings already downloaded, will skip.\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=150)
        context = browser.new_context()
        page    = context.new_page()

        try:
            total = set_up_search(page)
            processed = 0
            page_num   = 1

            while True:
                log.info(f"\n📄 Page {page_num}...")
                filings = collect_rows(page)
                log.info(f"  {len(filings)} filings on this page.")

                for filing in filings:
                    processed += 1
                    fln = filing["fileLogNumber"]
                    company = filing["companyName"]
                    action  = filing["finalAction"] or "OPEN"
                    log.info(f"\n[{processed}/{total}] {fln} — {company} ({action})")

                    if fln in done_filings:
                        log.info("  ⏭️  Already downloaded, skipping.")
                        continue

                    status = "error"
                    notes  = ""
                    try:
                        status = process_filing(page, filing, anthropic_client,
                                                gmail_service, context)
                    except Exception as e:
                        notes = str(e)[:120]
                        log.warning(f"  ❌ {fln}: {notes}")
                        # Dismiss any open modal
                        try:
                            page.keyboard.press("Escape")
                        except Exception:
                            pass
                        try:
                            page.click("button:has-text('Close')", timeout=1000)
                        except Exception:
                            pass

                    append_log({
                        "fileLogNumber": fln,
                        "companyName":   company,
                        "finalAction":   action,
                        "dateClosed":    filing["dateClosed"],
                        "downloadStatus": status,
                        "notes":         notes,
                        "timestamp":     datetime.utcnow().isoformat(),
                    })

                    time.sleep(DELAY_BETWEEN_FILINGS)

                # ── Next page ─────────────────────────────────────────────────
                try:
                    next_btn = page.locator(
                        "a:has-text('Next'), button:has-text('Next'), [aria-label='Next page']"
                    ).first
                    if next_btn.is_disabled():
                        raise Exception("disabled")
                    next_btn.click()
                    page.wait_for_selector("text=rows returned", timeout=15000)
                    time.sleep(DELAY_BETWEEN_PAGES)
                    page_num += 1
                except Exception:
                    log.info("\n✅ All pages done!")
                    break

        except KeyboardInterrupt:
            log.info("\n⛔  Interrupted by user. Progress saved — re-run to continue.")
        except Exception:
            log.error(f"\n💥 Fatal error:\n{traceback.format_exc()}")
        finally:
            browser.close()

    done_count = len(load_done_filings())
    log.info(f"\n🏁 Done! {done_count} filings downloaded.")
    log.info(f"📁 PDFs: {DOWNLOAD_DIR.resolve()}")
    log.info(f"📋 Log:  {LOG_FILE.resolve()}")


if __name__ == "__main__":
    main()
