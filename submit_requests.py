"""
IRFS Allied Lines — Submit document requests.

Searches IRFS for Allied Lines filings in a given year range,
submits document requests (with manual captcha), and logs to submitted.csv.
Run download_pdfs.py separately to fetch the PDFs from Gmail.

Usage:
    python submit_requests.py --year 2024
    python submit_requests.py --from-year 2020 --to-year 2022
    python submit_requests.py --from-year 2015 --to-year 2026  # all years
"""

import argparse
import csv
import logging
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

# ─── CONFIG ───────────────────────────────────────────────────────────────────

EMAIL       = "mohammadmustafa.arif1998@gmail.com"
LOG_FILE    = Path("./submitted.csv")
SEARCH_URL  = "https://irfssearch.floir.gov/"

DELAY_BETWEEN_FILINGS = 3.0
DELAY_BETWEEN_PAGES   = 2.0
DELAY_AFTER_SUBMIT    = 2.0

LOG_HEADERS = ["fileLogNumber", "companyName", "finalAction", "dateFiled",
               "dateClosed", "submitStatus", "notes", "timestamp"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("submit.log")],
)
log = logging.getLogger(__name__)

# ─── CSV ──────────────────────────────────────────────────────────────────────

def load_submitted() -> set:
    done = set()
    if not LOG_FILE.exists():
        return done
    with LOG_FILE.open() as f:
        for row in csv.DictReader(f):
            if row.get("submitStatus") == "submitted":
                done.add(row["fileLogNumber"])
    return done


def append_log(record: dict):
    file_exists = LOG_FILE.exists()
    with LOG_FILE.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(record)

# ─── SEARCH SETUP ─────────────────────────────────────────────────────────────

def set_up_search(page, date_from: str, date_to: str):
    log.info(f"🔍 Searching Allied Lines: {date_from} → {date_to}")
    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("text=Advanced Search", timeout=30000)
    time.sleep(1)

    page.click("text=Advanced Search")
    time.sleep(1.5)

    # P&C radio
    page.evaluate('() => { const r = document.querySelector(\'input[name="bureauArea"][value="1"]\'); if(r) r.click(); }')
    time.sleep(1.0)

    # LOB checkbox
    page.evaluate('() => { const cb = document.getElementById("selLobSh"); if(cb && !cb.checked) cb.click(); }')
    time.sleep(0.5)

    # Wait for Allied Lines option then select it
    try:
        page.wait_for_function("""() => {
            const input = document.getElementById('selLob');
            if (!input) return false;
            try { const w = kendo.widgetInstance($(input)); return w && w.dataSource.data().length > 0; }
            catch(e) { return false; }
        }""", timeout=15000)
    except Exception:
        pass

    lob = page.evaluate("""() => {
        const input = document.getElementById('selLob');
        if (!input) return 'no input';
        try {
            const w = kendo.widgetInstance($(input));
            const items = w.dataSource.data();
            for (const item of items) {
                if ((item.NameAndCode||'').toLowerCase().includes('allied lines')) {
                    w.value(item.Id); w.trigger('change');
                    return 'set: ' + item.NameAndCode;
                }
            }
            return 'not found, options: ' + items.length;
        } catch(e) { return 'error: ' + e.message; }
    }""")
    log.info(f"  LOB: {lob}")
    time.sleep(0.3)

    # Date Filed checkbox + dates
    page.evaluate('() => { const cb = document.getElementById("fileDtSh"); if(cb && !cb.checked) cb.click(); }')
    time.sleep(0.5)

    # Use Kendo DatePicker API to set dates (plain .value assignment is ignored by Kendo)
    dates_set = page.evaluate(f"""() => {{
        try {{
            const fromPicker = $('#fileDtFrom').data('kendoDatePicker');
            const toPicker   = $('#fileDtTo').data('kendoDatePicker');
            if (!fromPicker || !toPicker) return 'pickers not found';
            fromPicker.value('{date_from}');
            fromPicker.trigger('change');
            toPicker.value('{date_to}');
            toPicker.trigger('change');
            return 'set: ' + fromPicker.value() + ' → ' + toPicker.value();
        }} catch(e) {{ return 'error: ' + e.message; }}
    }}""")
    log.info(f"  Dates: {dates_set}")
    time.sleep(0.3)

    # Uncheck Date Closed and Final Action (default-checked)
    page.evaluate('() => { const cb = document.getElementById("closeDtSh"); if(cb && cb.checked) cb.click(); }')
    page.evaluate('() => { const cb = document.getElementById("selFinActSh"); if(cb && cb.checked) cb.click(); }')
    time.sleep(0.3)

    # Submit search
    for selector in ["input[type='submit'][value='Search']", "button:has-text('Search')", "#btnSearch"]:
        try:
            page.locator(selector).first.click(timeout=3000)
            break
        except Exception:
            continue

    page.wait_for_selector("#resultsMessage", timeout=60000)
    time.sleep(1)

    # Click View All
    try:
        page.click("#viewall", timeout=5000)
        time.sleep(2)
    except Exception:
        pass

    msg = page.locator("#resultsMessage").text_content(timeout=5000)
    total = int(re.search(r"(\d+)", msg).group(1))
    log.info(f"  Found {total} filings\n")
    return total


