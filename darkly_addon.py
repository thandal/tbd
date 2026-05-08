from mitmproxy import http
import asyncio
import os
import time
from dotenv import load_dotenv
from openai import AsyncOpenAI
from bs4 import BeautifulSoup, Comment
from urllib.parse import urljoin, quote
import markdown
import re
import bs4

load_dotenv()

DEFAULT_INSTRUCTIONS = """
Below is a text representation of a web page. Your task is to rewrite it into a streamlined Markdown version.
    
Rules:
* Keep all meaningful text and main content.
* Remove all ads, tracking scripts, navigation (nav), sidebars, footers, and other non-content elements. Use the hints in the text to identify bloat.
* Do not invent new links. If a link or image was provided like [text](id:X) or ![alt](id:Y), preserve the (id:X) exactly.
* Return ONLY pure Markdown (no markdown fences, no explanation)."""

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

def clean_text(text):
    return re.sub(r'\s+', ' ', text).strip()

def dom_to_condensed(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    for tag in soup(['script', 'style', 'noscript', 'svg', 'canvas', 'video', 'audio', 'iframe', 'button', 'input', 'form', 'select', 'textarea']):
        tag.decompose()
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    mapping = {}
    next_id = 1
    block_tags = {'div', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'article', 'section', 'header', 'footer', 'nav', 'main', 'aside', 'figure', 'ul', 'ol', 'table', 'tr', 'td', 'th'}

    def process_node(node):
        nonlocal next_id
        if node.name == 'a':
            href = node.get('href')
            if href:
                id_val = next_id; next_id += 1
                mapping[id_val] = {'type': 'a', 'href': href}
                text = "".join(process_node(c) for c in node.children)
                text = clean_text(text)
                if text: return f"[{text}][{id_val}]"
            return ""
        elif node.name == 'img':
            src = node.get('src')
            if src:
                id_val = next_id; next_id += 1
                mapping[id_val] = {'type': 'img', 'src': src, 'alt': node.get('alt', '')}
                return f"![{node.get('alt', '').strip()}][{id_val}]"
            return ""
        elif type(node) == bs4.element.NavigableString:
            return str(node)
        elif node.name is not None:
            is_block = node.name in block_tags
            hint = ""
            if node.name in ['nav', 'header', 'footer', 'aside']:
                hint = f"({node.name.upper()}) "
            classes = node.get('class', [])
            if classes:
                classes_str = " ".join(classes).lower()
                if any(bad in classes_str for bad in ['ad', 'sponsor', 'nav', 'menu', 'sidebar', 'footer', 'header', 'promo']):
                    hint = f"({node.name.upper()} hint:{' '.join(classes)}) "
            children_text = "".join(process_node(c) for c in node.children)
            if is_block:
                clean_children = clean_text(children_text)
                if clean_children:
                    return f"\n{hint}{clean_children}\n"
                return ""
            else:
                return children_text
        return ""

    body = soup.find('body') or soup
    raw_condensed = process_node(body)
    condensed = re.sub(r'\n+', '\n', raw_condensed).strip()
    return condensed, mapping

def _get_llm_client():
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

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    return client, model_name

async def _call_llm_stream(client, model_name, prompt):
    start_time = time.time()
    response = await client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        stream=True
    )
    async for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
    duration = time.time() - start_time
    print(f"--- AI Generation ({model_name}) took {duration:.2f}s ---")

