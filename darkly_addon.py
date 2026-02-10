from mitmproxy import http
import os
import time
from dotenv import load_dotenv
from openai import OpenAI
from bs4 import BeautifulSoup, Comment
from urllib.parse import urljoin, quote

load_dotenv()

DEFAULT_INSTRUCTIONS = """
Below is the HTML content of a webpage. Your task is to rewrite it into a streamlined version.
    
Rules:
* Keep all meaningful text and links (hrefs).
* Remove all ads, tracking scripts, and other non-content elements.
* Include a <style> block with a simple, modern design (vibrant colors, clean typography, responsive layout).
* Add links to wikipedia pages where applicable.
* Return ONLY the complete HTML code starting with <!DOCTYPE html>."""

# GLOBAL STATE for instructions (can be overridden by http://dark.ly)
INSTRUCTIONS_FILE = "ai_instructions.txt"

def load_instructions():
    if os.path.exists(INSTRUCTIONS_FILE):
        with open(INSTRUCTIONS_FILE, "r") as f:
            return f.read()
    return DEFAULT_INSTRUCTIONS

def save_instructions(instructions):
    with open(INSTRUCTIONS_FILE, "w") as f:
        f.write(instructions)

current_instructions = load_instructions()

def simplify_html_rule_based(html_content):
    """Strip known non-content tags and simplify HTML structure."""
    soup = BeautifulSoup(html_content, 'html.parser')

    # Remove tags that never contain useful visible content
    for tag in soup(['script', 'style', 'noscript', 'iframe', 'svg', 'canvas',
                     'video', 'audio', 'picture', 'source', 'object', 'embed',
                     'button', 'input', 'textarea', 'select', 'form']):
        tag.decompose()

    # html.parser incorrectly treats <link>/<meta> as containers, swallowing
    # sibling content. unwrap() removes the tag but keeps any swallowed children.
    for tag in soup(['link', 'meta']):
        tag.unwrap()

    # Strip attributes (keep href on <a>, src/alt on <img>)
    for tag in soup.find_all(True):
        if tag.name == 'a':
            href = tag.get('href')
            tag.attrs = {'href': href} if href else {}
        elif tag.name == 'img':
            src = tag.get('src')
            alt = tag.get('alt', '')
            tag.attrs = {'src': src, 'alt': alt} if src else {}
        else:
            tag.attrs = {}

    # Remove comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    # Remove only true leaf-level empty tags (no children, no text)
    keep_tags = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'td', 'th',
                 'tr', 'table', 'thead', 'tbody', 'ul', 'ol', 'dl', 'dt', 'dd',
                 'blockquote', 'pre', 'article', 'section', 'main', 'figure',
                 'body', 'html', 'head', 'title', 'nav', 'header', 'footer'}
    for _ in range(3):
        empty = [t for t in soup.find_all(True)
                 if t.name not in keep_tags
                 and not t.get_text(strip=True)
                 and not t.find_all('img')
                 and t.name != 'img'
                 and not (t.name == 'a' and t.get('href'))
                 and not list(t.children)]
        if not empty:
            break
        for t in empty:
            t.decompose()

    return soup.prettify()


def _get_llm_client():
    """Return (client, model_name) based on env config."""
    model_provider = os.getenv("AI_PROVIDER")
    if model_provider == "cerebras":
        api_key = os.getenv("CEREBRAS_API_KEY")
        base_url = "https://api.cerebras.ai/v1"
        model_name = os.getenv("CEREBRAS_MODEL")
    elif model_provider == "gemini":
        api_key = os.getenv("GEMINI_API_KEY")
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        model_name = os.getenv("GEMINI_MODEL")
    elif model_provider == "groq":
        api_key = os.getenv("GROQ_API_KEY")
        base_url = "https://api.groq.com/openai/v1"
        model_name = os.getenv("GROQ_MODEL")
    elif model_provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = "https://api.openai.com/v1"
        model_name = os.getenv("OPENAI_MODEL")
    else:
        return None, None

    client = OpenAI(api_key=api_key, base_url=base_url)
    return client, model_name


def _call_llm(client, model_name, prompt):
    """Call the LLM and return the response text, stripping markdown fences."""
    start_time = time.time()
    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}]
    )
    text = response.choices[0].message.content
    duration = time.time() - start_time
    print(f"--- AI Generation ({model_name}) took {duration:.2f}s ---")

    if "```html" in text:
        text = text.split("```html")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    return text.strip()


import re

