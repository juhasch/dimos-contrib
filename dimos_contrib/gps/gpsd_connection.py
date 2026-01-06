#!/usr/bin/env python3
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

"""GPSd connection for DimOS."""

import json
import logging
import socket
import threading
import time
from typing import Any

from reactivex import Subject

from dimos.mapping.types import LatLon
from dimos.utils.logging_config import setup_logger

logger = setup_logger(level=logging.INFO)


class GPSdConnection:
    """Connection to gpsd daemon for GPS data."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 2947,
        timeout: float = 5.0,
    ) -> None:
        """Initialize GPSd connection.

        Args:
            host: GPSd host address
            port: GPSd port (default 2947)
            timeout: Connection timeout in seconds
        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket: socket.socket | None = None
        self.connected = False

        # Reactive streams for GPS data
        self._gps_location_subject: Subject[LatLon] = Subject()
        self._gps_velocity_subject: Subject[dict[str, float]] = Subject()
        self._gps_quality_subject: Subject[dict[str, Any]] = Subject()

        # Threading
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

        # Latest GPS data cache
        self._latest_location: LatLon | None = None
        self._latest_velocity: dict[str, float] = {}
        self._latest_quality: dict[str, Any] = {}

    def connect(self) -> bool:
        """Connect to gpsd daemon."""
        try:
            logger.info(f"Connecting to gpsd at {self.host}:{self.port}")
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(self.timeout)
            self.socket.connect((self.host, self.port))

            # Send WATCH command to start receiving data
            watch_cmd = '?WATCH={"enable":true,"json":true}\n'
            self.socket.sendall(watch_cmd.encode())

            self.connected = True
            logger.info("Connected to gpsd")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to gpsd: {e}")
            self.connected = False
            return False

    def disconnect(self) -> None:
        """Disconnect from gpsd."""
        self.connected = False
        if self.socket:
            try:
                self.socket.close()
            except Exception as e:
                logger.warning(f"Error closing socket: {e}")
            self.socket = None
        logger.info("Disconnected from gpsd")

    def start_streaming(self) -> bool:
        """Start streaming GPS data in background thread."""
        if self._running:
            logger.warning("GPS streaming already running")
            return True

        if not self.connected and not self.connect():
            return False

        self._running = True
        self._thread = threading.Thread(target=self._gps_loop, daemon=True)
        self._thread.start()
        logger.info("Started GPS streaming thread")
        return True

    def stop_streaming(self) -> None:
        """Stop streaming GPS data."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.disconnect()
        logger.info("Stopped GPS streaming")

    def _gps_loop(self) -> None:
        """Background thread loop for reading GPS data from gpsd."""
        buffer = ""

        while self._running:
            try:
                if not self.socket:
                    logger.error("Socket is None in GPS loop")
                    time.sleep(1.0)
                    continue

                # Read data from socket
                try:
                    data = self.socket.recv(4096).decode("utf-8")
                except socket.timeout:
                    continue
                except Exception as e:
                    logger.warning(f"Socket read error: {e}")
                    if self._running:
                        time.sleep(0.5)
                        if not self.connect():
                            time.sleep(5.0)
                    continue

                if not data:
                    logger.warning("No data from gpsd, reconnecting...")
                    time.sleep(1.0)
                    if not self.connect():
                        time.sleep(5.0)
                    continue

                # Add to buffer and process complete JSON lines
                buffer += data
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        self._process_gps_message(line)

            except Exception as e:
                logger.error(f"Error in GPS loop: {e}")
                time.sleep(1.0)

    def _process_gps_message(self, message: str) -> None:
        """Process a JSON message from gpsd.

        Args:
            message: JSON string from gpsd
        """
        try:
            data = json.loads(message)
            msg_class = data.get("class", "")

            if msg_class == "TPV":  # Time-Position-Velocity report
                self._process_tpv(data)
            elif msg_class == "SKY":  # Satellite data
                self._process_sky(data)

        except json.JSONDecodeError:
            logger.debug(f"Failed to parse GPS message: {message}")
        except Exception as e:
            logger.error(f"Error processing GPS message: {e}")

    def _process_tpv(self, data: dict[str, Any]) -> None:
        """Process TPV (Time-Position-Velocity) message.

        Args:
            data: TPV message data from gpsd
        """
        mode = data.get("mode", 0)

        # mode 2 = 2D fix, mode 3 = 3D fix
        if mode < 2:
            return

        # Extract location
        lat = data.get("lat")
        lon = data.get("lon")
        alt = data.get("alt") if mode >= 3 else None

        if lat is not None and lon is not None:
            with self._lock:
                self._latest_location = LatLon(lat=lat, lon=lon, alt=alt)
            self._gps_location_subject.on_next(self._latest_location)

        # Extract velocity and heading
        speed = data.get("speed")  # m/s
        track = data.get("track")  # degrees (course over ground)
        climb = data.get("climb")  # m/s (vertical velocity)

        if speed is not None or track is not None or climb is not None:
            velocity_data = {
                "speed": speed if speed is not None else 0.0,
                "track": track if track is not None else 0.0,
                "climb": climb if climb is not None else 0.0,
            }
            with self._lock:
                self._latest_velocity = velocity_data
            self._gps_velocity_subject.on_next(velocity_data)

    def _process_sky(self, data: dict[str, Any]) -> None:
        """Process SKY (satellite) message.

        Args:
            data: SKY message data from gpsd
        """
        satellites = data.get("satellites", [])
        n_sats = len(satellites)
        n_used = sum(1 for sat in satellites if sat.get("used", False))

        quality_data = {
            "satellites": n_sats,
            "satellites_used": n_used,
            "hdop": data.get("hdop"),  # Horizontal dilution of precision
            "vdop": data.get("vdop"),  # Vertical dilution of precision
            "pdop": data.get("pdop"),  # Position dilution of precision
        }

        with self._lock:
            self._latest_quality = quality_data
        self._gps_quality_subject.on_next(quality_data)

    def gps_location_stream(self) -> Subject[LatLon]:
        """Get GPS location stream.

        Returns:
            Observable stream of LatLon positions
        """
        return self._gps_location_subject

    def gps_velocity_stream(self) -> Subject[dict[str, float]]:
        """Get GPS velocity stream.

        Returns:
            Observable stream of velocity data (speed, track, climb)
        """
        return self._gps_velocity_subject

    def gps_quality_stream(self) -> Subject[dict[str, Any]]:
        """Get GPS quality stream.

        Returns:
            Observable stream of GPS quality data (satellites, DOP)
        """
        return self._gps_quality_subject

    def get_latest_location(self) -> LatLon | None:
        """Get the most recent GPS location.

        Returns:
            Latest LatLon or None if no fix
        """
        with self._lock:
            return self._latest_location

    def get_latest_velocity(self) -> dict[str, float]:
        """Get the most recent velocity data.

        Returns:
            Dictionary with speed, track, climb
        """
        with self._lock:
            return self._latest_velocity.copy()

    def get_latest_quality(self) -> dict[str, Any]:
        """Get the most recent GPS quality data.

        Returns:
            Dictionary with satellite count and DOP values
        """
        with self._lock:
            return self._latest_quality.copy()
