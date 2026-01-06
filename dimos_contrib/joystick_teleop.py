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

"""
Joystick teleop script using pygame to read analog axes and send movement commands.

Analog axes mapping:
    Axis 0: Turn left/right (angular.z)
    Axis 1: Move forward/backward (linear.x)
    Axis 3: Move left/right/strafe (linear.y)

Usage:
    python examples/joystick_teleop.py [--headless]

Options:
    --headless    Run without GUI (works in background)

Controls:
    Joystick analog sticks control movement
    Button 0 (usually X/A): Emergency stop
    ESC or close window: Quit (GUI mode only)
    Ctrl+C: Quit
"""

import argparse
import os
import sys
import time

try:
    import pygame
except ImportError:
    print("ERROR: pygame not installed. Install with: pip install pygame")
    sys.exit(1)

from dimos.core import LCMTransport
from dimos.msgs.geometry_msgs import Twist, Vector3


class JoystickTeleop:
    """Joystick teleop controller using pygame."""

    def __init__(self, headless: bool = False) -> None:
        """Initialize the joystick teleop controller.

        Args:
            headless: If True, run without GUI (works in background)
        """
        self.running = True
        self.headless = headless
        self.joystick: pygame.joystick.Joystick | None = None
        self.screen: pygame.Surface | None = None
        self.clock: pygame.time.Clock | None = None
        self.font: pygame.font.Font | None = None
        self.transport = LCMTransport("/cmd_vel", Twist)

        # Maximum velocities (reduced for smoother, less aggressive control)
        self.max_linear_vel = 0.5  # m/s (reduced from 1.0)
        self.max_angular_vel = 0.5  # rad/s (reduced from 1.5)

        # Deadzone for analog sticks (to prevent drift)
        self.deadzone = 0.05

        # Track previous joystick state to detect transitions
        self.was_active = False

        # Track previous button state for emergency stop
        self.prev_button_0 = False

    def initialize(self) -> bool:
        """Initialize pygame and joystick."""
        # Set video driver for headless mode
        if self.headless:
            os.environ["SDL_VIDEODRIVER"] = "dummy"

        pygame.init()

        # Initialize joystick
        pygame.joystick.init()

        # Check if any joystick is connected
        joystick_count = pygame.joystick.get_count()
        if joystick_count == 0:
            print("ERROR: No joystick/gamepad detected!")
            print("Please connect a joystick or gamepad and try again.")
            return False

        # Use the first joystick
        self.joystick = pygame.joystick.Joystick(0)
        self.joystick.init()

        print(f"Joystick initialized: {self.joystick.get_name()}")
        print(f"  Axes: {self.joystick.get_numaxes()}")
        print(f"  Buttons: {self.joystick.get_numbuttons()}")

        # Create display window (only in GUI mode)
        if not self.headless:
            # Force X11 driver to avoid OpenGL threading issues
            self.screen = pygame.display.set_mode((500, 400), pygame.SWSURFACE)
            pygame.display.set_caption("Joystick Teleop")
            self.font = pygame.font.Font(None, 24)

        self.clock = pygame.time.Clock()

        return True

    def apply_deadzone(self, value: float) -> float:
        """Apply deadzone to analog stick value."""
        if abs(value) < self.deadzone:
            return 0.0
        # Scale value to remove deadzone
        sign = 1.0 if value >= 0 else -1.0
        return sign * (abs(value) - self.deadzone) / (1.0 - self.deadzone)

    def read_joystick(self) -> tuple[Twist, bool]:
        """Read joystick axes and create a Twist message.
        
        Returns:
            Tuple of (Twist message, has_input) where has_input is True if joystick is active
        """
        if self.joystick is None:
            return Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, 0)), False
        
        # Read analog axes
        # Axis 0: Turn left/right (angular.z)
        # Negative because pygame axis 0 is often inverted for rotation
        turn_raw = -self.joystick.get_axis(0)
        angular_z = self.apply_deadzone(turn_raw) * self.max_angular_vel
        
        # Axis 1: Move forward/backward (linear.x)
        # Negative because pygame axis 1 is often inverted
        forward_raw = -self.joystick.get_axis(1)
        linear_x = self.apply_deadzone(forward_raw) * self.max_linear_vel
        
        # Axis 3: Move left/right/strafe (linear.y)
        # Map to linear.y for strafing (inverted so left stick = right movement)
        strafe_raw = 0.0
        if self.joystick.get_numaxes() > 3:
            strafe_raw = -self.joystick.get_axis(3)  # Inverted
        linear_y = self.apply_deadzone(strafe_raw) * self.max_linear_vel
        
        # Check if there's any actual input (above deadzone)
        has_input = (
            abs(linear_x) > 0.01
            or abs(linear_y) > 0.01
            or abs(angular_z) > 0.01
        )
        
        # Create Twist message
        twist = Twist(
            linear=Vector3(linear_x, linear_y, 0.0),
            angular=Vector3(0.0, 0.0, angular_z),
        )
        
        return twist, has_input

    def send_stop_command(self) -> None:
        """Send a stop command to the robot."""
        stop_twist = Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, 0))
        self.transport.broadcast(None, stop_twist)

    def update_display(self, twist: Twist) -> None:
        """Update the pygame display with current status."""
        if self.screen is None or self.font is None or self.joystick is None:
            return
        
        self.screen.fill((30, 30, 30))
        
        y_pos = 20
        
        # Title
        title_text = self.font.render("Joystick Teleop", True, (0, 255, 255))
        self.screen.blit(title_text, (20, y_pos))
        y_pos += 35
        
        # Joystick info
        joystick_name = self.font.render(
            f"Device: {self.joystick.get_name()}", True, (200, 200, 200)
        )
        self.screen.blit(joystick_name, (20, y_pos))
        y_pos += 30
        
        # Velocity values
        texts = [
            f"Linear X (Forward/Back): {twist.linear.x:+.2f} m/s",
            f"Linear Y (Strafe L/R):   {twist.linear.y:+.2f} m/s",
            f"Angular Z (Turn L/R):    {twist.angular.z:+.2f} rad/s",
        ]
        
        for text in texts:
            surf = self.font.render(text, True, (255, 255, 255))
            self.screen.blit(surf, (20, y_pos))
            y_pos += 30
        
        # Status indicator
        y_pos += 10
        is_moving = (
            abs(twist.linear.x) > 0.01
            or abs(twist.linear.y) > 0.01
            or abs(twist.angular.z) > 0.01
        )
        
        if is_moving:
            pygame.draw.circle(self.screen, (255, 0, 0), (450, 30), 15)  # Red = moving
            status_text = self.font.render("MOVING", True, (255, 0, 0))
        else:
            pygame.draw.circle(self.screen, (0, 255, 0), (450, 30), 15)  # Green = stopped
            status_text = self.font.render("STOPPED", True, (0, 255, 0))
        
        self.screen.blit(status_text, (370, 20))
        
        # Help text
        y_pos = 300
        help_texts = [
            "Axis 0: Turn | Axis 1: Forward/Back | Axis 3: Strafe",
            "Button 0: Emergency Stop | ESC: Quit",
            "Note: Commands only sent when joystick is active",
        ]
        for text in help_texts:
            surf = self.font.render(text, True, (150, 150, 150))
            self.screen.blit(surf, (20, y_pos))
            y_pos += 25
        
        pygame.display.flip()

    def run(self) -> None:
        """Main control loop."""
        if not self.initialize():
            return

        print("\n" + "=" * 60)
        print(f"Joystick Teleop Started ({'Headless' if self.headless else 'GUI'} Mode)")
        print("=" * 60)
        print("Controls:")
        print("  Axis 0: Turn left/right")
        print("  Axis 1: Move forward/backward")
        print("  Axis 3: Strafe left/right")
        print("  Button 0: Emergency stop")
        if not self.headless:
            print("  ESC or close window: Quit")
        print("  Ctrl+C: Quit")
        print("")
        print("NOTE: Commands are only sent when joystick is actively moved.")
        print("      When idle, other commands (e.g., human CLI) can control the robot.")
        if self.headless:
            print("")
            print("Running in headless mode - works even when window is not active!")
        print("=" * 60 + "\n")

        try:
            while self.running:
                # Update pygame's internal state
                # In headless mode, use pump() which doesn't require window focus
                # In GUI mode, process events normally
                if self.headless:
                    pygame.event.pump()
                else:
                    # Handle pygame events (GUI mode only)
                    for event in pygame.event.get():
                        if event.type == pygame.QUIT:
                            self.running = False
                        elif event.type == pygame.KEYDOWN:
                            if event.key == pygame.K_ESCAPE:
                                self.running = False

                # Poll button state directly (works in both modes)
                if self.joystick is not None and self.joystick.get_numbuttons() > 0:
                    button_0 = self.joystick.get_button(0)
                    # Detect button press (transition from not pressed to pressed)
                    if button_0 and not self.prev_button_0:
                        print("EMERGENCY STOP!")
                        self.send_stop_command()
                        time.sleep(0.1)
                    self.prev_button_0 = button_0

                # Read joystick and send command
                twist, has_input = self.read_joystick()

                # Send commands when joystick is active
                if has_input:
                    self.transport.broadcast(None, twist)
                    self.was_active = True
                # When transitioning from active to idle, send one zero command to stop the robot
                elif self.was_active:
                    # Transition from active to idle - send stop command once
                    stop_twist = Twist(linear=Vector3(0, 0, 0), angular=Vector3(0, 0, 0))
                    self.transport.broadcast(None, stop_twist)
                    self.was_active = False
                    # Note: After this one zero command, we stop sending commands
                    # This allows other sources (e.g., human CLI) to control the robot when joystick is idle

                # Update display (GUI mode only)
                if not self.headless:
                    self.update_display(twist)
                elif has_input:
                    # In headless mode, print occasional status updates when moving
                    print(f"\rLinear: [{twist.linear.x:+.2f}, {twist.linear.y:+.2f}] | "
                          f"Angular: {twist.angular.z:+.2f}", end="", flush=True)

                # Maintain 50Hz rate (same as keyboard teleop)
                if self.clock is None:
                    raise RuntimeError("Clock not initialized")
                self.clock.tick(50)

        except KeyboardInterrupt:
            print("\nInterrupted by user")
        finally:
            # Cleanup
            print("\nSending stop command...")
            self.send_stop_command()
            time.sleep(0.1)
            pygame.quit()
            print("Joystick teleop stopped.")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Joystick teleop for robot control"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without GUI (works in background)",
    )
    args = parser.parse_args()

    controller = JoystickTeleop(headless=args.headless)
    controller.run()


if __name__ == "__main__":
    main()

