from playwright.sync_api import sync_playwright

def test_fetch():
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            print("Navigating to yahoo...")
            page.goto("https://www.yahoo.com", wait_until="domcontentloaded", timeout=20000)
            print("Successfully loaded DOM.")
            print(f"Content length: {len(page.content())}")
            browser.close()
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    test_fetch()
