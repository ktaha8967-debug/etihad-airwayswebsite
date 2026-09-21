import os
import tempfile
from dotenv import load_dotenv

load_dotenv()

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

# Etihad Configuration
ETIHAD_MANAGE_BOOKING_URL = "https://www.etihad.com/en-in/manage"

SAUDIA_SIGNUP_URL = "https://www.saudia.com"  # Saudia Home Page (bypass WAF check)

# Browser Settings
HEADLESS = os.getenv("HEADLESS", "True" if os.getenv("RENDER") or os.getenv("VERCEL") or os.getenv("RAILWAY_STATIC_URL") else "False").lower() in ("true", "1", "yes")
TIMEOUT = int(os.getenv("TIMEOUT", "30000")) # ms

