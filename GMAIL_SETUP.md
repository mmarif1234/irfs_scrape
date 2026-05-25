# Gmail API Setup

The scraper uses Gmail API to watch for OIR emails and extract download links automatically.

## Steps

### 1. Create a Google Cloud project
1. Go to https://console.cloud.google.com
2. Click "New Project" → name it "IRFS Scraper" → Create
3. Select the project

### 2. Enable Gmail API
1. Go to "APIs & Services" → "Library"
2. Search "Gmail API" → Enable it

### 3. Create OAuth credentials
1. Go to "APIs & Services" → "Credentials"
2. Click "Create Credentials" → "OAuth client ID"
3. If prompted, configure the consent screen first:
   - User type: External
   - App name: IRFS Scraper
   - Add your Gmail address as a test user
4. Application type: **Desktop app**
5. Name: IRFS Scraper
6. Click Create → Download JSON
7. Rename the downloaded file to `credentials.json`
8. Place it in this folder (same folder as `download_pdfs.py`)

### 4. First run — authorize
On first run, the script will open a browser window asking you to authorize Gmail access.
Log in with the Gmail account where OIR emails arrive (mohammadmustafa.arif1998@gmail.com).
After authorizing, a `token.json` file is saved — you won't need to authorize again.

## That's it
The scraper will automatically:
- Poll for emails from NOREPLY-IRFS.Admin@floir.com
- Extract the download link
- Download the PDF to ./downloads/
