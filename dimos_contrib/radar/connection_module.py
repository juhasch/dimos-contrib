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

from dataclasses import dataclass
import logging
import time

import reactivex as rx
from reactivex import operators as ops

from dimos.core import Module, ModuleConfig, Out, rpc
from dimos.msgs.geometry_msgs import Quaternion, Transform, Vector3
from dimos.msgs.sensor_msgs import RadarInfo, RadarPointCloud
from dimos.utils.logging_config import setup_logger

logger = setup_logger(level=logging.INFO)


@dataclass
class RadarConnectionModuleConfig(ModuleConfig):
    """Configuration for radar connection module."""

    bridge_ip: str = "192.168.0.90"
    control_endpoint: str | None = None  # Auto-generated from bridge_ip if None
    data_endpoint: str | None = None  # Auto-generated from bridge_ip if None
    control_timeout_ms: int = 1000
    data_timeout_ms: int = 1000
    radar_config_file: str | None = None  # Path to radar config file (optional)
    radar_x: float = 0.2  # Position relative to base_link (meters)
    radar_y: float = 0.0
    radar_z: float = 0.1
    radar_roll: float = 0.0  # Orientation (radians)
    radar_pitch: float = 0.0
    radar_yaw: float = 0.0
    target_frame: str = "odom"
    info_publish_interval: float = 5.0  # Publish RadarInfo every N seconds


class RadarConnectionModule(Module):
    """Module for connecting to radar via radarbridge and publishing to LCM.

    This module connects to the radar sensor through the radarbridge ZMQ interface,
    receives radar point cloud data, and publishes it to LCM for consumption by
    other dimos modules.

    Outputs:
        radar_point_cloud: Radar point cloud data with Cartesian and spherical coordinates
        radar_info: Radar sensor configuration and status information
    """

    radar_point_cloud: Out[RadarPointCloud]
    radar_info: Out[RadarInfo]

    connection = None  # RadarBridgeConnection from xwr68xxisk
    default_config = RadarConnectionModuleConfig

    @rpc
    def start(self) -> None:
        """Start the radar connection and subscribe to sensor streams."""
        super().start()

        # Import here to avoid dependency issues if xwr68xxisk is not installed
        try:
            from xwr68xxisk.radar import RadarBridgeConnection
        except ImportError as e:
            logger.error(
                "Failed to import xwr68xxisk. Please install it with: pip install -e /path/to/xwr68xxisk"
            )
            raise e

        config: RadarConnectionModuleConfig = self.config  # type: ignore[assignment]

        # Auto-generate endpoints if not provided
        control_endpoint = config.control_endpoint or f"tcp://{config.bridge_ip}:5557"
        data_endpoint = config.data_endpoint or f"tcp://{config.bridge_ip}:5556"

        logger.info(f"Connecting to radar bridge at {config.bridge_ip}")
        logger.info(f"  Control endpoint: {control_endpoint}")
        logger.info(f"  Data endpoint: {data_endpoint}")

        # Create radar connection
        try:
            self.connection = RadarBridgeConnection(
                control_endpoint=control_endpoint,
                data_endpoint=data_endpoint,
                control_timeout_ms=config.control_timeout_ms,
                data_timeout_ms=config.data_timeout_ms,
            )

            # Connect and start radar
            # RadarBridgeConnection.connect() requires config: str (path to config file or config content)
            # The config parameter can be either:
            # 1. A path to a configuration file (string)
            # 2. A configuration string (the actual config content)
            
            radar_config: str | None = None
            
            # Use config file if provided
            if config.radar_config_file:
                radar_config = config.radar_config_file
                logger.info(f"Using radar config file: {radar_config}")
            else:
                # If no config file provided, try using empty string
                # Some bridges might use their current configuration if already configured
                logger.warning(
                    "No radar_config_file provided. Attempting to connect with empty config string. "
                    "If the bridge is not pre-configured, this will fail. "
                    "Provide radar_config_file parameter to specify a config file."
                )
                radar_config = ""  # Try empty string - bridge might use current config
            
            # Connect with the config (can be file path or config string)
            # connect() handles both connection and configuration
            self.connection.connect(radar_config)

            logger.info("Radar connection established and configured")

        except Exception as e:
            logger.error(f"Failed to connect to radar: {e}")
            raise

        # Subscribe to point cloud stream
        unsub = (
            self.connection.point_cloud_stream()
            .pipe(
                # Convert xwr68xxisk.RadarPointCloud to dimos.RadarPointCloud
                ops.map(
                    lambda pc: RadarPointCloud.from_xwr68xxisk(pc, frame_id="radar", ts=time.time())
                ),
                # Publish transforms for each point cloud
                ops.do_action(lambda _: self._publish_tf()),
            )
            .subscribe(
                on_next=self.radar_point_cloud.publish,
                on_error=lambda e: logger.error(f"Error in radar point cloud stream: {e}"),
            )
        )
        self._disposables.add(unsub)

        # Publish RadarInfo periodically
        def publish_info(_: int) -> None:
            """Publish radar info message."""
            try:
                info = RadarInfo.from_radar_config(
                    self.connection, frame_id="radar", ts=time.time()
                )
                #self.radar_info.publish(info)
            except Exception as e:
                logger.error(f"Error publishing radar info: {e}")

        unsub_info = (
            rx.interval(config.info_publish_interval)
            .pipe(
                ops.start_with(0),  # Publish immediately on start
            )
            .subscribe(
                on_next=publish_info,
                on_error=lambda e: logger.error(f"Error in radar info stream: {e}"),
            )
        )
        self._disposables.add(unsub_info)

        logger.info("Radar streams initialized")

    @rpc
    def stop(self) -> None:
        """Stop the radar and close the connection."""
        if self.connection:
            try:
                self.connection.stop()
                self.connection.close()
                logger.info("Radar connection stopped")
            except Exception as e:
                logger.error(f"Error stopping radar: {e}")

        super().stop()

    def _publish_tf(self) -> None:
        """Publish static transform from base_link to radar."""
        config: RadarConnectionModuleConfig = self.config  # type: ignore[assignment]

        transform = self._radar_to_tf(config)
        self.tf.publish(transform)

    @classmethod
    def _radar_to_tf(cls, config: RadarConnectionModuleConfig) -> Transform:
        """Create static transform for radar position.

        Args:
            config: Module configuration with radar position and orientation

        Returns:
            Transform from base_link to radar frame
        """
        # Convert Euler angles to quaternion (roll, pitch, yaw)
        # For small angles, this approximation is sufficient
        # For more accuracy, use scipy.spatial.transform.Rotation
        import math

        roll, pitch, yaw = config.radar_roll, config.radar_pitch, config.radar_yaw

        # Simplified quaternion conversion (for small angles)
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

        return Transform(
            translation=Vector3(config.radar_x, config.radar_y, config.radar_z),
            rotation=Quaternion(qx, qy, qz, qw),
            frame_id="base_link",
            child_frame_id="radar",
            ts=time.time(),
        )