class MarkdownStreamParser:
    def __init__(self, mapping, base_url="", proxy_prefix=""):
        self.mapping = mapping
        self.base_url = base_url
        self.proxy_prefix = proxy_prefix
        self.buffer = ""
        self.md = markdown.Markdown()

    def process_chunk(self, chunk_text):
        self.buffer += chunk_text
        
        # Strip leading markdown fences if they arrive in the first few chunks
        if self.buffer.startswith("```"):
            self.buffer = re.sub(r'^```[a-zA-Z]*\s*\n?', '', self.buffer)
            
        blocks = self.buffer.split("\n\n")
        
        if not self.buffer.endswith("\n\n"):
            self.buffer = blocks.pop()
        else:
            self.buffer = ""
            
        html_chunks = []
        for block in blocks:
            # Strip trailing fences if they appear
            block = re.sub(r'\n?```\s*$', '', block)
            if not block.strip():
                continue
            html = self.md.convert(block.strip())
            html = self.restore_ids(html)
            html_chunks.append(html + "\n")
        
        return "".join(html_chunks)

    def finish(self):
        # Strip trailing fences
        self.buffer = re.sub(r'\n?```\s*$', '', self.buffer)
        if self.buffer.strip():
            html = self.md.convert(self.buffer.strip())
            html = self.restore_ids(html)
            return html + "\n"
        return ""

    def restore_ids(self, html):
        def replace_a(match):
            text = match.group(1)
            id_val = int(match.group(2))
            if id_val in self.mapping:
                map_data = self.mapping[id_val]
                original_href = map_data["href"]
                if self.base_url:
                    absolute_href = urljoin(self.base_url, original_href)
                    final_href = f"{self.proxy_prefix}{quote(absolute_href)}" if self.proxy_prefix else absolute_href
                else:
                    final_href = original_href
                return f'<a href="{final_href}">{text}</a>'
            return match.group(0)

        def replace_img(match):
            alt = match.group(1)
            id_val = int(match.group(2))
            if id_val in self.mapping:
                map_data = self.mapping[id_val]
                original_src = map_data["src"]
                if self.base_url:
                    absolute_src = urljoin(self.base_url, original_src)
                    final_src = f"{self.proxy_prefix}{quote(absolute_src)}" if self.proxy_prefix else absolute_src
                else:
                    final_src = original_src
                return f'<img src="{final_src}" alt="{alt}" />'
            return match.group(0)

        html = re.sub(r'!\[([^\]]*)\]\[(\d+)\]', replace_img, html)
        html = re.sub(r'\[([^\]]+)\]\[(\d+)\]', replace_a, html)
        return html

