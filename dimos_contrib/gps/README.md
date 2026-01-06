# GPS Module for DimOS

This module provides GPS location data from gpsd for outdoor navigation in DimOS.

## Overview

The GPS module connects to a running gpsd daemon and publishes:
- **GPS Location**: Latitude, longitude, and altitude (via `LatLon` type)
- **GPS Velocity**: Speed (m/s), track/heading (degrees), and climb rate (m/s)
- **GPS Quality**: Number of satellites, satellites in use, and DOP (dilution of precision) metrics

## Prerequisites

### Install gpsd

**Ubuntu/Debian:**
```bash
sudo apt-get install gpsd gpsd-clients
```

**macOS:**
```bash
brew install gpsd
```

### Start gpsd

Connect your GPS device (e.g., USB GPS dongle) and start gpsd:

```bash
# Find your GPS device (usually /dev/ttyUSB0 or /dev/ttyACM0)
ls /dev/tty*

# Start gpsd (replace /dev/ttyUSB0 with your GPS device)
sudo gpsd /dev/ttyUSB0 -F /var/run/gpsd.sock

# Or start as a service
sudo systemctl start gpsd
```

### Verify gpsd is working

```bash
# Test with cgps (curses GPS client)
cgps

# Or use gpsmon
gpsmon

# Or check with gpspipe
gpspipe -w
```

You should see GPS data streaming if everything is working correctly.

## Usage

### Basic Integration

```python
from dimos.robot.gps import GPSModule
from dimos.core import core

# Create and deploy GPS module
dimos = core.DimOS()
gps = dimos.deploy(GPSModule, host="localhost", port=2947)

# Set up transports for GPS data
gps.gps_location.transport = core.LCMTransport("/gps_location", LatLon)
gps.gps_velocity.transport = core.LCMTransport("/gps_velocity", dict)
gps.gps_quality.transport = core.LCMTransport("/gps_quality", dict)

# Start the module
gps.start()
```

### Integration with GPS Navigation Skill

To use GPS navigation with the `gps_nav_skill`:

```python
from dimos.agents.skills.gps_nav_skill import gps_nav_skill
from dimos.robot.gps import GPSModule
from dimos.core import autoconnect

# Connect GPS module to navigation skill
gps_module = GPSModule(host="localhost", port=2947)
gps_nav = gps_nav_skill()

system = autoconnect(
    gps_module,      # Publishes GPS location
    gps_nav,         # Consumes GPS location for navigation
    # ... other modules
)
```

### Integration with Existing Robot

If you have an existing robot module, you can add GPS alongside it:

```python
from dimos.robot.unitree.connection import GO2Connection
from dimos.robot.gps import GPSModule
from dimos.agents.skills.gps_nav_skill import gps_nav_skill
from dimos.core import autoconnect

# Create robot and GPS modules
robot = GO2Connection()
gps = GPSModule()
gps_nav = gps_nav_skill()

# Connect everything together
system = autoconnect(
    robot,      # Your robot's connection module
    gps,        # GPS location provider
    gps_nav,    # GPS navigation skill
)
```

### Using GPS Skills

The GPS module provides skills for observing GPS data:

```python
# Get current GPS information
gps_info = gps.get_gps_info()
print(gps_info)
# Output:
# GPS Location: 37.780924°, -122.406829°
# Altitude: 15.2m
# Speed: 1.5 m/s (5.4 km/h)
# Heading: 45.0°
# Satellites: 8/12
# HDOP: 1.2

# Observe GPS (skill for LLM agents)
result = gps.observe_gps()
```

### Accessing GPS Data Directly

```python
# Get latest location
location = gps.get_location()
if location:
    print(f"Lat: {location.lat}, Lon: {location.lon}, Alt: {location.alt}")

# Get velocity data
velocity = gps.get_velocity()
print(f"Speed: {velocity['speed']} m/s")
print(f"Heading: {velocity['track']}°")

# Get GPS quality metrics
quality = gps.get_quality()
print(f"Satellites: {quality['satellites_used']}/{quality['satellites']}")
print(f"HDOP: {quality['hdop']}")
```

## Configuration

### Module Parameters

```python
GPSModule(
    host="localhost",    # GPSd host address
    port=2947,          # GPSd port (default is 2947)
    timeout=5.0,        # Connection timeout in seconds
)
```

### Remote gpsd

To connect to gpsd running on a different machine:

```python
gps = GPSModule(host="192.168.1.100", port=2947)
```

## Data Formats

### GPS Location (`LatLon`)

```python
@dataclass(frozen=True)
class LatLon:
    lat: float           # Latitude in decimal degrees
    lon: float           # Longitude in decimal degrees
    alt: float | None    # Altitude in meters (None if no 3D fix)
```

