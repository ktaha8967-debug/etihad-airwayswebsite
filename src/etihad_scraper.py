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
        """Initializes the playwright browser session."""
        logger.info("Starting Playwright browser for Etihad Scraper...")
        if not self.playwright:
            import asyncio
            try:
                asyncio.set_event_loop(None)
            except Exception:
                pass
            self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=HEADLESS,
            args=["--disable-http2"]
        )
        self.context = self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self.page = self.context.new_page()
        # Apply standard Playwright stealth configuration using the correct Class method
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
        logger.info("Etihad Scraper stopped.")

    def access_booking(self, pnr: str, last_name: str) -> bool:
        """
        Navigates to Etihad Manage Booking and logs in with PNR and Last Name.
        """
        logger.info(f"Accessing booking for PNR: {pnr}, Last Name: {last_name}")
        try:
            self.page.goto(ETIHAD_MANAGE_BOOKING_URL, wait_until="domcontentloaded")

            # Close cookies consent if it appears
            try:
                # Based on screenshot: cookie banner button has text 'Close' or similar
                cookie_close = self.page.locator("button:has-text('Close'), button:has-text('Accept'), #onetrust-accept-btn-handler")
                if cookie_close.first.is_visible():
                    cookie_close.first.click()
            except Exception:
                pass

            # Fill PNR and Last name
            # Selectors updated based on screenshot placeholders
            pnr_input = self.page.locator("[placeholder*='Trip reference'], [placeholder*='reference or ticket number'], input[name='bookingReference']")
            lastname_input = self.page.locator("[placeholder*='Last name'], [placeholder*='Last Name'], input[name='lastName']")

            # Wait for PNR field to be visible in case elements load dynamically
            pnr_input.first.wait_for(state="visible", timeout=15000)

            pnr_input.first.fill(pnr)
            lastname_input.first.fill(last_name)

            # Click search/submit button
            submit_btn = self.page.locator("button:has-text('Search'), button:has-text('Submit')")
            submit_btn.click()

            # Wait for redirection/load state
            self.page.wait_for_load_state("domcontentloaded")
            self.page.wait_for_timeout(1000) # Reduced grace period
            
            # Wait for either the passenger info/flight details selector to appear or a specific page elements
            try:
                # Wait for any element containing booking/flight details to become visible (waiting out skeleton loader)
                loc1 = self.page.locator("text=Trip reference")
                loc2 = self.page.locator("text=Boarding pass")
                loc3 = self.page.locator("text=Flight details")
                success_selector = loc1.or_(loc2).or_(loc3).first
                success_selector.wait_for(state="visible", timeout=20000)
                logger.info(f"Successfully accessed booking for PNR: {pnr}")
                return True
            except Exception:
                # If we timed out, check if an error banner is visible
                error_msg = self.page.locator(":text('We cannot find this booking'), :text('incorrect'), :text('error'), [class*='error'], [class*='alert']").first
                if error_msg.is_visible():
                    logger.warning(f"Failed to access booking. Error shown: {error_msg.inner_text()}")
                else:
                    logger.warning(f"Failed to access booking for PNR: {pnr} (Timeout waiting for details to load)")
                return False
        except Exception as e:
            logger.error(f"Error accessing booking {pnr}: {e}")
            try:
                self.page.screenshot(path="etihad_error.png")
                logger.info("Saved error screenshot to etihad_error.png")
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
        details = {}
        try:
            # Locate the specific passenger card container filtering by last name
            passenger_card = self.page.locator("[class*='passenger-card'], [class*='passenger-details'], [class*='Passenger'], .passenger-details").filter(has_text=last_name).first
            
            if passenger_card.is_visible():
                # Expand the card if not already expanded (force click to bypass overlay labels)
                passenger_card.click(force=True)
                self.page.wait_for_timeout(500) # Reduced delay
                
                # Extract the full name from inside this passenger's card
                name_locator = passenger_card.locator("[class*='PassengerName'], [class*='passengerName'], .passenger-name, p").first
                if name_locator.count() > 0 and name_locator.inner_text().strip():
                    details["passenger_name"] = name_locator.inner_text().strip()
            
            # Fallback if specific card extraction fails
            if "passenger_name" not in details or not details["passenger_name"]:
                # Check for elements with "Adult" label and extract the name below them
                adult_label = self.page.locator("text=Adult").first
                if adult_label.is_visible():
                    parent_text = adult_label.locator("xpath=..").inner_text()
                    lines = [line.strip() for line in parent_text.split("\n") if line.strip()]
                    if len(lines) > 1:
                        details["passenger_name"] = lines[1] if "Adult" in lines[0] else lines[0]
            
            # Default fallback if still not found
            if "passenger_name" not in details or not details["passenger_name"]:
                details["passenger_name"] = f"Passenger {last_name}"

            # Flight details extraction
            flight_locator = self.page.locator("h2:has-text('to'), [class*='flight-title'], [class*='FlightTitle']").first
            if flight_locator.is_visible():
                details["flight_number"] = flight_locator.inner_text().strip()
            else:
                details["flight_number"] = "Flight Info"
                
            logger.info(f"Extracted details for {last_name}: {details}")
        except Exception as e:
            logger.warning(f"Could not extract details for {last_name}: {e}")
        return details
