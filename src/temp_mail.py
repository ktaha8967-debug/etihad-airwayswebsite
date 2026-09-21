import imaplib
import email
import re
import time
import logging
import requests
import random
import string

logger = logging.getLogger(__name__)

# IMAP Configuration (Change these to use a real email for production verification)
IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993
EMAIL_ADDRESS = "ktaha8967@gmail.com"
APP_PASSWORD = "mwxeaewdzvqjwnzo"

class TempMailClient:
    """
    Email client that supports Mail.tm for temporary mail fallback,
    or standard secure IMAP (e.g., Gmail) to prevent blocks and fetch activation codes.
    """
    def __init__(self):
        self.api_url = "https://api.mail.tm"
        self.email_address = None
        self.password = None
        self.token = None
        self.account_id = None
        self.use_imap = bool(EMAIL_ADDRESS and APP_PASSWORD)

    def generate_email(self) -> str:
        """Generates email address. Uses Gmail base if IMAP configured, otherwise Mail.tm."""
        if self.use_imap:
            # Generate a unique plus-alias (e.g. username+pnr123@gmail.com)
            username, domain = EMAIL_ADDRESS.split("@")
            random_tag = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
            self.email_address = f"{username}+{random_tag}@{domain}"
            logger.info(f"Using IMAP alias email address: {self.email_address}")
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

    def get_messages_imap(self, keyword: str):
        """Polls Gmail/IMAP server for recent messages containing keyword."""
        messages_found = []
        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
            mail.login(EMAIL_ADDRESS, APP_PASSWORD)
            mail.select("inbox")
            
            # Search for unread emails containing keyword in subject/body
            status, data = mail.search(None, f'(SUBJECT "{keyword}")')
            if status == "OK" and data[0]:
                mail_ids = data[0].split()
                # Fetch last 5 messages to check
                for mail_id in mail_ids[-5:]:
                    status, msg_data = mail.fetch(mail_id, "(RFC822)")
                    if status == "OK":
                        raw_email = msg_data[0][1]
                        msg = email.message_from_bytes(raw_email)
                        
                        subject = msg.get("Subject", "")
                        # Decode subject if encoded
                        try:
                            from email.header import decode_header
                            decoded = decode_header(subject)
                            subject = "".join(
                                [str(t[0], t[1] or "utf-8") if isinstance(t[0], bytes) else t[0] for t in decoded]
                            )
                        except Exception:
                            pass
                            
                        # Extract body
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                content_type = part.get_content_type()
                                if content_type in ["text/plain", "text/html"]:
                                    try:
                                        body += part.get_payload(decode=True).decode()
                                    except Exception:
                                        pass
                        else:
                            try:
                                body = msg.get_payload(decode=True).decode()
                            except Exception:
                                pass
                                
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

    def wait_for_email_matching(self, keyword: str, timeout_seconds: int = 120, check_interval: int = 10):
        """Polls the inbox for an email containing the specified keyword."""
        logger.info(f"Waiting for email containing '{keyword}'...")
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            if self.use_imap:
                messages = self.get_messages_imap(keyword)
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
            time.sleep(check_interval)
        logger.warning(f"Timeout waiting for email containing '{keyword}'")
        return None
