import os
from dotenv import load_dotenv
from flask import Flask, render_template, request, Response, jsonify
import requests
from darkly_addon import simplify_html_stream

load_dotenv()

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/proxy')
async def proxy():
    url = request.args.get('url')
    
    if not url:
        return "No URL provided", 400
    
    if not url.startswith('http'):
        url = 'https://' + url
        
    try:
        # Fetch HTML directly with requests
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
        }
        # requests is synchronous, but works fine for this test script
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        
        content_type = response.headers.get('Content-Type', '')
        
        # If not HTML, return as is (binary content)
        if 'text/html' not in content_type:
            return Response(response.content, mimetype=content_type)
            
        html_content = response.text

        # Use AI to simplify the HTML and stream the response
        def generate():
            import asyncio
            import queue
            import threading
            
            q = queue.Queue()
            
            def run_loop():
                async def fetch():
                    try:
                        async for chunk in simplify_html_stream(html_content, url, "/proxy?url="):
                            q.put(chunk)
                    except Exception as e:
                        q.put(f"Error streaming: {str(e)}")
                    finally:
                        q.put(None)
                
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(fetch())
                loop.close()
                
            threading.Thread(target=run_loop, daemon=True).start()
            
            while True:
                chunk = q.get()
                if chunk is None:
                    break
                yield chunk
                
        return Response(generate(), mimetype='text/html')
            
    except requests.RequestException as e:
        return f"Error fetching page: {str(e)}", 500
    except Exception as e:
        return f"Error processing page: {str(e)}", 500

@app.route('/api/instructions', methods=['GET', 'POST'])
def handle_instructions():
    import darkly_addon
    if request.method == 'POST':
        try:
            data = request.get_json()
            new_instructions = data.get('instructions')
            if new_instructions:
                darkly_addon.save_instructions(new_instructions)
                darkly_addon.current_instructions = new_instructions
                return jsonify({"status": "success"})
            return jsonify({"status": "error", "message": "No instructions provided"}), 400
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
            
    return jsonify({
        "instructions": darkly_addon.load_instructions(),
        "default": darkly_addon.DEFAULT_INSTRUCTIONS
    })

if __name__ == '__main__':
    app.run(host="0.0.0.0", debug=True, port=5337)
