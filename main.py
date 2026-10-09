import os
if "DISPLAY" not in os.environ:
    os.environ["DISPLAY"] = ":99"

import sys
import logging
import asyncio
# Clear any active event loop to allow Playwright Sync API to launch
try:
    loop = asyncio.get_event_loop()
    if not loop.is_running():
        loop.close()
except Exception:
    pass
asyncio.set_event_loop(None)
from src.utils import setup_logging, read_excel_data, save_excel_data
from src.etihad_scraper import EtihadScraper
from src.saudia_signup import SaudiaSignupManager
from src.temp_mail import TempMailClient
from src.config import INPUT_EXCEL_PATH, OUTPUT_EXCEL_PATH, STATUS_FILE_PATH

# Set up logging first
setup_logging()
logger = logging.getLogger("Orchestrator")

import json
import time
import datetime
import threading

user_stop_events = {}
events_lock = threading.Lock()

def get_stop_event(username=None):
    key = str(username or "__global__")
    with events_lock:
        if key not in user_stop_events:
            user_stop_events[key] = threading.Event()
        return user_stop_events[key]

def request_stop(username=None):
    """Signals running automation to stop promptly for a specific user or globally."""
    if username:
        get_stop_event(username).set()
        logger.info(f"🛑 Automation stop signal requested for user '{username}'.")
    else:
        with events_lock:
            for ev in user_stop_events.values():
                ev.set()
        logger.info("🛑 Global automation stop signal requested.")

def clear_stop(username=None):
    """Clears stop signal for fresh execution."""
    get_stop_event(username).clear()

def is_stopped(username=None) -> bool:
    return get_stop_event(username).is_set()