def _split_html_into_chunks(html_content, max_chunk_size=50000):
    """Split HTML at heading boundaries into chunks of roughly max_chunk_size."""
    if len(html_content) <= max_chunk_size:
        return [html_content]

    # Split at major heading tags (<h1>, <h2>, <h3>) to keep sections intact
    # Use lookahead so the heading stays with the chunk that follows it
    parts = re.split(r'(?=<(?:h[1-3])[\s>])', html_content, flags=re.IGNORECASE)

    chunks = []
    current_chunk = ""

    for part in parts:
        # If adding this part would exceed the limit and we already have content,
        # save the current chunk and start a new one
        if current_chunk and len(current_chunk) + len(part) > max_chunk_size:
            chunks.append(current_chunk)
            current_chunk = part
        else:
            current_chunk += part

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _compress_chunk(client, model_name, chunk, chunk_index, total_chunks):
    """Use LLM to compress one chunk into compact HTML, preserving content."""
    prompt = f"""You are processing part {chunk_index + 1} of {total_chunks} of a web page.

Rewrite this HTML fragment into MUCH more compact HTML. Rules:
* Keep ALL meaningful text content — do not omit or summarize.
* Keep all links (a href) and images (img src).
* Remove all redundant wrapper divs, spans, and empty tags.
* Remove navigation, sidebars, footers, and non-content elements.
* Use simple semantic HTML (p, h1-h6, ul, li, table, a, img).  
* Do NOT add any styling, <head>, or <body> tags — just the content fragment.
* Return ONLY the HTML fragment, no explanation.

HTML to compress:
{chunk}"""

    print(f"  Compressing chunk {chunk_index + 1}/{total_chunks} ({len(chunk)} chars)...")
    result = _call_llm(client, model_name, prompt)
    print(f"  Chunk {chunk_index + 1}: {len(chunk)} → {len(result)} chars")
    return result


def simplify_html_ai(html_content):
    """Single-pass AI simplification for small pages."""
    if not html_content:
        return "Error: No HTML content provided"

    client, model_name = _get_llm_client()
    if not client:
        return "Error: Unsupported model type"

    prompt = f"""\
    {current_instructions}
    
    Content to transform:
    {html_content}
    """
    return _call_llm(client, model_name, prompt)


def simplify_html(html_content):
    if not html_content:
        return "Error: No HTML content provided"
    
    print(f"Original HTML content length: {len(html_content)}")
    with open("debug_original.html", "w") as f:
        f.write(html_content)

    rule_simplified = simplify_html_rule_based(html_content)
    print(f"Rule-simplified HTML content length: {len(rule_simplified)}")

    with open("debug_rule_simplified.html", "w") as f:
        f.write(rule_simplified)

    # If small enough, single-pass through LLM
    MAX_CHUNK_SIZE = 50000
    if len(rule_simplified) <= MAX_CHUNK_SIZE:
        print("Content fits in single LLM call")
        ai_simplified = simplify_html_ai(rule_simplified)
    else:
        # Chunked processing for large pages
        client, model_name = _get_llm_client()
        if not client:
            return "Error: Unsupported model type"

        chunks = _split_html_into_chunks(rule_simplified, MAX_CHUNK_SIZE)
        print(f"Content too large — splitting into {len(chunks)} chunks")

        # Compress each chunk
        compressed = []
        for i, chunk in enumerate(chunks):
            compressed_chunk = _compress_chunk(client, model_name, chunk, i, len(chunks))
            compressed.append(compressed_chunk)

        # Assemble compressed chunks with final LLM call
        assembled_content = "\n\n<!-- SECTION BREAK -->\n\n".join(compressed)
        print(f"Assembled compressed content: {len(assembled_content)} chars")

        assemble_prompt = f"""\
{current_instructions}

The content below was split into {len(chunks)} separate chunks.
Assemble them into a single, unified HTML page.
Make sure the content flows naturally and there are no duplicated sections.

Compressed content:
{assembled_content}
"""
        print("Assembling final page...")
        ai_simplified = _call_llm(client, model_name, assemble_prompt)

    print(f"AI-simplified HTML content length: {len(ai_simplified)}")

    with open("debug_ai_simplified.html", "w") as f:
        f.write(ai_simplified)

    return ai_simplified

def rewrite_links(html_content, base_url, proxy_prefix):
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Rewrite <a> tags
    for a in soup.find_all('a', href=True):
        original_href = a['href']
        # Resolve relative links
        absolute_href = urljoin(base_url, original_href)
        # Wrap in proxy prefix
        a['href'] = f"{proxy_prefix}{quote(absolute_href)}"
        
    # Rewrite <img> tags
    for img in soup.find_all('img', src=True):
        original_src = img['src']
        absolute_src = urljoin(base_url, original_src)
        img['src'] = f"{proxy_prefix}{quote(absolute_src)}"
        
    return str(soup)

