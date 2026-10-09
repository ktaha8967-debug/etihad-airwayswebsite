import os
import sqlite3
import logging
from datetime import datetime
import pandas as pd
from src.config import BASE_DIR

logger = logging.getLogger(__name__)

DB_DIR = os.path.join(BASE_DIR, "data", "databases")
os.makedirs(DB_DIR, exist_ok=True)


def get_db_path(username: str) -> str:
    """Returns absolute path to the user's isolated SQLite database."""
    safe_name = "".join(c for c in username.strip().lower() if c.isalnum() or c in ("_", "-"))
    if not safe_name:
        safe_name = "default"
    return os.path.join(DB_DIR, f"{safe_name}.db")


def get_connection(username: str) -> sqlite3.Connection:
    """Opens a connection to the user's database with Row factory enabled."""
    db_path = get_db_path(username)
    conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_user_db(username: str, default_creds: dict = None):
    """Initializes the isolated SQLite database schema for the specific user."""
    conn = get_connection(username)
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS credentials (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    username TEXT UNIQUE,
                    name TEXT,
                    email TEXT,
                    app_password TEXT,
                    imap_server TEXT DEFAULT 'imap.gmail.com',
                    imap_port INTEGER DEFAULT 993,
                    saudia_password TEXT DEFAULT 'TempPass123!',
                    updated_at TEXT
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS passengers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pnr TEXT,
                    last_name TEXT,
                    saudia_id TEXT DEFAULT '',
                    status TEXT DEFAULT 'Pending',
                    flight_details TEXT DEFAULT '',
                    extracted_passenger_name TEXT DEFAULT '',
                    remarks TEXT DEFAULT '',
                    email TEXT DEFAULT '',
                    password TEXT DEFAULT '',
                    created_at TEXT,
                    updated_at TEXT
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS status (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    status_text TEXT DEFAULT 'Idle',
                    processed INTEGER DEFAULT 0,
                    total INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 0,
                    failed INTEGER DEFAULT 0,
                    skipped INTEGER DEFAULT 0,
                    current_pnr TEXT DEFAULT '',
                    current_name TEXT DEFAULT '',
                    last_updated TEXT
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    level TEXT,
                    message TEXT
                );
            """)

            # Ensure initial status row
            cur = conn.cursor()
            cur.execute("SELECT id FROM status WHERE id = 1")
            if not cur.fetchone():
                conn.execute("""
                    INSERT INTO status (id, status_text, processed, total, success, failed, skipped, last_updated)
                    VALUES (1, 'Idle', 0, 0, 0, 0, 0, ?);
                """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))

            # Ensure credentials row
            cur = conn.cursor()
            cur.execute("SELECT id, email, name FROM credentials WHERE id = 1")
            row = cur.fetchone()
            user_email = (default_creds.get("email") if default_creds else "") or ""
            user_name = (default_creds.get("name") if default_creds else "") or username.capitalize()
            if not row:
                conn.execute("""
                    INSERT INTO credentials 
                    (id, username, name, email, app_password, imap_server, imap_port, saudia_password, updated_at)
                    VALUES (1, ?, ?, ?, 'mwxeaewdzvqjwnzo', 'imap.gmail.com', 993, 'TempPass123!', ?);
                """, (
                    username,
                    user_name,
                    user_email,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ))
            elif default_creds and (default_creds.get("email") or default_creds.get("name")):
                if default_creds.get("email"):
                    conn.execute("UPDATE credentials SET email = ?, updated_at = ? WHERE id = 1", (default_creds["email"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                if default_creds.get("name"):
                    conn.execute("UPDATE credentials SET name = ?, updated_at = ? WHERE id = 1", (default_creds["name"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    finally:
        conn.close()


def get_credentials(username: str) -> dict:
    """Returns stored user credentials from user database, with fallbacks to user_manager."""
    conn = get_connection(username)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM credentials WHERE id = 1")
        row = cur.fetchone()
        if row:
            res = dict(row)
            if res.get("email"):
                return res
    except Exception as e:
        logger.warning(f"Error reading credentials for {username}: {e}")
    finally:
        conn.close()

    from src.user_manager import get_user
    u = get_user(username) or {}
    return {
        "username": username,
        "name": u.get("name", username.capitalize()),
        "email": u.get("email", ""),
        "app_password": u.get("app_password", "mwxeaewdzvqjwnzo"),
        "imap_server": u.get("imap_server", "imap.gmail.com"),
        "imap_port": u.get("imap_port", 993),
        "saudia_password": u.get("saudia_password", "TempPass123!")
    }


def save_credentials(username: str, data: dict):
    """Saves user's credentials into their isolated database."""
    init_user_db(username, default_creds=data)
    conn = get_connection(username)
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with conn:
            conn.execute("""
                INSERT OR REPLACE INTO credentials 
                (id, username, name, email, app_password, imap_server, imap_port, saudia_password, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                username,
                data.get("name", username.capitalize()),
                data.get("email", ""),
                (data.get("app_password") or "mwxeaewdzvqjwnzo").replace(" ", "").strip(),
                data.get("imap_server", "imap.gmail.com"),
                int(data.get("imap_port", 993)),
                data.get("saudia_password", "TempPass123!"),
                now
            ))
    finally:
        conn.close()


def import_passengers_from_df(username: str, df: pd.DataFrame):
    """Replaces or loads passenger records into user's database with flexible column mapping."""
    init_user_db(username)
    conn = get_connection(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Clean header whitespace
    df.columns = [str(c).strip() for c in df.columns]

    pnr_col = next((c for c in df.columns if any(k in c.lower() for k in ["pnr", "booking", "reference", "ref"])), None)
    lastname_col = next((c for c in df.columns if any(k in c.lower() for k in ["last name", "lastname", "surname", "last_name"])), None)
    firstname_col = next((c for c in df.columns if any(k in c.lower() for k in ["first name", "firstname", "given name", "first_name"])), None)
    email_col = next((c for c in df.columns if any(k in c.lower() for k in ["gmail/email id", "email", "gmail", "e-mail", "mail"])), None)
    saudia_col = next((c for c in df.columns if any(k in c.lower() for k in ["saudia membership id", "saudia", "membership", "alfursan", "alfusan"])), None)
    status_col = next((c for c in df.columns if c.lower() in ["status", "state"]), None)
    flight_col = next((c for c in df.columns if any(k in c.lower() for k in ["flight details", "flight itinerary", "flight_details"])), None)
    ext_name_col = next((c for c in df.columns if any(k in c.lower() for k in ["extracted passenger name", "full name", "passenger name"])), None)
    remarks_col = next((c for c in df.columns if any(k in c.lower() for k in ["remarks", "remark", "notes", "e-ticket", "ticket"])), None)
    pwd_col = next((c for c in df.columns if any(k in c.lower() for k in ["password", "pwd", "pass"])), None)

    if not pnr_col or not lastname_col:
        raise ValueError("Could not find PNR and Last Name columns in Excel.")

    try:
        with conn:
            conn.execute("DELETE FROM passengers;")
            for _, row in df.iterrows():
                pnr = str(row[pnr_col]).strip() if pd.notna(row[pnr_col]) else ""
                last_name = str(row[lastname_col]).strip() if pd.notna(row[lastname_col]) else ""
                if not pnr or pnr.lower() == "nan" or not last_name or last_name.lower() == "nan":
                    continue

                first_name = str(row[firstname_col]).strip() if firstname_col and pd.notna(row[firstname_col]) else ""
                if first_name.lower() == "nan": first_name = ""

                ext_name = str(row[ext_name_col]).strip() if ext_name_col and pd.notna(row[ext_name_col]) else ""
                if not ext_name and first_name:
                    ext_name = f"{first_name} {last_name}".strip()

                saudia_id = str(row[saudia_col]).strip() if saudia_col and pd.notna(row[saudia_col]) else ""
                status = str(row[status_col]).strip() if status_col and pd.notna(row[status_col]) else "Pending"
                flight = str(row[flight_col]).strip() if flight_col and pd.notna(row[flight_col]) else ""
                remarks = str(row[remarks_col]).strip() if remarks_col and pd.notna(row[remarks_col]) else ""
                email = str(row[email_col]).strip() if email_col and pd.notna(row[email_col]) else ""
                pwd = str(row[pwd_col]).strip() if pwd_col and pd.notna(row[pwd_col]) else ""

                if saudia_id.lower() == "nan": saudia_id = ""
                if status.lower() == "nan": status = "Pending"
                if flight.lower() == "nan": flight = ""
                if ext_name.lower() == "nan": ext_name = ""
                if remarks.lower() == "nan": remarks = ""
                if email.lower() == "nan": email = ""
                if pwd.lower() == "nan": pwd = ""

                conn.execute("""
                    INSERT INTO passengers
                    (pnr, last_name, saudia_id, status, flight_details, extracted_passenger_name, remarks, email, password, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (pnr, last_name, saudia_id, status, flight, ext_name, remarks, email, pwd, now, now))
    finally:
        conn.close()


def get_passengers_df(username: str) -> pd.DataFrame:
    """Exports all passengers from user's database into a Pandas DataFrame."""
    init_user_db(username)
    conn = get_connection(username)
    try:
        df = pd.read_sql_query("""
            SELECT 
                pnr AS "PNR",
                last_name AS "Last Name",
                saudia_id AS "Saudia Membership ID",
                status AS "Status",
                flight_details AS "Flight Details",
                extracted_passenger_name AS "Extracted Passenger Name",
                remarks AS "Remarks",
                email AS "Gmail/Email ID",
                password AS "Password"
            FROM passengers ORDER BY id ASC;
        """, conn)
        return df.fillna("")
    finally:
        conn.close()


def get_passengers_list(username: str) -> list[dict]:
    """Returns passenger list as dict records for the dashboard."""
    df = get_passengers_df(username)
    return df.to_dict(orient="records")


def update_passenger_record(username: str, pnr: str, last_name: str, updates: dict):
    """Updates a single passenger's fields in user's isolated database."""
    conn = get_connection(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clauses = []
    values = []
    
    col_mapping = {
        "Saudia Membership ID": "saudia_id",
        "Status": "status",
        "Flight Details": "flight_details",
        "Extracted Passenger Name": "extracted_passenger_name",
        "Remarks": "remarks",
        "Gmail/Email ID": "email",
        "Password": "password"
    }

    for key, val in updates.items():
        col = col_mapping.get(key, key)
        set_clauses.append(f"{col} = ?")
        values.append(str(val) if val is not None else "")

    set_clauses.append("updated_at = ?")
    values.append(now)

    values.extend([pnr.strip(), last_name.strip()])
    sql = f"UPDATE passengers SET {', '.join(set_clauses)} WHERE UPPER(pnr) = UPPER(?) AND UPPER(last_name) = UPPER(?)"

    try:
        with conn:
            conn.execute(sql, values)
    finally:
        conn.close()


def get_user_status(username: str) -> dict:
    """Gets execution status for the user from their isolated database."""
    init_user_db(username)
    conn = get_connection(username)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM status WHERE id = 1")
        row = cur.fetchone()
        if row:
            return {
                "status": row["status_text"],
                "processed": row["processed"],
                "total": row["total"],
                "success": row["success"],
                "failed": row["failed"],
                "skipped": row["skipped"],
                "current_pnr": row["current_pnr"] or "",
                "current_name": row["current_name"] or "",
                "last_updated": row["last_updated"] or ""
            }
        return {"status": "Idle", "processed": 0, "total": 0, "success": 0, "failed": 0, "skipped": 0}
    finally:
        conn.close()


def update_user_status(username: str, status_text: str, processed: int, total: int,
                       success: int, failed: int, skipped: int, current_pnr: str = "", current_name: str = ""):
    """Updates execution status for the user in their isolated database."""
    init_user_db(username)
    conn = get_connection(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with conn:
            conn.execute("""
                UPDATE status SET
                    status_text = ?,
                    processed = ?,
                    total = ?,
                    success = ?,
                    failed = ?,
                    skipped = ?,
                    current_pnr = ?,
                    current_name = ?,
                    last_updated = ?
                WHERE id = 1;
            """, (status_text, int(processed), int(total), int(success), int(failed), int(skipped), current_pnr, current_name, now))
    finally:
        conn.close()


def reset_passengers(username: str):
    """Resets passenger statuses to Pending, clears Saudia IDs, flight details, and extracted names for a clean fresh run."""
    init_user_db(username)
    conn = get_connection(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with conn:
            conn.execute("""
                UPDATE passengers 
                SET status = 'Pending', saudia_id = '', flight_details = '', extracted_passenger_name = '', remarks = 'Ready for processing', updated_at = ?;
            """, (now,))
    finally:
        conn.close()
