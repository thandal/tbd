import sys
import types

from darkly_addon import simplify_html_rule_based

# Test HTML content with various tags to be stripped and empty elements
test_html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Test Page</title>
    <link rel="stylesheet" href="style.css">
    <script>console.log('remove me');</script>
    <style>body { color: red; }</style>
</head>
<body>
    <header>
        <div>
            <!-- This empty div should be removed -->
        </div>
        <div>
            <span></span> <!-- Empty span inside div -->
        </div>
        <h1>Keep me</h1>
        <button>Click me (remove)</button>
        <form action="/submit">
            <input type="text" name="test">
            <textarea>Type here</textarea>
            <select><option>Option</option></select>
            <label>Label</label>
        </form>
    </header>
    <main>
        <p>Keep this text.</p>
        <p></p> <!-- Empty p -->
        <div>
            <p>
                <span></span> <!-- Nested empty -->
            </p>
        </div>
        <img src="image.jpg" alt="An image">
        <a href="https://example.com">Link</a>
        <iframe></iframe>
        <video></video>
    </main>
    <footer>
        Copyright
    </footer>
</body>
</html>
"""

try:
    simplified = simplify_html_rule_based(test_html)
    print("Simplification successful.")
    print("-" * 20)
    print(simplified)
    print("-" * 20)

    # Verification checks
    errors = []
    
    # Check for stripped tags
    forbidden_tags = ['script', 'style', 'meta', 'link', 'button', 'form', 'input', 'textarea', 'select', 'label', 'iframe', 'video']
    for tag in forbidden_tags:
        if f"<{tag}" in simplified:
            errors.append(f"Found forbidden tag: {tag}")

    # Check for empty elements (basic check for empty divs/spans we expect to be gone)
    # Note: BeautifulSoup prettify adds whitespace, so we check logic mostly by inspection of output or by parsing again.
    # But let's check for specific artifacts we expect to be gone.
    
    if "This empty div should be removed" in simplified: # Comments should be gone
        errors.append("Comments were not removed")

    # Check if images are preserved (bug fix verification)
    if '<img' not in simplified:
        errors.append("Images were incorrectly removed")
        
    if len(errors) > 0:
        print("Verification FAILED:")
        for err in errors:
            print(f"- {err}")
        sys.exit(1)
    else:
        print("Verification PASSED: All forbidden tags and comments removed.")
        
except Exception as e:
    print(f"An error occurred: {e}")
    sys.exit(1)