### GPS Velocity (JSON)

```json
{
    "speed": 1.5,      // Speed in m/s
    "track": 45.0,     // Course over ground in degrees (0-360)
    "climb": 0.2       // Vertical velocity in m/s
}
```

### GPS Quality (JSON)

```json
{
    "satellites": 12,        // Total satellites visible
    "satellites_used": 8,    // Satellites used in fix
    "hdop": 1.2,            // Horizontal dilution of precision
    "vdop": 1.8,            // Vertical dilution of precision
    "pdop": 2.1             // Position dilution of precision
}
```

## Complete Example

Here's a complete example of a robot with GPS navigation:

```python
#!/usr/bin/env python3
from dimos.core import autoconnect, core
from dimos.robot.gps import GPSModule
from dimos.agents.skills.gps_nav_skill import gps_nav_skill
from dimos.agents.skills.llm_agent import llm_agent
from dimos.agents.skills.human_input import human_input

def main():
    # Create GPS-enabled navigation system
    system = autoconnect(
        GPSModule(host="localhost", port=2947),
        gps_nav_skill(),
        human_input(),
        llm_agent(system_prompt="You are a GPS navigation assistant."),
    )

    # Start the system
    system.start()

    # Example: Navigate to a specific location
    # The LLM will use the GPS nav skill to navigate
    # User can say: "Navigate to lat: 37.7749, lon: -122.4194"

    try:
        system.wait()
    except KeyboardInterrupt:
        system.stop()

if __name__ == "__main__":
    main()
```

## Troubleshooting

### GPS module fails to start

- **Check gpsd is running**: `sudo systemctl status gpsd`
- **Test gpsd manually**: `cgps` or `gpsmon`
- **Check firewall**: Ensure port 2947 is not blocked
- **Verify GPS device**: `ls /dev/tty*` and check your GPS is connected

### No GPS fix

- **Wait for satellite lock**: Can take 30-60 seconds outdoors
- **Check satellite visibility**: `cgps` shows satellite SNR
- **Move outdoors**: GPS requires clear sky view
- **Check antenna**: Ensure GPS antenna is properly connected

### Poor GPS accuracy

- **Check HDOP**: Values < 2.0 are good, > 5.0 is poor
- **Wait for more satellites**: 8+ satellites gives best accuracy
- **Avoid obstructions**: Buildings, trees, and tunnels degrade GPS
- **Use DGPS/RTK**: For centimeter-level accuracy (requires additional hardware)

### Connection errors

- **Verify gpsd address**: Check `host` and `port` parameters
- **Check network**: If using remote gpsd, verify connectivity
- **Restart gpsd**: `sudo systemctl restart gpsd`

## gpsd Protocol Details

The module uses the gpsd JSON protocol over TCP socket (port 2947). It processes two main message types:

- **TPV (Time-Position-Velocity)**: GPS location, altitude, speed, heading
- **SKY**: Satellite information and signal quality

For more information, see: https://gpsd.io/

## Integration with Other Skills

### Google Maps Skill

Combine with `google_maps_skill` to navigate to named locations:

```python
from dimos.agents.skills.google_maps_skill import GoogleMapsSkillContainer

system = autoconnect(
    GPSModule(),
    gps_nav_skill(),
    GoogleMapsSkillContainer(),  # Convert "Fort Mason" -> GPS coords
    llm_agent(),
)

# User can say: "Navigate to Golden Gate Bridge"
# LLM will: get_gps_position_for_queries("Golden Gate Bridge")
#          -> set_gps_travel_points(lat, lon)
```

### OSM (OpenStreetMap) Skill

Use vision-based map querying:

```python
from dimos.agents.skills.osm import osm_skill

system = autoconnect(
    GPSModule(),
    gps_nav_skill(),
    osm_skill(),  # Visual map analysis
    llm_agent(),
)

# User: "Navigate to the park shown in this map" (with image)
# LLM uses map_query() to find coordinates
```

## Advanced: Custom GPS Hardware

If you have GPS hardware that isn't supported by gpsd, you can use the `GPSdConnection` class directly:

```python
from dimos.robot.gps.gpsd_connection import GPSdConnection
from dimos.mapping.types import LatLon

# Create custom GPS connection
class MyGPSConnection:
    def __init__(self):
        self._gps_subject = Subject()

    def gps_location_stream(self):
        return self._gps_subject

    def start_streaming(self):
        # Your custom GPS reading code here
        # Publish: self._gps_subject.on_next(LatLon(lat=..., lon=...))
        pass
```

## License

Copyright 2025 Dimensional Inc. Licensed under Apache 2.0.
