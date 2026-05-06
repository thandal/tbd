"""Compare LLM models on the Darkly HTML-munging task.

Usage:
    python3 darkly_compare.py [URL ...]

Saves outputs to comparison/{slug}__{label}.html and prints a timing summary.
"""
import os
import sys
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

import darkly_addon

CONFIGS = [
    ("cerebras_gpt-oss-120b", {
        "AI_PROVIDER": "cerebras",
        "CEREBRAS_MODEL": "gpt-oss-120b",
    }),
    ("groq_llama-4-scout", {
        "AI_PROVIDER": "groq",
        "GROQ_MODEL": "meta-llama/llama-4-scout-17b-16e-instruct",
    }),
    ("cerebras_qwen-3-235b", {
        "AI_PROVIDER": "cerebras",
        "CEREBRAS_MODEL": "qwen-3-235b-a22b-instruct-2507",
    }),
]

DEFAULT_URLS = [
    "https://news.ycombinator.com",
    "https://slashdot.org",
    "https://www.bbc.com/news",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")


def slugify(url):
    p = urlparse(url)
    s = (p.netloc + p.path).replace("/", "_").strip("_")
    return s or "root"


def absolutize_urls(html, base_url):
    """Resolve relative href/src to absolute so the saved file works in a browser."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        a["href"] = urljoin(base_url, a["href"])
    for img in soup.find_all("img", src=True):
        img["src"] = urljoin(base_url, img["src"])
    return str(soup)


def fetch(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return r.text


def apply_env(env):
    for k, v in env.items():
        os.environ[k] = v


def run_one(label, env, html, base_url, out_dir):
    apply_env(env)
    t0 = time.time()
    try:
        out = darkly_addon.simplify_html(html)
    except Exception as e:
        return None, time.time() - t0, f"exception: {e}"
    elapsed = time.time() - t0
    if not out or out.startswith("Error"):
        return None, elapsed, out or "empty"
    abs_out = absolutize_urls(out, base_url)
    path = os.path.join(out_dir, f"{label}.html")
    with open(path, "w") as f:
        f.write(abs_out)
    return path, elapsed, None


def main():
    urls = sys.argv[1:] or DEFAULT_URLS
    os.makedirs("comparison", exist_ok=True)

    results = []
    for url in urls:
        slug = slugify(url)
        page_dir = os.path.join("comparison", slug)
        os.makedirs(page_dir, exist_ok=True)
        print(f"\n=== {url} ===")

        try:
            html = fetch(url)
        except Exception as e:
            print(f"  fetch failed: {e}")
            continue
        print(f"  fetched {len(html)} chars")

        with open(os.path.join(page_dir, "_original.html"), "w") as f:
            f.write(absolutize_urls(html, url))

        for label, env in CONFIGS:
            print(f"\n  --- {label} ---")
            path, elapsed, err = run_one(label, env, html, url, page_dir)
            if err:
                print(f"    FAIL ({elapsed:.2f}s): {err[:200]}")
                results.append((url, label, elapsed, None, err))
            else:
                size = os.path.getsize(path)
                print(f"    OK  {elapsed:.2f}s  {size} bytes  -> {path}")
                results.append((url, label, elapsed, size, None))

    print("\n\n=== SUMMARY ===")
    print(f"{'URL':<45} {'Model':<28} {'Time':>8} {'Bytes':>10}")
    print("-" * 95)
    for url, label, elapsed, size, err in results:
        t = f"{elapsed:.2f}s"
        b = str(size) if size else "FAIL"
        print(f"{url[:45]:<45} {label:<28} {t:>8} {b:>10}")


if __name__ == "__main__":
    main()
