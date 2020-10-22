# Copyright (C) 2019 OpenMotics BV
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
Module for the frontpanel
"""
from __future__ import absolute_import

import fcntl
import logging
import time

from gateway.daemon_thread import DaemonThread
from gateway.hal.frontpanel_controller import FrontpanelController
from ioc import INJECTED, Inject
from platform_utils import Hardware

if False:  # MYPY
    from typing import Dict, Optional

logger = logging.getLogger("openmotics")


class FrontpanelClassicController(FrontpanelController):

    IOCTL_I2C_SLAVE = 0x0703
    BOARD_TYPE = Hardware.get_board_type()
    ACTION_BUTTON_GPIO = 38 if BOARD_TYPE == Hardware.BoardType.BB else 26
    BUTTON = FrontpanelController.Buttons.ACTION
    INDICATE_SEQUENCE = [True, False, False, False]
    AUTH_MODE_LEDS = [FrontpanelController.Leds.ALIVE,
                      FrontpanelController.Leds.CLOUD,
                      FrontpanelController.Leds.VPN,
                      FrontpanelController.Leds.COMMUNICATION_1,
                      FrontpanelController.Leds.COMMUNICATION_2]
    if not BOARD_TYPE == Hardware.BoardType.BB:
        GPIO_LED_CONFIG = {FrontpanelController.Leds.POWER: 60,
                           FrontpanelController.Leds.STATUS_RED: 48}
        I2C_LED_CONFIG = {FrontpanelController.Leds.COMMUNICATION_1: 64,
                          FrontpanelController.Leds.COMMUNICATION_2: 128,
                          FrontpanelController.Leds.VPN: 16,
                          FrontpanelController.Leds.ALIVE: 1,
                          FrontpanelController.Leds.CLOUD: 4}
    else:
        GPIO_LED_CONFIG = {FrontpanelController.Leds.POWER: 75,
                           FrontpanelController.Leds.STATUS_RED: 60,
                           FrontpanelController.Leds.ALIVE: 49}
        I2C_LED_CONFIG = {FrontpanelController.Leds.COMMUNICATION_1: 64,
                          FrontpanelController.Leds.COMMUNICATION_2: 128,
                          FrontpanelController.Leds.VPN: 16,
                          FrontpanelController.Leds.CLOUD: 4}
    I2C_DEVICE = '/dev/i2c-2' if BOARD_TYPE == Hardware.BoardType.BB else '/dev/i2c-1'

    @Inject
    def __init__(self, leds_i2c_address=INJECTED):  # type: (int) -> None
        super(FrontpanelClassicController, self).__init__()
        self._leds_i2c_address = leds_i2c_address
        self._button_states = {}  # type: Dict[str, bool]
        self._poll_button_thread = None
        self._write_leds_thread = None
        self._enabled_leds = {}  # type: Dict[str, bool]
        self._previous_leds = {}  # type: Dict[str, bool]
        self._last_i2c_led_code = None  # type: Optional[int]
        self._button_pressed_since = None  # type: Optional[float]
        self._button_released = False
        self._indicate_pointer = 0

    def _poll_button(self):
        # Check new state
        with open('/sys/class/gpio/gpio{0}/value'.format(FrontpanelClassicController.ACTION_BUTTON_GPIO), 'r') as fh_inp:
            line = fh_inp.read()
        button_pressed = int(line) == 0
        self._button_states[FrontpanelClassicController.BUTTON] = button_pressed

        # Check for authorized mode
        if not button_pressed:
            self._button_released = True
        if self._authorized_mode:
            if time.time() > self._authorized_mode_timeout or (button_pressed and self._button_released):
                self._authorized_mode = False
        else:
            if button_pressed:
                self._button_released = False
                if self._button_pressed_since is None:
                    self._button_pressed_since = time.time()
                if time.time() - self._button_pressed_since > FrontpanelController.AUTH_MODE_PRESS_DURATION:
                    self._authorized_mode = True
                    self._authorized_mode_timeout = time.time() + FrontpanelController.AUTH_MODE_TIMEOUT
                    self._button_pressed_since = None
            else:
                self._button_pressed_since = None

    def start(self):
        super(FrontpanelClassicController, self).start()
        # Enable power led
        self._enabled_leds[FrontpanelController.Leds.POWER] = True
        # Start polling/writing threads
        self._poll_button_thread = DaemonThread(name='Button poller',
                                                target=self._poll_button,
                                                interval=0.25)
        self._poll_button_thread.start()
        self._write_leds_thread = DaemonThread(name='Led writer',
                                               target=self._write_leds,
                                               interval=0.25)
        self._write_leds_thread.start()

    def stop(self):
        super(FrontpanelClassicController, self).stop()
        if self._poll_button_thread is not None:
            self._poll_button_thread.stop()
        if self._write_leds_thread is not None:
            self._write_leds_thread.stop()

    def _toggle_led(self, led):
        currently_enabled = self._enabled_leds.get(led, False)
        self._enabled_leds[led] = not currently_enabled

    def _report_carrier(self, carrier):
        # type: (bool) -> None
        self._enabled_leds[FrontpanelController.Leds.STATUS_RED] = not carrier

    def _report_connectivity(self, connectivity):
        # type: (bool) -> None
        pass  # No support for connectivity

    def _report_network_activity(self, activity):
        # type: (bool) -> None
        if activity:
            self._toggle_led(FrontpanelController.Leds.ALIVE)
        else:
            self._enabled_leds[FrontpanelController.Leds.ALIVE] = False

    def _report_serial_activity(self, serial_port, activity):
        # type: (str, Optional[bool]) -> None
        led = {FrontpanelController.SerialPorts.ENERGY: FrontpanelController.Leds.COMMUNICATION_1,
               FrontpanelController.SerialPorts.MASTER_API: FrontpanelController.Leds.COMMUNICATION_2}.get(serial_port)
        if led is None:
            return
        if activity:
            self._toggle_led(led)
        else:
            self._enabled_leds[led] = False

    def _report_cloud_reachable(self, reachable):
        # type: (bool) -> None
        self._enabled_leds[FrontpanelController.Leds.CLOUD] = reachable

    def _report_vpn_open(self, vpn_open):
        # type: (bool) -> None
        self._enabled_leds[FrontpanelController.Leds.VPN] = vpn_open

    def _write_leds(self):
        # Override for indicate
        if self._indicate:
            self._enabled_leds[FrontpanelController.Leds.STATUS_RED] = FrontpanelClassicController.INDICATE_SEQUENCE[self._indicate_pointer]
            self._indicate_pointer = self._indicate_pointer + 1
            if self._indicate_pointer >= len(FrontpanelClassicController.INDICATE_SEQUENCE):
                self._indicate_pointer = 0

        # Drive I2C leds
        try:
            code = 0x0
            for led in FrontpanelClassicController.I2C_LED_CONFIG:
                if self._enabled_leds.get(led, False) is True:
                    code |= FrontpanelClassicController.I2C_LED_CONFIG[led]
            if self._authorized_mode:
                # Light all leds in authorized mode
                for led in FrontpanelClassicController.AUTH_MODE_LEDS:
                    code |= FrontpanelClassicController.I2C_LED_CONFIG.get(led, 0x0)
            code = (~ code) & 0xFF

            # Push code if needed
            if code != self._last_i2c_led_code:
                self._last_i2c_led_code = code
                with open(FrontpanelClassicController.I2C_DEVICE, 'r+', 1) as i2c:
                    fcntl.ioctl(i2c, FrontpanelClassicController.IOCTL_I2C_SLAVE, self._leds_i2c_address)
                    i2c.write(chr(code))
        except Exception as ex:
            logger.error('Error while writing to i2c: {0}'.format(ex))

        # Drive GPIO leds
        try:
            for led in FrontpanelClassicController.GPIO_LED_CONFIG:
                on = self._enabled_leds.get(led, False)
                if self._previous_leds.get(led) != on:
                    self._previous_leds[led] = on
                    try:
                        gpio = FrontpanelClassicController.GPIO_LED_CONFIG[led]
                        with open('/sys/class/gpio/gpio{0}/value'.format(gpio), 'w') as fh_s:
                            fh_s.write('1' if on else '0')
                    except IOError:
                        pass  # The GPIO doesn't exist or is read only
        except Exception as ex:
            logger.error('Error while writing to GPIO: {0}'.format(ex))
