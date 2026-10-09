import logging
from playwright.sync_api import sync_playwright
from playwright_stealth.stealth import Stealth
from src.config import ETIHAD_MANAGE_BOOKING_URL, HEADLESS, TIMEOUT

logger = logging.getLogger(__name__)

class EtihadScraper:
    def __init__(self, playwright_instance=None):
        self.playwright = playwright_instance
        self.browser = None
        self.page = None

    def start(self):
        """Initializes the playwright browser session with real Chrome under Xvfb to bypass Akamai."""
        logger.info("Starting Playwright browser for Etihad Scraper...")
        if not self.playwright:
            import asyncio
            try:
                asyncio.set_event_loop(None)
            except Exception:
                pass
            self.playwright = sync_playwright().start()

        import os
        if "DISPLAY" not in os.environ:
            os.environ["DISPLAY"] = ":99"

        chrome_path = "/home/taha/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome"
        executable_path = chrome_path if os.path.exists(chrome_path) else None

        launch_kwargs = {
            "headless": False if os.environ.get("DISPLAY") else HEADLESS,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ],
            "env": {"DISPLAY": os.environ.get("DISPLAY", ":99"), **os.environ}
        }
        if executable_path:
            launch_kwargs["executable_path"] = executable_path

        self.browser = self.playwright.chromium.launch(**launch_kwargs)
        self.context = self.browser.new_context(
            viewport={"width": 1280, "height": 800}
        )
        self.page = self.context.new_page()
        self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
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
        logger.info("Etihad Scraper stopped.")

    def access_booking(self, pnr: str, last_name: str) -> bool:
        """
        Navigates to Etihad Manage Booking and logs in with PNR and Last Name.
        """
        logger.info(f"Accessing booking for PNR: {pnr}, Last Name: {last_name}")
        try:
            self.page.goto(ETIHAD_MANAGE_BOOKING_URL, wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)

            # Dismiss OneTrust cookie banner if visible
            try:
                cookie_btn = self.page.locator("#onetrust-accept-btn-handler, button:has-text('Accept')").first
                if cookie_btn.is_visible():
                    cookie_btn.click(timeout=2000)
            except Exception:
                pass

            # Fill PNR and Last name
            pnr_input = self.page.locator("input[name='bookingReference'], [placeholder*='Trip reference'], #bookingReference").first
            lastname_input = self.page.locator("input[name='lastName'], [placeholder*='Last name'], #lastName").first

            pnr_input.wait_for(state="visible", timeout=15000)
            pnr_input.fill(pnr)
            lastname_input.fill(last_name)

            # Click search
            submit_btn = self.page.locator("button:has-text('Search'), button:has-text('Find'), button[type='submit']").first
            submit_btn.click()

            # Wait for either success (Trip reference / Flight details) or error banner
            self.page.wait_for_timeout(3000)
            for _ in range(8):
                body_text = self.page.locator("body").inner_text()
                
                # Check for booking not found error
                if "unable to find a booking" in body_text.lower() or "error-code=ssg" in self.page.url.lower():
                    logger.warning(f"Etihad booking not found for PNR {pnr}, Last Name {last_name}")
                    try:
                        self.page.screenshot(path="scratch/etihad_not_found.png")
                    except Exception:
                        pass
                    return False
                    
                # Check for success
                if "trip reference" in body_text.lower() or "flight details" in body_text.lower():
                    logger.info(f"Successfully accessed Etihad booking for PNR: {pnr}")
                    return True
                    
                self.page.wait_for_timeout(1000)

            logger.warning(f"Timeout waiting for Etihad booking details for PNR: {pnr}")
            try:
                self.page.screenshot(path="scratch/etihad_timeout.png")
            except Exception:
                pass
            return False
        except Exception as e:
            logger.error(f"Error accessing booking {pnr}: {e}")
            try:
                self.page.screenshot(path="scratch/etihad_error.png")
            except Exception:
                pass
            return False

    def check_frequent_flyer_status(self, last_name: str) -> bool:
        """
        Expands passenger details and checks if a frequent flyer number is already attached.
        """
        try:
            # Locate and expand passenger card to make details visible
            passenger_card = self.page.locator("[class*='passenger-card'], [class*='passenger-details'], [class*='Passenger'], .passenger-details").filter(has_text=last_name).first
            if passenger_card.is_visible():
                passenger_card.click(force=True)
                self.page.wait_for_timeout(500) # Reduced delay

            # Get the entire page text content and analyze the line flow
            page_text = self.page.locator("body").inner_text()
            lines = [l.strip() for l in page_text.split("\n") if l.strip()]
            
            # Find the line index matching the passenger
            target_idx = -1
            for idx, line in enumerate(lines):
                if last_name.lower() in line.lower() and ("mr" in line.lower() or "mrs" in line.lower() or "ms" in line.lower() or "miss" in line.lower() or "adult" in lines[max(0, idx-1)].lower()):
                    target_idx = idx
                    break
            
            if target_idx != -1:
                # Scan the next 4 lines following the passenger's name
                scan_range = lines[target_idx + 1 : target_idx + 5]
                logger.info(f"Scanning lines below passenger {last_name}: {scan_range}")
                
                import re
                for line in scan_range:
                    # Check for 9-15 digit loyalty numbers
                    if re.search(r'\b\d{9,15}\b', line):
                        logger.info(f"Frequent flyer loyalty number found below passenger name: {line}")
                        return True
                    # Check for tier keywords
                    for kw in ["Bronze", "Silver", "Gold", "Platinum", "Etihad Guest"]:
                        if kw.lower() in line.lower():
                            logger.info(f"Frequent flyer loyalty indicator '{kw}' found below passenger name: {line}")
                            return True
            return False
        except Exception as e:
            logger.error(f"Error checking frequent flyer status: {e}")
            return False

    def update_booking_email(self, temp_email: str) -> bool:
        """
        Updates the booking contact details with a temporary email to receive e-tickets.
        """
        logger.info(f"Updating booking contact email to: {temp_email}")
        try:
            # Navigate to/click edit contact details
            edit_btn = self.page.locator(":text('Edit details'), [class*='edit-contact'], #editContactBtn")
            if edit_btn.is_visible():
                edit_btn.click()
                
            email_input = self.page.locator("input[type='email'], input[name='emailAddress']")
            email_input.fill(temp_email)
            
            save_btn = self.page.locator("button:text('Save'), button:text('Update')")
            save_btn.click()
            
            self.page.wait_for_load_state("networkidle")
            logger.info("Successfully updated contact email on Etihad.")
            return True
        except Exception as e:
            logger.error(f"Failed to update booking email: {e}")
            return False

    def extract_passenger_details(self, last_name: str) -> dict:
        """
        Extracts passenger data specifically for the passenger matching the last name.
        """
        import re
        details = {}
        try:
            # Wait for content to stabilize
            self.page.wait_for_timeout(2000)
            
            # Locate and expand passenger card for last name
            passenger_card = self.page.locator(f":text('{last_name}')").first
            if passenger_card.is_visible():
                passenger_card.click(force=True)
                self.page.wait_for_timeout(1000)

            body_text = self.page.locator("body").inner_text()
            lines = [line.strip() for line in body_text.split("\n") if line.strip()]

            # 1. Passenger Name
            pax_name = ""
            for idx, line in enumerate(lines):
                if last_name.lower() in line.lower() and not any(k in line.lower() for k in ["trip reference", "manage trip"]):
                    pax_name = line
                    break
            if not pax_name:
                pax_name = f"Passenger {last_name}"
            details["passenger_name"] = pax_name

            # 2. Route
            route = ""
            for line in lines:
                if " to " in line and not any(k in line.lower() for k in ["changes", "hours", "welcome", "up to", "manage", "download"]):
                    route = line
                    break

            # 3. Flight Numbers (e.g. EY 6, EY 300)
            flights = sorted(list(set(re.findall(r'\bEY\s*\d+\b', body_text))))
            
            # Combine into a clear flight details description
            flight_desc_parts = []
            if route:
                flight_desc_parts.append(route)
            if flights:
                flight_desc_parts.append(f"({', '.join(flights)})")
                
            flight_desc = " ".join(flight_desc_parts) if flight_desc_parts else "Etihad Flight"
            details["flight_number"] = flight_desc
            details["flights_list"] = flights
            details["route"] = route

            logger.info(f"Extracted Etihad details for {last_name}: {details}")
        except Exception as e:
            logger.warning(f"Could not extract details for {last_name}: {e}")
        return details
