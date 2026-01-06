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

import os

from dimos.core import DimosCluster, LCMTransport
from dimos.msgs.sensor_msgs import RadarInfo, RadarPointCloud
from dimos.robot.radar.connection_module import RadarConnectionModule


def deploy_radar(dimos: DimosCluster, **kwargs):  # type: ignore[no-untyped-def]
    """Deploy radar connection module with LCM transports.

    This function deploys the RadarConnectionModule and configures LCM transports
    for publishing radar point cloud and info messages.

    Args:
        dimos: DimosCluster instance
        **kwargs: Additional configuration parameters passed to RadarConnectionModule

    Returns:
        RadarConnectionModule instance

    Example:
        >>> from dimos.core import start
        >>> from dimos.robot.radar import deploy_radar
        >>>
        >>> dimos = start()
        >>> radar = deploy_radar(dimos, bridge_ip="192.168.0.90")
        >>> radar.start()
        >>> start()
    """
    # Deploy the radar connection module
    # Use bridge_ip from kwargs if provided, otherwise fall back to environment variable or default
    bridge_ip = kwargs.pop("bridge_ip", os.getenv("RADAR_IP", "192.168.0.90"))
    radar_connection = dimos.deploy(
        RadarConnectionModule,
        bridge_ip=bridge_ip,
        **kwargs,
    )

    # Configure LCM transports for radar outputs
    radar_connection.radar_point_cloud.transport = LCMTransport(
        "/radar/point_cloud", RadarPointCloud
    )
    radar_connection.radar_info.transport = LCMTransport("/radar/info", RadarInfo)

    return radar_connection
