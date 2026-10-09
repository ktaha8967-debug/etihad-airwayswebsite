import os
import tempfile
from dotenv import load_dotenv

load_dotenv()

if "DISPLAY" not in os.environ:
    os.environ["DISPLAY"] = ":99"

# Determine a writable base directory
def get_base_dir():
    # If running in Vercel or read-only environment
    if os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        return tempfile.gettempdir()
    return "."

BASE_DIR = get_base_dir()

# File paths
INPUT_EXCEL_PATH = os.getenv("INPUT_EXCEL_PATH", os.path.join(BASE_DIR, "Etihad_Bookings.xlsx") if BASE_DIR != "." else "Etihad_Bookings.xlsx")
OUTPUT_EXCEL_PATH = os.getenv("OUTPUT_EXCEL_PATH", os.path.join(BASE_DIR, "Etihad_Bookings_Processed.xlsx") if BASE_DIR != "." else "Etihad_Bookings_Processed.xlsx")
STATUS_FILE_PATH = os.getenv("STATUS_FILE_PATH", os.path.join(BASE_DIR, "status.json") if BASE_DIR != "." else "status.json")
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", os.path.join(BASE_DIR, "automation.log") if BASE_DIR != "." else "automation.log")
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUTS_DIR = os.path.join(DATA_DIR, "outputs")
os.makedirs(OUTPUTS_DIR, exist_ok=True)

# Etihad Configuration
ETIHAD_MANAGE_BOOKING_URL = "https://www.etihad.com/en-ae/manage"

SAUDIA_SIGNUP_URL = "https://www.saudia.com"  # Saudia Home Page (bypass WAF check)

# Browser Settings
HEADLESS = os.getenv("HEADLESS", "True" if os.getenv("RENDER") or os.getenv("VERCEL") or os.getenv("RAILWAY_STATIC_URL") else "False").lower() in ("true", "1", "yes")
TIMEOUT = int(os.getenv("TIMEOUT", "30000")) # ms

# KTaha Default Credentials
DEFAULT_EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "ktaha8967@gmail.com")
DEFAULT_APP_PASSWORD = os.getenv("APP_PASSWORD", "mwxeaewdzvqjwnzo")
DEFAULT_IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
DEFAULT_IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
DEFAULT_SAUDIA_PASSWORD = os.getenv("SAUDIA_PASSWORD", "TempPass123!")

