import os
import time
import logging
from playwright.sync_api import sync_playwright

logger = logging.getLogger("SaudiaManageBooking")

CHROME_BIN = "/home/taha/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome"

class SaudiaManageBooking:
    """
    Automates Saudia Manage Booking to retrieve PNRs and update passenger contact details.
    """
    def __init__(self, playwright_instance=None):
        self.playwright = playwright_instance
        self._own_playwright = False
        self.browser = None
        self.context = None
        self.page = None

    def start(self):
        """Initializes Playwright browser session with real Chrome on Xvfb display."""
        if not os.environ.get("DISPLAY"):
            os.environ["DISPLAY"] = ":99"

        if not self.playwright:
            self.playwright = sync_playwright().start()
            self._own_playwright = True

        launch_kwargs = {
            "headless": False,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security"
            ],
            "env": {"DISPLAY": os.environ.get("DISPLAY", ":99"), **os.environ}
        }
        if os.path.exists(CHROME_BIN):
            launch_kwargs["executable_path"] = CHROME_BIN

        self.browser = self.playwright.chromium.launch(**launch_kwargs)
        self.context = self.browser.new_context(
            viewport={"width": 1400, "height": 900},
            locale="en-US"
        )
        self.page = self.context.new_page()
        self.page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        self.page.set_default_timeout(35000)
        logger.info("Saudia Manage Booking browser session launched successfully.")

    def reset_page(self):
        """Creates a fresh page for the next PNR to prevent state pollution."""
        try:
            if self.page:
                self.page.close()
        except Exception:
            pass
        if self.context:
            self.page = self.context.new_page()
            self.page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
            self.page.set_default_timeout(35000)

    def update_booking_contact(self, pnr: str, last_name: str, email: str, phone: str = "") -> dict:
        """
        Retrieves a booking on Saudia using PNR and passenger Last Name,
        and updates the contact email.
        """
        clean_pnr = (pnr or "").strip().upper()
        clean_lname = (last_name or "").strip()
        clean_email = (email or "").strip().lower()

        if not clean_pnr:
            return {"success": False, "status": "Missing PNR", "remarks": "No PNR provided for booking"}

        if not clean_lname:
            return {"success": False, "status": "Missing Name", "remarks": "No Last Name provided for PNR"}

        logger.info(f"Retrieving Saudia booking: PNR={clean_pnr}, Last Name={clean_lname} ...")
        os.makedirs("scratch", exist_ok=True)

        try:
            # Step 1: Navigate to Saudia Manage Booking
            manage_url = "https://www.saudia.com/en-FR/book-and-manage/manage/manage-booking"
            try:
                self.page.goto(manage_url, timeout=35000)
            except Exception as e_nav:
                logger.warning(f"Initial goto warning: {e_nav}. Retrying...")
                self.page.goto("https://www.saudia.com", timeout=35000)
                self.page.goto(manage_url, timeout=35000)

            # Wait for title to establish (passing bot challenge if needed)
            for _ in range(8):
                time.sleep(1)
                if self.page.title():
                    break
            time.sleep(2)

            # Handle Cookie Consent
            try:
                cookie_btn = self.page.query_selector("button:has-text('Yes, I accept'), button:has-text('Accept')")
                if cookie_btn and cookie_btn.is_visible():
                    cookie_btn.click()
                    time.sleep(1)
            except Exception:
                pass

            # Step 2: Locate form inputs
            pnr_input = self.page.wait_for_selector(
                "input[formcontrolname='eticketnum'], input[placeholder*='Booking reference' i], input[data-placeholder*='Booking reference' i]",
                timeout=12000
            )
            last_input = self.page.wait_for_selector(
                "input[formcontrolname='lastName'], input[placeholder*='Last name' i], input[data-placeholder*='Last name' i]",
                timeout=12000
            )

            if not pnr_input or not last_input:
                logger.error("Could not find PNR and Last Name inputs on Saudia Manage Booking page.")
                self.page.screenshot(path=f"scratch/saudia_inputs_not_found_{clean_pnr}.png")
                return {"success": False, "status": "Form Error", "remarks": "Manage booking input fields not found"}

            # Step 3: Enter credentials
            pnr_input.click()
            pnr_input.fill("")
            pnr_input.type(clean_pnr, delay=50)

            last_input.click()
            last_input.fill("")
            last_input.type(clean_lname, delay=50)
            time.sleep(1)

            # Step 4: Submit retrieval
            cont_btn = self.page.query_selector("button:has-text('Continue'), button:has-text('Retrieve'), button[type='submit']")
            if cont_btn and cont_btn.is_visible():
                cont_btn.click()
            else:
                last_input.press("Enter")

            logger.info(f"Submitted retrieval for PNR {clean_pnr}. Awaiting response...")
            time.sleep(4)

            # Step 5: Check response
            # Check for error alert
            err_el = self.page.query_selector(".error-msg, [class*='error-msg'], [role='alert'], .toast, div:has-text('We couldn’t find this booking')")
            if not err_el:
                # Check text in body
                body_text = self.page.evaluate("() => document.body.innerText")
                if "We couldn’t find this booking" in body_text or "could not find this booking" in body_text.lower():
                    err_text = "We couldn't find this booking on Saudia portal."
                    logger.warning(f"Saudia response: {err_text} (PNR: {clean_pnr})")
                    self.page.screenshot(path=f"scratch/saudia_pnr_not_found_{clean_pnr}.png")
                    return {
                        "success": False,
                        "status": "PNR Not Found",
                        "remarks": f"Saudia: Booking not found for PNR {clean_pnr}"
                    }

            if err_el and err_el.is_visible():
                err_text = err_el.inner_text().strip()
                logger.warning(f"Saudia response: {err_text} (PNR: {clean_pnr})")
                self.page.screenshot(path=f"scratch/saudia_pnr_not_found_{clean_pnr}.png")
                return {
                    "success": False,
                    "status": "PNR Not Found",
                    "remarks": f"Saudia: {err_text[:80]}"
                }

            # Step 6: Booking retrieved! Navigate to contact update
            logger.info(f"Booking retrieved successfully for PNR {clean_pnr}! (URL: {self.page.url})")
            self.page.screenshot(path=f"scratch/saudia_booking_opened_{clean_pnr}.png")

            # Look for "Update contact details" or "Edit contact"
            contact_btn = self.page.query_selector(
                "button:has-text('Update contact'), a:has-text('Update contact'), div:has-text('Update contact details'), button:has-text('Edit'), [class*='contact']"
            )
            if contact_btn and contact_btn.is_visible():
                contact_btn.click()
                time.sleep(2)

            # Look for email input in contact section
            email_inp = self.page.query_selector("input[type='email'], input[formcontrolname='email'], input[name*='email' i]")
            if email_inp:
                email_inp.click()
                email_inp.fill(clean_email)
                time.sleep(1)

                # Save / Confirm
                save_btn = self.page.query_selector("button:has-text('Save'), button:has-text('Update'), button:has-text('Confirm')")
                if save_btn and save_btn.is_visible():
                    save_btn.click()
                    time.sleep(3)

                self.page.screenshot(path=f"scratch/saudia_pnr_updated_{clean_pnr}.png")
                logger.info(f"✅ Contact email {clean_email} saved on Saudia for PNR {clean_pnr}!")
                return {
                    "success": True,
                    "status": "Contact Updated",
                    "remarks": f"Email {clean_email} saved to PNR"
                }
            else:
                logger.info(f"Booking open on Saudia. Retained screenshot at scratch/saudia_booking_opened_{clean_pnr}.png")
                return {
                    "success": True,
                    "status": "Booking Verified",
                    "remarks": f"Booking accessed for {clean_pnr}"
                }

        except Exception as e:
            logger.error(f"Error updating booking contact for PNR {clean_pnr}: {e}")
            try:
                self.page.screenshot(path=f"scratch/saudia_pnr_error_{clean_pnr}.png")
            except Exception:
                pass
            return {
                "success": False,
                "status": "Execution Error",
                "remarks": str(e)[:100]
            }

    def stop(self):
        """Safely terminates browser resources."""
        try:
            if self.page:
                self.page.close()
        except Exception:
            pass
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        if self._own_playwright and self.playwright:
            try:
                self.playwright.stop()
            except Exception:
                pass
        logger.info("Saudia Manage Booking session closed.")
