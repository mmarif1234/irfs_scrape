"""
IRFS Allied Lines — Download PDFs from Gmail.

Reads submitted.csv (produced by submit_requests.py),
polls Gmail for OIR response emails, and downloads PDFs.

Usage:
    python download_pdfs.py
    python download_pdfs.py --year 2024         # only process filings from that year's run
    python download_pdfs.py --log submitted.csv  # custom log file
"""

import argparse
import base64
import csv
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ─── CONFIG ───────────────────────────────────────────────────────────────────

DOWNLOAD_DIR     = Path("./downloads")
SUBMITTED_LOG    = Path("./submitted.csv")
DOWNLOADED_LOG   = Path("./downloaded.csv")
CREDENTIALS_FILE = Path("./credentials.json")
TOKEN_FILE       = Path("./token.json")
GMAIL_SCOPES     = ["https://www.googleapis.com/auth/gmail.readonly"]

GMAIL_POLL_INTERVAL = 15   # seconds between checks
GMAIL_POLL_TIMEOUT  = 300  # seconds to wait per filing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("download.log")],
)
log = logging.getLogger(__name__)

DOWNLOADED_HEADERS = ["fileLogNumber", "companyName", "downloadStatus", "filePath", "timestamp"]

# ─── GMAIL ────────────────────────────────────────────────────────────────────

def get_gmail_service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def decode_body(part) -> str:
    if part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
    for sub in part.get("parts", []):
        result = decode_body(sub)
        if result:
            return result
    return ""


def extract_download_url(html: str) -> str | None:
    patterns = [
        r'href=["\']?(https?://irfssearch\.[^"\'>\s]+(?:Download|download|Request)[^"\'>\s]*)',
        r'(https?://irfssearch\.(?:floir|fldfs)\.(?:gov|com)[^\s"\'<>]+)',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            return m.group(1).replace("&amp;", "&")
    return None


def wait_for_email(service, file_log_number: str, after_ts: int) -> str | None:
    """Poll Gmail for an OIR email for this filing. Returns download URL or None."""
    deadline = time.time() + GMAIL_POLL_TIMEOUT
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
                        url = url.strip().strip("'\"")
                        log.info(f"  📧 Download URL: {url}")
                        return url
        except Exception as e:
            log.warning(f"  Gmail poll error: {e}")
    return None

# ─── DOWNLOAD ─────────────────────────────────────────────────────────────────




def download_pdf_via_browser(download_page_url: str, file_log_number: str) -> Path | None:
    """
    Download PDF from the IRFS request URL.
    The URL contains id + sid which authenticates the request — no login needed.
    The page has a Download link that points directly to the PDF.
    """
    file_path = DOWNLOAD_DIR / f"{file_log_number}.pdf"
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})

        # Fetch the download page
        resp = session.get(download_page_url, timeout=30)
        resp.raise_for_status()
        html = resp.text

        # Check for expiry message
        if "expired" in html.lower() or "no longer accessible" in html.lower():
            log.warning(f"  ⚠️  Request expired — need to resubmit")
            return None

        # Find the Download link — from image 5 it was a plain "Download" text link
        href = None
        for pattern in [
            r'<a[^>]+href="([^"]+)"[^>]*>\s*Download\s*</a>',
            r'href="([^"]*[Dd]ownload[^"]*\.pdf[^"]*)"',
            r'href="([^"]*GetFile[^"]*)"',
            r'href="([^"]*Request[^"]*Download[^"]*)"',
            r'<a[^>]+href="([^"]+)"',  # last resort: first link
        ]:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                href = m.group(1).replace("&amp;", "&")
                log.info(f"  Found link: {href[:100]}")
                break

        if not href:
            log.info(f"  Page snippet: {html[:800]}")
            return None

        if not href.startswith("http"):
            from urllib.parse import urljoin
            href = urljoin(download_page_url, href)

        pdf_resp = session.get(href, timeout=60, stream=True)
        pdf_resp.raise_for_status()

        with open(file_path, "wb") as f:
            for chunk in pdf_resp.iter_content(chunk_size=8192):
                f.write(chunk)

        size_kb = file_path.stat().st_size // 1024
        log.info(f"  💾 Saved: {file_log_number}.pdf ({size_kb} KB)")
        return file_path

    except Exception as e:
        log.warning(f"  Download failed: {e}")
        return None

