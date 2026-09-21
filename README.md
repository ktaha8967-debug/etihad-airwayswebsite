# Enterprise Airline Automation Suite & Saudia AlFursan Membership Manager

A production-grade Python and Playwright automation suite with a modern Glassmorphism Web Dashboard GUI. Designed to extract flight details from **Etihad Airways**, register memberships on **Saudia Airlines (AlFursan)**, parse IMAP activation emails, and claim missing miles.

---

## 🚀 Features

* **Glassmorphism Web Dashboard GUI**: Real-time browser control center with live progress bar, real-time log terminal, email tracker rail, and categorized passenger grids.
* **4 Modular Automation Modes**:
  1. **Run 1: Extract Flights Only**: Logs into Etihad Manage Booking via PNR + Last Name, extracts flight details and passenger itineraries.
  2. **Run 2: Register Memberships**: Multi-step Saudia AlFursan signup with automated Gmail IMAP activation and real 100-prefix AlFursan ID extraction.
  3. **Run 3: Claim Miles Only**: Automated retro-claim helper for missing flight miles.
  4. **Run All (End-to-End)**: Complete automated end-to-end pipeline.
* **Frequent Flyer Detection**: Automatically detects if a frequent flyer number is already attached to avoid duplicate registrations.
* **IMAP Email Automation**: Live integration with Gmail IMAP (`imap.gmail.com`) to poll for activation emails, click verification links, and parse official Welcome emails.
* **Dual Format Export**: Automatically saves and synchronizes both `.xlsx` (Excel) and `.csv` formats.

---

## 📁 Project Structure

```
etihad-airways/
│
├── src/
│   ├── __init__.py
│   ├── config.py            # Endpoints, timeouts, and env settings
│   ├── utils.py             # Excel/CSV parsers, random generators, logging
│   ├── temp_mail.py         # Gmail IMAP client for activation & welcome emails
│   ├── etihad_scraper.py    # Playwright scraper for Etihad Airways
│   └── saudia_signup.py     # Playwright automation for Saudia AlFursan & Claim Miles
│
├── templates/
│   └── index.html           # Web Dashboard UI
│
├── app.py                   # Flask server backend & REST APIs
├── main.py                  # CLI Orchestrator supporting all modes
├── requirements.txt         # Required Python packages
├── .env                     # Local runtime configuration
└── README.md                # Documentation
```

---

## 🛠️ Setup & Installation

### 1. Install Dependencies
Ensure Python 3.10+ is installed, then run:
```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

### 2. Configure Environment Variables (`.env`)
```ini
INPUT_EXCEL_PATH=Etihad passenger details 9th Aug.xlsx
OUTPUT_EXCEL_PATH=Etihad passenger details 9th Aug_Processed.xlsx
HEADLESS=false
TIMEOUT=45000
```

---

## 🏃 Running the Application

### Option A: Launch Web Dashboard GUI (Recommended)
Start the dashboard server:
```powershell
python app.py
```
Open your browser and navigate to:
```
http://127.0.0.1:5000
```
From the dashboard, you can:
* Upload any `.xlsx` or `.xls` passenger sheet.
* Click any of the 4 run buttons (**Run 1**, **Run 2**, **Run 3**, **Run All**).
* View live execution logs, active passenger cards, and data tables.
* Export updated **Excel (.xlsx)** or **CSV (.csv)** files with a single click.

### Option B: Run via CLI Orchestrator
```powershell
# Run All (End-to-End)
python main.py

# Or run specific modes via Python:
python -c "from main import run_automation; run_automation('flight_only')"
python -c "from main import run_automation; run_automation('signup_only')"
python -c "from main import run_automation; run_automation('claim_only')"
```

---

## ⚠️ Notes on WAF / Security Challenges
Saudia Airlines employs Imperva/Cloudflare security protection. When browser verification or CAPTCHA appears:
* The system detects it and pauses execution for up to 120 seconds.
* Simply complete the checkbox in the visible browser window, and the automation will automatically resume!
"# etihad-airwayswebsite" 
