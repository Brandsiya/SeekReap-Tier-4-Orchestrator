import requests

URL = "https://seekreap-tier4-tif2gmgi4q-uc.a.run.app/api/worker-forward/health"

def test_pipeline():
    print(f"\n--- [SeekReap Debug: Tier-4 -> Tier-3] ---")
    try:
        response = requests.get(URL, timeout=15)
        print(f"Status: {response.status_code}")
        
        try:
            print("Data:", response.json())
        except:
            print("Raw Output (Non-JSON):", response.text[:500])
            
    except Exception as e:
        print(f"Network Error: {str(e)}")

if __name__ == "__main__":
    test_pipeline()