def write_status(status_str, processed, total, success, failed, skipped, current_pnr="", current_name="", status_file_path=None, username=None):
    target_status = status_file_path or STATUS_FILE_PATH
    try:
        status_data = {
            "status": status_str,
            "processed": int(processed),
            "total": int(total),
            "success": int(success),
            "failed": int(failed),
            "skipped": int(skipped),
            "current_pnr": current_pnr,
            "current_name": current_name,
            "last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(target_status, "w") as f:
            json.dump(status_data, f, indent=4)
        if username:
            from src.db_manager import update_user_status
            update_user_status(username, status_str, processed, total, success, failed, skipped, current_pnr, current_name)
    except Exception as e:
        logger.warning(f"Failed to write status: {e}")


def run_automation(mode="all", username=None, credentials=None):
    clear_stop(username)
    user_stop_check = lambda: is_stopped(username)
    from src.user_manager import get_user, get_user_input_path, get_user_output_path, get_user_status_path, get_user_log_path

    if username and not credentials:
        credentials = get_user(username)

    input_target_path = get_user_input_path(username) if username else INPUT_EXCEL_PATH
    output_target_path = get_user_output_path(username) if username else OUTPUT_EXCEL_PATH
    status_file_path = get_user_status_path(username) if username else STATUS_FILE_PATH
    log_file_path = get_user_log_path(username) if username else LOG_FILE_PATH

    # Add user log file handler
    user_log_handler = None
    if username:
        try:
            user_log_handler = logging.FileHandler(log_file_path, encoding="utf-8")
            user_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
            logging.getLogger().addHandler(user_log_handler)
        except Exception:
            pass

    logger.info(f"Starting Airline Passenger Automation in mode: {mode} (User: {username or 'default'})...")
    
    from src.db_manager import (
        get_passengers_df, update_passenger_record, update_user_status,
        import_passengers_from_df
    )

    # 1. Load Passenger Data from User's Isolated Database
    df = None
    if username:
        try:
            from src.temp_mail import sync_welcome_membership_ids
            sync_welcome_membership_ids(username)
        except Exception:
            pass
        try:
            db_df = get_passengers_df(username)
            if len(db_df) > 0:
                df = db_df
                logger.info(f"Loaded {len(df)} passenger records from isolated SQLite database ({username}.db)")
        except Exception as e:
            logger.warning(f"Could not load from user SQLite database: {e}")

    if df is None or len(df) == 0:
        target_path = output_target_path if (mode == "claim_only" and os.path.exists(output_target_path)) else input_target_path
        logger.info(f"Loading input file: {target_path}")
        try:
            df = read_excel_data(target_path)
            if username:
                try:
                    import_passengers_from_df(username, df)
                except Exception as e:
                    logger.warning(f"Failed to import into SQLite: {e}")
        except Exception as e:
            logger.error(f"Failed to read excel file {target_path}: {e}")
            return

    # Print columns to aid debugging
    logger.info(f"Loaded columns: {list(df.columns)}")

    # Standardize column mappings (adjust as necessary for the actual sheet)
    pnr_col = next((c for c in df.columns if any(k in c.lower() for k in ["pnr", "booking", "reference", "ref"])), None)
    lastname_col = next((c for c in df.columns if any(k in c.lower() for k in ["last name", "lastname", "surname"])), None)
    
    if not pnr_col or not lastname_col:
        logger.error(f"Could not map PNR (found: {pnr_col}) or Last Name (found: {lastname_col}) columns correctly.")
        logger.info("Please verify the headers of the input file.")
        write_status("Error", 0, len(df) if df is not None else 0, 0, 0, len(df) if df is not None else 0, status_file_path=status_file_path, username=username)
        return

    logger.info(f"Mapping columns: PNR -> '{pnr_col}', Last Name -> '{lastname_col}'")

    # Add output columns if not exists
    if "Saudia Membership ID" not in df.columns:
        df["Saudia Membership ID"] = ""
    if "Status" not in df.columns:
        df["Status"] = ""
    if "Extracted Passenger Name" not in df.columns:
        df["Extracted Passenger Name"] = ""
    if "Flight Details" not in df.columns:
        df["Flight Details"] = ""

    from playwright.sync_api import sync_playwright
    
    # Initialize a single shared Playwright session to avoid event loop conflicts
    p = sync_playwright().start()
    
    etihad = EtihadScraper(p)
    saudia = SaudiaSignupManager(p)
    
    success_count = 0
    failed_count = 0
    skipped_count = 0
    processed_count = 0
    total_count = len(df)
    
    write_status("Running", 0, total_count, 0, 0, 0, status_file_path=status_file_path, username=username)
    
    try:
        # Start browsers once before entering the loop to ensure high-performance reuse
        if mode != "signup_only" and mode != "claim_only":
            logger.info("Initializing Etihad Scraper browser session...")
            etihad.start()
        
        for index, row in df.iterrows():
            if is_stopped(username):
                logger.info("🛑 Stop requested. Halting flight extraction loop.")
                break
            if mode == "claim_only" or mode == "signup_only":
                break
            pnr = str(row[pnr_col]).strip()
            last_name = str(row[lastname_col]).strip()
            
            # Skip empty entries
            if not pnr or pnr == "nan" or not last_name or last_name == "nan":
                continue
                
            # If already processed in both stages (has Saudia ID and genuine Flight Details), skip in 'all' mode
            has_saudia_id = str(row.get("Saudia Membership ID", "")).strip() not in ["", "nan"]
            has_flight = str(row.get("Flight Details", "")).strip() not in ["", "nan"]
            
            if mode == "all" and has_saudia_id and has_flight:
                logger.info(f"Skipping PNR {pnr}: Already fully processed (Saudia ID: {row.get('Saudia Membership ID')}).")
                skipped_count += 1
                processed_count += 1
                write_status("Running", processed_count, total_count, success_count, failed_count, skipped_count, pnr, last_name, status_file_path=status_file_path, username=username)
                continue

            logger.info(f"--- Processing Passenger {index + 1}/{len(df)}: PNR {pnr}, Last Name: {last_name} ---")
            
            passenger_details = {}
            temp_email = ""
            
            if mode != "signup_only":
                # Ensure browser is running (in case of self-healing restarts)
                if not etihad.browser:
                    etihad.start()
                
                # Step A: Access Etihad Booking
                success = etihad.access_booking(pnr, last_name)
                if not success:
                    if mode == "all":
                        logger.warning(f"Etihad booking access not available for PNR {pnr}. Proceeding to Saudia AlFursan registration using Excel passenger details...")
                        df.at[index, "Status"] = "Etihad Skipped / Pending Saudia"
                        if username:
                            try:
                                update_passenger_record(username, pnr, last_name, {"Status": "Etihad Skipped / Pending Saudia"})
                            except Exception as e:
                                logger.warning(f"Error updating SQLite: {e}")
                        continue
                    else:
                        df.at[index, "Status"] = "Etihad Login Failed"
                        failed_count += 1
                        processed_count += 1
                        write_status("Running", processed_count, total_count, success_count, failed_count, skipped_count, pnr, last_name, status_file_path=status_file_path, username=username)
                        if username:
                            try:
                                update_passenger_record(username, pnr, last_name, {"Status": "Etihad Login Failed"})
                            except Exception as e:
                                logger.warning(f"Error updating SQLite: {e}")
                        continue
                    
                if is_stopped(username):
                    logger.info("🛑 Stop requested after Etihad booking access. Halting.")
                    break
                    
                # Step B: Check frequent flyer status
                is_ff = etihad.check_frequent_flyer_status(last_name)
                if is_ff:
                    logger.info(f"Frequent Flyer already present on Etihad for PNR {pnr}. Skipping.")
                    df.at[index, "Status"] = "Skipped (FF Present)"
                    skipped_count += 1
                    processed_count += 1
                    write_status("Running", processed_count, total_count, success_count, failed_count, skipped_count, pnr, last_name, status_file_path=status_file_path, username=username)
                    if username:
                        try:
                            update_passenger_record(username, pnr, last_name, {"Status": "Skipped (FF Present)"})
                        except Exception as e:
                            logger.warning(f"Error updating SQLite: {e}")
                    continue

                # Step E: Extract passenger travel details
                passenger_details = etihad.extract_passenger_details(last_name)
                
                # Save extracted details to Excel
                df.at[index, "Extracted Passenger Name"] = passenger_details.get("passenger_name", "")
                df.at[index, "Flight Details"] = passenger_details.get("flight_number", "")
                
                if mode == "flight_only":
                    df.at[index, "Status"] = "Flight Extracted"
                    df.at[index, "Remarks"] = passenger_details.get("flight_number", "")
                    success_count += 1
                    processed_count += 1
                    write_status("Running", processed_count, total_count, success_count, failed_count, skipped_count, pnr, last_name, status_file_path=status_file_path, username=username)
                else:
                    df.at[index, "Status"] = "Flight Extracted | Pending Saudia"
                    df.at[index, "Remarks"] = passenger_details.get("flight_number", "")
                    write_status("Running", processed_count, total_count, success_count, failed_count, skipped_count, pnr, last_name, status_file_path=status_file_path, username=username)
                    
                # Auto-save changes to Excel and user SQLite database
                save_excel_data(df, output_target_path)
                if username:
                    try:
                        update_passenger_record(username, pnr, last_name, {
                            "Extracted Passenger Name": passenger_details.get("passenger_name", ""),
                            "Flight Details": passenger_details.get("flight_number", ""),
                            "Status": df.at[index, "Status"] if "Status" in df.columns else "",
                            "Remarks": df.at[index, "Remarks"] if "Remarks" in df.columns else ""
                        })
                    except Exception as e:
                        logger.warning(f"Error updating SQLite: {e}")
                
        if mode != "signup_only" and mode != "claim_only":
            logger.info("Stopping Etihad Scraper browser session...")
            etihad.stop()
            
        # Stage 2: Saudia Signup / Claim Loop
        if mode != "flight_only":
            logger.info("Initializing Saudia browser session...")
            saudia.start()
            
            # Determine processing subset
            items_to_process = []
            for index, row in df.iterrows():
                pnr = str(row[pnr_col]).strip()
                last_name = str(row[lastname_col]).strip()
                if not pnr or pnr == "nan" or not last_name or last_name == "nan":
                    continue
                items_to_process.append((index, pnr, last_name))
                
            logger.info(f"Loaded records count for processing: {len(items_to_process)}")
            if mode == "claim_only":
                # User requested: process only a single passenger for claim test
                active_claims = []
                for index, pnr, last_name in items_to_process:
                    saudia_id = str(df.at[index, "Saudia Membership ID"]).strip() if "Saudia Membership ID" in df.columns else ""
                    status = str(df.at[index, "Status"]).strip() if "Status" in df.columns else ""
                    logger.info(f"Checking claim candidate: PNR {pnr} | Saudia ID: {saudia_id} | Status: {status}")
                    if saudia_id and saudia_id != "nan" and "Claimed" not in status:
                        active_claims.append((index, pnr, last_name))
                        break
                items_to_process = active_claims
                
            for index, pnr, last_name in items_to_process:
                if is_stopped(username):
                    logger.info("🛑 Stop requested. Halting Saudia operations loop.")
                    break

                # If doing normal signup and already processed or failed at login, skip
                if mode != "claim_only":
                    saudia_id_val = str(df.at[index, "Saudia Membership ID"]).strip() if "Saudia Membership ID" in df.columns else ""
                    if saudia_id_val not in ["", "nan"]:
                        logger.info(f"Skipping Saudia signup for PNR {pnr}: Already has ID {saudia_id_val}")
                        continue
                    if df.at[index, "Status"] in ["Skipped (FF Present)"]:
                        continue
                    if mode == "flight_only":
                        continue

                logger.info(f"--- Saudia Stage: Processing Passenger {index + 1}/{len(df)}: PNR {pnr}, Last Name: {last_name} ---")
                
                # Retrieve passenger details freshly from df (including live Etihad extractions)
                first_name_val = str(df.at[index, "First Name"] if "First Name" in df.columns else (df.at[index, "FIRST NAME"] if "FIRST NAME" in df.columns else (df.at[index, "firstname"] if "firstname" in df.columns else ""))).strip()
                extracted_name = str(df.at[index, "Extracted Passenger Name"]).strip() if "Extracted Passenger Name" in df.columns else ""
                if not extracted_name or extracted_name.lower() == "nan":
                    if first_name_val and first_name_val.lower() != "nan":
                        extracted_name = f"{first_name_val} {last_name}".strip()
                    else:
                        extracted_name = f"Passenger {last_name}"
                elif not first_name_val or first_name_val.lower() == "nan":
                    parts = extracted_name.split()
                    if len(parts) > 1:
                        first_name_val = parts[0]
                    else:
                        first_name_val = extracted_name
                
                flight_val = str(df.at[index, "Flight Details"]).strip() if "Flight Details" in df.columns else ""
                if flight_val.lower() == "nan":
                    flight_val = ""

                passenger_details = {
                    "passenger_name": extracted_name,
                    "first_name": first_name_val,
                    "last_name": last_name,
                    "flight_number": flight_val or "Etihad Flight"
                }

                if mode == "claim_only":
                    # Run claim miles workflow
                    claim_success = saudia.claim_miles(passenger_details)
                    if claim_success:
                        df.at[index, "Status"] = "Miles Claimed"
                        df.at[index, "Remarks"] = "Claim submitted successfully"
                        success_count += 1
                    else:
                        df.at[index, "Status"] = "Claim Failed"
                        df.at[index, "Remarks"] = "Failed to autofill/submit claim"
                        failed_count += 1
                    
                    processed_count += 1
                    write_status("Running", processed_count, total_count, success_count, failed_count, skipped_count, pnr, last_name, status_file_path=status_file_path, username=username)
                    save_excel_data(df, output_target_path)
                    if username:
                        try:
                            update_passenger_record(username, pnr, last_name, {
                                "Status": df.at[index, "Status"],
                                "Remarks": df.at[index, "Remarks"]
                            })
                        except Exception as e:
                            logger.warning(f"Error updating SQLite: {e}")
                    continue

                # Step C: Initialize Email Client with user's configured automation credentials or fallback
                user_creds = credentials or {}
                auto_email = (user_creds.get("email") if user_creds else None) or None
                auto_pwd = (user_creds.get("app_password") if user_creds else None) or None
                temp_mail = TempMailClient(
                    email_address=auto_email,
                    app_password=auto_pwd
                )

                # Prioritize client-provided email from Excel sheet
                email_candidates = ["Gmail/Email ID", "Email", "email", "Gmail", "gmail"]
                provided_email = ""
                for ec in email_candidates:
                    if ec in df.columns:
                        v = str(df.at[index, ec]).strip()
                        if v and v.lower() != "nan" and "@" in v:
                            provided_email = v
                            break
                if provided_email:
                    unique_email = provided_email
                    logger.info(f"Using client-provided email from Excel for passenger {last_name}: {unique_email}")
                else:
                    unique_email = temp_mail.generate_email(passenger_pnr=pnr, passenger_lastname=last_name)
                    logger.info(f"Generated passenger-identifiable email alias: {unique_email}")
                
                # Generate required random details for Saudia signup
                from src.utils import generate_random_passport, generate_random_dob, generate_random_mobile, generate_gmail_alias
                passenger_details["passport"] = generate_random_passport()
                passenger_details["dob"] = generate_random_dob()
                passenger_details["mobile"] = generate_random_mobile()
                
                # Step F: Wait for e-ticket / confirmation email to temp mailbox (skip if details already extracted or in signup_only mode)
                if mode != "signup_only" and (not passenger_details.get("passenger_name") or passenger_details.get("passenger_name") == f"Passenger {last_name}"):
                    logger.info("Waiting for Etihad e-ticket/confirmation mail to extract further details...")
                    mail_received = temp_mail.wait_for_email_matching(keyword="Etihad", timeout_seconds=20, stop_check=user_stop_check)
                    if mail_received:
                        logger.info("Etihad e-ticket received via temp email.")
                else:
                    logger.info("Skipping Etihad email wait to run Saudia enrollment at maximum speed.")

                # Step G: Register on Saudia with user credentials
                password = credentials.get("saudia_password", "TempPass123!") if credentials else "TempPass123!"
                passenger_ts = time.time() - 30
                
                signup_status = saudia.create_membership(passenger_details, unique_email, password)
                if is_stopped(username):
                    logger.info("🛑 Stop requested after Saudia registration. Halting.")
                    break
                
                if signup_status == "SUCCESS_PENDING_ACTIVATION":
                    df.at[index, "Gmail/Email ID"] = unique_email
                    df.at[index, "Password"] = password
                    df.at[index, "Status"] = "Pending Activation"
                    if username:
                        try:
                            update_passenger_record(username, pnr, last_name, {
                                "Gmail/Email ID": unique_email,
                                "Password": password,
                                "Status": "Pending Activation"
                            })
                        except Exception:
                            pass

                    # Step H: Locate the Alfursan activation email
                    logger.info(f"Saudia signup submitted. Waiting for activation email for {unique_email}...")
                    activation_email = temp_mail.wait_for_email_matching(
                        keyword="Activation", 
                        timeout_seconds=90, 
                        stop_check=user_stop_check,
                        target_email=unique_email,
                        min_timestamp=passenger_ts
                    )
                    
                    if activation_email:
                        # Find activation link
                        import re
                        email_body = activation_email.get("textBody", "") + activation_email.get("htmlBody", "")
                        urls = re.findall(r'https?://[^\s<>"]+|www\.[^\s<>"]+', email_body)
                        # Filter out images and static assets
                        clean_urls = [u for u in urls if not any(ext in u.lower() for ext in ['.png', '.jpg', '.jpeg', '.gif', '.css', '.ashx'])]
                        activation_url = next((u for u in clean_urls if "activate" in u.lower() or "verification" in u.lower() or "verify" in u.lower()), None)
                        if not activation_url:
                            activation_url = next((u for u in clean_urls if "saudia" in u.lower()), None)
                        
                        if activation_url:
                            logger.info(f"Activation URL found: {activation_url}. Clicking activation link...")
                            activation_page = saudia.context.new_page()
                            try:
                                activation_page.goto(activation_url, wait_until="networkidle", timeout=30000)
                                activation_page.wait_for_timeout(2000)
                                os.makedirs("scratch", exist_ok=True)
                                activation_page.screenshot(path="scratch/saudia_account_activated.png")
                                logger.info(f"Activation link loaded successfully (URL: {activation_page.url}).")
                            except Exception as act_err:
                                logger.warning(f"Error loading activation link: {act_err}")
                            finally:
                                try:
                                    activation_page.close()
                                except Exception:
                                    pass
                            
                            # Step I: Check email for welcome/confirmation message with membership number
                            logger.info(f"Checking for Saudia welcome email for {unique_email}...")
                            welcome_email = temp_mail.wait_for_email_matching(
                                keyword="Welcome", 
                                timeout_seconds=20, 
                                stop_check=user_stop_check,
                                target_email=unique_email,
                                min_timestamp=passenger_ts
                            )
                            
                            if welcome_email:
                                raw_html = welcome_email.get("htmlBody", "")
                                text_body = welcome_email.get("textBody", "")
                                clean_text = text_body
                                if raw_html:
                                    try:
                                        from bs4 import BeautifulSoup
                                        soup = BeautifulSoup(raw_html, "html.parser")
                                        for s in soup(["style", "script"]):
                                            s.decompose()
                                        clean_text += " " + soup.get_text(separator=" ")
                                    except Exception:
                                        clean_html = re.sub(r'<style.*?</style>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
                                        clean_html = re.sub(r'<script.*?</script>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)
                                        clean_text += " " + re.sub(r'<[^>]+>', ' ', clean_html)
                                
                                # First prioritize Saudia 100-prefix format (e.g. 100xxxxxxx)
                                member_match = re.search(r'\b(100\d{5,9})\b', clean_text)
                                if not member_match:
                                    # Next look for explicit labels (Membership No / Alfursan ID: xxxxxxxx)
                                    member_match = re.search(r'(?:Membership|AlFursan|Member)\s*(?:ID|No|Number|#)?\s*[:#-]?\s*(\d{8,12})', clean_text, re.IGNORECASE)
                                if not member_match:
                                    member_match = re.search(r'\b\d{8,12}\b', clean_text)
                                
                                if member_match:
                                    membership_id = member_match.group(1) if member_match.lastindex else member_match.group(0)
                                    logger.info(f"Membership successfully created! ID: {membership_id}")
                                    df.at[index, "Saudia Membership ID"] = membership_id
                                    df.at[index, "Status"] = "Activated"
                                    df.at[index, "Remarks"] = "Completed"
                                else:
                                    logger.warning("Could not extract Membership ID from welcome email.")
                                    df.at[index, "Saudia Membership ID"] = ""
                                    df.at[index, "Status"] = "Pending ID"
                                    df.at[index, "Remarks"] = "Welcome email received, ID parsing pending"
                            else:
                                logger.warning(f"Welcome email not received in time for {unique_email}.")
                                df.at[index, "Saudia Membership ID"] = ""
                                df.at[index, "Status"] = "Activated"
                                df.at[index, "Remarks"] = "Activation link clicked, welcome mail pending"
                        else:
                            logger.error("Could not locate activation URL inside the email.")
                            df.at[index, "Saudia Membership ID"] = ""
                            df.at[index, "Status"] = "Pending Activation"
                            df.at[index, "Remarks"] = "Activation email received, URL not found"
                    else:
                        logger.info(f"Activation email not received within timeout for {unique_email}.")
                        df.at[index, "Saudia Membership ID"] = ""
                        df.at[index, "Status"] = "Pending Activation"
                        df.at[index, "Remarks"] = "Saudia registration submitted, activation email pending"
                else:
                    df.at[index, "Status"] = "Saudia Signup Failed"
                    df.at[index, "Remarks"] = "Form submission failed"

                # Determine success/failure and increment counters
                if "Activated" in str(df.at[index, "Status"]) or "Created" in str(df.at[index, "Status"]):
                    success_count += 1
                else:
                    failed_count += 1
                processed_count += 1
                write_status("Running", processed_count, total_count, success_count, failed_count, skipped_count, pnr, last_name, status_file_path=status_file_path, username=username)
                save_excel_data(df, output_target_path)
                if username:
                    try:
                        update_passenger_record(username, pnr, last_name, {
                            "Saudia Membership ID": df.at[index, "Saudia Membership ID"],
                            "Status": df.at[index, "Status"],
                            "Extracted Passenger Name": df.at[index, "Extracted Passenger Name"],
                            "Flight Details": df.at[index, "Flight Details"],
                            "Gmail/Email ID": df.at[index, "Gmail/Email ID"] if "Gmail/Email ID" in df.columns else "",
                            "Password": df.at[index, "Password"] if "Password" in df.columns else "",
                            "Remarks": df.at[index, "Remarks"] if "Remarks" in df.columns else ""
                        })
                    except Exception as e:
                        logger.warning(f"Error updating SQLite: {e}")
                
            logger.info("Stopping Saudia Signup browser session...")
            saudia.stop()

        if username:
            try:
                from src.temp_mail import sync_welcome_membership_ids
                sync_welcome_membership_ids(username)
            except Exception:
                pass

        if is_stopped(username):
            write_status("Stopped", processed_count, total_count, success_count, failed_count, skipped_count, status_file_path=status_file_path, username=username)
            logger.info("🛑 Automation workflow stopped by user.")
        else:
            write_status("Completed", processed_count, total_count, success_count, failed_count, skipped_count, status_file_path=status_file_path, username=username)
            logger.info("Automation workflow completed.")

    except Exception as e:
        logger.error(f"Critical error during run: {e}", exc_info=True)
        write_status("Failed", processed_count, total_count, success_count, failed_count, skipped_count, status_file_path=status_file_path, username=username)
    finally:
        etihad.stop()
        saudia.stop()
        try:
            p.stop()
        except Exception:
            pass
        # Remove user log handler
        if user_log_handler:
            try:
                logging.getLogger().removeHandler(user_log_handler)
                user_log_handler.close()
            except Exception:
                pass
        # Save final state
        save_excel_data(df, output_target_path)
        final_state = "Stopped" if is_stopped(username) else "Completed"
        write_status(final_state, processed_count, total_count, success_count, failed_count, skipped_count, status_file_path=status_file_path, username=username)
        logger.info(f"Automation workflow finalized ({final_state}).")

if __name__ == "__main__":
    run_automation()