def collect_rows(page) -> list[dict]:
    result = page.evaluate("""() => {
        try {
            const grid = $('#resultsGrid').data('kendoGrid');
            if (!grid) return {error: 'no grid'};
            return { filings: grid.dataSource.data().toJSON().map(item => ({
                fileLogNumber: item.FileLogNumber || '',
                companyName:   (item.CoNm || '').trim(),
                dateFiled:     item.FileDt || '',
                dateClosed:    item.CloseDt || '',
                finalAction:   item.FinalAction || '',
                filingId:      item.FilingId || '',
                isRestricted:  item.IsRestricted || false,
            }))};
        } catch(e) { return {error: e.message}; }
    }""")
    if result.get("error"):
        log.warning(f"  Kendo error: {result['error']}")
        return []
    filings = result.get("filings", [])
    log.info(f"  {len(filings)} filings from dataSource")
    return filings

# ─── SUBMIT ONE FILING ────────────────────────────────────────────────────────

def submit_filing(page, filing: dict) -> str:
    fln = filing["fileLogNumber"]
    if filing.get("isRestricted"):
        log.info("  🔒 Restricted — skipping")
        return "restricted"

    # Expand row
    expanded = page.evaluate(f"""() => {{
        for (const cell of document.querySelectorAll('#resultsGrid td')) {{
            if (cell.innerText.trim() === '{fln}') {{
                const row = cell.closest('tr');
                if (!row) return 'no row';
                if (row.getAttribute('aria-expanded') === 'true') return 'already expanded';
                const caret = row.querySelector('td.k-hierarchy-cell a');
                if (caret) {{ caret.click(); return 'clicked'; }}
            }}
        }}
        return 'not found';
    }}""")
    time.sleep(1.5)

    # Open modal
    opened = page.evaluate(f"""() => {{
        const a = document.querySelector('a.oir-documents[data-oir-fln="{fln}"]');
        if (a) {{ a.click(); return 'clicked'; }}
        for (const el of document.querySelectorAll('a.oir-documents')) {{
            if (el.offsetParent !== null) {{ el.click(); return 'fallback'; }}
        }}
        return 'not found';
    }}""")
    time.sleep(2.0)

    # Select request type
    page.evaluate("""() => {
        const input = document.getElementById('selectedPdfRequestOption');
        if (!input) return;
        try {
            const w = kendo.widgetInstance($(input));
            const items = w.dataSource.data();
            for (const item of items) {
                if ((item.name||'').toLowerCase().includes('single')) {
                    w.value(item.id); w.trigger('change'); return;
                }
            }
            if (items.length) { w.value(items[0].id); w.trigger('change'); }
        } catch(e) {}
    }""")
    time.sleep(0.5)

    # Fill email
    page.locator("#requestEmail").fill(EMAIL)
    time.sleep(0.3)

    # Manual captcha
    for attempt in range(2):
        if attempt > 0:
            # Re-expand and re-open modal on retry
            page.evaluate(f"""() => {{
                for (const cell of document.querySelectorAll('#resultsGrid td')) {{
                    if (cell.innerText.trim() === '{fln}') {{
                        const row = cell.closest('tr');
                        const caret = row && row.querySelector('td.k-hierarchy-cell a');
                        if (caret) caret.click();
                        return;
                    }}
                }}
            }}""")
            time.sleep(1.5)
            page.evaluate(f"""() => {{
                const a = document.querySelector('a.oir-documents[data-oir-fln="{fln}"]');
                if (a) a.click();
            }}""")
            time.sleep(2.0)
            page.evaluate("""() => {
                const input = document.getElementById('selectedPdfRequestOption');
                if (!input) return;
                try {
                    const w = kendo.widgetInstance($(input));
                    const items = w.dataSource.data();
                    for (const item of items) {
                        if ((item.name||'').toLowerCase().includes('single')) {
                            w.value(item.id); w.trigger('change'); return;
                        }
                    }
                } catch(e) {}
            }""")
            time.sleep(0.5)
            page.locator("#requestEmail").fill(EMAIL)
            time.sleep(0.3)

        time.sleep(1.0)
        log.info(f"  🧑 CAPTCHA visible in browser — type the answer:")
        answer = input("  >>> Captcha answer: ").strip()
        page.locator("#requestImage").fill(answer)

        submitted_at = time.time()
        page.locator("#modalDocuments .btn-primary").first.click(timeout=5000)
        time.sleep(DELAY_AFTER_SUBMIT)

        # Confirm dialog
        try:
            ok_btn = page.locator("button:has-text('Ok'), button:has-text('OK')").first
            ok_btn.wait_for(timeout=3000)
            ok_btn.click()
            log.info("  ✅ Confirmed")
            time.sleep(1)
        except Exception:
            pass

        # Check if modal closed (success) or still open (wrong captcha)
        time.sleep(1.5)
        modal_open = page.evaluate("""() => {
            const m = document.getElementById('modalDocuments');
            return m && m.classList.contains('in');
        }""")
        if not modal_open:
            log.info(f"  ✅ Request submitted for {fln}")
            return "submitted"
        else:
            log.warning(f"  ⚠️  Captcha wrong, retrying...")

    return "captcha_failed"

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Submit IRFS document requests for Allied Lines")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--year", type=int, help="Single year, e.g. 2023")
    group.add_argument("--from-year", type=int, dest="from_year", help="Start year")
    parser.add_argument("--to-year", type=int, dest="to_year", help="End year (required with --from-year)")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.year:
        date_from = f"01/01/{args.year}"
        date_to   = f"12/31/{args.year}"
        label = str(args.year)
    else:
        if not args.to_year:
            print("❌ --to-year required when using --from-year")
            sys.exit(1)
        date_from = f"01/01/{args.from_year}"
        date_to   = f"12/31/{args.to_year}"
        label = f"{args.from_year}-{args.to_year}"

    log.info(f"🚀 IRFS Submit Requests — Allied Lines {label}\n")

    already_submitted = load_submitted()
    log.info(f"📋 {len(already_submitted)} already submitted, will skip.\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=150)
        context = browser.new_context()
        page    = context.new_page()

        try:
            total = set_up_search(page, date_from, date_to)
            filings = collect_rows(page)

            for i, filing in enumerate(filings, 1):
                fln     = filing["fileLogNumber"]
                company = filing["companyName"]
                action  = filing["finalAction"] or "OPEN"
                log.info(f"\n[{i}/{total}] {fln} — {company} ({action})")

                if fln in already_submitted:
                    log.info("  ⏭️  Already submitted, skipping.")
                    continue

                status = "error"
                notes  = ""
                try:
                    status = submit_filing(page, filing)
                except Exception as e:
                    notes = str(e)[:120]
                    log.warning(f"  ❌ {fln}: {notes}")
                    try: page.keyboard.press("Escape")
                    except Exception: pass

                append_log({
                    "fileLogNumber": fln,
                    "companyName":   company,
                    "finalAction":   action,
                    "dateFiled":     filing.get("dateFiled", ""),
                    "dateClosed":    filing.get("dateClosed", ""),
                    "submitStatus":  status,
                    "notes":         notes,
                    "timestamp":     datetime.utcnow().isoformat(),
                })

                time.sleep(DELAY_BETWEEN_FILINGS)

        except KeyboardInterrupt:
            log.info("\n⛔  Interrupted. Progress saved in submitted.csv — re-run to continue.")
        except Exception:
            log.error(f"\n💥 Fatal error:\n{traceback.format_exc()}")
        finally:
            browser.close()

    submitted_count = load_submitted()
    log.info(f"\n🏁 Done! {len(submitted_count)} requests submitted.")
    log.info(f"📋 Log: {LOG_FILE.resolve()}")
    log.info(f"▶️  Now run: python download_pdfs.py")


if __name__ == "__main__":
    main()