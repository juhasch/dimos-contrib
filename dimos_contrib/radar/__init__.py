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

"""Radar sensor integration for dimos.

This package provides integration for TI xWR68xx mmWave radar sensors via the
radarbridge ZMQ interface. It includes modules for connecting to the radar,
receiving point cloud data, and publishing to LCM.

Example:
    >>> from dimos.core import DimosCluster, start
    >>> from dimos.robot.radar import deploy_radar
    >>>
    >>> dimos = DimosCluster()
    >>> radar = deploy_radar(dimos, bridge_ip="192.168.0.197")
    >>> radar.start()
    >>> start()
"""

from dimos.robot.radar.connection_module import (
    RadarConnectionModule,
    RadarConnectionModuleConfig,
)
from dimos.robot.radar.deploy import deploy_radar

__all__ = [
    "RadarConnectionModule",
    "RadarConnectionModuleConfig",
    "deploy_radar",
]
