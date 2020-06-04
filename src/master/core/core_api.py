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
Contains the definition of the Core API
"""

from __future__ import absolute_import
from master.core.core_command import CoreCommandSpec
from master.core.fields import (ByteField, WordField, ByteArrayField, WordArrayField, LiteralBytesField,
                                AddressField, CharField, PaddingField, VersionField, TemperatureArrayField,
                                HumidityArrayField)


class CoreAPI(object):

    class SlaveBusMode(object):
        LIVE = 0
        INIT = DISCOVERY = 1
        TRANSPARENT = 2

    # Direct control
    # TODO: Use property

    @staticmethod
    def basic_action():
        """ Basic action spec """
        return CoreCommandSpec(instruction='BA',
                               request_fields=[ByteField('type'), ByteField('action'), WordField('device_nr'), WordField('extra_parameter')],
                               response_fields=[ByteField('type'), ByteField('action'), WordField('device_nr'), WordField('extra_parameter')])

    # Events and other messages from Core to Gateway

    @staticmethod
    def event_information():
        """ Event information """
        return CoreCommandSpec(instruction='EV',
                               response_fields=[ByteField('type'), ByteField('action'), WordField('device_nr'), ByteArrayField('data', 4)])

    @staticmethod
    def error_information():
        """ Error information """
        return CoreCommandSpec(instruction='ER',
                               response_fields=[ByteField('type'), ByteField('parameter_a'), WordField('parameter_b'), WordField('parameter_c')])

    # Generic information and configuration

    @staticmethod
    def device_information_list_outputs():
        """ Device information list for output """
        return CoreCommandSpec(instruction='DL',
                               request_fields=[LiteralBytesField(0)],
                               response_fields=[ByteField('type'), ByteArrayField('information', lambda length: length - 1)])

    @staticmethod
    def device_information_list_inputs():
        """ Device information list for inputs """
        return CoreCommandSpec(instruction='DL',
                               request_fields=[LiteralBytesField(1)],
                               response_fields=[ByteField('type'), ByteArrayField('information', lambda length: length - 1)])

    @staticmethod
    def general_configuration_number_of_modules():
        """ Receives general configuration regarding number of modules """
        return CoreCommandSpec(instruction='GC',
                               request_fields=[LiteralBytesField(0)],
                               response_fields=[ByteField('type'), ByteField('output'), ByteField('input'),
                                                ByteField('sensor'), ByteField('ucan'), ByteField('ucan_input'), ByteField('ucan_sensor'),
                                                ByteField('power_rs485'), ByteField('power_can')])

    @staticmethod
    def general_configuration_max_specs():
        """ Receives general configuration regarding maximum specifications (e.g. max number of input modules, max number of basic actions, ...) """
        return CoreCommandSpec(instruction='GC',
                               request_fields=[LiteralBytesField(1)],
                               response_fields=[ByteField('type'), ByteField('output'), ByteField('input'), ByteField('sensor'),
                                                ByteField('ucan'), WordField('groups'), WordField('basic_actions'),
                                                ByteField('shutters'), ByteField('shutter_groups')])

    @staticmethod
    def module_information():
        """ Receives module information """
        return CoreCommandSpec(instruction='MC',
                               request_fields=[ByteField('module_nr'), ByteField('module_family')],
                               response_fields=[ByteField('module_nr'), ByteField('module_family'), ByteField('module_type'),
                                                AddressField('address'), WordField('bus_errors'), ByteField('module_status')])

    @staticmethod
    def get_slave_bus_mode():
        """ Receives the slave bus mode """
        return CoreCommandSpec(instruction='ST',
                               request_fields=[LiteralBytesField(0)],
                               response_fields=[ByteField('info_type'), ByteField('mode')])

    @staticmethod
    def get_firmware_version():
        """ Receives the Core firmware version """
        return CoreCommandSpec(instruction='ST',
                               request_fields=[LiteralBytesField(1)],
                               response_fields=[ByteField('info_type'), VersionField('version')])

    @staticmethod
    def get_date_time():
        """ Reads the date/time from the Core """
        return CoreCommandSpec(instruction='TR',
                               request_fields=[LiteralBytesField(0)],
                               response_fields=[ByteField('info_type'),
                                                ByteField('hours'), ByteField('minutes'), ByteField('seconds'),
                                                ByteField('weekday'), ByteField('day'), ByteField('month'), ByteField('year')])

    @staticmethod
    def set_date_time():
        """ Writes the date/time from the Core """
        return CoreCommandSpec(instruction='TW',
                               request_fields=[LiteralBytesField(0),
                                               ByteField('hours'), ByteField('minutes'), ByteField('seconds'),
                                               ByteField('weekday'), ByteField('day'), ByteField('month'), ByteField('year')],
                               response_fields=[ByteField('info_type'),
                                                ByteField('hours'), ByteField('minutes'), ByteField('seconds'),
                                                ByteField('weekday'), ByteField('day'), ByteField('month'), ByteField('year')])

    # States

    @staticmethod
    def output_detail():
        """ Received output detail information """
        return CoreCommandSpec(instruction='OD',
                               request_fields=[WordField('device_nr')],
                               response_fields=[WordField('device_nr'), ByteField('status'),
                                                ByteField('dimmer'), ByteField('dimmer_min'), ByteField('dimmer_max'),
                                                ByteField('timer_type'), ByteField('timer_type_standard'),
                                                WordField('timer'), WordField('timer_standard'),
                                                WordField('group_action'), ByteField('dali_output')])

    @staticmethod
    def sensor_temperature_values():
        """ Receive sensor temperature values """
        return CoreAPI._sensor_values(0, TemperatureArrayField('values', length=8))

    @staticmethod
    def sensor_humidity_values():
        """ Receive sensor humidity values """
        return CoreAPI._sensor_values(1, HumidityArrayField('values', length=8))

    @staticmethod
    def sensor_brightness_values():
        """ Receive sensor brightness values """
        return CoreAPI._sensor_values(2, WordArrayField('values', length=8))

    @staticmethod
    def sensor_co2_values():
        """ Receive sensor CO2 values """
        return CoreAPI._sensor_values(3, WordArrayField('values', length=8))

    @staticmethod
    def sensor_voc_values():
        """ Receive sensor VOC values """
        return CoreAPI._sensor_values(4, WordArrayField('values', length=8))

    @staticmethod
    def sensor_extra_values():
        """ Receive sensor extra values """
        return CoreAPI._sensor_values(5, WordArrayField('values', length=8))

    @staticmethod
    def _sensor_values(instruction, field):
        """ Receive sensor byte values """
        return CoreCommandSpec(instruction='SI',
                               request_fields=[ByteField('module_nr'), LiteralBytesField(instruction)],
                               response_fields=[ByteField('module_nr'), PaddingField(1), field])

    # Memory (EEPROM/FRAM) actions

    @staticmethod
    def memory_read():
        """ Reads memory """
        return CoreCommandSpec(instruction='MR',
                               request_fields=[CharField('type'), WordField('page'), ByteField('start'), ByteField('length')],
                               response_fields=[CharField('type'), WordField('page'), ByteField('start'), ByteArrayField('data', lambda length: length - 4)])

    @staticmethod
    def memory_write(length):
        """ Writes memory """
        return CoreCommandSpec(instruction='MW',
                               request_fields=[CharField('type'), WordField('page'), ByteField('start'), ByteArrayField('data', length)],
                               response_fields=[CharField('type'), WordField('page'), ByteField('start'), ByteField('length'), CharField('result')])

    # Slave bus

    @staticmethod
    def set_slave_bus_mode():
        """ Sets the slave bus to a different mode"""
        return CoreCommandSpec(instruction='SM',
                               request_fields=[ByteField('mode')],
                               response_fields=[ByteField('mode')])

    @staticmethod
    def slave_tx_transport_message(length):
        """ Slave transport layer packages """
        return CoreCommandSpec(instruction='TC',
                               request_fields=[ByteArrayField('payload', length)],
                               response_fields=[ByteField('length')])

    @staticmethod
    def slave_rx_transport_message():
        """ Slave transport layer packages """
        return CoreCommandSpec(instruction='TM',
                               response_fields=[ByteArrayField('payload', lambda length: length)])

    # CAN

    @staticmethod
    def get_amount_of_ucans():
        """ Receives amount of uCAN modules """
        return CoreCommandSpec(instruction='FS',
                               request_fields=[AddressField('cc_address'), LiteralBytesField(0), LiteralBytesField(0)],
                               response_fields=[AddressField('cc_address'), PaddingField(2), ByteField('amount'), PaddingField(2)])

    @staticmethod
    def get_ucan_address():
        """ Receives the uCAN address of a specific uCAN """
        return CoreCommandSpec(instruction='FS',
                               request_fields=[AddressField('cc_address'), LiteralBytesField(1), ByteField('ucan_nr')],
                               response_fields=[AddressField('cc_address'), PaddingField(2), AddressField('ucan_address', 3)])

    @staticmethod
    def ucan_tx_transport_message():
        """ uCAN transport layer packages """
        return CoreCommandSpec(instruction='FM',
                               request_fields=[AddressField('cc_address'), ByteField('nr_can_bytes'), ByteField('sid'), ByteArrayField('payload', 8)],
                               response_fields=[AddressField('cc_address')])

    @staticmethod
    def ucan_rx_transport_message():
        """ uCAN transport layer packages """
        return CoreCommandSpec(instruction='FM',
                               response_fields=[AddressField('cc_address'), ByteField('nr_can_bytes'), ByteField('sid'), ByteArrayField('payload', 8)])

    @staticmethod
    def ucan_module_information():
        """ Receives information from a uCAN module """
        return CoreCommandSpec(instruction='CD',
                               response_fields=[AddressField('ucan_address', 3), WordArrayField('input_links', 6),
                                                ByteArrayField('sensor_links', 2), ByteField('sensor_type'), VersionField('version'),
                                                ByteField('bootloader'), CharField('new_indicator'),
                                                ByteField('min_led_brightness'), ByteField('max_led_brightness')])
