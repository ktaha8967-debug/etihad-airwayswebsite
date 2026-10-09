import os
import re
import json
import time
import logging
from playwright.sync_api import sync_playwright
from src.config import SAUDIA_SIGNUP_URL, HEADLESS, TIMEOUT
from src.utils import generate_random_passport, generate_random_dob, generate_random_mobile

logger = logging.getLogger(__name__)

CHROME_BIN = "/home/taha/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome"

class SaudiaSignupManager:
    def __init__(self, playwright_instance=None):
        self.playwright = playwright_instance
        self.browser = None
        self.context = None
        self.page = None

    def start(self):
        """Initializes the browser session using real Chrome on Xvfb display."""
        logger.info("Starting Playwright browser session for Saudia registration...")
        if not os.environ.get("DISPLAY"):
            os.environ["DISPLAY"] = ":99"

        if not self.playwright:
            import asyncio
            try:
                asyncio.set_event_loop(None)
            except Exception:
                pass
            self.playwright = sync_playwright().start()

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
            viewport={"width": 1280, "height": 800}
        )
        self.page = self.context.new_page()
        self._setup_page(self.page)

    def _setup_page(self, page):
        """Configures page with webdriver stealth and DAPI route CORS header injection."""
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        def handle_dapi_route(route):
            try:
                if route.request.method == 'OPTIONS':
                    route.fulfill(
                        status=200,
                        headers={
                            'access-control-allow-origin': 'https://www.saudia.com',
                            'access-control-allow-methods': 'GET, POST, PUT, DELETE, OPTIONS',
                            'access-control-allow-headers': '*',
                            'access-control-allow-credentials': 'true',
                        }
                    )
                    return
                response = route.fetch()
                h = dict(response.headers)
                h['access-control-allow-origin'] = 'https://www.saudia.com'
                h['access-control-allow-credentials'] = 'true'
                b_text = ""
                try:
                    b_text = response.text()
                except Exception:
                    pass
                u = route.request.url.lower()
                if "register-user" in u or "is-available-login-id" in u:
                    logger.info(f"Saudia API [{response.status}] {route.request.url.split('?')[0]} -> {b_text[:250]}")
                if hasattr(self, 'api_results'):
                    self.api_results.append({"url": route.request.url, "status": response.status, "body": b_text})
                route.fulfill(response=response, headers=h)
            except Exception as e:
                logger.debug(f"DAPI route intercept note: {e}")
                route.continue_()

        page.route("**/customer/**", handle_dapi_route)
        page.set_default_timeout(TIMEOUT)

    def reset_session(self):
        """Creates a completely fresh browser context and page to prevent Angular state pollution."""
        logger.info("Resetting Saudia browser session to a fresh context...")
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
            self.context = self.browser.new_context(
                viewport={"width": 1280, "height": 800}
            )
            self.page = self.context.new_page()
            self._setup_page(self.page)
            logger.info("Fresh Saudia session ready for next passenger.")
        except Exception as e:
            logger.error(f"Error creating fresh Saudia session: {e}")

    def stop(self):
        """Closes the browser session safely."""
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
        finally:
            self.context = None
            self.browser = None
            self.page = None
        logger.info("Saudia Sign-up Manager stopped.")

    def create_membership(self, passenger_details: dict, email: str, password: str) -> str:
        """
        Automates registration on Saudia's membership signup portal using passenger details.
        Returns 'SUCCESS_PENDING_ACTIVATION' if successfully submitted, else returns None.
        """
        logger.info(f"Initiating Saudia membership creation for {passenger_details.get('passenger_name', 'Passenger')} with email: {email}")
        try:
            reg_page = self.page

            # Register network response tracking for Saudia backend APIs
            self.api_results = []
            self.last_registered_email = email
            def on_response(res):
                u = res.url.lower()
                if "saudia.com" in u and not any(ext in u for ext in [".png", ".jpg", ".jpeg", ".svg", ".css", ".woff", ".woff2", ".js", ".ico"]):
                    body_snip = ""
                    if "register-user" in u or "is-available-login-id" in u:
                        try:
                            body_snip = res.text()[:250]
                        except Exception:
                            pass
                        logger.info(f"Saudia API [{res.status}] {res.url.split('?')[0]} -> {body_snip}")
                    self.api_results.append({"url": res.url, "status": res.status, "body": body_snip})

            reg_page.on("response", on_response)

            logger.info("Navigating to Saudia Registration page...")
            reg_page.goto("https://www.saudia.com/en-PK/Register", wait_until="networkidle")
            reg_page.wait_for_timeout(2000)

            # Injected Webpack hook to capture Angular RegisterForm component
            reg_page.evaluate('''() => {
                let req = null;
                window.webpackChunkSaudiaWebApp.push([['reg_capture_' + Math.random()], {}, (r) => { req = r; }]);
                const ngMod = req(83554).AppSaudiaEWalletModule;
                
                let foundComp = null;
                function searchObj(obj, depth) {
                    if (!obj || depth > 5) return;
                    if (obj.RegisterForm) { foundComp = obj.RegisterForm; return; }
                    if (typeof obj === 'object') {
                        for (let k of Object.keys(obj)) searchObj(obj[k], depth + 1);
                    }
                }
                searchObj(ngMod.ɵinj, 0);
                
                window.__capturedComponent = null;
                const proto = foundComp.prototype;
                const origConfirm = proto.onConfirmClick;
                proto.onConfirmClick = function() {
                    window.__capturedComponent = this;
                    return origConfirm.apply(this, arguments);
                };
                const origSubmit = proto.onSubmit;
                proto.onSubmit = function() {
                    window.__capturedComponent = this;
                    return origSubmit.apply(this, arguments);
                };
            }''')

            # Handle Cookie Consent overlay
            try:
                cookie_btn = reg_page.locator("button:has-text('Yes, I accept'), button:has-text('Accept'), button:has-text('Agree')").first
                if cookie_btn.is_visible(timeout=2000):
                    logger.info("Dismissing cookie policy banner...")
                    cookie_btn.click()
                    reg_page.wait_for_timeout(1000)
            except Exception:
                pass

            # Extract details
            passport = passenger_details.get("passport") or generate_random_passport()
            dob = passenger_details.get("dob") or generate_random_dob()
            raw_mobile = passenger_details.get("mobile") or generate_random_mobile()
            clean_m = re.sub(r'[^0-9]', '', str(raw_mobile))
            if len(clean_m) != 9 or not clean_m.startswith(('6', '7')):
                clean_m = generate_random_mobile()
            mobile = clean_m

            # ----------------- STEP 1 -----------------
            logger.info(f"Filling Step 1: Document={passport}, DOB={dob}, Phone={mobile}")
            doc_input = reg_page.locator("input[formcontrolname='documentNumber']").first
            doc_input.fill(passport)

            dob_input = reg_page.locator("input[aria-label='Date of birth']").first
            dob_input.fill(dob)
            reg_page.keyboard.press("Escape")

            # Nationality: France
            nat = reg_page.locator("input[aria-label='Nationality']").first
            nat.fill("France")
            reg_page.wait_for_timeout(800)
            reg_page.locator("mat-option:visible").first.click()
            reg_page.keyboard.press("Escape")

            # Country of residence
            try:
                cor = reg_page.locator("input[aria-label='Country of Residence']").first
                cor.scroll_into_view_if_needed()
                cor.fill("France")
                reg_page.wait_for_timeout(800)
                reg_page.locator("mat-option:visible").first.click()
                reg_page.keyboard.press("Escape")
            except Exception as e:
                logger.debug(f"Country of residence fill note: {e}")

            # Phone Country code
            cc = reg_page.locator("input[aria-label='Country code']").first
            cc.fill("France")
            reg_page.wait_for_timeout(800)
            reg_page.locator("mat-option:visible").first.click()
            reg_page.keyboard.press("Escape")

            # Phone, Email, Password
            reg_page.locator("input[formcontrolname='phoneNumber']").first.fill(mobile)
            reg_page.locator("input[formcontrolname='email']").first.fill(email)
            reg_page.locator("input[formcontrolname='email']").first.blur()
            logger.info("Waiting 3 seconds for email availability check to settle...")
            reg_page.wait_for_timeout(3000)

            # Self-healing check: If email is already linked, append fresh suffix to prevent stuck form
            try:
                email_err = reg_page.locator(":text('already linked'), :text('already registered'), :text('already in use')").first
                if email_err.is_visible(timeout=1000):
                    import random
                    import string
                    fresh_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
                    if "@" in email:
                        user_part, domain_part = email.split("@")
                        email = f"{user_part}_{fresh_suffix}@{domain_part}"
                    else:
                        email = f"{email}_{fresh_suffix}"
                    logger.info(f"Email was already linked to an account. Auto-switched to fresh unique alias: {email}")
                    self.last_registered_email = email
                    email_input = reg_page.locator("input[formcontrolname='email']").first
                    email_input.fill("")
                    email_input.fill(email)
                    email_input.blur()
                    reg_page.wait_for_timeout(3000)
            except Exception:
                pass

            # Password
            pwd = reg_page.locator("input[formcontrolname='password']").first
            pwd.scroll_into_view_if_needed()
            pwd.click()
            pwd.fill(password or "TempPass123!")
            pwd.blur()
            reg_page.wait_for_timeout(500)

            # Terms & conditions checkbox
            try:
                cb = reg_page.locator("mat-checkbox").first
                if cb.is_visible():
                    is_checked = "mat-checkbox-checked" in (cb.get_attribute("class") or "")
                    if not is_checked:
                        cb.click()
                        reg_page.wait_for_timeout(500)
            except Exception:
                pass

            logger.info("Submitting Step 1 Account details...")
            confirm_btn = reg_page.locator("button:has-text('Confirm')").first
            confirm_btn.scroll_into_view_if_needed()
            for _ in range(10):
                if confirm_btn.is_enabled():
                    break
                reg_page.wait_for_timeout(500)
            confirm_btn.click()
            try:
                reg_page.wait_for_selector("input[formcontrolname='firstName']", timeout=12000)
            except Exception:
                logger.info("Retrying Step 1 Confirm click...")
                confirm_btn.click()
                reg_page.wait_for_selector("input[formcontrolname='firstName']", timeout=8000)
            reg_page.wait_for_timeout(1000)

            # ----------------- STEP 2 -----------------
            logger.info("Filling Step 2 - Name & Passport details...")
            full_name = passenger_details.get("passenger_name", "").strip()
            first_name_input = passenger_details.get("first_name", "").strip()
            last_name_input = passenger_details.get("last_name", "").strip()
            name_parts = [p.replace(".", "").strip() for p in full_name.split() if p.strip()]

            # Determine title
            title_to_select = "Mr"
            words = (first_name_input + " " + full_name).split()
            for w in words:
                clean_w = w.replace(".", "").lower()
                if clean_w == "mrs":
                    title_to_select = "Mrs"
                    break
                elif clean_w in ["miss", "ms"]:
                    title_to_select = "Ms"
                    break
                elif clean_w in ["mr", "dr"]:
                    title_to_select = "Mr"
                    break

            clean_first = first_name_input
            for prefix in ["mr.", "mr", "mrs.", "mrs", "ms.", "ms", "miss", "dr.", "dr"]:
                if clean_first.lower().startswith(prefix):
                    clean_first = clean_first[len(prefix):].strip()
                    break

            if clean_first:
                first_name = clean_first.split()[0]
            elif name_parts:
                first_name = name_parts[1] if len(name_parts) > 1 and name_parts[0].lower() in ["mr", "mrs", "ms", "miss"] else name_parts[0]
            else:
                first_name = "Passenger"

            if last_name_input:
                last_name = last_name_input
            elif len(name_parts) > 1:
                last_name = name_parts[-1]
            else:
                last_name = "Traveler"

            logger.info(f"Selecting passenger title: {title_to_select}")
            reg_page.locator("mat-select[formcontrolname='title']").first.click()
            reg_page.wait_for_timeout(500)
            reg_page.locator(f"mat-option:has-text('{title_to_select}')").first.click()
            reg_page.wait_for_timeout(500)

            logger.info(f"Filling names -> First Name: {first_name} | Last Name: {last_name}")
            fn = reg_page.locator("input[formcontrolname='firstName']").first
            fn.click()
            fn.fill(first_name)
            fn.blur()

            ln = reg_page.locator("input[formcontrolname='lastName']").first
            ln.click()
            ln.fill(last_name)
            ln.blur()

            logger.info("Selecting Issuing Country: France...")
            ic = reg_page.locator("input[aria-label='Issuing country']").first
            ic.click()
            reg_page.wait_for_timeout(500)
            ic.fill("France")
            reg_page.wait_for_timeout(800)
            reg_page.locator("mat-option:visible").first.click()
            reg_page.keyboard.press("Escape")
            reg_page.wait_for_timeout(1000)

            # Fix Angular Form validators to ensure form is 100% valid for Passport submission
            logger.info("Applying Angular form control validity adjustments...")
            reg_page.evaluate('''() => {
                const c = window.__capturedComponent;
                if (!c) return;
                c.isPassportNumber = true;
                c.isNationalId = false;
                c.isIqamaNumber = false;
                if (c.eWalletRegistrationForm && c.eWalletRegistrationForm.controls) {
                    if (c.eWalletRegistrationForm.controls.documentNumber_sec) {
                        c.eWalletRegistrationForm.controls.documentNumber_sec.clearValidators();
                        c.eWalletRegistrationForm.controls.documentNumber_sec.updateValueAndValidity();
                    }
                    if (c.eWalletRegistrationForm.controls.email) {
                        c.eWalletRegistrationForm.controls.email.setErrors(null);
                    }
                    c.eWalletRegistrationForm.updateValueAndValidity();
                }
            }''')
            reg_page.wait_for_timeout(500)

            # Submit registration
            join_btn = reg_page.locator("button:has-text('Join Now')").first
            is_enabled = join_btn.is_enabled()
            logger.info(f"Join Now button enabled status: {is_enabled}")

            if is_enabled:
                logger.info("Clicking Join Now button...")
                join_btn.click()
            else:
                logger.info("Triggering onSubmit via component...")
                reg_page.evaluate('''() => {
                    const c = window.__capturedComponent;
                    if (c && typeof c.onSubmit === 'function') {
                        c.onSubmit({ preventDefault: () => {}, stopPropagation: () => {} });
                    }
                }''')

            # Poll for registration API response from backend
            logger.info("Waiting for Saudia backend registration response (allowing up to 35s for bot protection & processing)...")
            reg_success = False
            for step_i in range(35):
                reg_page.wait_for_timeout(1000)
                if step_i == 18 and not any("register-user" in r["url"] for r in self.api_results):
                    logger.info("Retrying Join Now submission click at step 18...")
                    reg_page.evaluate('''() => {
                        const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Join Now'));
                        if (btn) btn.click();
                    }''')
                for r in self.api_results:
                    if "register-user" in r["url"]:
                        if r["status"] in [200, 201, 204, 206]:
                            reg_success = True
                            logger.info(f"Saudia registration confirmed! API Status: {r['status']}")
                            break
                        elif r["status"] in [400, 403, 500]:
                            logger.warning(f"Registration API rejected with status {r['status']}: {r.get('body', '')}")
                            return None
                if reg_success:
                    break

            # Save state screenshot
            os.makedirs("scratch", exist_ok=True)
            shot_path = "scratch/saudia_registration_status.png"
            reg_page.screenshot(path=shot_path)
            logger.info(f"Saved registration screenshot to: {shot_path}")

            if reg_success:
                actual_email = getattr(self, "last_registered_email", email)
                logger.info(f"Saudia registration confirmed submitted for {actual_email}! Account pending activation email.")
                return "SUCCESS_PENDING_ACTIVATION"
            else:
                logger.warning(f"Registration was NOT confirmed by Saudia API for {email}.")
                return None

        except Exception as e:
            logger.error(f"Error during Saudia registration: {e}", exc_info=True)
            try:
                os.makedirs("scratch", exist_ok=True)
                diag_path = "scratch/saudia_error_diagnostic.png"
                self.page.screenshot(path=diag_path)
                logger.info(f"Saved error diagnostic screenshot to: {diag_path}")
            except Exception:
                pass
            return None

    def enter_otp(self, otp_code: str) -> bool:
        """Enters verification code received via email to finalize signup."""
        try:
            otp_input = self.page.locator("input[name='otp'], input[name='verificationCode']")
            if otp_input.is_visible():
                otp_input.fill(otp_code)
                verify_btn = self.page.locator("button:text('Verify'), button:text('Submit')")
                verify_btn.click()
                self.page.wait_for_load_state("networkidle")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to enter OTP: {e}")
            return False

    def claim_miles(self, passenger_details: dict) -> bool:
        """
        Semi-automatically claims miles for the passenger on Saudia.
        Navigates to Saudia, waits for user to log in and go to the Claim Miles form,
        then autofills the travel details and submits.
        """
        try:
            logger.info("Directing browser to Saudia home page. Please log in and navigate to the 'Claim Miles' section...")
            self.page.goto("https://www.saudia.com", wait_until="domcontentloaded")
            
            # Wait up to 120 seconds for the Claim Miles form to be visible (indicated by ticket number input)
            logger.info("Waiting for Claim Miles form elements to load (Waiting for user login & navigation)...")
            ticket_input = self.page.locator("input[formcontrolname='ticketNumber'], input[formcontrolname='documentNumber'], input[aria-label*='ticket'], input[placeholder*='Ticket']").first
            ticket_input.wait_for(state="visible", timeout=120000)
            logger.info("Claim Miles form detected! Starting autofill...")
            
            flight_info = passenger_details.get("flight_number") or ""
            flight_digits = re.findall(r'\d+', flight_info)
            flight_num = flight_digits[0] if flight_digits else "001"
            
            travel_date = "12/04/2026"
            import random
            ticket_num = "065" + "".join(random.choices("0123456789", k=10))
            
            ticket_input.fill(ticket_num)
            ticket_input.blur()
            
            flight_input = self.page.locator("input[formcontrolname='flightNumber'], input[placeholder*='Flight'], input[aria-label*='flight']").first
            if flight_input.is_visible():
                flight_input.fill(flight_num)
                flight_input.blur()
                
            date_input = self.page.locator("input[appdatepickerformat='DD/MM/YYYY'], input[placeholder*='DD/MM/YYYY']").first
            if date_input.is_visible():
                date_input.fill(travel_date)
                date_input.blur()
                self.page.keyboard.press("Escape")
                
            logger.info("Autofill complete. Please review the details and click Submit to claim miles.")
            self.page.wait_for_timeout(5000)
            return True
        except Exception as e:
            logger.error(f"Error during Claim Miles: {e}")
            return False
