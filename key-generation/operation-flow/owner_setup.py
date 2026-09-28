"""Create a grant through the API. See ../LOCAL_TESTING.md for authentication."""
import json
import os
import requests

if __name__ == "__main__":
    response = requests.post(os.getenv("GRANT_API_URL", "http://127.0.0.1:5001") + "/api/grants",
        headers={"Authorization": "Bearer " + os.environ["ACCESS_TOKEN"]},
        json={"recipient_id": os.environ["RECIPIENT_ID"],
              "record_ids": os.environ["RECORD_IDS"].split(",")}, timeout=60)
    print(json.dumps(response.json(), indent=2))
    response.raise_for_status()
