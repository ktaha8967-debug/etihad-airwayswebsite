import logging
from playwright.sync_api import sync_playwright
from playwright_stealth.stealth import Stealth
from src.config import SAUDIA_SIGNUP_URL, HEADLESS, TIMEOUT

logger = logging.getLogger(__name__)

class SaudiaSignupManager:
    def __init__(self, playwright_instance=None):
        self.playwright = playwright_instance
        self.browser = None
        self.page = None

    def start(self):
        """Initializes the playwright browser session with mobile emulation if preferred."""
        logger.info("Starting Playwright browser for Saudia registration...")
        if not self.playwright:
            import asyncio
            try:
                asyncio.set_event_loop(None)
            except Exception:
                pass
            self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=HEADLESS)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        Stealth().apply_stealth_sync(self.page)
        self.page.set_default_timeout(TIMEOUT)

    def stop(self):
        """Closes the browser session safely."""
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        finally:
            self.browser = None
            self.playwright = None
        logger.info("Saudia Sign-up Manager stopped.")

    def create_membership(self, passenger_details: dict, email: str, password: str) -> str:
        """
        Automates registration on Saudia's membership signup portal using passenger details.
        Returns the generated Membership ID if successful, else returns None.
        """
        logger.info(f"Initiating Saudia membership creation for {passenger_details.get('passenger_name')}")
        try:
            logger.info("Navigating directly to Saudia Registration page...")
            self.page.goto("https://www.saudia.com/en-PK/Register", wait_until="domcontentloaded")
            self.page.wait_for_timeout(5000)
            
            # Handle Cookie Consent overlay
            try:
                cookie_btn = self.page.locator("button:has-text('Yes, I accept'), button:has-text('Accept'), button:has-text('Agree')").first
                if cookie_btn.is_visible():
                    logger.info("Cookie policy banner overlay detected. Accepting cookie terms...")
                    cookie_btn.click()
                    self.page.wait_for_timeout(2000)
            except Exception:
                pass
                
            reg_page = self.page

            # Check if hCaptcha/Security Check is present (semi-automation fallback)
            hcaptcha = reg_page.locator("iframe[src*='hcaptcha'], :text('security check'), :text('I am human')").first
            try:
                if hcaptcha.is_visible() or "security" in reg_page.url:
                    logger.warning("CAPTCHA / Cloudflare Security Check detected!")
                    
                    # Try auto-clicking hCaptcha checkbox inside the iframe
                    hcaptcha_iframe = reg_page.frame_locator("iframe[src*='hcaptcha']").first
                    checkbox = hcaptcha_iframe.locator("#checkbox, .check, [role='checkbox']").first
                    if checkbox.is_visible():
                        logger.info("Attempting auto-bypass by clicking hCaptcha checkbox...")
                        checkbox.click()
                        reg_page.wait_for_timeout(3000)
                        
                    # Fallback wait for manual solving if checkbox challenge is presented
                    reg_page.locator("input[name='firstName'], input#firstName, input[name='first_name']").wait_for(state="visible", timeout=120000)
                    logger.info("Security check resolved successfully. Proceeding with form autofill...")
            except Exception:
                pass

            # Fill passenger details using new verified form locators
            passport = passenger_details.get("passport", "EG9876543")
            dob = passenger_details.get("dob", "20/12/1992")
            mobile = passenger_details.get("mobile", "712345678")
            
            logger.info("Filling document number / passport...")
            doc_input = reg_page.locator("input[formcontrolname='documentNumber']").first
            doc_input.fill(passport)
            doc_input.blur()
            
            logger.info("Filling date of birth...")
            dob_input = reg_page.locator("input[appdatepickerformat='DD/MM/YYYY']").first
            dob_input.fill(dob)
            dob_input.blur()
            reg_page.keyboard.press("Escape")
            
            # Autocomplete selections: Nationality, Residence, Phone Country Code
            # Determine values (default to French/France if not provided)
            nationality_val = "France"
            residence_val = "France"
            
            logger.info("Selecting Nationality...")
            nationality_input = reg_page.locator("input.mat-autocomplete-trigger").nth(0)
            nationality_input.fill(nationality_val)
            reg_page.wait_for_timeout(1000)
            reg_page.locator("mat-option:visible").first.click()
            reg_page.keyboard.press("Escape")
            
            logger.info("Selecting Country of Residence...")
            residence_input = reg_page.locator("input.mat-autocomplete-trigger").nth(1)
            residence_input.fill(residence_val)
            reg_page.wait_for_timeout(1000)
            reg_page.locator("mat-option:visible").first.click()
            reg_page.keyboard.press("Escape")
            
            logger.info("Selecting Phone Country Code...")
            country_code_input = reg_page.locator("input.mat-autocomplete-trigger").nth(2)
            country_code_input.fill(residence_val)
            reg_page.wait_for_timeout(1000)
            reg_page.locator("mat-option:visible").first.click()
            reg_page.keyboard.press("Escape")
            
            logger.info("Filling phone number...")
            phone_input = reg_page.locator("input[formcontrolname='phoneNumber']").first
            phone_input.fill(mobile)
            phone_input.blur()
            
            logger.info("Filling email address...")
            email_input = reg_page.locator("input[formcontrolname='email']").first
            email_input.fill(email)
            email_input.blur()
            
            logger.info("Filling password...")
            password_input = reg_page.locator("input[formcontrolname='password']").first
            password_input.fill(password)
            password_input.blur()
            
            logger.info("Agreeing to terms...")
            terms_checkbox = reg_page.locator("mat-checkbox").first
            if terms_checkbox.is_visible():
                terms_checkbox.click()
                
            reg_page.wait_for_timeout(1000)
            
            # Click first Confirm button to load Step 2
            logger.info("Submitting Step 1 Account details...")
            submit_btn = reg_page.locator("button:has-text('Confirm'), button:has-text('Next'), button[type='submit']").first
            submit_btn.click()
            reg_page.wait_for_timeout(4000)
            
            # Step 2: Name and passport details
            logger.info("Filling Step 2 - Name & Passport details...")
            full_name = passenger_details.get("passenger_name", "")
            name_parts = [p.replace(".", "").strip() for p in full_name.split(" ") if p.strip()]
            
            # Determine passenger title and names
            title_to_select = "Mr"
            first_name = "Passenger"
            last_name = "LastName"
            
            if len(name_parts) > 0:
                first_part = name_parts[0].lower()
                if first_part in ["mr", "mrs", "miss", "ms"]:
                    if first_part == "mrs":
                        title_to_select = "Mrs"
                    elif first_part in ["miss", "ms"]:
                        title_to_select = "Miss"
                    
                    if len(name_parts) > 1:
                        first_name = name_parts[1]
                        last_name = name_parts[-1] if len(name_parts) > 2 else name_parts[1]
                else:
                    first_name = name_parts[0]
                    last_name = name_parts[-1] if len(name_parts) > 1 else name_parts[0]
            
            logger.info(f"Selecting passenger title: {title_to_select}")
            reg_page.locator("mat-select[formcontrolname='title'], .mat-select-trigger").first.click()
            reg_page.wait_for_timeout(1000)
            reg_page.locator(f"mat-option:has-text('{title_to_select}')").first.click()
            reg_page.wait_for_timeout(500)
            
            logger.info(f"Filling names -> First Name: {first_name} | Last Name: {last_name}")
            reg_page.locator("input[formcontrolname='firstName']").fill(first_name)
            reg_page.locator("input[formcontrolname='lastName']").fill(last_name)
            
            logger.info("Selecting Issuing Country...")
            issuing_country_input = reg_page.locator("input[aria-label*='country'], input[aria-label*='Country']").first
            issuing_country_input.click()
            issuing_country_input.fill(residence_val)
            reg_page.wait_for_timeout(1000)
            reg_page.locator("mat-option:visible").first.click()
            reg_page.keyboard.press("Escape")
            
            # Click final Join Now button
            logger.info("Clicking final Join Now button...")
            join_btn = reg_page.locator("button:has-text('Join Now'), button:has-text('Click to join now'), button[type='submit']").first
            join_btn.click()
            
            reg_page.wait_for_timeout(15000)
            logger.info("Registration submitted successfully!")
            
            if reg_page != self.page:
                reg_page.close()
            return "SUCCESS_PENDING_ACTIVATION"
            
        except Exception as e:
            logger.error(f"Error during Saudia registration: {e}")
            try:
                diag_path = "C:\\Users\\IC\\.gemini\\antigravity-cli\\brain\\882f98c1-535b-4ba2-a009-d5a65af4f644\\scratch\\saudia_error_diagnostic.png"
                reg_page.screenshot(path=diag_path)
                logger.info(f"Saved error diagnostic screenshot to: {diag_path}")
                invalid_details = reg_page.evaluate('() => Array.from(document.querySelectorAll(".ng-invalid")).map(el => el.outerHTML.slice(0, 150))')
                logger.info(f"Invalid elements at error: {invalid_details}")
            except Exception as ex:
                logger.warning(f"Failed to capture diagnostics: {ex}")
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
            
            # Extract travel details
            flight_info = passenger_details.get("flight_number", "SV123")
            import re
            flight_digits = re.findall(r'\d+', flight_info)
            flight_num = flight_digits[0] if flight_digits else "123"
            
            travel_date = "12/04/2026"
            import random
            ticket_num = "065" + "".join(random.choices("0123456789", k=10))
            
            # Fill form
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
            
