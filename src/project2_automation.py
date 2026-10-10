import os
if "DISPLAY" not in os.environ:
    os.environ["DISPLAY"] = ":99"

import sys
import logging
import time
import re
import random
from datetime import datetime
from playwright.sync_api import sync_playwright

from src.project2_manager import (
    get_project2_passengers_list,
    update_project2_passenger_record,
    update_project2_status,
    get_project2_config,
    sync_project2_welcome_ids
)
from src.saudia_signup import SaudiaSignupManager
from src.temp_mail import TempMailClient
from src.utils import generate_random_passport, generate_random_dob, generate_random_mobile

logger = logging.getLogger("Project2Automation")

# User stop flags
p2_stop_events = {}

def request_project2_stop(username: str):
    p2_stop_events[username] = True

def clear_project2_stop(username: str):
    p2_stop_events[username] = False

def is_project2_stopped(username: str) -> bool:
    return p2_stop_events.get(username, False)


def run_project2_automation(username: str, sample_limit: int = None):
    """
    Executes dedicated Project 2 Saudia direct enrollment pipeline.
    If sample_limit is provided (e.g. 2 or 3), executes only on sample passengers.
    """
    clear_project2_stop(username)
    log_dir = "data/logs"
    os.makedirs(log_dir, exist_ok=True)
    p2_log_path = os.path.join(log_dir, f"project2_{username}.log")

    # Setup file handler
    file_handler = logging.FileHandler(p2_log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(file_handler)
    logger.setLevel(logging.INFO)
    logging.getLogger("src.saudia_signup").addHandler(file_handler)
    logging.getLogger("src.saudia_signup").setLevel(logging.INFO)
    logging.getLogger("src.temp_mail").addHandler(file_handler)
    logging.getLogger("src.temp_mail").setLevel(logging.INFO)

    mode_label = f"Sample Run ({sample_limit} passengers)" if sample_limit else "Full Enrollment"
    logger.info(f"=== Starting Project 2 Saudia Direct Enrollment: {mode_label} for user '{username}' ===")

    config = get_project2_config(username)
    passengers = get_project2_passengers_list(username)

    if not passengers:
        logger.warning("No passengers found in Project 2 queue. Please upload an Excel sheet first.")
        update_project2_status(username, "Failed", 0, 0, 0, 0, 0, "No passengers loaded")
        logger.removeHandler(file_handler)
        file_handler.close()
        return

    # Filter out already activated passengers or passengers who already have an AlFursan ID
    def is_pax_pending(p):
        sid = str(p.get("saudia_id") or "").strip()
        if sid and sid.lower() not in ["", "none", "nan"]:
            return False
        return p.get("status") != "Activated"

    pending_pax = [p for p in passengers if is_pax_pending(p)]
    if sample_limit and sample_limit > 0:
        pending_pax = pending_pax[:sample_limit]

    total_count = len(pending_pax)
    processed_count = 0
    success_count = 0
    failed_count = 0
    skipped_count = 0

    update_project2_status(username, "Running", 0, total_count, 0, 0, 0, "Initializing")

    # Start email client
    master_email = (config.get("master_email") or "").strip()
    master_pwd = (config.get("master_password") or "").strip()
    if not master_email:
        try:
            from src.user_manager import load_users
            users = load_users()
            u_info = users.get(username, {})
            master_email = u_info.get("email", "").strip()
            if not master_pwd:
                master_pwd = u_info.get("app_password", "").strip()
        except Exception:
            pass

    imap_server = config.get("imap_server") or "imap.gmail.com"
    imap_port = int(config.get("imap_port") or 993)
    domain_name = (config.get("domain_name") or "").strip().lower().replace("@", "")

    temp_mail = TempMailClient(
        email_address=master_email,
        app_password=master_pwd,
        imap_server=imap_server,
        imap_port=imap_port
    )

    p = sync_playwright().start()
    saudia = SaudiaSignupManager(p)

    try:
        logger.info("Launching Saudia Playwright browser session on display :99...")
        saudia.start()

        for idx, pax in enumerate(pending_pax):
            if is_project2_stopped(username):
                logger.info("🛑 Project 2 stop requested. Halting enrollment loop.")
                break

            if idx > 0:
                logger.info("Creating fresh browser context for next passenger to avoid state pollution...")
                saudia.reset_session()

            pax_id = pax["id"]
            fname = pax.get("first_name", "").strip() or "Passenger"
            lname = pax.get("last_name", "").strip() or "Guest"
            full_name = f"{fname} {lname}".strip()

            pax_desc = f"{full_name} (ID: {pax_id})"
            logger.info(f"--- [Project 2] Processing Passenger {idx + 1}/{total_count}: {pax_desc} ---")
            update_project2_status(username, "Running", processed_count, total_count, success_count, failed_count, skipped_count, pax_desc)

            # Determine email to use
            existing_email = (pax.get("email") or "").strip()
            is_old_ktaha_alias = "ktaha8967" in existing_email.lower()
            is_auto_alias = "+" in existing_email or is_old_ktaha_alias
            if existing_email and "@" in existing_email and existing_email.lower() != "nan" and not is_auto_alias and not is_old_ktaha_alias:
                target_email = existing_email
                logger.info(f"Using client-provided email from sheet: {target_email}")
            elif domain_name:
                # Custom domain catch-all routing (e.g. ali.khan_f3a1@agency.com)
                clean_fn = re.sub(r'[^a-zA-Z0-9]', '', fname.lower()) or "pax"
                clean_ln = re.sub(r'[^a-zA-Z0-9]', '', lname.lower()) or "guest"
                rand_sfx = ''.join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=5))
                target_email = f"{clean_fn}.{clean_ln}_{rand_sfx}@{domain_name}"
                logger.info(f"Generated custom domain email for catch-all routing: {target_email}")
            else:
                # Master email plus alias with identifiable suffix
                target_email = temp_mail.generate_email(passenger_lastname=lname)
                logger.info(f"Generated master email identifiable alias: {target_email}")

            # Fill missing required demographics
            dob = pax.get("dob") or generate_random_dob()
            passport = pax.get("passport") or generate_random_passport()
            mobile = pax.get("mobile") or generate_random_mobile()
            title = pax.get("title") or "Mr"
            if title.lower() in ["mrs", "miss", "female", "f"]:
                title = "Mrs"
            elif title.lower() in ["ms"]:
                title = "Ms"
            else:
                title = "Mr"

            passenger_data = {
                "passenger_name": full_name,
                "first_name": fname,
                "last_name": lname,
                "title": title,
                "dob": dob,
                "passport": passport,
                "mobile": mobile,
                "flight_number": "Etihad Flight"
            }

            signup_password = pax.get("password") or "TempPass123!"

            # Step 1: Submit Saudia Registration
            t0 = time.time()
            submission_result = saudia.create_membership(passenger_data, target_email, signup_password)

            if submission_result == "SUCCESS_PENDING_ACTIVATION":
                target_email = getattr(saudia, "last_registered_email", target_email)
                logger.info(f"Saudia registration submitted for {target_email}. Awaiting activation link...")
                update_project2_passenger_record(username, pax_id, {
                    "email": target_email,
                    "status": "Pending Activation",
                    "remarks": "Form submitted. Waiting for activation email"
                })

                # Step 2: Fetch activation email
                activation_email = temp_mail.wait_for_email_matching(
                    keyword="Activation",
                    timeout_seconds=120,
                    stop_check=lambda: is_project2_stopped(username),
                    target_email=target_email,
                    min_timestamp=t0 - 120
                )

                if activation_email:
                    html_content = activation_email.get("htmlBody") or ""
                    text_content = activation_email.get("textBody") or ""
                    raw_url = None
                    try:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(html_content, "html.parser")
                        for a in soup.find_all("a", href=True):
                            href = a.get("href", "").replace("&amp;", "&").strip()
                            text = a.get_text().strip().lower()
                            if "activate" in text or "verify" in text or "verify_redirect" in href or "verification_code" in href or "passwordless" in href:
                                raw_url = href
                                break
                    except Exception:
                        pass

                    if not raw_url:
                        body = html_content + " " + text_content
                        urls = re.findall(r'https?://[^\s<>"]+|www\.[^\s<>"]+', body)
                        clean_urls = [u.replace("&amp;", "&") for u in urls if not any(ext in u.lower() for ext in ['.png', '.jpg', '.jpeg', '.gif', '.css', '.ashx', 'media'])]
                        raw_url = next((u for u in clean_urls if "verify_redirect" in u.lower() or "activate" in u.lower() or "verify" in u.lower()), None)

                    if raw_url:
                        logger.info(f"Activation link discovered: {raw_url[:65]}... Triggering activation.")
                        
                        # 1. Direct HTTP activation call to verify token with Auth0 backend
                        try:
                            import requests
                            s_act = requests.Session()
                            s_act.get(raw_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}, allow_redirects=True, timeout=15)
                            logger.info(f"Dispatched direct HTTP activation request for {target_email}.")
                        except Exception as e_req:
                            logger.debug(f"Direct HTTP activation attempt note: {e_req}")

                        # 2. Browser navigation to ensure full redirect completion
                        act_page = saudia.context.new_page() if saudia.context else saudia.browser.new_page()
                        try:
                            act_page.goto(raw_url, wait_until="domcontentloaded", timeout=20000)
                            act_page.wait_for_timeout(2500)
                            os.makedirs("scratch", exist_ok=True)
                            act_page.screenshot(path="scratch/project2_saudia_activated.png")
                            logger.info(f"Saudia account activation visited! (Page URL: {act_page.url})")
                        except Exception as e:
                            logger.warning(f"Activation navigation note: {e}")
                        finally:
                            try:
                                act_page.close()
                            except Exception:
                                pass

                        # Step 3: Fetch Welcome email for Membership ID
                        logger.info(f"Listening for Saudia Welcome email for {target_email}...")
                        welcome_email = temp_mail.wait_for_email_matching(
                            keyword="Welcome",
                            timeout_seconds=65,
                            stop_check=lambda: is_project2_stopped(username),
                            target_email=target_email,
                            min_timestamp=t0 - 120
                        )

                        membership_id = ""
                        if welcome_email:
                            clean_text = (welcome_email.get("textBody") or "") + " " + (welcome_email.get("htmlBody") or "")
                            member_match = re.search(r'\b(100\d{5,9})\b', clean_text) or re.search(r'(?:Membership|AlFursan|Member)\s*(?:ID|No|Number|#)?\s*[:#-]?\s*(\d{8,12})', clean_text, re.IGNORECASE)
                            if member_match:
                                membership_id = member_match.group(1) if member_match.lastindex else member_match.group(0)

                        if not membership_id:
                            # Direct inbox scan fallback
                            found_id = temp_mail.find_membership_id_for_email(target_email)
                            if found_id:
                                membership_id = found_id

                        if membership_id:
                            logger.info(f"🎉 AlFursan Membership ID confirmed: {membership_id} for {full_name}")
                            update_project2_passenger_record(username, pax_id, {
                                "email": target_email,
                                "saudia_id": membership_id,
                                "status": "Activated",
                                "remarks": "Completed successfully"
                            })
                            success_count += 1
                        else:
                            logger.warning(f"Account activated. Membership ID sync scheduled for {target_email}.")
                            update_project2_passenger_record(username, pax_id, {
                                "email": target_email,
                                "status": "Activated",
                                "remarks": "Activated (ID sync in progress)"
                            })
                            success_count += 1
                    else:
                        logger.warning(f"Activation link not parsed from activation email for {target_email}.")
                        update_project2_passenger_record(username, pax_id, {
                            "email": target_email,
                            "status": "Failed Activation",
                            "remarks": "Activation email received, link missing"
                        })
                        failed_count += 1
                else:
                    logger.warning(f"Timeout waiting for activation email for {target_email}.")
                    update_project2_passenger_record(username, pax_id, {
                        "email": target_email,
                        "status": "Pending Activation",
                        "remarks": "Registration submitted, activation email pending"
                    })
                    failed_count += 1
            else:
                logger.error(f"Saudia signup form submission failed for {pax_desc}.")
                update_project2_passenger_record(username, pax_id, {
                    "email": target_email,
                    "status": "Signup Failed",
                    "remarks": "Form submission failed on Saudia portal"
                })
                failed_count += 1

            processed_count += 1
            update_project2_status(username, "Running", processed_count, total_count, success_count, failed_count, skipped_count, pax_desc)
            time.sleep(3)

        # Post-loop sync pass: check if any welcome emails arrived
        try:
            synced_res = sync_project2_welcome_ids(username)
            synced = synced_res[0] if isinstance(synced_res, tuple) else synced_res
            if synced > 0:
                logger.info(f"Post-enrollment IMAP scan synced {synced} AlFursan Membership IDs!")
                success_count += synced
                failed_count = max(0, failed_count - synced)
        except Exception as e:
            logger.warning(f"Post-loop sync note: {e}")

        final_status = "Stopped" if is_project2_stopped(username) else "Completed"
        update_project2_status(username, final_status, processed_count, total_count, success_count, failed_count, skipped_count, "")
        logger.info(f"=== Project 2 Enrollment Finished ({final_status}) -> Processed: {processed_count}, Success: {success_count}, Failed: {failed_count} ===")

    except Exception as e:
        logger.error(f"Critical error during Project 2 execution: {e}", exc_info=True)
        update_project2_status(username, "Failed", processed_count, total_count, success_count, failed_count, skipped_count, str(e))
    finally:
        try:
            saudia.stop()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass
        logger.removeHandler(file_handler)
        logging.getLogger("src.saudia_signup").removeHandler(file_handler)
        logging.getLogger("src.temp_mail").removeHandler(file_handler)
        file_handler.close()


