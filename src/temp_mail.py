import imaplib
import email
import re
import time
import logging
import requests
import random
import string

logger = logging.getLogger(__name__)

import os

# Default IMAP Configuration (Can be overridden per user dynamically)
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "ktaha8967@gmail.com")
APP_PASSWORD = os.getenv("APP_PASSWORD", "mwxeaewdzvqjwnzo")

class TempMailClient:
    """
    Email client using KTaha's default Gmail IMAP credentials to prevent blocks
    and reliably fetch activation codes.
    """
    def __init__(self, email_address: str = None, app_password: str = None, imap_server: str = None, imap_port: int = None):
        self.api_url = "https://api.mail.tm"
        self.email_address = None
        # Use user-provided automation credentials if provided, otherwise fallback to KTaha default engine
        clean_email = (email_address or "").strip().lower()
        clean_pwd = (app_password or "").replace(" ", "").replace("-", "").strip()
        self.imap_user_email = clean_email if (clean_email and "@" in clean_email) else EMAIL_ADDRESS
        self.imap_app_password = clean_pwd if clean_pwd else APP_PASSWORD

        # Auto-detect IMAP server if domain is known
        server = (imap_server or "").strip()
        if not server:
            if "@" in self.imap_user_email:
                domain = self.imap_user_email.split("@")[1]
                if any(d in domain for d in ["outlook.com", "hotmail.com", "live.com"]):
                    server = "imap-mail.outlook.com"
                elif "yahoo.com" in domain:
                    server = "imap.mail.yahoo.com"
                elif "icloud.com" in domain:
                    server = "imap.mail.me.com"
                else:
                    server = IMAP_SERVER
            else:
                server = IMAP_SERVER

        self.imap_server = server
        self.imap_port = int(imap_port) if imap_port else int(IMAP_PORT)
        self.password = None
        self.imap_password = self.imap_app_password
        self.token = None
        self.account_id = None
        self.use_imap = True

        self.imap_connected = True
        # Upfront validation of custom IMAP credentials (NEVER switch email address)
        if clean_email and "@" in clean_email:
            try:
                import imaplib
                test_mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port, timeout=8)
                test_mail.login(self.imap_user_email, self.imap_app_password)
                test_mail.logout()
                logger.info(f"Custom IMAP credentials validated for '{self.imap_user_email}'.")
            except Exception as e:
                self.imap_connected = False
                if "Application-specific password required" in str(e):
                    logger.warning(
                        f"IMAP Auth Note: Custom Gmail '{self.imap_user_email}' requires a 16-character Google App Password! "
                        f"All passenger signups will strictly continue using '{self.imap_user_email}' aliases."
                    )
                else:
                    logger.warning(
                        f"Custom IMAP login note for '{self.imap_user_email}': {e}. "
                        f"All passenger signups will strictly continue using '{self.imap_user_email}' aliases."
                    )

    def generate_email(self, passenger_pnr: str = None, passenger_lastname: str = None) -> str:
        """Generates email address. Uses passenger-identifiable alias if IMAP configured, otherwise Mail.tm."""
        if self.use_imap:
            username, domain = self.imap_user_email.split("@")
            rand_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
            if passenger_pnr and passenger_lastname:
                clean_pnr = re.sub(r'[^a-zA-Z0-9]', '', str(passenger_pnr)).lower()
                clean_name = re.sub(r'[^a-zA-Z0-9]', '', str(passenger_lastname)).lower()
                tag = f"{clean_pnr}_{clean_name}_{rand_suffix}"
            elif passenger_pnr:
                clean_pnr = re.sub(r'[^a-zA-Z0-9]', '', str(passenger_pnr)).lower()
                tag = f"{clean_pnr}_{rand_suffix}"
            elif passenger_lastname:
                clean_name = re.sub(r'[^a-zA-Z0-9]', '', str(passenger_lastname)).lower()
                tag = f"{clean_name}_{rand_suffix}"
            else:
                tag = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
            self.email_address = f"{username}+{tag}@{domain}"
            logger.info(f"Using identifiable IMAP alias: {self.email_address}")
            return self.email_address

        # Fallback to Mail.tm
        for attempt in range(3):
            try:
                domains_res = requests.get(f"{self.api_url}/domains", timeout=10)
                if domains_res.status_code == 200:
                    domains = domains_res.json().get("hydra:member", [])
                    if domains:
                        domain = domains[0].get("domain")
                        username = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
                        self.password = "".join(random.choices(string.ascii_letters + string.digits, k=12))
                        self.email_address = f"{username}@{domain}"
                        
                        create_res = requests.post(
                            f"{self.api_url}/accounts",
                            json={"address": self.email_address, "password": self.password},
                            timeout=10
                        )
                        if create_res.status_code == 201:
                            self.account_id = create_res.json().get("id")
                            
                            token_res = requests.post(
                                f"{self.api_url}/token",
                                json={"address": self.email_address, "password": self.password},
                                timeout=10
                            )
                            if token_res.status_code == 200:
                                self.token = token_res.json().get("token")
                                logger.info(f"Generated mail.tm email: {self.email_address}")
                                return self.email_address
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} to generate mail.tm email failed: {e}")
                time.sleep(2)
        
        self.email_address = "offline_test@mail.tm"
        logger.info(f"Generated fallback email offline: {self.email_address}")
        return self.email_address

    def get_messages_imap(self, keyword: str, target_email: str = None, min_timestamp: float = None):
        """
        Polls Gmail/IMAP server for recent messages containing keyword,
        strictly verifying recipient and timestamp to avoid false matches.
        """
        messages_found = []
        try:
            try:
                mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port, timeout=20)
                mail.login(self.imap_user_email, self.imap_app_password)
            except Exception as login_err:
                if "Application-specific password required" in str(login_err):
                    logger.error(
                        f"IMAP Error: Gmail account '{self.imap_user_email}' requires a 16-character App Password! "
                        f"Regular Google login password will not work. (Error: {login_err})"
                    )
                else:
                    logger.warning(f"IMAP login failed for '{self.imap_user_email}': {login_err}")
                return []
            mail.select("inbox")
            
            import datetime
            import email.utils
            today_str = datetime.datetime.now().strftime("%d-%b-%Y")
            
            # Restrict IMAP search to today's messages
            status, data = mail.search(None, f'(SINCE "{today_str}")')
            if status != "OK" or not data or not data[0]:
                status, data = mail.search(None, 'ALL')
                
            if status == "OK" and data[0]:
                mail_ids = data[0].split()
                # Inspect most recent 35 messages
                for mail_id in mail_ids[-35:]:
                    status, msg_data = mail.fetch(mail_id, "(RFC822)")
                    if status != "OK" or not msg_data:
                        continue
                        
                    raw_email = msg_data[0][1]
                    msg = email.message_from_bytes(raw_email)
                    
                    # 1. Date / Timestamp verification
                    if min_timestamp:
                        date_str = msg.get("Date")
                        if date_str:
                            try:
                                date_tuple = email.utils.parsedate_tz(date_str)
                                if date_tuple:
                                    msg_epoch = email.utils.mktime_tz(date_tuple)
                                    if msg_epoch < (min_timestamp - 180):  # 180s tolerance
                                        continue
                            except Exception:
                                pass
                                
                    subject = msg.get("Subject", "")
                    try:
                        from email.header import decode_header
                        decoded = decode_header(subject)
                        subject = "".join(
                            [str(t[0], t[1] or "utf-8") if isinstance(t[0], bytes) else t[0] for t in decoded]
                        )
                    except Exception:
                        pass
                        
                    to_header = str(msg.get("To", "")).lower()
                    deliv_to = str(msg.get("Delivered-To", "")).lower()
                    from_header = str(msg.get("From", "")).lower()
                    
                    # 2. Extract body
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            if content_type in ["text/plain", "text/html"]:
                                try:
                                    body += part.get_payload(decode=True).decode(errors="ignore")
                                except Exception:
                                    pass
                    else:
                        try:
                            body = msg.get_payload(decode=True).decode(errors="ignore")
                        except Exception:
                            pass
                            
                    full_content = (subject + " " + body + " " + from_header).lower()
                    
                    # Strict Keyword check: differentiate Welcome from Activation
                    kw_lower = keyword.lower()
                    if kw_lower == "welcome":
                        if "welcome to alfursan" not in subject.lower() and "welcome to alfursan" not in full_content:
                            continue
                    elif kw_lower == "activation":
                        if "account activation" not in subject.lower() and "activation" not in full_content:
                            continue
                    elif kw_lower not in full_content:
                        continue
                        
                    # 3. Target Email check
                    if target_email:
                        tgt = target_email.lower().strip()
                        alias_tag = None
                        if "+" in tgt and "@" in tgt:
                            alias_tag = tgt.split("+")[1].split("@")[0].lower()
                            
                        matched_recipient = False
                        if tgt in to_header or tgt in deliv_to:
                            matched_recipient = True
                        elif alias_tag and (alias_tag in to_header or alias_tag in deliv_to or alias_tag in full_content):
                            matched_recipient = True
                        elif not alias_tag and (self.imap_user_email.lower() in to_header or self.imap_user_email.lower() in deliv_to):
                            matched_recipient = True
                            
                        if not matched_recipient:
                            logger.info(f"Skipping email '{subject}' - recipient did not match target '{target_email}'")
                            continue
                            
                    messages_found.append({
                        "subject": subject,
                        "textBody": body,
                        "htmlBody": body,
                        "from": msg.get("From", "")
                    })
            mail.close()
            mail.logout()
        except Exception as e:
            logger.error(f"IMAP fetch error: {e}")
        return messages_found

    def get_messages(self):
        """Fetches all messages in the mailbox (Mail.tm path)."""
        if not self.token:
            return []
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            response = requests.get(f"{self.api_url}/messages", headers=headers, timeout=10)
            if response.status_code == 200:
                return response.json().get("hydra:member", [])
        except Exception as e:
            logger.error(f"Error fetching mail.tm messages: {e}")
        return []

    def fetch_email_content(self, mail_id):
        """Fetches detailed content of a specific email by ID (Mail.tm path)."""
        if not self.token:
            return None
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            response = requests.get(f"{self.api_url}/messages/{mail_id}", headers=headers, timeout=10)
            if response.status_code == 200:
                msg_data = response.json()
                mapped = {
                    "id": msg_data.get("id"),
                    "subject": msg_data.get("subject", ""),
                    "textBody": msg_data.get("text", ""),
                    "htmlBody": msg_data.get("html", [msg_data.get("text", "")]),
                    "from": msg_data.get("from", {}).get("address", "")
                }
                if isinstance(mapped["htmlBody"], list) and len(mapped["htmlBody"]) > 0:
                    mapped["htmlBody"] = mapped["htmlBody"][0]
                return mapped
        except Exception as e:
            logger.error(f"Error fetching message {mail_id}: {e}")
        return None

    def wait_for_email_matching(self, keyword: str, timeout_seconds: int = 120, check_interval: int = 5, stop_check=None, target_email: str = None, min_timestamp: float = None):
        """Polls the inbox for an email containing the specified keyword, respecting stop requests."""
        tgt = target_email or self.email_address
        logger.info(f"Waiting for email containing '{keyword}' for recipient: {tgt or 'any'} (min_ts={min_timestamp})...")
        start_time = time.time()
        min_ts = min_timestamp or (start_time - 30)
        while time.time() - start_time < timeout_seconds:
            if stop_check and stop_check():
                logger.info("Stop requested during email wait. Exiting loop.")
                return None
            if self.use_imap:
                messages = self.get_messages_imap(keyword, target_email=tgt, min_timestamp=min_ts)
                if messages:
                    # Return latest matched email details
                    return messages[-1]
            else:
                messages = self.get_messages()
                for msg in messages:
                    subject = msg.get("subject", "") or ""
                    from_addr = msg.get("from", {}).get("address", "") or ""
                    if keyword.lower() in subject.lower() or keyword.lower() in from_addr.lower():
                        details = self.fetch_email_content(msg["id"])
                        if details:
                            return details
            for _ in range(check_interval):
                if stop_check and stop_check():
                    return None
                time.sleep(1)
        logger.warning(f"Timeout waiting for email containing '{keyword}' for {tgt}")
        return None

    def wait_for_email_with_phrase(self, phrase: str, timeout_seconds: int = 120, check_interval: int = 5, stop_check=None, target_email: str = None, min_timestamp: float = None):
        """Compatibility alias forwarding to wait_for_email_matching."""
        return self.wait_for_email_matching(
            keyword=phrase,
            timeout_seconds=timeout_seconds,
            check_interval=check_interval,
            stop_check=stop_check,
            target_email=target_email,
            min_timestamp=min_timestamp
        )

    def find_membership_id_for_email(self, target_email: str) -> str:
        """Directly scans recent IMAP messages for a welcome email matching target_email and extracts membership ID."""
        if not self.use_imap or not self.imap_user_email or not self.imap_password:
            return ""
        try:
            import imaplib
            import email
            import re
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port, timeout=15)
            mail.login(self.imap_user_email, self.imap_password)
            mail.select("inbox")
            status, data = mail.search(None, 'ALL')
            if not data or not data[0]:
                mail.logout()
                return ""
            
            mail_ids = data[0].split()
            tgt = target_email.lower().strip()
            alias_tag = None
            if "+" in tgt and "@" in tgt:
                alias_tag = tgt.split("+")[1].split("@")[0]

            for mid in reversed(mail_ids[-30:]):
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
                    
                    matched = False
                    if tgt in to_addr or tgt in deliv_to:
                        matched = True
                    elif alias_tag and (alias_tag in to_addr or alias_tag in deliv_to):
                        matched = True
                    elif not alias_tag and (self.imap_user_email.lower() in to_addr or self.imap_user_email.lower() in deliv_to):
                        matched = True

                    if not matched:
                        continue

                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() in ["text/plain", "text/html"]:
                                body += part.get_payload(decode=True).decode(errors="ignore")
                    else:
                        body = msg.get_payload(decode=True).decode(errors="ignore")

                    clean_text = re.sub(r'<[^>]+>', ' ', body)
                    match = re.search(r'\b(100\d{6,9})\b', clean_text)
                    if match:
                        mail.logout()
                        return match.group(1)
                except Exception:
                    continue
            mail.logout()
        except Exception as e:
            logger.warning(f"Error in find_membership_id_for_email: {e}")
        return ""

