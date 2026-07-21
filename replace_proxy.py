import sys

def replace_in_file(filepath, target, replacement):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if target not in content:
        print(f"Error: Target text not found in {filepath}")
        sys.exit(1)
        
    new_content = content.replace(target, replacement, 1)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Success: Replacement complete.")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python replace_proxy.py <filepath> <target_file_containing_target> <replacement_file>")
        sys.exit(1)
        
    filepath = sys.argv[1]
    target_path = sys.argv[2]
    replacement_path = sys.argv[3]
    
    with open(target_path, 'r', encoding='utf-8') as f:
        target = f.read()
        
    with open(replacement_path, 'r', encoding='utf-8') as f:
        replacement = f.read()
        
    replace_in_file(filepath, target, replacement)
