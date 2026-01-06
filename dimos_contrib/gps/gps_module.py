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

"""DimOS module wrapper for GPS via gpsd."""

import json
from typing import Any

from dimos_lcm.std_msgs import String  # type: ignore[import-untyped]
from reactivex.disposable import CompositeDisposable, Disposable

from dimos.core import Module, Out, rpc
from dimos.mapping.types import LatLon
from dimos.protocol.skill.skill import skill
from dimos.protocol.skill.type import Output
from dimos.robot.gps.gpsd_connection import GPSdConnection
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


def _add_disposable(composite: CompositeDisposable, item: Disposable | Any) -> None:
    """Add a disposable to a composite disposable."""
    if isinstance(item, Disposable):
        composite.add(item)
    elif callable(item):
        composite.add(Disposable(item))


class GPSModule(Module):
    """Module that provides GPS location data from gpsd."""

    # Outputs
    gps_location: Out[LatLon]  # GPS coordinates (lat, lon, alt)
    gps_velocity: Out[Any]  # Velocity data (speed, track, climb) as JSON
    gps_quality: Out[Any]  # GPS quality metrics (satellites, DOP) as JSON

    # Internal state
    _latest_location: LatLon | None = None
    _latest_velocity: dict[str, float] = {}
    _latest_quality: dict[str, Any] = {}

    def __init__(
        self,
        host: str = "localhost",
        port: int = 2947,
        timeout: float = 5.0,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Initialize GPS module.

        Args:
            host: GPSd host address
            port: GPSd port (default 2947)
            timeout: Connection timeout in seconds
        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self.connection: GPSdConnection | None = None
        Module.__init__(self, *args, **kwargs)

    @rpc
    def start(self) -> bool:
        """Start the GPS connection and subscribe to data streams."""
        logger.info(f"Starting GPS module (gpsd at {self.host}:{self.port})")

        # Create connection to gpsd
        self.connection = GPSdConnection(host=self.host, port=self.port, timeout=self.timeout)

        # Start streaming GPS data
        if not self.connection.start_streaming():
            logger.error("Failed to start GPS streaming")
            return False

        # Subscribe to GPS location stream and publish
        _add_disposable(
            self._disposables,
            self.connection.gps_location_stream().subscribe(self._publish_location),
        )

        # Subscribe to GPS velocity stream and publish
        _add_disposable(
            self._disposables,
            self.connection.gps_velocity_stream().subscribe(self._publish_velocity),
        )

        # Subscribe to GPS quality stream and publish
        _add_disposable(
            self._disposables,
            self.connection.gps_quality_stream().subscribe(self._publish_quality),
        )

        logger.info("GPS module started successfully")
        return True

    @rpc
    def stop(self) -> None:
        """Stop the GPS module."""
        logger.info("Stopping GPS module")

        # Stop GPS streaming
        if self.connection:
            self.connection.stop_streaming()

        logger.info("GPS module stopped")

    def _publish_location(self, location: LatLon) -> None:
        """Publish GPS location.

        Args:
            location: LatLon object with GPS coordinates
        """
        self._latest_location = location
        self.gps_location.publish(location)
        logger.debug(f"GPS location: lat={location.lat:.6f}, lon={location.lon:.6f}")

    def _publish_velocity(self, velocity: dict[str, float]) -> None:
        """Publish GPS velocity data.

        Args:
            velocity: Dictionary with speed, track, climb
        """
        self._latest_velocity = velocity
        velocity_str = String(json.dumps(velocity))
        self.gps_velocity.publish(velocity_str)
        logger.debug(
            f"GPS velocity: speed={velocity.get('speed', 0):.2f} m/s, "
            f"track={velocity.get('track', 0):.1f}°"
        )

    def _publish_quality(self, quality: dict[str, Any]) -> None:
        """Publish GPS quality metrics.

        Args:
            quality: Dictionary with satellite count and DOP values
        """
        self._latest_quality = quality
        quality_str = String(json.dumps(quality))
        self.gps_quality.publish(quality_str)
        logger.debug(
            f"GPS quality: {quality.get('satellites_used', 0)}/{quality.get('satellites', 0)} sats, "
            f"HDOP={quality.get('hdop', 0):.1f}"
        )

    @rpc
    def get_location(self) -> LatLon | None:
        """Get the most recent GPS location.

        Returns:
            Latest LatLon or None if no GPS fix
        """
        return self._latest_location

    @rpc
    def get_velocity(self) -> dict[str, float]:
        """Get the most recent velocity data.

        Returns:
            Dictionary with speed (m/s), track (degrees), climb (m/s)
        """
        return self._latest_velocity.copy()

    @rpc
    def get_quality(self) -> dict[str, Any]:
        """Get the most recent GPS quality data.

        Returns:
            Dictionary with satellite count and DOP values
        """
        return self._latest_quality.copy()

    @skill()
    def get_gps_info(self) -> str:
        """Get current GPS status and location information.

        Returns:
            Human-readable GPS status string
        """
        if self._latest_location is None:
            return "GPS: No fix available"

        location = self._latest_location
        velocity = self._latest_velocity
        quality = self._latest_quality

        info_parts = [
            f"GPS Location: {location.lat:.6f}°, {location.lon:.6f}°",
        ]

        if location.alt is not None:
            info_parts.append(f"Altitude: {location.alt:.1f}m")

        if velocity:
            speed = velocity.get("speed", 0)
            track = velocity.get("track", 0)
            info_parts.append(f"Speed: {speed:.1f} m/s ({speed * 3.6:.1f} km/h)")
            info_parts.append(f"Heading: {track:.1f}°")

        if quality:
            sats = quality.get("satellites", 0)
            sats_used = quality.get("satellites_used", 0)
            hdop = quality.get("hdop")
            info_parts.append(f"Satellites: {sats_used}/{sats}")
            if hdop is not None:
                info_parts.append(f"HDOP: {hdop:.1f}")

        return "\n".join(info_parts)

    @skill()
    def observe_gps(self) -> Output:
        """Observe the current GPS location and status.

        Returns:
            GPS information output
        """
        return Output(self.get_gps_info())
