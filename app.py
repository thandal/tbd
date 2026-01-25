import os
from dotenv import load_dotenv
import google.generativeai as genai
from openai import OpenAI
from bs4 import BeautifulSoup, Comment
from flask import Flask, render_template, request, Response
from playwright.sync_api import sync_playwright
import re

load_dotenv()

app = Flask(__name__)

def simplify_html_rule_based(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # List of tags to remove entirely
    strip_tags = [
        'script', 'style', 'noscript', 'iframe', 'svg', 'canvas', 
        'video', 'audio', 'picture', 'source', 'object', 'embed'
    ]
    for tag in soup(strip_tags):
        tag.decompose()
        
    # Simplify the structure
    for tag in soup.find_all(True):
        # Keep only basic attributes for a few tags
        if tag.name == 'a':
            href = tag.get('href')
            tag.attrs = {'href': href} if href else {}
        elif tag.name == 'img':
            src = tag.get('src')
            alt = tag.get('alt', '')
            tag.attrs = {'src': src, 'alt': alt} if src else {}
        else:
            # Strip all attributes from other tags
            tag.attrs = {}
            
    # Remove comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    return soup.prettify()

def simplify_html_ai(html_content, model_type="gemini"):
    # First, use rule-based simplification to reduce token count
    pre_simplified = simplify_html_rule_based(html_content)
    
    prompt = f"""
    You are a web design expert. Below is a simplified HTML content of a webpage.
    Your task is to rewrite it into a compact, modern, and aesthetically pleasing version.
    
    Rules:
    1. Keep all meaningful text and links (hrefs).
    2. Use semantic HTML5.
    3. Include a <style> block with a premium, modern design (vibrant colors, clean typography, responsive layout).
    4. Focus on readability and visual excellence.
    5. Return ONLY the complete HTML code starting with <!DOCTYPE html>.
    
    Content to transform:
    {pre_simplified}
    """
    
    try:
        if model_type == "gemini":
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                return "Error: GEMINI_API_KEY not found in .env"
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-3-flash-preview')
            response = model.generate_content(prompt)
            # Remove markdown code blocks if present
            text = response.text
            if "```html" in text:
                text = text.split("```html")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            return text.strip()
            
        elif model_type == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                return "Error: OPENAI_API_KEY not found in .env"
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.choices[0].message.content
            if "```html" in text:
                text = text.split("```html")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            return text.strip()
            
        else:
            return "Error: Unsupported model type"
    except Exception as e:
        return f"Error during AI processing: {str(e)}"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/proxy')
def proxy():
    url = request.args.get('url')
    model_type = request.args.get('model', os.getenv('DEFAULT_AI_MODEL', 'gemini'))
    
    if not url:
        return "No URL provided", 400
    
    if not url.startswith('http'):
        url = 'https://' + url
        
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                viewport={'width': 1280, 'height': 800}
            )
            page = context.new_page()
            
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000) 
                html_content = page.content()
            except Exception as e:
                html_content = page.content()
                if not html_content or len(html_content) < 100:
                    raise e
            finally:
                browser.close()
            
            # Use AI to simplify the HTML
            simplified = simplify_html_ai(html_content, model_type=model_type)
            
            if simplified.startswith("Error:"):
                return simplified, 500
                
            return Response(simplified, mimetype='text/html')
            
    except Exception as e:
        return f"Error fetching page: {str(e)}", 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