class DarklyAddon:
    def __init__(self):
        print("Darkly Proxy Addon Loaded")
        print("Control Panel available at http://dark.ly")

    def request(self, flow: http.HTTPFlow):
        if flow.request.pretty_host == "dark.ly":
            if flow.request.method == "POST":
                # Save instructions
                try:
                    form_data = flow.request.multipart_form or flow.request.urlencoded_form
                    new_instructions = form_data.get("instructions")
                    action = form_data.get("action")
                    
                    global current_instructions
                    if action == "reset":
                        current_instructions = DEFAULT_INSTRUCTIONS
                    elif new_instructions:
                        current_instructions = new_instructions
                    
                    save_instructions(current_instructions)
                    print(f"Instructions updated/reset.")
                    
                    # Redirect back to home after saving
                    flow.response = http.Response.make(302, b"", {"Location": "/"})
                except Exception as e:
                    flow.response = http.Response.make(500, f"Error saving: {str(e)}".encode(), {"Content-Type": "text/plain"})
                return

            # GET / - Show editor
            html_page = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Through a Browser, Darkly - Config</title>
                <link rel="preconnect" href="https://fonts.googleapis.com">
                <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
                <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600&family=JetBrains+Mono&display=swap" rel="stylesheet">
                <style>
                    :root {{
                        --primary: #737373;
                        --bg: #171717;
                        --card: #262626;
                        --text: #f5f5f5;
                        --text-dim: #a3a3a3;
                    }}
                    body {{
                        font-family: 'Outfit', sans-serif;
                        background-color: var(--bg);
                        color: var(--text);
                        margin: 0;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        min-height: 100vh;
                        overflow: hidden;
                    }}
                    .container {{
                        background: var(--card);
                        padding: 2.5rem;
                        border-radius: 1.5rem;
                        box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
                        width: 100%;
                        max-width: 700px;
                        border: 1px solid rgba(255, 255, 255, 0.05);
                        backdrop-filter: blur(10px);
                        animation: slideIn 0.6s ease-out;
                    }}
                    @keyframes slideIn {{
                        from {{ opacity: 0; transform: translateY(20px); }}
                        to {{ opacity: 1; transform: translateY(0); }}
                    }}
                    h1 {{
                        font-weight: 600;
                        margin-top: 0;
                        font-size: 1.875rem;
                        color: var(--text);
                        margin-bottom: 0.5rem;
                    }}
                    p {{ color: var(--text-dim); margin-bottom: 2rem; }}
                    textarea {{
                        width: 100%;
                        height: 300px;
                        background: #171717;
                        border: 2px solid #404040;
                        border-radius: 0.75rem;
                        color: #e5e5e5;
                        font-family: 'JetBrains Mono', monospace;
                        padding: 1rem;
                        font-size: 0.9rem;
                        resize: none;
                        box-sizing: border-box;
                        transition: border-color 0.2s;
                        margin-bottom: 1.5rem;
                    }}
                    textarea:focus {{
                        outline: none;
                        border-color: var(--primary);
                        box-shadow: 0 0 0 4px rgba(255, 255, 255, 0.05);
                    }}
                    .btn {{
                        background: #404040;
                        color: white;
                        border: none;
                        padding: 0.75rem 2rem;
                        border-radius: 0.75rem;
                        font-weight: 600;
                        cursor: pointer;
                        font-family: inherit;
                        transition: all 0.2s;
                        width: 100%;
                        font-size: 1rem;
                    }}
                    .btn:hover {{
                        transform: translateY(-2px);
                        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);
                        background: #525252;
                    }}
                    .btn:active {{ transform: translateY(0); }}
                    .btn-secondary {{
                        background: #262626;
                        border: 1px solid #404040;
                    }}
                    .btn-secondary:hover {{
                        background: #333333;
                        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>Darkly Config</h1>
                    <p>Edit the instructions used by the AI to simplify web pages.</p>
                    <form action="/" method="POST">
                        <textarea name="instructions">{current_instructions}</textarea>
                        <div style="display: flex; gap: 1rem;">
                            <button type="submit" name="action" value="save" class="btn">Save Instructions</button>
                            <button type="submit" name="action" value="reset" class="btn btn-secondary">Reset to Defaults</button>
                        </div>
                    </form>
                </div>
            </body>
            </html>
            """
            flow.response = http.Response.make(200, html_page.encode(), {"Content-Type": "text/html"})
            return

    def response(self, flow: http.HTTPFlow):
        # We only want to simplify HTML responses
        content_type = flow.response.headers.get("Content-Type", "")
        
        # Check if this is a request we should simplify 
        # (e.g., avoid modifying mitmproxy's own internal pages)
        if "text/html" in content_type and flow.request.pretty_host != "dark.ly" and flow.request.pretty_host != "mitm.it":
            print(f"Simplifying: {flow.request.pretty_url}")
            try:
                # Decompress the response if needed
                flow.response.decode()
                
                html_content = flow.response.get_text()
                
                # Apply AI simplification
                simplified_html = simplify_html(html_content)
                
                if simplified_html and not simplified_html.startswith("Error"):
                    flow.response.set_text(simplified_html)
                    # Update headers to reflect modification
                    flow.response.headers["Content-Length"] = str(len(flow.response.raw_content))
                    flow.response.headers["x-darkly"] = "true"
                else:
                    flow.response.set_text(f"Skipping simplification for {flow.request.pretty_url}: {simplified_html}...")
            except Exception as e:
                flow.response.set_text(f"Failed to simplify {flow.request.pretty_url}: {str(e)}")

addons = [
    DarklyAddon()
]
