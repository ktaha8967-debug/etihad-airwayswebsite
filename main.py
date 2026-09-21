import os
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
import datetime

def write_status(status_str, processed, total, success, failed, skipped, current_pnr="", current_name=""):
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
        with open(STATUS_FILE_PATH, "w") as f:
            json.dump(status_data, f, indent=4)
    except Exception as e:
        logger.warning(f"Failed to write status: {e}")


def run_automation(mode="all"):
    logger.info(f"Starting Airline Passenger Automation process in mode: {mode}...")
    
    # 1. Load Excel Data
    target_path = OUTPUT_EXCEL_PATH if (mode == "claim_only" and os.path.exists(OUTPUT_EXCEL_PATH)) else INPUT_EXCEL_PATH
    logger.info(f"Loading input file: {target_path}")
    try:
        df = read_excel_data(target_path)
    except Exception as e:
        logger.error(f"Failed to read excel file {target_path}: {e}")
        sys.exit(1)

    # Print columns to aid debugging
    logger.info(f"Loaded columns: {list(df.columns)}")

    # Standardize column mappings (adjust as necessary for the actual sheet)
    pnr_col = next((c for c in df.columns if any(k in c.lower() for k in ["pnr", "booking", "reference", "ref"])), None)
    lastname_col = next((c for c in df.columns if any(k in c.lower() for k in ["last name", "lastname", "surname"])), None)
    
    if not pnr_col or not lastname_col:
        logger.error(f"Could not map PNR (found: {pnr_col}) or Last Name (found: {lastname_col}) columns correctly.")
        logger.info("Please verify the headers of the input file.")
        sys.exit(1)

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
    
    write_status("Running", 0, total_count, 0, 0, 0)
    
    try:
        # Start browsers once before entering the loop to ensure high-performance reuse
        if mode != "signup_only" and mode != "claim_only":
            logger.info("Initializing Etihad Scraper browser session...")
            etihad.start()
        
        for index, row in df.iterrows():
            if mode == "claim_only" or mode == "signup_only":
                break
            pnr = str(row[pnr_col]).strip()
            last_name = str(row[lastname_col]).strip()
            
            # Skip empty entries
            if not pnr or pnr == "nan" or not last_name or last_name == "nan":
                continue
                
            # If already processed, skip
            if str(row.get("Saudia Membership ID", "")).strip() not in ["", "nan"]:
                logger.info(f"Skipping PNR {pnr}: Saudia ID already registered.")
                skipped_count += 1
                processed_count += 1
                write_status("Running", processed_count, total_count, success_count, failed_count, skipped_count, pnr, last_name)
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
                    df.at[index, "Status"] = "Etihad Login Failed"
                    failed_count += 1
                    processed_count += 1
                    write_status("Running", processed_count, total_count, success_count, failed_count, skipped_count, pnr, last_name)
                    continue
                    
                # Step B: Check frequent flyer status
                is_ff = etihad.check_frequent_flyer_status(last_name)
                if is_ff:
                    logger.info(f"Frequent Flyer already present on Etihad for PNR {pnr}. Skipping.")
                    df.at[index, "Status"] = "Skipped (FF Present)"
                    skipped_count += 1
                    processed_count += 1
                    write_status("Running", processed_count, total_count, success_count, failed_count, skipped_count, pnr, last_name)
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
                    write_status("Running", processed_count, total_count, success_count, failed_count, skipped_count, pnr, last_name)
                    
                # Auto-save changes
                save_excel_data(df, OUTPUT_EXCEL_PATH)
                
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
                items_to_process.append((index, row, pnr, last_name))
                
            logger.info(f"Loaded records count for processing: {len(items_to_process)}")
            if mode == "claim_only":
                # User requested: process only a single passenger for claim test
                active_claims = []
                for index, row, pnr, last_name in items_to_process:
                    saudia_id = str(row.get("Saudia Membership ID", "")).strip()
                    status = str(row.get("Status", "")).strip()
                    logger.info(f"Checking claim candidate: PNR {pnr} | Saudia ID: {saudia_id} | Status: {status}")
                    if saudia_id and saudia_id != "nan" and "Claimed" not in status:
                        active_claims.append((index, row, pnr, last_name))
                        break
                items_to_process = active_claims
                
            for index, row, pnr, last_name in items_to_process:
                # If doing normal signup and already processed or failed at login, skip
                if mode != "claim_only":
                    if str(row.get("Saudia Membership ID", "")).strip() not in ["", "nan"]:
                        continue
                    if df.at[index, "Status"] in ["Etihad Login Failed", "Skipped (FF Present)"]:
                        continue

                logger.info(f"--- Saudia Stage: Processing Passenger {index + 1}/{len(df)}: PNR {pnr}, Last Name: {last_name} ---")
                
                # Retrieve passenger details
                extracted_name = str(row.get("Extracted Passenger Name", "")).strip()
                if not extracted_name or extracted_name == "nan":
                    extracted_name = f"Passenger {last_name}"
                
                passenger_details = {
                    "passenger_name": extracted_name,
                    "flight_number": str(row.get("Flight Details", "Unknown"))
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
                    write_status("Running", processed_count, total_count, success_count, failed_count, skipped_count, pnr, last_name)
                    save_excel_data(df, OUTPUT_EXCEL_PATH)
                    continue

                # Step C: Initialize Temp Mail
                temp_mail = TempMailClient()
                temp_email = temp_mail.generate_email()
                
                # Generate required random details for Saudia signup
                from src.utils import generate_random_passport, generate_random_dob, generate_random_mobile, generate_gmail_alias
                passenger_details["passport"] = generate_random_passport()
                passenger_details["dob"] = generate_random_dob()
                passenger_details["mobile"] = generate_random_mobile()
                
                # Step F: Wait for e-ticket / confirmation email to temp mailbox (skip if details already extracted)
                if not passenger_details.get("passenger_name") or passenger_details.get("passenger_name") == f"Passenger {last_name}":
                    logger.info("Waiting for Etihad e-ticket/confirmation mail to extract further details...")
                    mail_received = temp_mail.wait_for_email_matching(keyword="Etihad", timeout_seconds=20)
                    if mail_received:
                        logger.info("Etihad e-ticket received via temp email.")
                else:
                    logger.info("Passenger details already extracted from Etihad UI. Skipping email wait to run at maximum speed.")

                # Step G: Register on Saudia with standard temp email
                unique_email = temp_email
                password = "TempPass123!"
                
                signup_status = saudia.create_membership(passenger_details, unique_email, password)
                
                if signup_status == "SUCCESS_PENDING_ACTIVATION":
                    # Step H: Locate the Alfursan activation email
                    logger.info(f"Saudia signup submitted. Waiting for activation email for {unique_email}...")
                    activation_email = temp_mail.wait_for_email_matching(keyword="Activation", timeout_seconds=120)
                    
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
                            activation_page.goto(activation_url)
                            activation_page.wait_for_load_state("networkidle")
                            logger.info("Activation link loaded. Closing activation page...")
                            activation_page.close()
                            
                            # Step I: Check email for welcome/confirmation message with membership number
                            logger.info("Waiting for Saudia welcome email containing Alfursan Membership Number...")
                            welcome_email = temp_mail.wait_for_email_matching(keyword="Welcome", timeout_seconds=120)
                            
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
                                    df.at[index, "Gmail/Email ID"] = unique_email
                                    df.at[index, "Password"] = password
                                    df.at[index, "Remarks"] = "Completed"
                                else:
                                    logger.warning("Could not extract Membership ID from welcome email.")
                                    df.at[index, "Status"] = "Created / Pending ID"
                                    df.at[index, "Remarks"] = "Welcome email received, ID parsing failed"
                            else:
                                logger.warning("Welcome email not received in time.")
                                df.at[index, "Status"] = "Activated"
                                df.at[index, "Remarks"] = "Activation link clicked, welcome mail pending"
                        else:
                            logger.error("Could not locate activation URL inside the email.")
                            df.at[index, "Status"] = "Pending Activation"
                            df.at[index, "Remarks"] = "Activation email received, URL not found"
                    else:
                        logger.error("Activation email not received.")
                        df.at[index, "Status"] = "Pending Activation"
                        df.at[index, "Remarks"] = "Signup completed, activation email missing"
                else:
                    df.at[index, "Status"] = "Saudia Signup Failed"
                    df.at[index, "Remarks"] = "Form submission failed"

                # Determine success/failure and increment counters
                if "Activated" in str(df.at[index, "Status"]) or "Created" in str(df.at[index, "Status"]):
                    success_count += 1
                else:
                    failed_count += 1
                processed_count += 1
                write_status("Running", processed_count, total_count, success_count, failed_count, skipped_count, pnr, last_name)
                save_excel_data(df, OUTPUT_EXCEL_PATH)
                
            logger.info("Stopping Saudia Signup browser session...")
            saudia.stop()

        write_status("Completed", processed_count, total_count, success_count, failed_count, skipped_count)
        logger.info("Automation workflow completed.")

    except Exception as e:
        logger.error(f"Critical error during run: {e}", exc_info=True)
        write_status("Failed", processed_count, total_count, success_count, failed_count, skipped_count)
    finally:
        etihad.stop()
        saudia.stop()
        try:
            p.stop()
        except Exception:
            pass
        # Save final state
        save_excel_data(df, OUTPUT_EXCEL_PATH)
        write_status("Completed", processed_count, total_count, success_count, failed_count, skipped_count)
        logger.info("Automation workflow completed.")

if __name__ == "__main__":
    run_automation()
