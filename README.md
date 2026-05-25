# IRFS Allied Lines Scraper (Python)

Downloads all Allied Lines rate filings from Florida OIR (2015–2026) in two steps:
**submit document requests** (with manual captcha), then **download PDFs** when OIR
replies via email.

## What it does

**Step 1 — `submit_requests.py`**
1. Searches IRFS Advanced Search for Allied Lines filings in a given year range
2. For each filing, opens the document-request modal, fills your email, and pauses
   so you can read the captcha from the visible browser and type the answer
3. Submits the request and logs the result to `submitted.csv`

**Step 2 — `download_pdfs.py`**
1. Reads `submitted.csv`
2. Polls Gmail for OIR response emails (from `NOREPLY-IRFS.Admin@floir.com`)
3. Extracts the download link and saves the PDF to `./downloads/` named by file log
   number (e.g. `26-013710.pdf`)
4. Logs results to `downloaded.csv`

Both scripts are resumable — re-running skips entries already marked done.

---

## Setup (one-time, ~15 minutes)

### 1. Install Conda
If you don't have Conda, install **Miniconda** (lightweight):
1. Go to https://docs.conda.io/en/latest/miniconda.html
2. Download the **macOS Apple Silicon** installer if you have an M1/M2/M3 Mac,
   or **macOS Intel** if you have an older Mac
3. Run the installer and follow the prompts
4. Close and reopen Terminal when done
5. Verify: `conda --version`

### 2. Create the environment
Navigate to this folder and create the Conda environment from `environment.yml`:
```bash
cd ~/path/to/irfs_scrape
conda env create -f environment.yml
```
Installs Python 3.11 and dependencies. Takes 2–3 minutes.

### 3. Activate the environment
```bash
conda activate irfs_scraper
```
You'll see `(irfs_scraper)` in your terminal prompt.
**Activate this environment every time you open a new Terminal.**

### 4. Install Playwright's browser
One-time after creating the environment:
```bash
python -m playwright install chromium
```

### 5. Set up Gmail API
Follow `GMAIL_SETUP.md` — ~10 minutes, one-time. You'll place a `credentials.json`
file in this folder.

### 6. Connect ProtonVPN to a US server

---

## Running

### Step 1 — Submit requests
A Chrome window opens. When the captcha appears, type the answer in the terminal
prompt and hit Enter. The script handles the rest of the form.

```bash
python submit_requests.py --year 2024
python submit_requests.py --from-year 2020 --to-year 2022
python submit_requests.py --from-year 2015 --to-year 2026   # all years
```

Results land in `submitted.csv`. If a captcha is typed wrong, the script
re-opens the modal and asks again.

### Step 2 — Download PDFs
Run this after submitting — it polls Gmail until each PDF is ready.

```bash
python download_pdfs.py
python download_pdfs.py --year 2024            # only that year's submissions
python download_pdfs.py --log submitted.csv    # custom log file
```

Results land in `downloaded.csv`; PDFs in `./downloads/`.

---

## Output
- `./downloads/` — PDFs named by file log number
- `./submitted.csv` — one row per filing: status of the request submission
- `./downloaded.csv` — one row per filing: status of the PDF download
- `./submit.log`, `./download.log` — full run logs with timestamps

---

## Tips
- A real Chrome window opens during step 1 so you can watch and intervene
- Step 1 waits indefinitely at the captcha prompt — take your time
- Safe to interrupt (Ctrl+C) and re-run either script — completed entries are skipped
- OIR download links can expire. If `downloaded.csv` shows `expired` for a filing,
  re-run `submit_requests.py` for that year and then `download_pdfs.py` again

---

## Conda quick reference
| Command | What it does |
|---|---|
| `conda activate irfs_scraper` | Activate this project's environment |
| `conda deactivate` | Return to base environment |
| `conda env list` | List all environments |
| `conda env remove -n irfs_scraper` | Delete the environment |
| `conda env update -f environment.yml` | Update after changes to environment.yml |
