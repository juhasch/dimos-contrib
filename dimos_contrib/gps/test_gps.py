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

"""Test script for GPS module - verifies GPS connection and data."""

import time
import logging
from dimos.robot.gps import GPSModule
from dimos.core import core
from dimos.mapping.types import LatLon
from dimos.utils.logging_config import setup_logger

logger = setup_logger(level=logging.INFO)


def test_gps_module(host: str = "localhost", port: int = 2947, duration: int = 30):
    """Test GPS module for a specified duration.

    Args:
        host: GPSd host address
        port: GPSd port
        duration: Test duration in seconds
    """
    logger.info("=" * 60)
    logger.info("GPS Module Test")
    logger.info("=" * 60)
    logger.info(f"Connecting to gpsd at {host}:{port}")
    logger.info(f"Test duration: {duration} seconds")
    logger.info("")

    # Create DimOS instance
    dimos = core.DimOS()

    # Deploy GPS module
    gps = dimos.deploy(GPSModule, host=host, port=port)

    # Set up transports
    gps.gps_location.transport = core.LCMTransport("/gps_location", LatLon)

    # Start the module
    logger.info("Starting GPS module...")
    if not gps.start():
        logger.error("Failed to start GPS module!")
        logger.error("Make sure gpsd is running: sudo systemctl status gpsd")
        return False

    logger.info("GPS module started successfully!")
    logger.info("")
    logger.info("Waiting for GPS data...")
    logger.info("(This may take 30-60 seconds for initial satellite lock)")
    logger.info("")

    # Monitor GPS data for the specified duration
    start_time = time.time()
    last_location = None
    update_count = 0

    try:
        while time.time() - start_time < duration:
            location = gps.get_location()
            velocity = gps.get_velocity()
            quality = gps.get_quality()

            # Check if we got a new location
            if location and location != last_location:
                update_count += 1
                last_location = location

                logger.info("-" * 60)
                logger.info(f"GPS Update #{update_count} (at {time.time() - start_time:.1f}s)")
                logger.info(f"  Location: {location.lat:.8f}°, {location.lon:.8f}°")

                if location.alt is not None:
                    logger.info(f"  Altitude: {location.alt:.1f} m")

                if velocity:
                    speed = velocity.get("speed", 0)
                    track = velocity.get("track", 0)
                    climb = velocity.get("climb", 0)
                    logger.info(
                        f"  Velocity: {speed:.2f} m/s ({speed * 3.6:.1f} km/h) @ {track:.1f}°"
                    )
                    if climb != 0:
                        logger.info(f"  Climb: {climb:.2f} m/s")

                if quality:
                    sats = quality.get("satellites", 0)
                    sats_used = quality.get("satellites_used", 0)
                    hdop = quality.get("hdop")
                    vdop = quality.get("vdop")
                    pdop = quality.get("pdop")

                    quality_str = f"  Satellites: {sats_used}/{sats}"
                    if hdop is not None:
                        quality_str += f" | HDOP: {hdop:.2f}"
                    if vdop is not None:
                        quality_str += f" | VDOP: {vdop:.2f}"
                    if pdop is not None:
                        quality_str += f" | PDOP: {pdop:.2f}"

                    logger.info(quality_str)

                    # Interpret HDOP
                    if hdop is not None:
                        if hdop < 2:
                            quality_desc = "Excellent"
                        elif hdop < 5:
                            quality_desc = "Good"
                        elif hdop < 10:
                            quality_desc = "Moderate"
                        elif hdop < 20:
                            quality_desc = "Fair"
                        else:
                            quality_desc = "Poor"
                        logger.info(f"  GPS Quality: {quality_desc}")

            elif not location:
                # Still waiting for fix
                if update_count == 0 and int(time.time() - start_time) % 5 == 0:
                    logger.info(f"Still waiting for GPS fix... ({int(time.time() - start_time)}s)")

            time.sleep(0.5)

    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")

    finally:
        # Stop the module
        logger.info("")
        logger.info("=" * 60)
        logger.info("Test Summary")
        logger.info("=" * 60)
        logger.info(f"Total GPS updates received: {update_count}")
        logger.info(f"Test duration: {time.time() - start_time:.1f} seconds")

        if last_location:
            logger.info(f"Last known location: {last_location.lat:.8f}°, {last_location.lon:.8f}°")
            logger.info("")
            logger.info("GPS Test: PASSED ✓")
        else:
            logger.warning("No GPS fix obtained during test")
            logger.warning("")
            logger.warning("GPS Test: FAILED ✗")
            logger.warning("")
            logger.warning("Troubleshooting:")
            logger.warning("  1. Check gpsd is running: sudo systemctl status gpsd")
            logger.warning("  2. Test gpsd directly: cgps or gpsmon")
            logger.warning("  3. Ensure GPS device is connected: ls /dev/tty*")
            logger.warning("  4. Move to location with clear sky view")
            logger.warning("  5. Wait longer for initial satellite lock (can take 60+ seconds)")

        logger.info("")
        logger.info("Stopping GPS module...")
        gps.stop()
        logger.info("Done!")

    return last_location is not None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test GPS module")
    parser.add_argument(
        "--host", default="localhost", help="GPSd host address (default: localhost)"
    )
    parser.add_argument("--port", type=int, default=2947, help="GPSd port (default: 2947)")
    parser.add_argument(
        "--duration", type=int, default=30, help="Test duration in seconds (default: 30)"
    )

    args = parser.parse_args()

    success = test_gps_module(host=args.host, port=args.port, duration=args.duration)
    exit(0 if success else 1)