def sync_welcome_membership_ids(username: str = None):
    """
    Connects to IMAP inbox, scans recent 'Welcome to Alfursan loyalty program' emails,
    extracts genuine Saudia Membership IDs, and updates SQLite database and processed Excel files.
    """
    uname = username or "default"
    try:
        from src.db_manager import get_credentials as get_db_credentials, get_passengers_df, update_passenger_record
        from src.user_manager import get_user_output_path
        from src.utils import save_excel_data
        import sqlite3

        creds = get_db_credentials(uname) if uname != "default" else {}
        user_email = creds.get("email") or EMAIL_ADDRESS
        app_pwd = creds.get("app_password") or APP_PASSWORD
        imap_srv = creds.get("imap_server") or IMAP_SERVER
        imap_pt = creds.get("imap_port") or IMAP_PORT

        try:
            mail = imaplib.IMAP4_SSL(imap_srv, int(imap_pt), timeout=15)
            mail.login(user_email, app_pwd)
        except Exception as e:
            logger.warning(f"IMAP login failed for user '{user_email}': {e}")
            return 0
        mail.select("inbox")

        status, data = mail.search(None, 'ALL')
        if not data or not data[0]:
            mail.logout()
            return 0

        mail_ids = data[0].split()
        found_memberships = {}
        # Scan last 40 emails
        for mid in mail_ids[-40:]:
            try:
                status, msg_data = mail.fetch(mid, "(RFC822)")
                if status != "OK" or not msg_data:
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                subject = str(msg.get("Subject", ""))
                if "welcome to alfursan" not in subject.lower():
                    continue

                to_addr = str(msg.get("To", "")).lower()
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() in ["text/plain", "text/html"]:
                            body += part.get_payload(decode=True).decode(errors="ignore")
                else:
                    body = msg.get_payload(decode=True).decode(errors="ignore")

                clean_text = re.sub(r'<[^>]+>', ' ', body)
                match = re.search(r'\b(100\d{6,9})\b', clean_text)
                if match:
                    mem_id = match.group(1)
                    found_memberships[to_addr] = mem_id
                    logger.info(f"Sync discovered Welcome email: {to_addr} -> Saudia ID {mem_id}")
            except Exception:
                continue

        mail.logout()

        if not found_memberships:
            return 0

        # Update SQLite and Excel
        df = get_passengers_df(uname)
        updated_count = 0
        for idx, row in df.iterrows():
            pnr = str(row.get("PNR", "")).strip()
            last_name = str(row.get("Last Name", "")).strip()
            current_id = str(row.get("Saudia Membership ID", "")).strip()
            pemail = str(row.get("Gmail/Email ID", "")).strip().lower()

            if current_id and current_id != "nan":
                continue

            clean_pnr = pnr.lower()
            clean_lname = last_name.lower()
            for to_addr, mid in found_memberships.items():
                if (clean_pnr and clean_pnr in to_addr) or (clean_lname and clean_lname in to_addr) or (pemail and pemail in to_addr):
                    df.at[idx, "Saudia Membership ID"] = mid
                    df.at[idx, "Status"] = "Activated"
                    df.at[idx, "Remarks"] = "Completed"
                    update_passenger_record(uname, pnr, last_name, {
                        "Saudia Membership ID": mid,
                        "Status": "Activated",
                        "Remarks": "Completed"
                    })
                    updated_count += 1
                    logger.info(f"Synced Saudia Membership ID {mid} for passenger {pnr} ({last_name}).")
                    break

        if updated_count > 0:
            out_path = get_user_output_path(uname) if uname != "default" else OUTPUT_EXCEL_PATH
            save_excel_data(df, out_path)
            logger.info(f"Synced {updated_count} membership IDs to {out_path}.")

        return updated_count
    except Exception as e:
        logger.warning(f"Error during welcome membership ID sync: {e}")
        return 0

