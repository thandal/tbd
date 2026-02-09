import os
import sys

# Add local directory to path to find darkly_addon
sys.path.append(os.getcwd())

from darkly_addon import simplify_html_rule_based

def main():
    original_file = "debug_original.html"
    pre_simplified_file = "debug_pre_simplified.html"
    new_simplified_file = "debug_new_simplified.html"

    if not os.path.exists(original_file):
        print(f"Error: {original_file} not found.")
        return

    print(f"Reading {original_file}...")
    with open(original_file, "r") as f:
        html_content = f.read()
    
    original_size = len(html_content)
    print(f"Original size: {original_size} bytes")

    print("Running simplify_html_rule_based...")
    new_simplified_content = simplify_html_rule_based(html_content)
    
    new_size = len(new_simplified_content)
    print(f"New simplified size: {new_size} bytes")
    
    with open(new_simplified_file, "w") as f:
        f.write(new_simplified_content)
    print(f"Saved new simplified content to {new_simplified_file}")

    if os.path.exists(pre_simplified_file):
        with open(pre_simplified_file, "r") as f:
            pre_content = f.read()
        pre_size = len(pre_content)
        print(f"Previous simplified size ({pre_simplified_file}): {pre_size} bytes")
        
        diff = pre_size - new_size
        if diff > 0:
            print(f"New version is {diff} bytes SMALLER (Improvement)")
        elif diff < 0:
            print(f"New version is {abs(diff)} bytes LARGER (Regression or expected change due to kept images)")
        else:
            print("Sizes are identical.")
            
        # Optional: Checking for images count to see if that explains the size difference
        print("\nQuick content check:")
        print(f"Img tags in PRE: {pre_content.count('<img')}")
        print(f"Img tags in NEW: {new_simplified_content.count('<img')}")

    else:
        print(f"{pre_simplified_file} not found for comparison.")

if __name__ == "__main__":
    main()
