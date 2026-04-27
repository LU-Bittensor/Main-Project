"""
Minimal key exchange broker.
Owners publish public key here after generation
Users fetch before performing their half of DH exchange
Server holds only public keys, NO secrets
"""

from flask import Flask, request, jsonify
import json, os

app = Flask(__name__)
KEY_STORE = {}  # In-memory store for public keys, format: {username: public_key}

# Called by owner after generating keypair
@app.route("/publish_key", methods=["POST"])
def publish_key():
    data = request.json
    record_id = data["record_id"] # unique identifier for the medical record, e.g. "record_123"
    public_key = data["public_key"] # the owner's public key in PEM format
    KEY_STORE[record_id] = public_key
    return jsonify({"status": "success", "record_id": record_id, "message": f"Public key for {record_id} published."})

# User calls this to fetch owner's public key before performing their half of the DH exchange
@app.route("/get_key/<record_id>", methods=["GET"])
def get_key(record_id):
    key = KEY_STORE.get(record_id)
    if not key:
        return jsonify({"status": "error", "message": f"No public key found for {record_id}."}), 404
    return jsonify({"status": "success", "record_id": record_id, "public_key": key})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)