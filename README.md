# IRFS Allied Lines Scraper (Python)

Downloads all Allied Lines rate filings from Florida OIR (2015–2026) automatically.

## What it does
1. Searches IRFS Advanced Search for Allied Lines filings (Jan 2015 – Dec 2026)
2. For each filing: solves the math captcha using Claude Vision, submits document request
3. Polls Gmail for the OIR response email and downloads the PDF automatically
4. Saves all PDFs to `./downloads/` named by file log number (e.g. `26-013710.pdf`)
5. Maintains `log.csv` — if interrupted, re-running skips already-completed filings

---

## Setup (one-time, ~20 minutes)

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
cd ~/path/to/irfs_scraper
conda env create -f environment.yml
```
This installs Python 3.11 and all dependencies into an isolated environment.
Takes 2–3 minutes.

### 3. Activate the environment
```bash
conda activate irfs_scraper
```
You'll see `(irfs_scraper)` in your terminal prompt.
**You must activate this environment every time you open a new Terminal before running the scraper.**

### 4. Install Playwright's browser
This is a one-time step after creating the environment:
```bash
python -m playwright install chromium
```

### 5. Set up Gmail API
Follow `GMAIL_SETUP.md` — takes ~10 minutes, one-time only.
You'll place a `credentials.json` file in this folder.

### 6. Set your Anthropic API key
```bash
export ANTHROPIC_API_KEY=sk-ant-...
```
Get your key from https://console.anthropic.com

To avoid setting this every session, add it to your shell profile:
```bash
echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> ~/.zshrc
source ~/.zshrc
```

### 7. Connect ProtonVPN to a US server

### 8. Run
```bash
python scraper.py
```

---

## Every subsequent run
```bash
conda activate irfs_scraper   # activate environment
python scraper.py             # run scraper
```

---

## Output
- `./downloads/` — PDFs named by file log number
- `./log.csv` — every filing: log number, company, status, download result
- `./scraper.log` — full run log with timestamps

---

## Tips
- A real Chrome window opens so you can watch and intervene if needed
- Runs at ~4 second intervals between filings to avoid getting blocked
- Safe to interrupt (Ctrl+C) and re-run — already-downloaded filings are skipped
- If a captcha is wrong, the script retries automatically

---

## Conda quick reference
| Command | What it does |
|---|---|
| `conda activate irfs_scraper` | Activate this project's environment |
| `conda deactivate` | Return to base environment |
| `conda env list` | List all environments |
| `conda env remove -n irfs_scraper` | Delete the environment |
| `conda env update -f environment.yml` | Update after changes to environment.yml |
