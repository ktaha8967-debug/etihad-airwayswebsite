import os
import json
import logging
import re
import random
import time
from datetime import datetime
import pandas as pd
from src.db_manager import get_connection

logger = logging.getLogger("Project2Manager")

def init_project2_db(username: str):
    """Initializes Project 2 isolated database tables in the user's database."""
    conn = get_connection(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project2_passengers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    first_name TEXT DEFAULT '',
                    last_name TEXT DEFAULT '',
                    title TEXT DEFAULT 'Mr',
                    dob TEXT DEFAULT '',
                    passport TEXT DEFAULT '',
                    mobile TEXT DEFAULT '',
                    nationality TEXT DEFAULT 'Saudi Arabia',
                    email TEXT DEFAULT '',
                    saudia_id TEXT DEFAULT '',
                    status TEXT DEFAULT 'Pending',
                    remarks TEXT DEFAULT 'Ready for processing',
                    password TEXT DEFAULT 'TempPass123!',
                    created_at TEXT,
                    updated_at TEXT
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS project2_status (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    status_text TEXT DEFAULT 'Idle',
                    processed INTEGER DEFAULT 0,
                    total INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 0,
                    failed INTEGER DEFAULT 0,
                    skipped INTEGER DEFAULT 0,
                    current_passenger TEXT DEFAULT '',
                    last_updated TEXT
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS project2_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    domain_name TEXT DEFAULT '',
                    master_email TEXT DEFAULT '',
                    master_password TEXT DEFAULT '',
                    imap_server TEXT DEFAULT 'imap.gmail.com',
                    imap_port INTEGER DEFAULT 993,
                    email_mode TEXT DEFAULT 'auto',
                    updated_at TEXT
                );
            """)

            # Ensure default status row
            cur = conn.cursor()
            cur.execute("SELECT id FROM project2_status WHERE id = 1")
            if not cur.fetchone():
                conn.execute("""
                    INSERT INTO project2_status (id, status_text, processed, total, success, failed, skipped, last_updated)
                    VALUES (1, 'Idle', 0, 0, 0, 0, 0, ?);
                """, (now,))

            # Ensure default config row
            cur.execute("SELECT id FROM project2_config WHERE id = 1")
            if not cur.fetchone():
                user_email = ""
                user_pwd = ""
                try:
                    from src.user_manager import load_users
                    users = load_users()
                    u_info = users.get(username, {})
                    user_email = u_info.get("email", "")
                    user_pwd = u_info.get("app_password", "")
                except Exception:
                    pass
                conn.execute("""
                    INSERT INTO project2_config 
                    (id, domain_name, master_email, master_password, imap_server, imap_port, email_mode, updated_at)
                    VALUES (1, '', ?, ?, 'imap.gmail.com', 993, 'auto', ?);
                """, (user_email, user_pwd, now))
    finally:
        conn.close()


def get_project2_config(username: str) -> dict:
    """Returns stored Project 2 configuration for the user."""
    init_project2_db(username)
    conn = get_connection(username)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM project2_config WHERE id = 1")
        row = cur.fetchone()
        if row:
            return dict(row)
    except Exception as e:
        logger.warning(f"Error fetching project2_config for {username}: {e}")
    finally:
        conn.close()

    user_email = ""
    user_pwd = ""
    try:
        from src.user_manager import load_users
        users = load_users()
        user_info = users.get(username, {})
        user_email = user_info.get("email", "")
        user_pwd = user_info.get("app_password", "")
    except Exception:
        pass

    return {
        "domain_name": "",
        "master_email": user_email,
        "master_password": user_pwd,
        "imap_server": "imap.gmail.com",
        "imap_port": 993,
        "email_mode": "auto"
    }


def save_project2_config(username: str, data: dict):
    """Saves Project 2 configuration into user database."""
    init_project2_db(username)
    conn = get_connection(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Get user profile email if master_email not provided
    default_email = ""
    try:
        from src.user_manager import load_users
        default_email = load_users().get(username, {}).get("email", "")
    except Exception:
        pass

    target_email = (data.get("master_email") or default_email).strip().lower()
    target_pwd = (data.get("master_password") or "").replace(" ", "").strip()

    try:
        with conn:
            conn.execute("""
                INSERT OR REPLACE INTO project2_config
                (id, domain_name, master_email, master_password, imap_server, imap_port, email_mode, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?);
            """, (
                (data.get("domain_name") or "").strip().lower(),
                target_email,
                target_pwd,
                (data.get("imap_server") or "imap.gmail.com").strip(),
                int(data.get("imap_port") or 993),
                (data.get("email_mode") or "auto").strip(),
                now
            ))
    finally:
        conn.close()


def import_project2_passengers_from_df(username: str, df: pd.DataFrame):
    """Imports passenger records for Project 2 from an uploaded DataFrame."""
    init_project2_db(username)
    conn = get_connection(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    df.columns = [str(c).strip() for c in df.columns]

    fname_col = next((c for c in df.columns if any(k in c.lower() for k in ["first name", "firstname", "first_name", "given name", "first"])), None)
    lname_col = next((c for c in df.columns if any(k in c.lower() for k in ["last name", "lastname", "last_name", "surname", "last"])), None)
    if not fname_col:
        fname_col = next((c for c in df.columns if c != lname_col and "name" in c.lower()), None)
    title_col = next((c for c in df.columns if "title" in c.lower()), None)
    dob_col = next((c for c in df.columns if any(k in c.lower() for k in ["dob", "birth", "date of birth"])), None)
    doc_col = next((c for c in df.columns if any(k in c.lower() for k in ["passport", "document", "id no", "doc"])), None)
    phone_col = next((c for c in df.columns if any(k in c.lower() for k in ["phone", "mobile", "contact", "cell"])), None)
    email_col = next((c for c in df.columns if any(k in c.lower() for k in ["email", "mail", "gmail"])), None)
    saudia_col = next((c for c in df.columns if any(k in c.lower() for k in ["saudia", "alfursan", "alfusan", "membership"])), None)

    try:
        with conn:
            conn.execute("DELETE FROM project2_passengers;")
            for _, row in df.iterrows():
                if fname_col and lname_col and fname_col != lname_col:
                    fname = str(row[fname_col]).strip() if pd.notna(row[fname_col]) else ""
                    lname = str(row[lname_col]).strip() if pd.notna(row[lname_col]) else ""
                elif lname_col:
                    raw_name = str(row[lname_col]).strip() if pd.notna(row[lname_col]) else ""
                    if raw_name.lower() == "nan": raw_name = ""
                    parts = raw_name.split()
                    if len(parts) > 1:
                        fname = parts[0]
                        lname = " ".join(parts[1:])
                    else:
                        fname = "Guest"
                        lname = raw_name if raw_name else "Passenger"
                elif fname_col:
                    raw_name = str(row[fname_col]).strip() if pd.notna(row[fname_col]) else ""
                    if raw_name.lower() == "nan": raw_name = ""
                    parts = raw_name.split()
                    if len(parts) > 1:
                        fname = parts[0]
                        lname = " ".join(parts[1:])
                    else:
                        fname = raw_name if raw_name else "Guest"
                        lname = "Passenger"
                else:
                    fname = "Guest"
                    lname = "Passenger"

                if fname.lower() == "nan": fname = "Guest"
                if lname.lower() == "nan": lname = "Passenger"
                if not fname: fname = "Guest"
                if not lname: lname = "Passenger"

                title = str(row[title_col]).strip() if title_col and pd.notna(row[title_col]) else "Mr"
                if title.lower() in ["nan", ""]: title = "Mr"

                dob = str(row[dob_col]).strip() if dob_col and pd.notna(row[dob_col]) else ""
                if dob.lower() == "nan": dob = ""

                doc = str(row[doc_col]).strip() if doc_col and pd.notna(row[doc_col]) else ""
                if doc.lower() == "nan": doc = ""

                phone = str(row[phone_col]).strip() if phone_col and pd.notna(row[phone_col]) else ""
                if phone.lower() == "nan": phone = ""

                email = str(row[email_col]).strip() if email_col and pd.notna(row[email_col]) else ""
                if email.lower() == "nan": email = ""

                saudia_id = str(row[saudia_col]).strip() if saudia_col and pd.notna(row[saudia_col]) else ""
                if saudia_id.lower() == "nan": saudia_id = ""

                status = "Activated" if saudia_id else "Pending"

                conn.execute("""
                    INSERT INTO project2_passengers
                    (first_name, last_name, title, dob, passport, mobile, email, saudia_id, status, remarks, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Ready for processing', ?, ?);
                """, (fname, lname, title, dob, doc, phone, email, saudia_id, status, now, now))
    finally:
        conn.close()


def get_project2_passengers_list(username: str) -> list:
    """Returns all Project 2 passengers as dictionaries."""
    init_project2_db(username)
    conn = get_connection(username)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM project2_passengers ORDER BY id ASC")
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_project2_passengers_df(username: str) -> pd.DataFrame:
    """Exports Project 2 passengers as DataFrame for Excel download."""
    init_project2_db(username)
    conn = get_connection(username)
    try:
        df = pd.read_sql_query("""
            SELECT 
                id AS "ID",
                first_name AS "First Name",
                last_name AS "Last Name",
                title AS "Title",
                dob AS "Date of Birth",
                passport AS "Passport/Document",
                mobile AS "Mobile Number",
                email AS "Email ID",
                saudia_id AS "Saudia Membership ID",
                status AS "Status",
                remarks AS "Remarks",
                updated_at AS "Processed At"
            FROM project2_passengers
            ORDER BY id ASC;
        """, conn)
        if "Saudia Membership ID" in df.columns:
            def clean_id(val):
                s = str(val).strip()
                if s.endswith(".0"):
                    s = s[:-2]
                if s.lower() in ["nan", "none", ""]:
                    return ""
                return s
            df["Saudia Membership ID"] = df["Saudia Membership ID"].apply(clean_id)
        return df
    finally:
        conn.close()


def reset_project2_passengers(username: str):
    """Resets Project 2 passenger queue for a fresh run."""
    init_project2_db(username)
    conn = get_connection(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with conn:
            conn.execute("""
                UPDATE project2_passengers
                SET status = 'Pending', saudia_id = '', email = '', remarks = 'Ready for processing', updated_at = ?;
            """, (now,))
            conn.execute("""
                UPDATE project2_status
                SET status_text = 'Idle', processed = 0, success = 0, failed = 0, skipped = 0, current_passenger = '', last_updated = ?
                WHERE id = 1;
            """, (now,))
    finally:
        conn.close()


def get_project2_status(username: str) -> dict:
    """Returns Project 2 automation status."""
    init_project2_db(username)
    conn = get_connection(username)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM project2_status WHERE id = 1")
        row = cur.fetchone()
        res = dict(row) if row else {"status_text": "Idle", "processed": 0, "total": 0, "success": 0, "failed": 0, "skipped": 0}
        
        # Calculate real-time table totals
        cur.execute("""
            SELECT 
                COUNT(*) as total_count,
                SUM(CASE WHEN status = 'Activated' THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN status IN ('Signup Failed', 'Failed Activation') THEN 1 ELSE 0 END) as failed_count,
                SUM(CASE WHEN status != 'Pending' THEN 1 ELSE 0 END) as processed_count
            FROM project2_passengers;
        """)
        c_row = cur.fetchone()
        if c_row and c_row["total_count"] > 0:
            res["total"] = c_row["total_count"]
            res["success"] = c_row["success_count"] or 0
            res["failed"] = c_row["failed_count"] or 0
            res["processed"] = c_row["processed_count"] or 0
            
        return res
    finally:
        conn.close()


def update_project2_status(username: str, status_text: str, processed: int, total: int,
                           success: int, failed: int, skipped: int, current_pax: str = ""):
    """Updates Project 2 status."""
    init_project2_db(username)
    conn = get_connection(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with conn:
            conn.execute("""
                UPDATE project2_status SET
                    status_text = ?,
                    processed = ?,
                    total = ?,
                    success = ?,
                    failed = ?,
                    skipped = ?,
                    current_passenger = ?,
                    last_updated = ?
                WHERE id = 1;
            """, (status_text, int(processed), int(total), int(success), int(failed), int(skipped), current_pax, now))
    finally:
        conn.close()


def update_project2_passenger_record(username: str, pax_id: int, updates: dict):
    """Updates a single Project 2 passenger record."""
    conn = get_connection(username)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fields = []
    values = []
    for k, v in updates.items():
        fields.append(f"{k} = ?")
        values.append(v)
    fields.append("updated_at = ?")
    values.append(now)
    values.append(pax_id)
    query = f"UPDATE project2_passengers SET {', '.join(fields)} WHERE id = ?;"
    try:
        with conn:
            conn.execute(query, tuple(values))
    finally:
        conn.close()


def sync_project2_welcome_ids(username: str) -> tuple[int, str]:
    """
    Connects to IMAP using Project 2 config, scans recent welcome emails,
    and updates any unactivated or missing ID passengers in project2_passengers.
    """
    try:
        import imaplib
        import email
        import re
        config = get_project2_config(username)
        user_email = (config.get("master_email") or "").strip()
        app_pwd = (config.get("master_password") or "").strip()
        imap_srv = config.get("imap_server") or "imap.gmail.com"
        imap_pt = int(config.get("imap_port") or 993)

        if not user_email or not app_pwd:
            logger.warning(f"No IMAP credentials configured for Project 2 ({username}). Skipping welcome sync.")
            return 0, "No email address or App Password configured in Project 2 settings."

        try:
            mail = imaplib.IMAP4_SSL(imap_srv, imap_pt, timeout=15)
            mail.login(user_email, app_pwd)
        except Exception as ex:
            err_str = str(ex)
            if "AUTHENTICATIONFAILED" in err_str:
                hint = "IMAP authentication failed! Please verify: 1) IMAP is enabled in Gmail Settings > Forwarding and POP/IMAP, and 2) A 16-character Google App Password was generated for this account."
            elif "Application-specific password required" in err_str:
                hint = "Regular Google password detected! Please generate a 16-character App Password at https://myaccount.google.com/apppasswords."
            else:
                hint = f"IMAP connection failed: {err_str}"
            logger.warning(f"Project 2 IMAP login failed for user '{user_email}': {hint}")
            return 0, hint

        mail.select("inbox")

        status, data = mail.search(None, 'ALL')
        if not data or not data[0]:
            mail.logout()
            return 0, "Inbox opened, but no messages found."

        mail_ids = data[0].split()
        found_memberships = []
        # Scan last 100 emails to ensure none are missed
        for mid in reversed(mail_ids[-100:]):
            try:
                s, mdata = mail.fetch(mid, "(RFC822)")
                if s != "OK" or not mdata:
                    continue
                msg = email.message_from_bytes(mdata[0][1])
                subject = str(msg.get("Subject", "")).lower()
                if "welcome to alfursan" not in subject and "alfursan" not in subject:
                    continue
                to_addr = str(msg.get("To", "")).lower()
                deliv_to = str(msg.get("Delivered-To", "")).lower()
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() in ["text/plain", "text/html"]:
                            body += part.get_payload(decode=True).decode(errors="ignore")
                else:
                    body = msg.get_payload(decode=True).decode(errors="ignore")

                clean_text = re.sub(r'<[^>]+>', ' ', body)
                match = re.search(r'\b(100\d{6,9})\b', clean_text) or re.search(r'(?:Membership|AlFursan|Member)\s*(?:ID|No|Number|#)?\s*[:#-]?\s*(\d{8,12})', clean_text, re.IGNORECASE)
                if match:
                    mem_id = match.group(1) if match.lastindex else match.group(0)
                    combined_addr = f"{to_addr} {deliv_to}"
                    found_memberships.append((combined_addr, clean_text.lower(), mem_id))
            except Exception:
                continue
        mail.logout()

        if not found_memberships:
            return 0, "Inbox checked, but no Saudia Alfursan Welcome emails were found in recent messages."

        passengers = get_project2_passengers_list(username)
        updated = 0
        for p in passengers:
            p_id = p["id"]
            cur_mid = str(p.get("saudia_id") or "").strip()
            if cur_mid and cur_mid != "nan":
                continue
            p_email = str(p.get("email") or "").lower().strip()
            p_alias = p_email.split("+")[1].split("@")[0] if "+" in p_email and "@" in p_email else ""
            lname = str(p.get("last_name") or "").lower().strip()
            fname = str(p.get("first_name") or "").lower().strip()

            for addr_info, body_text, mid in found_memberships:
                matched = False
                if p_email and p_email in addr_info:
                    matched = True
                elif p_alias and p_alias in addr_info:
                    matched = True
                elif len(lname) > 3 and (lname in addr_info or lname in body_text):
                    matched = True
                elif len(fname) > 3 and fname in body_text and len(lname) > 2 and lname in body_text:
                    matched = True

                if matched:
                    update_project2_passenger_record(username, p_id, {
                        "saudia_id": mid,
                        "status": "Activated",
                        "remarks": "Completed successfully (synced from welcome email)"
                    })
                    updated += 1
                    logger.info(f"Project 2 sync matched Saudia ID {mid} for passenger ID {p_id} ({fname} {lname})")
                    break

        if updated > 0:
            try:
                from src.utils import save_excel_data
                from src.config import OUTPUTS_DIR
                df = get_project2_passengers_df(username)
                out_file = os.path.join(OUTPUTS_DIR, f"{username}_project2_output.xlsx")
                save_excel_data(df, out_file)
                logger.info(f"Auto-exported updated Project 2 records to {out_file}")
            except Exception as ex:
                logger.warning(f"Error auto-exporting project2 excel: {ex}")

        return updated, f"Successfully synced {updated} AlFursan Membership ID(s) from inbox!"
    except Exception as e:
        logger.warning(f"Error in sync_project2_welcome_ids: {e}")
        return 0, f"Error scanning emails: {e}"

