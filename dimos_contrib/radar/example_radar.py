#!/usr/bin/env python3

# Copyright 2025-2026 Dimensional Inc.
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

"""Example script for running radar point cloud streaming.

This script demonstrates how to:
1. Connect directly to the radar bridge
2. Subscribe to radar point cloud stream
3. Publish to LCM topics for visualization

Prerequisites:
    - radarbridge running at 192.168.0.197 (or configured IP)
    - xwr68xxisk package installed
    - Radar sensor connected to the radarbridge

Usage:
    python example_radar.py

    Then open Foxglove Studio and connect to the LCM websocket to view:
    - /radar/point_cloud - Radar point cloud data
    - /radar/info - Radar configuration and status
    - /tf - Transform from base_link to radar frame

Environment Variables:
    RADAR_IP: IP address of radarbridge (default: 192.168.0.197)
"""

import os
import time
import math
import threading

from dimos.core import LCMTransport
from dimos.msgs.geometry_msgs import Quaternion, Transform, Vector3
from dimos.msgs.sensor_msgs import RadarInfo, RadarPointCloud
from dimos.msgs.tf2_msgs import TFMessage

# Configuration
BRIDGE_IP = os.getenv("RADAR_IP", "192.168.0.90")
RADAR_CONFIG_FILE = "/home/juhasch/git/xwr68xxisk/configs/user_profile.cfg"
RADAR_X = 0.2  # 20cm forward from base_link
RADAR_Y = 0.0  # centered
RADAR_Z = 0.1  # 10cm up
RADAR_ROLL = 0.0  # Orientation (radians)
RADAR_PITCH = 0.0
RADAR_YAW = 0.0
INFO_PUBLISH_INTERVAL = 5.0  # Publish RadarInfo every N seconds

# Create transports for publishing
point_cloud_transport = LCMTransport("/radar/point_cloud", RadarPointCloud)
info_transport = LCMTransport("/radar/info", RadarInfo)
tf_transport = LCMTransport("/tf", TFMessage)


def euler_to_quaternion(roll: float, pitch: float, yaw: float) -> Quaternion:
    """Convert Euler angles to quaternion."""
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)

    qw = cr * cp * cy + sr * sp * sy
    qx = sr * cp * cy - cr * sp * sy
    qy = cr * sp * cy + sr * cp * sy
    qz = cr * cp * sy - sr * sp * cy

    return Quaternion(qx, qy, qz, qw)


def create_radar_transform() -> Transform:
    """Create static transform from base_link to radar frame."""
    return Transform(
        translation=Vector3(RADAR_X, RADAR_Y, RADAR_Z),
        rotation=euler_to_quaternion(RADAR_ROLL, RADAR_PITCH, RADAR_YAW),
        frame_id="base_link",
        child_frame_id="radar",
        ts=time.time(),
    )


def publish_tf() -> None:
    """Publish transform from base_link to radar."""
    transform = create_radar_transform()
    tf_transport.broadcast(None, TFMessage(transform))


def main() -> None:
    """Main function to run radar streaming example."""
    # Import here to avoid dependency issues if xwr68xxisk is not installed
    try:
        from xwr68xxisk.radar import RadarBridgeConnection
    except ImportError as e:
        print(
            f"Failed to import xwr68xxisk: {e}\n"
            "Please install it with: pip install -e /path/to/xwr68xxisk"
        )
        return

    # Auto-generate endpoints
    control_endpoint = f"tcp://{BRIDGE_IP}:5557"
    data_endpoint = f"tcp://{BRIDGE_IP}:5556"

    print("Connecting to radar bridge...")
    print(f"  Bridge IP: {BRIDGE_IP}")
    print(f"  Control endpoint: {control_endpoint}")
    print(f"  Data endpoint: {data_endpoint}")

    # Create radar connection
    try:
        connection = RadarBridgeConnection(
            control_endpoint=control_endpoint,
            data_endpoint=data_endpoint,
            control_timeout_ms=1000,
            data_timeout_ms=1000,
        )

        # Connect with config file if provided
        if RADAR_CONFIG_FILE:
            print(f"Using radar config file: {RADAR_CONFIG_FILE}")
            connection.connect(RADAR_CONFIG_FILE)
        else:
            print("Warning: No radar_config_file provided. Attempting to connect with empty config.")
            connection.connect("")

        # Configure and start the radar to begin streaming
        connection.configure_and_start()

        print("Radar connection established, configured, and started")

    except Exception as e:
        print(f"Failed to connect to radar: {e}")
        return

    # Import RadarData from xwr68xxisk
    from xwr68xxisk.parse import RadarData

    # Data acquisition loop
    def data_acquisition_loop():
        """Main data acquisition loop running in separate thread."""
        try:
            while True:
                try:
                    # Get radar data (this automatically reads and parses the frame)
                    radar_data = RadarData(connection)
                    
                    if radar_data and radar_data.pc is not None and len(radar_data.pc[0]) > 0:
                        # Convert to xwr68xxisk RadarPointCloud
                        xwr_pc = radar_data.to_point_cloud()
                        
                        # Convert to dimos RadarPointCloud
                        dimos_pc = RadarPointCloud.from_xwr68xxisk(xwr_pc, frame_id="radar", ts=time.time())
                        
                        # Publish point cloud
                        point_cloud_transport.broadcast(None, dimos_pc)
                        
                        # Publish transform
                        publish_tf()
                    
                    # Small delay to prevent overwhelming the system
                    time.sleep(0.001)
                    
                except Exception as e:
                    print(f"Error in data acquisition: {e}")
                    time.sleep(0.1)
                    
        except Exception as e:
            print(f"Error in data acquisition loop: {e}")

    # Start data acquisition thread
    data_thread = threading.Thread(target=data_acquisition_loop, daemon=True)
    data_thread.start()

    # Publish RadarInfo periodically
    last_info_time = 0

    def publish_info() -> None:
        """Publish radar info message."""
        try:
            info = RadarInfo.from_radar_config(connection, frame_id="radar", ts=time.time())
            info_transport.broadcast(None, info)
        except ValueError as e:
            print(f"Error publishing radar info: {e}")

    print("\nRadar started successfully!")
    print("Publishing to:")
    print("  - /radar/point_cloud (RadarPointCloud)")
    print("  - /radar/info (RadarInfo)")
    print("  - /tf (TFMessage)")
    print("\nOpen Foxglove Studio to visualize the data.")
    print("Press Ctrl+C to stop.\n")

    # Publish initial info
    publish_info()

    try:
        # Main loop
        while True:
            current_time = time.time()
            # Publish info periodically
            if current_time - last_info_time >= INFO_PUBLISH_INTERVAL:
                publish_info()
                last_info_time = current_time
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopping radar...")
        try:
            # Stop the radar
            connection.stop()
            connection.close()
            print("Radar stopped.")
        except Exception as e:
            print(f"Error stopping radar: {e}")


if __name__ == "__main__":
    main()