# ─── CSV HELPERS ──────────────────────────────────────────────────────────────

def load_submitted(log_file: Path) -> list[dict]:
    if not log_file.exists():
        return []
    with log_file.open() as f:
        return [r for r in csv.DictReader(f) if r.get("submitStatus") == "submitted"]


def load_downloaded() -> set:
    done = set()
    if not DOWNLOADED_LOG.exists():
        return done
    with DOWNLOADED_LOG.open() as f:
        for row in csv.DictReader(f):
            if row.get("downloadStatus") == "done":
                done.add(row["fileLogNumber"])
    return done


def append_downloaded(record: dict):
    file_exists = DOWNLOADED_LOG.exists()
    with DOWNLOADED_LOG.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DOWNLOADED_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(record)

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Download IRFS PDFs from Gmail")
    parser.add_argument("--log", default="submitted.csv", help="Path to submitted.csv")
    parser.add_argument("--year", type=int, help="Only process filings from this year")
    return parser.parse_args()


def main():
    args = parse_args()
    log_path = Path(args.log)

    log.info("🚀 IRFS Download PDFs from Gmail\n")

    if not CREDENTIALS_FILE.exists():
        log.error("❌ credentials.json not found. See GMAIL_SETUP.md.")
        sys.exit(1)

    DOWNLOAD_DIR.mkdir(exist_ok=True)
    gmail = get_gmail_service()

    submitted = load_submitted(log_path)
    if not submitted:
        log.error(f"❌ No submitted filings found in {log_path}")
        sys.exit(1)

    if args.year:
        submitted = [r for r in submitted if str(args.year) in r.get("dateFiled", "")]
        log.info(f"  Filtered to year {args.year}: {len(submitted)} filings")

    already_done = load_downloaded()
    pending = [r for r in submitted if r["fileLogNumber"] not in already_done]
    log.info(f"📋 {len(submitted)} submitted | {len(already_done)} already downloaded | {len(pending)} pending\n")

    # Use current time as the "after" timestamp for Gmail search
    after_ts = int(time.time()) - 86400  # look back 24h to catch any already-sent emails

    for i, filing in enumerate(pending, 1):
        fln     = filing["fileLogNumber"]
        company = filing["companyName"]
        log.info(f"[{i}/{len(pending)}] {fln} — {company}")
        log.info(f"  📬 Waiting for email...")

        url = wait_for_email(gmail, fln, after_ts)
        if not url:
            log.warning(f"  ⏰ Timed out waiting for {fln}")
            append_downloaded({
                "fileLogNumber": fln,
                "companyName":   company,
                "downloadStatus": "timeout",
                "filePath":      "",
                "timestamp":     __import__("datetime").datetime.utcnow().isoformat(),
            })
            continue

        log.info(f"  ✅ Email received, downloading immediately...")
        path = download_pdf_via_browser(url, fln)
        if path is None:
            # Mark as expired so we know to resubmit
            log.warning(f"  ⚠️  Download failed — link may have expired. Resubmit this filing.")
            status = "expired"
        else:
            status = "done"
        append_downloaded({
            "fileLogNumber": fln,
            "companyName":   company,
            "downloadStatus": status,
            "filePath":      str(path or ""),
            "timestamp":     __import__("datetime").datetime.utcnow().isoformat(),
        })

    done_count = len(load_downloaded())
    log.info(f"\n🏁 Done! {done_count} PDFs downloaded to {DOWNLOAD_DIR.resolve()}")


if __name__ == "__main__":
    main()