# Copyright 2025 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import io
import queue
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests
import yaml
from nostr_sdk import (
    Client,
    EventBuilder,
    Filter,
    Keys,
    Kind,
    NostrSigner,
    PublicKey,
    RelayUrl,
    Tag,
    Timestamp,
    nip04_encrypt,
)
from reactivex.disposable import Disposable

from dimos.agents import Output, Reducer, Stream  # type: ignore[attr-defined]
from dimos.core import pLCMTransport
from dimos.core.core import rpc
from dimos.core.module import Module
from dimos.core.rpc_client import RpcCall
from dimos.core.skill_module import SkillModule
from dimos.core.transport import LCMTransport
from dimos.msgs.sensor_msgs.Image import Image
from dimos.protocol.skill.skill import skill
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class NostrSkill(SkillModule):
    _client: Client | None = None
    _sender_keys: Keys | None = None
    _recipient_pubkey: PublicKey | None = None
    _relays: list[str] = []
    _config_path: str = str(Path.home() / ".nostr.yaml")
    _connected: bool = False
    _send_lock: threading.Lock = threading.Lock()
    _listener_thread: threading.Thread | None = None
    _stop_listener: threading.Event = threading.Event()
    _check_interval: float = 2.0  # seconds between checks for new messages
    _lookback_hours: int = 1  # how far back to check on startup
    _transport: pLCMTransport | None = None  # type: ignore[type-arg]
    _image_transport: LCMTransport | None = None  # type: ignore[type-arg]
    _latest_image: Image | None = None
    _image_subscription: Disposable | None = None

    def __init__(self, config_path: str | None = None):
        super().__init__()
        if config_path:
            self._config_path = config_path
        else:
            # Expand ~ to home directory
            self._config_path = str(Path(self._config_path).expanduser())

    @rpc
    def start(self) -> None:
        super().start()
        self._load_config()
        self._initialize_client()
        self._start_listener()
        self._subscribe_to_camera()

    @rpc
    def stop(self) -> None:
        logger.info("Stopping NostrSkill...")

        # Signal the listener thread to stop
        self._stop_listener.set()

        # Wait for listener thread to finish
        if self._listener_thread and self._listener_thread.is_alive():
            logger.debug("Waiting for listener thread to stop...")
            self._listener_thread.join(timeout=3)
            if self._listener_thread.is_alive():
                logger.warning("Listener thread did not stop gracefully")

        # Disconnect from relays
        if self._client and self._connected:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self._disconnect())
                except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
                    pass
                finally:
                    try:
                        loop.close()
                    except Exception:
                        pass
            except Exception:
                # Suppress all exceptions during shutdown
                pass

        # Clean up image subscription
        if self._image_subscription:
            try:
                self._image_subscription.dispose()
            except Exception as e:
                logger.debug(f"Error disposing image subscription: {e}")
            self._image_subscription = None

        # Clean up transports
        if self._transport:
            try:
                self._transport.stop()
            except Exception as e:
                logger.debug(f"Error stopping transport: {e}")
            self._transport = None

        if self._image_transport:
            try:
                self._image_transport.stop()
            except Exception as e:
                logger.debug(f"Error stopping image transport: {e}")
            self._image_transport = None

        logger.info("NostrSkill stopped")
        super().stop()

    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        try:
            config_file = Path(self._config_path)
            if not config_file.exists():
                logger.error(f"Config file not found: {self._config_path}")
                raise FileNotFoundError(f"Config file not found: {self._config_path}")

            with open(config_file, "r") as f:
                config = yaml.safe_load(f)

            # Extract keys from config
            agent_seckey = config.get("agent_seckey:") or config.get("agent_seckey")
            user_pubkey = config.get("user_pubkey")

            if not agent_seckey:
                raise ValueError("Missing 'agent_seckey' in config.yaml")
            if not user_pubkey:
                raise ValueError("Missing 'user_pubkey' in config.yaml")

            # Parse keys
            self._sender_keys = Keys.parse(agent_seckey.strip())
            self._recipient_pubkey = PublicKey.parse(user_pubkey.strip())

            # Get relays
            config_relay = config.get("relay")
            config_relays = config.get("relays")

            if config_relays:
                self._relays = (
                    config_relays if isinstance(config_relays, list) else [config_relays]
                )
            elif config_relay:
                self._relays = [config_relay]
            else:
                # Fallback to default relays
                self._relays = [
                    "wss://relay.damus.io",
                    "wss://relay.primal.net",
                    "wss://nos.lol",
                ]

            logger.info(f"Nostr config loaded from {self._config_path}")
            logger.info(f"Agent: {self._sender_keys.public_key().to_bech32()}")
            logger.info(f"User: {self._recipient_pubkey.to_bech32()}")
            logger.info(f"Relays: {len(self._relays)}")

        except Exception as e:
            logger.error(f"Error loading nostr config: {e}")
            raise

    def _initialize_client(self) -> None:
        """Initialize the nostr client."""
        try:
            if self._sender_keys is None:
                raise ValueError("Sender keys not loaded")

            signer = NostrSigner.keys(self._sender_keys)
            self._client = Client(signer)
            logger.info("Nostr client initialized")
        except Exception as e:
            logger.error(f"Error initializing nostr client: {e}")
            raise

    async def _ensure_connected(self) -> None:
        """Ensure the client is connected to relays."""
        if self._connected:
            return

        if self._client is None:
            raise ValueError("Client not initialized")

        # Add relays
        for relay in self._relays:
            relay_url = RelayUrl.parse(relay)
            await self._client.add_relay(relay_url)

        # Connect to relays
        await self._client.connect()
        self._connected = True
        logger.info(f"Connected to {len(self._relays)} relays")

    async def _disconnect(self) -> None:
        """Disconnect from relays."""
        if self._client and self._connected:
            try:
                await self._client.disconnect()
                self._connected = False
                logger.info("Disconnected from relays")
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                # Suppress during shutdown
                self._connected = False
            except Exception as e:
                logger.debug(f"Error disconnecting: {e}")
                self._connected = False

    async def _send_dm_async(self, text: str) -> str:
        """Send a DM asynchronously using NIP-04."""
        try:
            if self._client is None or self._recipient_pubkey is None or self._sender_keys is None:
                return "Error: Nostr client not initialized"

            await self._ensure_connected()

            # Send DM using NIP-04 (widely supported encrypted DMs)
            # Encrypt the message
            encrypted_content = nip04_encrypt(
                self._sender_keys.secret_key(), self._recipient_pubkey, text
            )

            # Build event with Kind 4 and add 'p' tag for recipient
            event_builder = EventBuilder(Kind(4), encrypted_content).tags(
                [Tag.public_key(self._recipient_pubkey)]
            )

            # Sign and send
            output = await self._client.send_event_builder(event_builder)

            event_id = output.id.to_hex()
            logger.info(f"Sent DM (NIP-04): {text[:50]}... (Event ID: {event_id[:16]}...)")

            return f"Sent via Nostr: {text}"

        except Exception as e:
            logger.error(f"Error sending nostr DM: {e}")
            return f"Error sending Nostr DM: {e}"

    @skill()
    def send_nostr_dm(self, message: str) -> str:
        """Send a direct message via NOSTR to the configured user.

        This skill sends encrypted direct messages to the user via the NOSTR protocol.
        Messages are sent using NIP-04 (encrypted direct messages) for broad compatibility.

        IMPORTANT: Use this skill to respond when you receive messages that indicate they came
        via NOSTR DM. When you see "[Message received via NOSTR DM - please respond using send_nostr_dm]"
        in the input, you should use this skill to send your response.

        Args:
            message: The text message to send via NOSTR DM

        Example usage:
            send_nostr_dm("Hello, I have completed the task.")
        """
        if self._client is None:
            return "Error: Nostr client not initialized"

        # Use lock to prevent simultaneous sends
        with self._send_lock:
            # Run the async function in the event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(self._send_dm_async(message))
            finally:
                loop.close()

            return result

    def _upload_image(self, image: Image, upload_url: str = "http://192.168.1.233:5000/upload") -> str:
        """Upload image to image server and return the URL.

        Args:
            image: Image to upload
            upload_url: URL of the image server upload endpoint

        Returns:
            URL of the uploaded image

        Raises:
            Exception: If upload fails
        """
        # Convert image to JPEG bytes
        import base64
        import cv2
        import numpy as np

        logger.info(f"Uploading image to {upload_url}")
        # Extract base URL from upload_url (e.g., "http://192.168.1.233:5000" from "http://192.168.1.233:5000/upload")
        upload_parsed = urlparse(upload_url)
        base_url = f"{upload_parsed.scheme}://{upload_parsed.netloc}"

        # Convert to BGR (OpenCV format), then to RGB for web display
        bgr_image = image.to_bgr().to_opencv()
        rgb_image = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 85]
        success, buffer = cv2.imencode(".jpg", rgb_image, encode_param)
        if not success:
            raise ValueError("Failed to encode image as JPEG")

        # Create NIP-98 authentication event
        if self._sender_keys is None:
            raise ValueError("Sender keys not loaded")

        tags = [
            Tag.parse(["u", upload_url]),
            Tag.parse(["method", "POST"]),
        ]
        event_builder = EventBuilder(Kind(27235), "").tags(tags)
        event = event_builder.sign_with_keys(self._sender_keys)
        event_json = event.as_json()
        event_b64 = base64.b64encode(event_json.encode("utf-8")).decode("utf-8")

        # Upload to image server with NIP-98 auth
        files = {"file": ("image.jpg", io.BytesIO(buffer.tobytes()), "image/jpeg")}
        headers = {"Authorization": f"Nostr {event_b64}"}

        response = requests.post(upload_url, files=files, headers=headers, timeout=30)

        if response.status_code != 200:
            raise Exception(f"Upload failed with status {response.status_code}: {response.text}")

        # Parse response to get image URL
        result = response.json()
        if "url" not in result:
            raise Exception(f"Unexpected response format: {result}")

        image_url = result["url"]
        
        # Replace localhost in the returned URL with the actual host from upload_url
        # This fixes cases where the server returns localhost URLs but clients need the actual IP
        image_parsed = urlparse(image_url)
        if image_parsed.hostname == "localhost" or image_parsed.hostname == "127.0.0.1":
            # Replace with the actual host from upload_url
            image_url = urlunparse((
                image_parsed.scheme,
                upload_parsed.netloc,  # Use host:port from upload_url
                image_parsed.path,
                image_parsed.params,
                image_parsed.query,
                image_parsed.fragment
            ))
        
        logger.info(f"Uploaded image to image server: {image_url}")
        return image_url

    @skill()
    def send_camera_image(self, message: str = "") -> str:
        """Send the current camera image via NOSTR to the configured user.

        This skill captures the current camera image, uploads it to nostr.build,
        and sends the image URL via encrypted NOSTR DM. An optional message can accompany the image.

        Args:
            message: Optional text message to send with the image (default: "")

        Returns:
            Status message indicating success or failure

        Example usage:
            send_camera_image("Here's what I see right now")
            send_camera_image()  # Sends image without additional text
        """
        if self._client is None:
            return "Error: Nostr client not initialized"

        if self._latest_image is None:
            return "Error: No camera image available. Make sure the camera is running."

        try:
            # Upload image to image server
            logger.info("Uploading camera image to image server...")
            image_url = self._upload_image(self._latest_image)

            # Construct message with image URL
            if message:
                image_message = f"{message}\n\n{image_url}"
            else:
                image_message = image_url

            # Send via Nostr DM
            with self._send_lock:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(self._send_dm_async(image_message))
                finally:
                    loop.close()

            logger.info(
                f"Sent camera image via Nostr (resolution: {self._latest_image.width}x{self._latest_image.height}, URL: {image_url})"
            )
            return f"Sent camera image via Nostr: {image_url}"

        except Exception as e:
            logger.error(f"Error sending camera image: {e}")
            return f"Error sending camera image: {e}"

    def _subscribe_to_camera(self) -> None:
        """Subscribe to camera image stream to cache latest image."""
        try:
            # Create LCM transport for subscribing to /color_image with Image type
            self._image_transport = LCMTransport("/color_image", Image)

            # Subscribe to the stream and cache latest image
            def on_image(img: Image) -> None:
                self._latest_image = img
                logger.debug(f"Received camera image: {img.width}x{img.height}")

            self._image_subscription = self._image_transport.subscribe(on_image)
            logger.info("Subscribed to /color_image for camera images")
        except Exception as e:
            logger.error(f"Error subscribing to camera images: {e}")
            import traceback
            traceback.print_exc()

    def _start_listener(self) -> None:
        """Start the background listener thread."""
        if self._listener_thread is not None:
            logger.warning("Listener already started")
            return

        # Create transport for publishing to /human_input
        self._transport = pLCMTransport("/human_input")

        # Start the listener thread
        self._stop_listener.clear()
        self._listener_thread = threading.Thread(
            target=self._poll_for_messages_thread,
            daemon=True,
        )
        self._listener_thread.start()
        logger.info("NOSTR listener thread started")

    async def _poll_for_messages_async(self) -> None:
        """Async function to poll for new NOSTR DMs."""
        try:
            if self._client is None or self._sender_keys is None:
                logger.error("Client not initialized - cannot start listener")
                return

            if self._transport is None:
                logger.error("Transport not initialized - cannot start listener")
                return

            await self._ensure_connected()

            signer = NostrSigner.keys(self._sender_keys)
            pubkey = self._sender_keys.public_key()

            logger.info("Started listening for NOSTR DMs")

            # Track the last timestamp we've checked
            last_check = Timestamp.from_secs(
                int((datetime.now() - timedelta(hours=self._lookback_hours)).timestamp())
            )

            while not self._stop_listener.is_set():
                try:
                    # Calculate time range for this check
                    since = last_check
                    current_time = Timestamp.now()

                    # Filter for encrypted DMs (NIP-04) sent to us
                    filter_nip04 = Filter().kind(Kind(4)).pubkey(pubkey).since(since)

                    # Query events with timeout
                    # Check stop flag before expensive operation
                    if self._stop_listener.is_set():
                        break

                    events_nip04 = await self._client.fetch_events(
                        filter_nip04, timedelta(seconds=3)
                    )

                    if events_nip04.len() > 0:
                        logger.info(f"Received {events_nip04.len()} new NOSTR message(s)")

                        # Process messages
                        events_list = events_nip04.to_vec()
                        for event in sorted(
                            events_list, key=lambda e: e.created_at().as_secs()
                        ):
                            try:
                                # Decrypt the message
                                decrypted = await signer.nip04_decrypt(
                                    event.author(), event.content()
                                )

                                timestamp = datetime.fromtimestamp(
                                    event.created_at().as_secs()
                                )
                                sender = event.author().to_bech32()

                                logger.info(
                                    f"NOSTR DM from {sender[:16]}... at {timestamp}: {decrypted}"
                                )

                                # Publish to /human_input with NOSTR context
                                if self._transport:
                                    # Add context so the agent knows to respond via NOSTR
                                    contextual_message = (
                                        f"[Message received via NOSTR DM - please respond using send_nostr_dm]\n"
                                        f"{decrypted}"
                                    )
                                    self._transport.publish(contextual_message)
                                    logger.debug(f"Published NOSTR message to /human_input with context")

                            except Exception as e:
                                logger.error(f"Error processing NOSTR message: {e}")

                    # Update last check timestamp
                    last_check = current_time

                    # Wait before next check (break immediately if stop is set)
                    for _ in range(int(self._check_interval * 10)):
                        if self._stop_listener.is_set():
                            break
                        await asyncio.sleep(0.1)

                except asyncio.CancelledError:
                    # Graceful cancellation
                    break
                except Exception as e:
                    if not self._stop_listener.is_set():
                        logger.error(f"Error polling for NOSTR messages: {e}")
                    # Short sleep before retry, but check stop flag
                    for _ in range(int(self._check_interval * 10)):
                        if self._stop_listener.is_set():
                            break
                        await asyncio.sleep(0.1)

            logger.info("NOSTR listener stopped polling")

        except asyncio.CancelledError:
            # Graceful cancellation during shutdown
            logger.debug("NOSTR listener cancelled")
        except Exception as e:
            if not self._stop_listener.is_set():
                logger.error(f"Fatal error in NOSTR listener: {e}")
                import traceback
                traceback.print_exc()

    def _poll_for_messages_thread(self) -> None:
        """Thread function to run the async polling loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._poll_for_messages_async())
        except (KeyboardInterrupt, SystemExit):
            # Silently handle Ctrl+C
            pass
        except Exception as e:
            if not self._stop_listener.is_set():
                logger.error(f"Error in listener thread: {e}")
        finally:
            try:
                # Cancel any pending tasks silently
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                # Wait for tasks to complete cancellation
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
                # Suppress during shutdown
                pass
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
                logger.debug("Listener thread event loop closed")


nostr_skill = NostrSkill.blueprint

__all__ = ["NostrSkill", "nostr_skill"]
