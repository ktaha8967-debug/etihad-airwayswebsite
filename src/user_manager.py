import os
import json
import imaplib
import logging
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from src.config import BASE_DIR, INPUT_EXCEL_PATH, OUTPUT_EXCEL_PATH, STATUS_FILE_PATH, LOG_FILE_PATH

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(BASE_DIR, "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
OUTPUTS_DIR = os.path.join(DATA_DIR, "outputs")
STATUS_DIR = os.path.join(DATA_DIR, "status")
LOGS_DIR = os.path.join(DATA_DIR, "logs")

for directory in [DATA_DIR, UPLOADS_DIR, OUTPUTS_DIR, STATUS_DIR, LOGS_DIR]:
    os.makedirs(directory, exist_ok=True)


def load_users() -> dict:
    """Loads all user records from JSON file."""
    if not os.path.exists(USERS_FILE):
        return init_default_users()
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading users: {e}")
        return init_default_users()


def save_users(users: dict):
    """Persists user dictionary to JSON."""
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving users: {e}")


def init_default_users() -> dict:
    """Initializes default profile (Taha) with existing credentials."""
    default_users = {
        "taha": {
            "username": "taha",
            "password_hash": generate_password_hash("123456"),
            "name": "Taha",
            "email": "ktaha8967@gmail.com",
            "app_password": "mwxeaewdzvqjwnzo",
            "imap_server": "imap.gmail.com",
            "imap_port": 993,
            "saudia_password": "TempPass123!",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    }
    save_users(default_users)
    return default_users


def get_user(username: str) -> dict | None:
    """Retrieves full user data including credentials."""
    users = load_users()
    return users.get(username.strip().lower())


def get_user_safe(username: str) -> dict | None:
    """Returns user profile without sensitive password hash and with masked app password."""
    user = get_user(username)
    if not user:
        return None
    safe = dict(user)
    safe.pop("password_hash", None)
    app_pwd = safe.get("app_password", "")
    safe["has_app_password"] = bool(app_pwd)
    # Mask app password for display
    if app_pwd:
        safe["masked_app_password"] = app_pwd[:3] + " •••• •••• " + app_pwd[-3:] if len(app_pwd) >= 6 else "••••••••"
    else:
        safe["masked_app_password"] = ""
    return safe


def list_users_safe() -> list[dict]:
    """Returns a summary list of all registered team members / profiles."""
    users = load_users()
    summaries = []
    for uname, data in users.items():
        summaries.append({
            "username": uname,
            "name": data.get("name", uname.capitalize()),
            "email": data.get("email", ""),
            "has_imap": bool(data.get("email") and data.get("app_password")),
            "created_at": data.get("created_at", "")
        })
    return summaries


def verify_user(identifier: str, password: str) -> dict | None:
    """Authenticates by username OR email with password."""
    users = load_users()
    clean_id = identifier.strip().lower()
    target_uname = None

    if clean_id in users:
        target_uname = clean_id
    else:
        for uname, udata in users.items():
            if udata.get("email", "").strip().lower() == clean_id:
                target_uname = uname
                break

    if not target_uname:
        return None

    user = users[target_uname]
    if check_password_hash(user.get("password_hash", ""), password):
        return get_user_safe(target_uname)
    return None


def create_user(username: str, password: str, name: str = "", email: str = "", app_password: str = "mwxeaewdzvqjwnzo",
                imap_server: str = "imap.gmail.com", imap_port: int = 993, saudia_password: str = "TempPass123!") -> tuple[bool, str]:
    """Creates a brand new user with their personal email and password."""
    username = username.strip().lower()
    if not username:
        return False, "Username is required."
    users = load_users()
    if username in users:
        return False, f"Username '{username}' already exists."

    users[username] = {
        "username": username,
        "password_hash": generate_password_hash(password),
        "name": name.strip() or username.capitalize(),
        "email": email.strip(),
        "app_password": (app_password or "mwxeaewdzvqjwnzo").replace(" ", "").strip(),
        "imap_server": imap_server.strip() or "imap.gmail.com",
        "imap_port": int(imap_port) if imap_port else 993,
        "saudia_password": saudia_password.strip() or "TempPass123!",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_users(users)
    return True, "User registered successfully."


def update_user_profile(username: str, name: str = None, email: str = None, new_password: str = None) -> tuple[bool, str]:
    """Updates user display name, account email, or login password."""
    uname = username.strip().lower()
    users = load_users()
    if uname not in users:
        return False, f"User '{username}' not found."

    user = users[uname]
    if name is not None and name.strip():
        user["name"] = name.strip()
    if email is not None:
        user["email"] = email.strip()
    if new_password is not None and new_password.strip():
        user["password_hash"] = generate_password_hash(new_password.strip())

    users[uname] = user
    save_users(users)
    return True, "Profile updated successfully."


def delete_user(username: str) -> tuple[bool, str]:
    """Deletes a user account and cleans up user files."""
    uname = username.strip().lower()
    users = load_users()
    if uname not in users:
        return False, f"User '{username}' not found."
    if len(users) <= 1:
        return False, "Cannot delete the only remaining user account."

    del users[uname]
    save_users(users)

    # Clean up user database and files
    for filepath in [
        os.path.join(BASE_DIR, "data", "databases", f"{uname}.db"),
        os.path.join(BASE_DIR, "data", "uploads", f"{uname}_input.xlsx"),
        os.path.join(BASE_DIR, "data", "outputs", f"{uname}_output.xlsx"),
        os.path.join(BASE_DIR, "data", "status", f"status_{uname}.json"),
        os.path.join(BASE_DIR, "data", "logs", f"automation_{uname}.log"),
    ]:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass
    return True, f"User '{username}' deleted successfully."


def update_credentials(username: str, email: str = None, app_password: str = None,
                       imap_server: str = None, imap_port: int = None, saudia_password: str = None,
                       name: str = None) -> tuple[bool, str]:
    """Updates personal credentials for an existing user."""
    users = load_users()
    uname = username.strip().lower()
    if uname not in users:
        return False, f"User '{username}' not found."

    user = users[uname]
    if name is not None:
        user["name"] = name.strip()
    if email is not None:
        user["email"] = email.strip()
    if app_password is not None and app_password.strip():
        # Clean whitespace from app password
        user["app_password"] = app_password.replace(" ", "").strip()
    if imap_server is not None:
        user["imap_server"] = imap_server.strip()
    if imap_port is not None:
        try:
            user["imap_port"] = int(imap_port)
        except ValueError:
            user["imap_port"] = 993
    if saudia_password is not None and saudia_password.strip():
        user["saudia_password"] = saudia_password.strip()

    users[uname] = user
    save_users(users)
    return True, "Credentials updated successfully."


def test_imap_connection(email_address: str, app_password: str, imap_server: str = "imap.gmail.com", imap_port: int = 993) -> tuple[bool, str]:
    """Tests live IMAP login to verify Gmail App Password."""
    clean_email = (email_address or "").strip().lower()
    clean_pwd = (app_password or "").replace(" ", "").replace("-", "").strip()
    if not clean_email or not clean_pwd:
        return False, "Email address and App Password cannot be empty."

    # Auto-detect IMAP server if domain is known
    server = (imap_server or "imap.gmail.com").strip()
    port = int(imap_port) if imap_port else 993
    if "@" in clean_email:
        domain = clean_email.split("@")[1]
        if any(d in domain for d in ["outlook.com", "hotmail.com", "live.com"]):
            server = "imap-mail.outlook.com"
        elif "yahoo.com" in domain:
            server = "imap.mail.yahoo.com"
        elif "icloud.com" in domain:
            server = "imap.mail.me.com"
        elif "gmail.com" in domain or "googlemail.com" in domain:
            server = "imap.gmail.com"

    try:
        mail = imaplib.IMAP4_SSL(server, port, timeout=15)
        mail.login(clean_email, clean_pwd)
        status, _ = mail.select("inbox")
        mail.close()
        mail.logout()
        return True, f"Connection successful! Logged into {clean_email}."
    except imaplib.IMAP4.error as e:
        error_msg = str(e)
        if "Application-specific password required" in error_msg:
            return False, (
                "⚠️ Regular Google Password Detected! Google strictly requires a 16-character 'App Password' for automated IMAP access. "
                "Your regular Gmail login password cannot be used. Please open https://myaccount.google.com/apppasswords to generate a 16-letter App Password (e.g. abcd efgh ijkl mnop)."
            )
        if "AUTHENTICATIONFAILED" in error_msg:
            return False, (
                "⚠️ Authentication failed! Please verify: "
                "1) 2-Step Verification is turned ON in your Google Account. "
                "2) You generated a 16-letter App Password at https://myaccount.google.com/apppasswords. "
                "3) In Gmail Settings > Forwarding and POP/IMAP, IMAP Access is Enabled."
            )
        return False, f"IMAP authentication failed: {error_msg}"
    except Exception as e:
        return False, f"Connection error: {e}"


def get_user_input_path(username: str) -> str:
    """Returns path to user's uploaded Excel input file."""
    user_file = os.path.join(UPLOADS_DIR, f"{username}_input.xlsx")
    if os.path.exists(user_file):
        return user_file
    # Fallback to general input path
    return INPUT_EXCEL_PATH


def get_user_output_path(username: str) -> str:
    """Returns path to user's processed output file."""
    user_file = os.path.join(OUTPUTS_DIR, f"{username}_output.xlsx")
    if os.path.exists(user_file):
        return user_file
    return OUTPUT_EXCEL_PATH


def get_user_status_path(username: str) -> str:
    """Returns path to user's individual status json file."""
    return os.path.join(STATUS_DIR, f"status_{username}.json")


def get_user_log_path(username: str) -> str:
    """Returns path to user's individual log file."""
    return os.path.join(LOGS_DIR, f"automation_{username}.log")
