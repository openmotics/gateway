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
Communication fields
"""
from __future__ import absolute_import
import struct

if False:  # MYPY
    from typing import Any, List


class Field(object):
    """
    Field of a command
    """

    def __init__(self, name, length):  # type: (str, int) -> None
        self.name = name
        self.length = length

    def encode(self, value):  # type: (Any) -> str
        """
        Encodes a high-level value into a byte string
        :param value: The high-level value (e.g. 'foobar', 23475, 15, '10.2.25.6')
        :return: The byte string (e.g. 'd%_\xf8\xa5?@_1')
        """
        value_bytes = self.encode_bytes(value)
        return ''.join(str(chr(byte)) for byte in value_bytes)

    def encode_bytes(self, value):  # type: (Any) -> List[int]
        """
        Encodes a high-level value into a byte array
        :param value: The high-level value (e.g. 'foobar', 23475, 15, '10.2.25.6')
        :return: The byte array (e.g. [234, 12, 65, 23, 119])
        """
        raise NotImplementedError()

    def decode(self, data):  # type: (str) -> Any
        """
        Decodes a low-level byte string into a high-level value
        :param data: Bytes to decode (e.g. 'd%_\xf8\xa5?@_1')
        :returns: High-level value (e.g. 'foobar', 23475, 15, '10.2.25.6')
        """
        data_bytes = [ord(item) for item in data]
        return self.decode_bytes(data_bytes)

    def decode_bytes(self, data):  # type: (List[int]) -> Any
        """
        Decodes a low-level byte array into a high-level value
        :param data: Bytes to decode (e.g. [234, 12, 65, 23, 119])
        :returns: High-level value (e.g. 'foobar', 23475, 15, '10.2.25.6')
        """
        raise NotImplementedError()

    def __str__(self):
        return '{0}({1})'.format(self.name, self.length)

    def __repr__(self):
        return str(self)


class ByteField(Field):
    def __init__(self, name):
        super(ByteField, self).__init__(name, 1)

    def encode_bytes(self, value):
        if not (0 <= value <= 255):
            raise ValueError('Value `{0}` out of limits: 0 <= value <= 255'.format(value))
        return [value]

    def decode_bytes(self, data):
        return data[0]


class CharField(Field):
    def __init__(self, name):
        super(CharField, self).__init__(name, 1)

    def encode_bytes(self, value):
        value = str(value)
        if len(value) != 1:
            raise ValueError('Value `{0}` must be a single-character string'.format(value))
        return [ord(value[0])]

    def decode_bytes(self, data):
        return str(chr(data[0]))


class TemperatureField(Field):
    def __init__(self, name):
        super(TemperatureField, self).__init__(name, 1)

    def encode_bytes(self, value):
        if value is not None and not (-32 <= value <= 95):
            raise ValueError('Value `{0}` out of limits: -32 <= value <= 95'.format(value))
        if value is None:
            return [255]
        value = int((float(value) + 32) * 2)
        return [value]

    def decode_bytes(self, data):
        if data[0] == 255:
            return None
        return float(data[0]) / 2 - 32


class HumidityField(Field):
    def __init__(self, name):
        super(HumidityField, self).__init__(name, 1)

    def encode_bytes(self, value):
        if value is not None and not (0 <= value <= 100):
            raise ValueError('Value `{0}` out of limits: 0 <= value <= 100'.format(value))
        if value is None:
            return [255]
        value = int(float(value) * 2)
        return [value]

    def decode_bytes(self, data):
        if data[0] == 255:
            return None
        return float(data[0]) / 2


class WordField(Field):
    def __init__(self, name):
        super(WordField, self).__init__(name, 2)

    @classmethod
    def encode_bytes(cls, value):
        if not (0 <= value <= 65535):
            raise ValueError('Value `{0}` out of limits: 0 <= value <= 65535'.format(value))
        return [value // 256, value % 256]

    @classmethod
    def decode_bytes(cls, data):
        return data[0] * 256 + data[1]

    @classmethod
    def decode(cls, data):
        data_bytes = [ord(item) for item in data]
        return cls.decode_bytes(data_bytes)

    @classmethod
    def encode(cls, value):
        value_bytes = cls.encode_bytes(value)
        return ''.join(str(chr(byte)) for byte in value_bytes)


class UInt32Field(Field):
    def __init__(self, name):
        super(UInt32Field, self).__init__(name, 4)

    @classmethod
    def encode(cls, value):
        limit = 256 ** 4 - 1
        if not (0 <= value <= limit):
            raise ValueError('Value `{0}` out of limits: 0 <= value <= {1}'.format(value, limit))
        return struct.pack('>I', value)

    @classmethod
    def decode(cls, data):
        return struct.unpack('>I', data)[0]

    @classmethod
    def decode_bytes(cls, data):
        return cls.decode(''.join(str(chr(byte)) for byte in data))

    @classmethod
    def encode_bytes(cls, value):
        value_string = cls.encode(value)
        return [ord(item) for item in value_string]


class ByteArrayField(Field):
    def __init__(self, name, length):
        super(ByteArrayField, self).__init__(name, length)
        self._field = ByteField(name)

    def encode_bytes(self, value):
        if len(value) != self.length:
            raise ValueError('Value `{0}` should be an array of {1} items with 0 <= item <= 255'.format(value, self.length))
        data = []
        for item in value:
            data += self._field.encode_bytes(item)
        return data

    def decode_bytes(self, data):
        result = []
        for i in range(len(data)):
            result.append(self._field.decode_bytes([data[i]]))
        return result


class TemperatureArrayField(ByteArrayField):
    def __init__(self, name, length):
        super(TemperatureArrayField, self).__init__(name, length)
        self._field = TemperatureField(name)


class HumidityArrayField(ByteArrayField):
    def __init__(self, name, length):
        super(HumidityArrayField, self).__init__(name, length)
        self._field = HumidityField(name)


class WordArrayField(Field):
    def __init__(self, name, length):
        super(WordArrayField, self).__init__(name, length * 2)
        self._word_length = length
        self._word_field = WordField(name)

    def encode_bytes(self, value):
        if len(value) != self._word_length:
            raise ValueError('Value `{0}` should be an array of {1} items with 0 <= item <= 65535'.format(value, self._word_length))
        data = []
        for item in value:
            data += self._word_field.encode_bytes(item)
        return data

    def decode_bytes(self, data):
        result = []
        for i in range(0, len(data), 2):
            result.append(self._word_field.decode_bytes(data[i:i + 2]))
        return result


class LiteralBytesField(Field):
    def __init__(self, *data):
        super(LiteralBytesField, self).__init__('literal_bytes', len(data))
        self._byte_field = ByteField(None)
        self._data = data

    def encode_bytes(self, value):
        if value is not None:
            raise ValueError('LiteralBytesField does no tsupport value encoding')
        data = []
        for item in self._data:
            data += self._byte_field.encode_bytes(item)
        return data

    def decode_bytes(self, data):
        raise ValueError('LiteralBytesField does not support decoding')


class AddressField(Field):
    def __init__(self, name, length=4):
        super(AddressField, self).__init__(name, length)

    def encode_bytes(self, value):
        example = '.'.join(['ID{0}'.format(i) for i in range(self.length - 1, -1, -1)])
        error_message = 'Value `{0}` should be a string in the format of {1}, where 0 <= IDx <= 255'.format(value, example)
        parts = str(value).split('.')
        if len(parts) != self.length:
            raise ValueError(error_message)
        data = []
        for part in parts:
            try:
                part = int(part)
            except ValueError:
                raise ValueError(error_message)
            if not (0 <= part <= 255):
                raise ValueError(error_message)
            data.append(part)
        return data

    def decode_bytes(self, data):
        return '.'.join('{0:03}'.format(item) for item in data)


class StringField(Field):
    def __init__(self, name):
        super(StringField, self).__init__(name, length=None)

    def encode(self, value):
        return '{0}\x00'.format(value)

    def encode_bytes(self, value):
        value_string = self.encode(value)
        return [ord(item) for item in value_string]

    def decode(self, data):
        return data.strip('\x00')

    def decode_bytes(self, data):
        return self.decode(''.join(str(chr(byte)) for byte in data))


class VersionField(AddressField):
    def __init__(self, name):
        super(VersionField, self).__init__(name, 3)

    def encode_bytes(self, value):
        example = '.'.join(['F{0}'.format(i) for i in range(self.length)])
        error_message = 'Value `{0}` should be a string in the format of {1}, where 0 <= Fx <= 255'.format(value, example)
        parts = str(value).split('.')
        if len(parts) != self.length:
            raise ValueError(error_message)
        data = []
        for part in parts:
            try:
                part = int(part)
            except ValueError:
                raise ValueError(error_message)
            if not (0 <= part <= 255):
                raise ValueError(error_message)
            data.append(part)
        return data

    def decode_bytes(self, data):
        return '.'.join(str(item) for item in data)


class PaddingField(Field):
    def __init__(self, length):
        super(PaddingField, self).__init__('padding', length)

    def encode_bytes(self, value):
        _ = value
        return [0] * self.length

    def decode_bytes(self, data):
        _ = data
        return '.' * self.length
