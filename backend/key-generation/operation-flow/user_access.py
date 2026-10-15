"""Check a received grant through the authenticated API; no secrets are printed."""
import json
import os
import requests

if __name__ == "__main__":
    response = requests.post(os.getenv("GRANT_API_URL", "http://127.0.0.1:5001") +
        "/api/grants/" + os.environ["GRANT_ID"] + "/access",
        headers={"Authorization": "Bearer " + os.environ["ACCESS_TOKEN"]}, timeout=60)
    print(json.dumps(response.json(), indent=2))
    response.raise_for_status()