def run_project2_pnr_update(username: str):
    """
    Executes dedicated Saudia Manage Booking automation to attach contact emails to all PNRs in queue.
    """
    clear_project2_stop(username)
    log_dir = "data/logs"
    os.makedirs(log_dir, exist_ok=True)
    p2_log_path = os.path.join(log_dir, f"project2_{username}.log")

    file_handler = logging.FileHandler(p2_log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(file_handler)
    logger.setLevel(logging.INFO)
    logging.getLogger("SaudiaManageBooking").addHandler(file_handler)
    logging.getLogger("SaudiaManageBooking").setLevel(logging.INFO)

    logger.info(f"=== Starting Saudia PNR Contact Automation for user '{username}' ===")

    config = get_project2_config(username)
    passengers = get_project2_passengers_list(username)

    # Filter passengers who have a PNR
    pnr_passengers = [p for p in passengers if p.get("pnr") and str(p.get("pnr")).strip()]
    if not pnr_passengers:
        logger.warning("No passengers with a PNR found in Project 2 queue.")
        update_project2_status(username, "Failed", 0, 0, 0, 0, 0, "No PNRs found in queue")
        logger.removeHandler(file_handler)
        logging.getLogger("SaudiaManageBooking").removeHandler(file_handler)
        file_handler.close()
        return

    total_count = len(pnr_passengers)
    processed_count = 0
    success_count = 0
    failed_count = 0

    update_project2_status(username, "Running", 0, total_count, 0, 0, 0, "Initializing Saudia Manage Booking")

    from src.saudia_manage_booking import SaudiaManageBooking
    manage = SaudiaManageBooking()

    try:
        manage.start()

        for idx, pax in enumerate(pnr_passengers):
            if is_project2_stopped(username):
                logger.info("🛑 PNR update stop requested. Halting loop.")
                break

            if idx > 0:
                manage.reset_page()

            pax_id = pax["id"]
            pnr = str(pax.get("pnr", "")).strip().upper()
            lname = str(pax.get("last_name", "")).strip() or "Passenger"
            fname = str(pax.get("first_name", "")).strip()
            target_email = str(pax.get("email", "")).strip() or (config.get("master_email") or "").strip()

            pax_desc = f"{fname} {lname} [PNR: {pnr}]"
            logger.info(f"--- [PNR Contact Update {idx + 1}/{total_count}] Processing: {pax_desc} ---")
            update_project2_status(username, "Running", processed_count, total_count, success_count, failed_count, 0, pax_desc)

            res = manage.update_booking_contact(pnr=pnr, last_name=lname, email=target_email)

            if res.get("success"):
                logger.info(f"✅ PNR {pnr} updated successfully for {lname}!")
                update_project2_passenger_record(username, pax_id, {
                    "remarks": res.get("remarks", "PNR Contact Updated")
                })
                success_count += 1
            else:
                remarks_msg = res.get("remarks", "PNR update failed")
                logger.warning(f"⚠️ PNR {pnr} note: {remarks_msg}")
                update_project2_passenger_record(username, pax_id, {
                    "remarks": remarks_msg
                })
                failed_count += 1

            processed_count += 1
            update_project2_status(username, "Running", processed_count, total_count, success_count, failed_count, 0, pax_desc)
            time.sleep(2)

        final_status = "Stopped" if is_project2_stopped(username) else "Completed"
        update_project2_status(username, final_status, processed_count, total_count, success_count, failed_count, 0, "")
        logger.info(f"=== Saudia PNR Contact Automation Finished ({final_status}) -> Processed: {processed_count}, Success: {success_count}, Failed: {failed_count} ===")

    except Exception as e:
        logger.error(f"Critical error during Saudia PNR update: {e}", exc_info=True)
        update_project2_status(username, "Failed", processed_count, total_count, success_count, failed_count, 0, str(e))
    finally:
        try:
            manage.stop()
        except Exception:
            pass
        logger.removeHandler(file_handler)
        logging.getLogger("SaudiaManageBooking").removeHandler(file_handler)
        file_handler.close()

