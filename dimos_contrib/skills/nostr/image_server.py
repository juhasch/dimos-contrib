#!/usr/bin/env python3
"""Simple image hosting server for Nostr image sharing with NIP-98 authentication.

Run with: python scripts/image_server.py
Images will be accessible at http://localhost:5000/images/<filename>
Upload requires NIP-98 authentication.
"""

import base64
import json
import os
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from nostr_sdk import Event, PublicKey
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = Path.home() / ".dimos" / "nostr_images"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

# Set the host URL - change this if deploying to a server
HOST = os.getenv("IMAGE_SERVER_HOST", "http://localhost:5000")


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def verify_nip98_auth(required_method: str) -> tuple[bool, str | None]:
    """Verify NIP-98 authentication from Authorization header.

    Args:
        required_method: The HTTP method that should be in the event (e.g., "POST")

    Returns:
        Tuple of (is_valid, error_message). error_message is None if valid.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return False, "Missing Authorization header"

    if not auth_header.startswith("Nostr "):
        return False, "Invalid Authorization header format. Expected 'Nostr <base64-event>'"

    # Extract base64 encoded event
    try:
        base64_event = auth_header[6:]  # Remove "Nostr " prefix
        event_json = base64.b64decode(base64_event).decode("utf-8")
        event_data = json.loads(event_json)
    except Exception as e:
        return False, f"Failed to decode authorization event: {e}"

    # Parse the Nostr event
    try:
        event = Event.from_json(event_json)
    except Exception as e:
        return False, f"Invalid Nostr event: {e}"

    # Verify event kind is 27235 (NIP-98 HTTP Auth)
    if event.kind().as_u16() != 27235:
        return False, f"Invalid event kind. Expected 27235 (NIP-98), got {event.kind().as_u16()}"

    # Verify signature
    if not event.verify():
        return False, "Invalid event signature"

    # Check timestamp (allow 60 second window)
    now = datetime.now().timestamp()
    event_timestamp = event.created_at().as_secs()
    time_diff = abs(now - event_timestamp)
    if time_diff > 60:
        return False, f"Event timestamp too old or in future (difference: {time_diff}s)"

    # Extract and verify tags
    tags = event.tags()
    url_tag = None
    method_tag = None

    for tag in tags.to_vec():
        tag_vec = tag.as_vec()
        if len(tag_vec) >= 2:
            if tag_vec[0] == "u":
                url_tag = tag_vec[1]
            elif tag_vec[0] == "method":
                method_tag = tag_vec[1]

    # Verify method tag
    if not method_tag:
        return False, "Missing 'method' tag in event"

    if method_tag.upper() != required_method.upper():
        return False, f"Method mismatch. Expected {required_method}, got {method_tag}"

    # Verify URL tag
    if not url_tag:
        return False, "Missing 'u' (URL) tag in event"

    # Build expected URL from request
    expected_url = request.url

    # Allow some flexibility in URL matching (with or without trailing slash)
    if url_tag.rstrip("/") != expected_url.rstrip("/"):
        return False, f"URL mismatch. Expected {expected_url}, got {url_tag}"

    return True, None


def require_nip98_auth(method: str):
    """Decorator to require NIP-98 authentication for an endpoint.

    Args:
        method: The HTTP method to verify (e.g., "POST", "GET")
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            is_valid, error = verify_nip98_auth(method)
            if not is_valid:
                return jsonify({"error": f"Authentication failed: {error}"}), 401
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@app.route("/upload", methods=["POST"])
@require_nip98_auth("POST")
def upload_file():
    """Upload an image and return its URL. Requires NIP-98 authentication."""
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    if file and allowed_file(file.filename):
        # Generate unique filename
        ext = file.filename.rsplit(".", 1)[1].lower()
        filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = UPLOAD_FOLDER / filename

        # Save file
        file.save(filepath)

        # Return URL
        url = f"{HOST}/images/{filename}"
        return jsonify({"url": url, "filename": filename}), 200

    return jsonify({"error": "Invalid file type"}), 400


@app.route("/images/<filename>")
def serve_image(filename):
    """Serve an uploaded image."""
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "upload_folder": str(UPLOAD_FOLDER)}), 200


if __name__ == "__main__":
    print(f"Starting image server...")
    print(f"Upload folder: {UPLOAD_FOLDER}")
    print(f"Upload endpoint: {HOST}/upload")
    print(f"Images will be accessible at: {HOST}/images/<filename>")

    # Run server
    app.run(host="0.0.0.0", port=5000, debug=False)
