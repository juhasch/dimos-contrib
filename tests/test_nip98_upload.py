#!/usr/bin/env python3
"""Test script for NIP-98 authenticated image upload.

This script demonstrates how to upload an image to the image server
using NIP-98 authentication.
"""

import base64
import io
import json
from pathlib import Path

import requests
from nostr_sdk import EventBuilder, Keys, Kind, Tag, Timestamp


def create_nip98_auth_event(keys: Keys, url: str, method: str) -> str:
    """Create a NIP-98 HTTP Auth event.

    Args:
        keys: Nostr keys to sign the event
        url: The full URL of the endpoint
        method: The HTTP method (e.g., "POST")

    Returns:
        Base64 encoded event JSON
    """
    # Create event with kind 27235 (NIP-98 HTTP Auth)
    tags = [
        Tag.parse(["u", url]),
        Tag.parse(["method", method]),
    ]

    event_builder = EventBuilder(Kind(27235), "").tags(tags)

    # Sign the event
    event = event_builder.sign_with_keys(keys)

    # Convert to JSON and base64 encode
    event_json = event.as_json()
    event_b64 = base64.b64encode(event_json.encode("utf-8")).decode("utf-8")

    return event_b64


def upload_image_with_nip98(
    keys: Keys,
    image_path: str,
    upload_url: str = "http://localhost:5000/upload"
) -> dict:
    """Upload an image using NIP-98 authentication.

    Args:
        keys: Nostr keys for authentication
        image_path: Path to the image file to upload
        upload_url: URL of the upload endpoint

    Returns:
        Response JSON from the server
    """
    # Create NIP-98 auth event
    auth_event = create_nip98_auth_event(keys, upload_url, "POST")

    # Prepare the image file
    with open(image_path, "rb") as f:
        files = {"file": (Path(image_path).name, f, "image/jpeg")}

        # Make request with NIP-98 authorization header
        headers = {
            "Authorization": f"Nostr {auth_event}"
        }

        response = requests.post(upload_url, files=files, headers=headers)

    return response.json(), response.status_code


def test_upload():
    """Test the upload functionality."""
    # Load keys from config (or generate new ones for testing)
    import yaml

    config_path = Path.home() / ".nostr.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
            agent_seckey = config.get("agent_seckey:") or config.get("agent_seckey")
            keys = Keys.parse(agent_seckey.strip())
        print(f"Using keys from {config_path}")
    else:
        # Generate test keys
        keys = Keys.generate()
        print(f"Generated test keys. Public key: {keys.public_key().to_bech32()}")

    # Test with a dummy image (create a small test image)
    test_image_path = "/tmp/test_image.jpg"

    # Create a simple test image if it doesn't exist
    try:
        from PIL import Image
        import numpy as np

        if not Path(test_image_path).exists():
            # Create a simple colored image
            img_array = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
            img = Image.fromarray(img_array)
            img.save(test_image_path)
            print(f"Created test image at {test_image_path}")
    except ImportError:
        print("PIL not available, please provide a test image at /tmp/test_image.jpg")
        return

    # Test upload
    print(f"\nUploading image with NIP-98 authentication...")
    result, status_code = upload_image_with_nip98(keys, test_image_path)

    print(f"\nStatus: {status_code}")
    print(f"Response: {json.dumps(result, indent=2)}")

    if status_code == 200:
        print(f"\nSuccess! Image URL: {result.get('url')}")
    else:
        print(f"\nFailed: {result.get('error')}")


if __name__ == "__main__":
    test_upload()
