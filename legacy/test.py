import requests
import time
import json

# --- CONFIGURATION ---
# The "Seeds" are the start of the sentence you want to investigate.
# For 3D printing, we use functional terms.
seeds = [
    "Wall mount for",
    "Stand for",
    "Bracket for",
    "Replacement for",
    "Holder for",
    "Adapter for"
]

# We loop through a-z to force Amazon to give us deep suggestions
# e.g. "Wall mount for a...", "Wall mount for b..."
alphabet = "abcdefghijklmnopqrstuvwxyz"

def get_suggestions(query):
    """
    Hits the Amazon UK completion API to get search suggestions.
    """
    # This is the hidden API URL used by the Amazon.co.uk search bar
    url = f"https://completion.amazon.co.uk/search/complete?method=completion&mkt=3&r=11&s=131-8765432-1234567&q={query}&search-alias=aps&client=amazon-search-ui"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        response = requests.get(url, headers=headers, timeout=5)
        data = response.json()
        # The suggestions are usually in the second element of the JSON response
        return data[1]
    except Exception as e:
        return []

def main():
    print(f"--- STARTING HARVEST FOR AMAZON UK ---")
    unique_ideas = set()

    for seed in seeds:
        print(f"\nScanning seed: '{seed}'...")
        
        # 1. Get generic suggestions first
        base_results = get_suggestions(seed)
        for res in base_results:
            if res not in unique_ideas:
                print(f"  [FOUND]: {res}")
                unique_ideas.add(res)

        # 2. Drill down with letters a-z
        for letter in alphabet:
            query = f"{seed} {letter}"
            results = get_suggestions(query)
            
            for res in results:
                if res not in unique_ideas:
                    print(f"  [FOUND]: {res}")
                    unique_ideas.add(res)
            
            # Sleep to be polite to Amazon servers
            time.sleep(1.0)

    print(f"\n--- DONE. Found {len(unique_ideas)} unique product ideas. ---")

if __name__ == "__main__":
    main()