async def simplify_html_stream(html_content, base_url="", proxy_prefix=""):
    if not html_content:
        yield "Error: No HTML content provided"
        return
        
    client, model_name = _get_llm_client()
    if not client:
        yield "Error: Unsupported model type"
        return

    print(f"Original HTML length: {len(html_content)}")
    condensed, mapping = dom_to_condensed(html_content)
    print(f"Condensed markdown length: {len(condensed)}, IDs mapped: {len(mapping)}")

    prompt = f"{current_instructions}\n\nContent to transform:\n{condensed}"
    parser = MarkdownStreamParser(mapping, base_url, proxy_prefix)
    
    yield f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Through a Browser, Darkly</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600&family=Lora:ital,wght@0,400;0,600;1,400&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #fafafa;
            --text: #171717;
            --link: #2563eb;
            --card: #ffffff;
            --accent: #3b82f6;
        }}
        @media (prefers-color-scheme: dark) {{
            :root {{
                --bg: #171717;
                --text: #f5f5f5;
                --link: #60a5fa;
                --card: #262626;
                --accent: #3b82f6;
            }}
        }}
        body {{
            font-family: 'Lora', serif;
            background-color: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 2rem;
            max-width: 800px;
            margin: 0 auto;
            font-size: 1.1rem;
        }}
        h1, h2, h3, h4, h5, h6 {{
            font-family: 'Outfit', sans-serif;
            color: var(--text);
            margin-top: 2rem;
            font-weight: 600;
        }}
        a {{
            color: var(--link);
            text-decoration: none;
            border-bottom: 1px solid transparent;
            transition: border-color 0.2s;
        }}
        a:hover {{ border-color: var(--link); }}
        img {{ max-width: 100%; height: auto; border-radius: 0.5rem; margin: 1rem 0; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }}
        p {{ margin-bottom: 1.5rem; }}
        blockquote {{ border-left: 4px solid var(--accent); margin: 0; padding-left: 1rem; color: #737373; font-style: italic; }}
    </style>
</head>
<body>
<div class="darkly-content">
"""

    async for md_chunk in _call_llm_stream(client, model_name, prompt):
        html_chunk = parser.process_chunk(md_chunk)
        if html_chunk:
            yield html_chunk
            
    final_chunk = parser.finish()
    if final_chunk:
        yield final_chunk
        
    yield "\n</div></body></html>"

class DarklyAddon:
    def __init__(self):
        print("Darkly Proxy Addon Loaded")
        print("Control Panel available at http://dark.ly")

    async def request(self, flow: http.HTTPFlow):
        if flow.request.pretty_host == "dark.ly":
            if flow.request.method == "POST":
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
                    flow.response = http.Response.make(302, b"", {"Location": "/"})
                except Exception as e:
                    flow.response = http.Response.make(500, f"Error saving: {str(e)}".encode(), {"Content-Type": "text/plain"})
                return

            html_page = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Through a Browser, Darkly - Config</title>
                <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600&family=JetBrains+Mono&display=swap" rel="stylesheet">
                <style>
                    :root {{ --primary: #737373; --bg: #171717; --card: #262626; --text: #f5f5f5; --text-dim: #a3a3a3; }}
                    body {{ font-family: 'Outfit', sans-serif; background-color: var(--bg); color: var(--text); margin: 0; display: flex; justify-content: center; align-items: center; min-height: 100vh; overflow: hidden; }}
                    .container {{ background: var(--card); padding: 2.5rem; border-radius: 1.5rem; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5); width: 100%; max-width: 700px; border: 1px solid rgba(255, 255, 255, 0.05); }}
                    h1 {{ font-weight: 600; margin-top: 0; font-size: 1.875rem; color: var(--text); margin-bottom: 0.5rem; }}
                    p {{ color: var(--text-dim); margin-bottom: 2rem; }}
                    textarea {{ width: 100%; height: 300px; background: #171717; border: 2px solid #404040; border-radius: 0.75rem; color: #e5e5e5; font-family: 'JetBrains Mono', monospace; padding: 1rem; font-size: 0.9rem; resize: none; box-sizing: border-box; margin-bottom: 1.5rem; }}
                    .btn {{ background: #404040; color: white; border: none; padding: 0.75rem 2rem; border-radius: 0.75rem; font-weight: 600; cursor: pointer; transition: all 0.2s; font-size: 1rem; }}
                    .btn-secondary {{ background: #262626; border: 1px solid #404040; }}
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
                            <button type="submit" name="action" value="reset" class="btn btn-secondary">Reset</button>
                        </div>
                    </form>
                </div>
            </body>
            </html>
            """
            flow.response = http.Response.make(200, html_page.encode(), {"Content-Type": "text/html"})
            return

    async def response(self, flow: http.HTTPFlow):
        content_type = flow.response.headers.get("Content-Type", "")
        if "text/html" in content_type and flow.request.pretty_host != "dark.ly" and flow.request.pretty_host != "mitm.it":
            print(f"Simplifying: {flow.request.pretty_url}")
            try:
                flow.response.decode()
                html_content = flow.response.get_text()
                
                chunks = []
                # Buffer the stream. Mitmproxy requires the full string assigned to response.set_text()
                async for chunk in simplify_html_stream(html_content, flow.request.scheme + "://" + flow.request.pretty_host, ""):
                    chunks.append(chunk)
                
                full_html = "".join(chunks)
                flow.response.set_text(full_html)
                flow.response.headers["Content-Length"] = str(len(flow.response.raw_content))
                flow.response.headers["x-darkly"] = "true"
            except Exception as e:
                flow.response.set_text(f"Failed to simplify {flow.request.pretty_url}: {str(e)}")

addons = [
    DarklyAddon()
]
