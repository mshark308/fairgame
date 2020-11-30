import json
import secrets
import time
import os
import math
from datetime import datetime
from price_parser import parse_price

from amazoncaptcha import AmazonCaptcha
from chromedriver_py import binary_path  # this will get you the path variable
from furl import furl
from selenium import webdriver
from selenium.common import exceptions

# from selenium.common.exceptions import (
#     NoSuchElementException,
#     SessionNotCreatedException,
#     TimeoutException,
# )
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from utils import selenium_utils
from utils.json_utils import InvalidAutoBuyConfigException
from utils.logger import log
from utils.selenium_utils import options, enable_headless, wait_for_element

AMAZON_URLS = {
    "BASE_URL": "https://{domain}/",
    "CART_URL": "https://{domain}/gp/aws/cart/add.html",
    "OFFER_URL": "https://{domain}/gp/offer-listing/",
}
CHECKOUT_URL = "https://{domain}/gp/cart/desktop/go-to-checkout.html/ref=ox_sc_proceed?partialCheckoutCart=1&isToBeGiftWrappedBefore=0&proceedToRetailCheckout=Proceed+to+checkout&proceedToCheckout=1&cartInitiateId={cart_id}"

AUTOBUY_CONFIG_PATH = "amazon_config.json"

SIGN_IN_TEXT = [
    "Hello, Sign in",
    "Hola, Identifícate",
    "Bonjour, Identifiez-vous",
    "Ciao, Accedi",
    "Hallo, Anmelden",
    "Hallo, Inloggen",
]
SIGN_IN_TITLES = [
    "Amazon Sign In",
    "Amazon Sign-In",
    "Amazon Anmelden",
    "Iniciar sesión en Amazon",
    "Connexion Amazon",
    "Amazon Accedi",
    "Inloggen bij Amazon",
]
CAPTCHA_PAGE_TITLES = ["Robot Check"]
HOME_PAGE_TITLES = [
    "Amazon.com: Online Shopping for Electronics, Apparel, Computers, Books, DVDs & more",
    "AmazonSmile: You shop. Amazon gives.",
    "Amazon.ca: Low Prices – Fast Shipping – Millions of Items",
    "Amazon.co.uk: Low Prices in Electronics, Books, Sports Equipment & more",
    "Amazon.de: Low Prices in Electronics, Books, Sports Equipment & more",
    "Amazon.de: Günstige Preise für Elektronik & Foto, Filme, Musik, Bücher, Games, Spielzeug & mehr",
    "Amazon.es: compra online de electrónica, libros, deporte, hogar, moda y mucho más.",
    "Amazon.de: Günstige Preise für Elektronik & Foto, Filme, Musik, Bücher, Games, Spielzeug & mehr",
    "Amazon.fr : livres, DVD, jeux vidéo, musique, high-tech, informatique, jouets, vêtements, chaussures, sport, bricolage, maison, beauté, puériculture, épicerie et plus encore !",
    "Amazon.it: elettronica, libri, musica, fashion, videogiochi, DVD e tanto altro",
    "Amazon.nl: Groot aanbod, kleine prijzen in o.a. Elektronica, boeken, sport en meer",
]
SHOPING_CART_TITLES = [
    "Amazon.com Shopping Cart",
    "Amazon.ca Shopping Cart",
    "Amazon.co.uk Shopping Basket",
    "Amazon.de Basket",
    "Amazon.de Einkaufswagen",
    "Cesta de compra Amazon.es",
    "Amazon.fr Panier",
    "Carrello Amazon.it",
    "AmazonSmile Shopping Cart",
    "Amazon.nl-winkelwagen",
]
CHECKOUT_TITLES = [
    "Amazon.com Checkout",
    "Amazon.co.uk Checkout",
    "Place Your Order - Amazon.ca Checkout",
    "Place Your Order - Amazon.co.uk Checkout",
    "Amazon.de Checkout",
    "Place Your Order - Amazon.de Checkout",
    "Amazon.de - Bezahlvorgang",
    "Bestellung aufgeben - Amazon.de-Bezahlvorgang",
    "Place Your Order - Amazon.com Checkout",
    "Place Your Order - Amazon.com",
    "Tramitar pedido en Amazon.es",
    "Processus de paiement Amazon.com",
    "Confirmar pedido - Compra Amazon.es",
    "Passez votre commande - Processus de paiement Amazon.fr",
    "Ordina - Cassa Amazon.it",
    "AmazonSmile Checkout",
    "Plaats je bestelling - Amazon.nl-kassa",
    "Place Your Order - AmazonSmile Checkout",
]
ORDER_COMPLETE_TITLES = [
    "Amazon.com Thanks You",
    "Amazon.ca Thanks You",
    "AmazonSmile Thanks You",
    "Thank you",
    "Amazon.fr Merci",
    "Merci",
    "Amazon.es te da las gracias",
    "Amazon.fr vous remercie.",
    "Grazie da Amazon.it",
    "Hartelijk dank",
]
ADD_TO_CART_TITLES = [
    "Amazon.com: Please Confirm Your Action",
    "Amazon.de: Bitte bestätigen Sie Ihre Aktion",
    "Amazon.de: Please Confirm Your Action",
    "Amazon.es: confirma tu acción",
    "Amazon.com : Veuillez confirmer votre action",  # Careful, required non-breaking space after .com (&nbsp)
    "Amazon.it: confermare l'operazione",
    "AmazonSmile: Please Confirm Your Action",
    "",  # Amazon.nl has en empty title, sigh.
]
DOGGO_TITLES = ["Sorry! Something went wrong!"]

# this is not non-US friendly
SHIPPING_ONLY_IF = "FREE Shipping on orders over"

TWOFA_TITLES = ["Two-Step Verification"]

PRIME_TITLES = ["Complete your Amazon Prime sign up"]

# OFFER_PAGE_TITLES = ["Amazon.com: Buying Choices:"]

DEFAULT_MAX_CHECKOUT_LOOPS = 20
DEFAULT_MAX_PTC_TRIES = 3
DEFAULT_MAX_PYO_TRIES = 3
DEFAULT_MAX_ATC_TRIES = 3
DEFAULT_MAX_WEIRD_PAGE_DELAY = 5
DEFAULT_PAGE_WAIT_DELAY = 0.5


class Amazon:
    def __init__(self, notification_handler, headless=False, checkshipping=False):
        self.notification_handler = notification_handler
        self.asin_list = []
        self.reserve = []
        self.checkshipping = checkshipping
        self.page_wait_delay = DEFAULT_PAGE_WAIT_DELAY
        if os.path.exists(AUTOBUY_CONFIG_PATH):
            with open(AUTOBUY_CONFIG_PATH) as json_file:
                try:
                    config = json.load(json_file)
                    self.username = config["username"]
                    self.password = config["password"]
                    self.asin_groups = int(config["asin_groups"])
                    self.amazon_website = config.get(
                        "amazon_website", "smile.amazon.com"
                    )
                    for x in range(self.asin_groups):
                        self.asin_list.append(config[f"asin_list_{x + 1}"])
                        self.reserve.append(float(config[f"reserve_{x + 1}"]))
                    # assert isinstance(self.asin_list, list)
                except Exception:
                    log.error(
                        "amazon_config.json file not formatted properly: https://github.com/Hari-Nagarajan/nvidia-bot/wiki/Usage#json-configuration"
                    )
                    exit(0)
        else:
            log.error(
                "No config file found, see here on how to fix this: https://github.com/Hari-Nagarajan/nvidia-bot/wiki/Usage#json-configuration"
            )
            exit(0)

        if headless:
            enable_headless()

        # profile_amz = ".profile-amz"
        # # keep profile bloat in check
        # if os.path.isdir(profile_amz):
        #     os.remove(profile_amz)
        options.add_argument(f"user-data-dir=.profile-amz")

        try:
            self.driver = webdriver.Chrome(executable_path=binary_path, options=options)
            self.wait = WebDriverWait(self.driver, 10)
        except Exception as e:
            log.error(e)
            exit(1)

        for key in AMAZON_URLS.keys():
            AMAZON_URLS[key] = AMAZON_URLS[key].format(domain=self.amazon_website)

    def run(self, delay=3, test=False):
        self.driver.get(AMAZON_URLS["BASE_URL"])
        log.info("Waiting for home page.")
        self.handle_startup()
        if not self.is_logged_in():
            self.login()
        self.save_screenshot("Bot Logged in and Starting up")
        keep_going = True

        while keep_going:
            asin = self.run_asins(delay)
            # found something in stock and under reserve
            # initialize loop limiter variables
            self.try_to_checkout = True
            self.checkout_retry = 0
            self.order_retry = 0
            loop_iterations = 0
            while self.try_to_checkout:
                self.navigate_pages(test)
                # if successful after running navigate pages, remove the asin_list from the list
                if not self.try_to_checkout:
                    self.remove_asin_list(asin)
                # checkout loop limiters
                elif self.checkout_retry > DEFAULT_MAX_PTC_TRIES:
                    self.try_to_checkout = False
                elif self.order_retry > DEFAULT_MAX_PYO_TRIES:
                    if test:
                        self.remove_asin_list(asin)
                    self.try_to_checkout = False
                loop_iterations += 1
                if loop_iterations > DEFAULT_MAX_CHECKOUT_LOOPS:
                    self.try_to_checkout = False
            # if no items left it list, let loop end
            if not self.asin_list:
                keep_going = False

    def handle_startup(self):
        if self.is_logged_in():
            log.info("Already logged in")
        else:
            log.info("Lets log in.")

            is_smile = "smile" in AMAZON_URLS["BASE_URL"]
            xpath = (
                '//*[@id="ge-hello"]/div/span/a'
                if is_smile
                else '//*[@id="nav-link-accountList"]/div/span'
            )
            selenium_utils.button_click_using_xpath(self.driver, xpath)
            log.info("Wait for Sign In page")
            time.sleep(self.page_wait_delay)

    def is_logged_in(self):
        try:
            text = wait_for_element(self.driver, "nav-link-accountList").text
            return not any(sign_in in text for sign_in in SIGN_IN_TEXT)
        except Exception:
            return False

    def login(self):

        try:
            log.info("Email")
            self.driver.find_element_by_xpath('//*[@id="ap_email"]').send_keys(
                self.username + Keys.RETURN
            )
        except:
            log.info("Email not needed.")
            pass

        if self.driver.find_elements_by_xpath('//*[@id="auth-error-message-box"]'):
            log.error("Login failed, check your username in amazon_config.json")
            time.sleep(240)
            exit(1)

        log.info("Remember me checkbox")
        selenium_utils.button_click_using_xpath(self.driver, '//*[@name="rememberMe"]')

        log.info("Password")
        self.driver.find_element_by_xpath('//*[@id="ap_password"]').send_keys(
            self.password + Keys.RETURN
        )
        time.sleep(1)
        if self.driver.title in TWOFA_TITLES:
            log.info("enter in your two-step verification code in browser")
            while self.driver.title in TWOFA_TITLES:
                time.sleep(DEFAULT_MAX_WEIRD_PAGE_DELAY)
        log.info(f"Logged in as {self.username}")

    def run_asins(self, delay):
        found_asin = False
        while not found_asin:
            for i in range(len(self.asin_list)):
                for asin in self.asin_list[i]:
                    if self.check_stock(asin, self.reserve[i]):
                        return asin
                    time.sleep(delay)

    def check_stock(self, asin, reserve, retry=0):
        if retry > DEFAULT_MAX_ATC_TRIES:
            log.info("max add to cart retries hit, returning to asin check")
            return False
        if self.checkshipping:
            f = furl(AMAZON_URLS["OFFER_URL"] + asin + "/ref=olp_f_new&f_new=true")
        else:
            f = furl(
                AMAZON_URLS["OFFER_URL"]
                + asin
                + "/ref=olp_f_new&f_new=true&f_freeShipping=on"
            )
        try:
            self.driver.get(f.url)
            elements = self.driver.find_elements_by_xpath(
                '//*[@name="submit.addToCart"]'
            )
            prices = self.driver.find_elements_by_xpath(
                '//*[@class="a-size-large a-color-price olpOfferPrice a-text-bold"]'
            )
            shipping = self.driver.find_elements_by_xpath(
                '//*[@class="a-color-secondary"]'
            )
        except Exception as e:
            log.error(e)
            return None

        for i in range(len(elements)):
            price = parse_price(prices[i].text)
            if SHIPPING_ONLY_IF in shipping[i].text:
                ship_price = parse_price("0")
            else:
                ship_price = parse_price(shipping[i].text)
            ship_float = ship_price.amount
            price_float = price.amount
            if price_float is None:
                return False
            if ship_float is None or not self.checkshipping:
                ship_float = 0

            if (ship_float + price_float) <= reserve or math.isclose(
                (price_float + ship_float), reserve, abs_tol=0.01
            ):
                log.info("Item in stock and under reserve!")
                log.info("clicking add to cart")
                elements[i].click()
                time.sleep(self.page_wait_delay)
                if self.driver.title in SHOPING_CART_TITLES:
                    return True
                else:
                    log.info("did not add to cart, trying again")
                    self.check_stock(asin=asin, reserve=reserve, retry=retry + 1)
        return False

    # def check_if_captcha(self, func, args):
    #     try:
    #         func(args)
    #     except Exception as e:
    #         log.debug(str(e))
    #         if self.on_captcha_page():
    #             self.get_captcha_help()
    #             func(args, t=300)
    #         else:
    #             log.debug(self.driver.title)
    #             log.error(
    #                 f"An error happened, please submit a bug report including a screenshot of the page the "
    #                 f"selenium browser is on. There may be a file saved at: amazon-{func.__name__}.png"
    #             )
    #             self.save_screenshot("title-fail")
    #             time.sleep(60)
    #             # self.driver.close()
    #             log.debug(e)
    #             pass

    def remove_asin_list(self, asin):
        for i in range(len(self.asin_list)):
            if asin in self.asin_list[i]:
                self.asin_list.pop(i)
                self.reserve.pop(i)
                break

    # checkout page navigator
    def navigate_pages(self, test):
        # delay to wait for page load - probably want to change this to something more adjustable
        time.sleep(self.page_wait_delay)
        title = self.driver.title
        if title in SIGN_IN_TITLES:
            self.login()
        elif title in CAPTCHA_PAGE_TITLES:
            self.handle_captcha()
        elif title in SHOPING_CART_TITLES:
            self.handle_cart()
        elif title in CHECKOUT_TITLES:
            self.handle_checkout(test)
        elif title in ORDER_COMPLETE_TITLES:
            self.handle_order_complete()
        elif title in PRIME_TITLES:
            self.handle_prime_signup()
        elif title in HOME_PAGE_TITLES:
            # if home page, something went wrong
            self.handle_home_page()
        elif title in DOGGO_TITLES:
            self.handle_doggos()
        else:
            log.error(
                f"{title} is not a known title, please create issue indicating the title with a screenshot of page"
            )
            self.save_screenshot("unknown-title")
            self.save_page_source("unknown-title")

    def handle_prime_signup(self):
        log.info("Prime offer page popped up, attempting to click No Thanks")
        button = None
        try:
            button = self.driver.find_element_by_xpath(
                '//*[@="class=a-button a-button-base no-thanks-button"]'
            )
        except exceptions.NoSuchElementException:
            log.error("could not find button")
        if button:
            button.click()
        else:
            self.notification_handler.send_notification(
                "Prime offer page popped up, user intervention required"
            )
            time.sleep(DEFAULT_MAX_WEIRD_PAGE_DELAY)

    def handle_home_page(self):
        log.info("On home page, trying to get back to checkout")
        button = None
        try:
            button = self.driver.find_element_by_xpath('//*[@id="nav-cart"]')
        except exceptions.NoSuchElementException:
            log.info("Could not find cart button")
        if button:
            button.click()
        else:
            self.notification_handler.send_notification(
                "Could not click cart button, user intervention required"
            )
            time.sleep(DEFAULT_MAX_WEIRD_PAGE_DELAY)

    def handle_cart(self):
        try:  # This is fast.
            log.info("Quick redirect to checkout page")
            cart_initiate_id = self.driver.find_element_by_name("cartInitiateId")
            cart_initiate_id = cart_initiate_id.get_attribute("value")
            self.driver.get(
                CHECKOUT_URL.format(
                    domain=self.amazon_website, cart_id=cart_initiate_id
                )
            )
        except:
            log.info("clicking checkout.")
            try:
                self.driver.find_element_by_xpath(
                    '//*[@id="hlb-ptc-btn-native"]'
                ).click()
            except exceptions.NoSuchElementException:
                try:
                    self.driver.find_element_by_xpath('//*[@id="hlb-ptc-btn"]').click()
                except exceptions.NoSuchElementException:
                    self.save_screenshot("start-checkout-fail")
                    log.info("Failed to checkout.")
                    self.driver.refresh()
                    self.checkout_retry += 1

    def handle_checkout(self, test):
        button_xpaths = [
            '//*[@id="submitOrderButtonId"]/span/input',
            '//*[@id="bottomSubmitOrderButtonId"]/span/input',
        ]
        # restarting with this, not sure where all of these came from, can add more as needed.
        # '//*[@id="orderSummaryPrimaryActionBtn"]',
        # '//*[@id="bottomSubmitOrderButtonId"]/span/input',
        # '//*[@id="placeYourOrder"]/span/input',
        # '//*[@id="submitOrderButtonId"]/span/input',
        # '//input[@name="placeYourOrder1"]',
        # '//*[@id="hlb-ptc-btn-native"]',
        # '//*[@id="sc-buy-box-ptc-button"]/span/input',
        button = None
        for button_xpath in button_xpaths:
            try:
                if (
                    self.driver.find_element_by_xpath(button_xpath).is_displayed()
                    and self.driver.find_element_by_xpath(button_xpath).is_enabled()
                ):
                    button = self.driver.find_element_by_xpath(button_xpath)
            except exceptions.NoSuchElementException:
                log.debug(f"{button_xpath}, lets try a different one.")
            if button:
                if not test:
                    log.info(f"Clicking Button: {button.text}")
                    button.click()
                    time.sleep(self.page_wait_delay)
                else:
                    log.info(f"Found button{button.text}, but this is a test")
        # Could not click button, refresh page and try again
        self.driver.refresh()
        self.order_retry += 1

    def handle_order_complete(self):
        log.info("Order Placed.")
        self.save_screenshot("order-placed")
        self.try_to_checkout = False

    def handle_doggos(self):
        self.notification_handler.send_notification(
            "You got dogs, bot may not work correctly. Ending Checkout"
        )
        self.try_to_checkout = False

    def handle_captcha(self):
        try:
            if self.driver.find_element_by_xpath(
                '//form[@action="/errors/validateCaptcha"]'
            ):
                try:
                    log.info("Stuck on a captcha... Lets try to solve it.")
                    captcha = AmazonCaptcha.fromdriver(self.driver)
                    solution = captcha.solve()
                    log.info(f"The solution is: {solution}")
                    if solution == "Not solved":
                        log.info(
                            f"Failed to solve {captcha.image_link}, lets reload and get a new captcha."
                        )
                        self.driver.refresh()
                        time.sleep(DEFAULT_MAX_WEIRD_PAGE_DELAY)
                    else:
                        self.save_screenshot("captcha")
                        self.driver.find_element_by_xpath(
                            '//*[@id="captchacharacters"]'
                        ).send_keys(solution + Keys.RETURN)
                except Exception as e:
                    log.debug(e)
                    log.info("Error trying to solve captcha. Refresh and retry.")
                    self.driver.refresh()
                    time.sleep(DEFAULT_MAX_WEIRD_PAGE_DELAY)
        except exceptions.NoSuchElementException:
            log.error("captcha page does not contain captcha element")
            log.error("refreshing")
            self.driver.refresh()

    def save_screenshot(self, page):
        file_name = get_timestamp_filename("screenshot-" + page, ".png")

        if self.driver.save_screenshot(file_name):
            try:
                self.notification_handler.send_notification(page, file_name)
            except exceptions.TimeoutException:
                log.info("Timed out taking screenshot, trying to continue anyway")
                pass
            except Exception as e:
                log.error(f"Trying to recover from error: {e}")
                pass
        else:
            log.error("Error taking screenshot due to File I/O error")

    def save_page_source(self, page):
        """Saves DOM at the current state when called.  This includes state changes from DOM manipulation via JS"""
        file_name = get_timestamp_filename(page + "_source", "html")

        page_source = self.driver.page_source
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(page_source)

def get_timestamp_filename(name, extension):
    """Utility method to create a filename with a timestamp appended to the root and before
    the provided file extension"""
    now = datetime.now()
    date = now.strftime("%m-%d-%Y_%H_%M_%S")
    if extension.startswith("."):
        return name + "_" + date + extension
    else:
        return name + "_" + date + "." + extension
