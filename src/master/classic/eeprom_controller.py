# Copyright (C) 2016 OpenMotics BV
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
Contains controller from reading and writing to the Master EEPROM.
"""

from __future__ import absolute_import
import inspect
import types
import logging
from threading import Lock
from ioc import Injectable, Inject, INJECTED, Singleton
from .master_api import eeprom_list, write_eeprom, activate_eeprom

if False:  # MYPY
    from typing import Any, Dict, List, Optional, Iterable, Type, TypeVar
    from master.classic.eeprom_extension import EepromExtension
    M = TypeVar('M', bound='EepromModel')

logger = logging.getLogger("openmotics")


@Injectable.named('eeprom_controller')
@Singleton
class EepromController(object):
    """ The controller takes EepromModels and reads or writes them from and to an EepromFile. """

    @Inject
    def __init__(self, eeprom_file=INJECTED, eeprom_extension=INJECTED):
        """
        Constructor takes the eeprom_file (for reading and writes from the eeprom) and the
        eeprom_extension (for reading the extensions from sqlite).

        :type eeprom_file: master.eeprom_controller.EepromFile
        :type eeprom_extension: master.eeprom_extension.EepromExtension
        """
        self._eeprom_file = eeprom_file
        self._eeprom_extension = eeprom_extension
        self.dirty = True

    def invalidate_cache(self):
        # type: () -> None
        """ Invalidate the cache, this should happen when maintenance mode was used. """
        self._eeprom_file.invalidate_cache()

    def read(self, eeprom_model, id=None, fields=None):
        # type: (Type[M], Optional[int], Optional[List[str]]) -> M
        """
        Create an instance of an EepromModel by reading it from the EepromFile. The id has to
        be specified if the model has an EepromId field.
        """
        return self.read_batch(eeprom_model, [id], fields)[0]

    def read_batch(self, eeprom_model, ids, fields=None):
        # type: (Type[M], Iterable[Optional[int]], List[str]) -> List[M]
        """
        Create a list of instances of an EepromModel by reading it from the EepromFile.
        """
        return_data = []
        for id in ids:
            entry = eeprom_model(id)
            entry.load_from_system(self._eeprom_file, self._eeprom_extension, fields)
            return_data.append(entry)
        return return_data

    def read_address(self, address):
        # type: (EepromAddress) -> EepromData
        """
        Reads a given address (+length) from the eeprom
        """
        return self._eeprom_file.read([address])[address]

    def read_all(self, eeprom_model, fields=None):
        # type: (Type[M], List[str]) -> List[M]
        """
        Create a list of instance of an EepromModel by reading all ids of that model from the
        EepromFile. Only applicable for EepromModels with an EepromId.
        """
        return self.read_batch(eeprom_model, range(eeprom_model.get_max_id(self._eeprom_file) + 1), fields)

    def write(self, eeprom_model):
        # type: (M) -> None
        """
        Write a given EepromModel to the EepromFile.

        :type eeprom_model: master.eeprom_models.EepromModel
        """
        return self.write_batch([eeprom_model])

    def write_batch(self, eeprom_models):
        # type: (List[M]) -> None
        """
        Write a list of EepromModel instances to the EepromFile.

        :type eeprom_models: list of master.eeprom_models.EepromModel
        """
        # Write to the eeprom
        eeprom_data = []
        for eeprom_model in eeprom_models:
            eeprom_data += eeprom_model.get_eeprom_data()
        if len(eeprom_data) > 0:
            if self._eeprom_file.write(eeprom_data):
                self._eeprom_file.activate()
                self.dirty = True
        # Write the extensions
        eext_data = []
        for eeprom_model in eeprom_models:
            eext_data += eeprom_model.get_eext_data()
        if len(eext_data) > 0:
            self._eeprom_extension.write_data(eext_data)
            self.dirty = True

    def write_address(self, address, data):
        # type: (EepromAddress, str) -> None
        """
        Writes data to a given address (+length)
        """
        self._eeprom_file.write([EepromData(address, data)])

    def activate(self):
        self._eeprom_file.activate()
        self.dirty = True


@Injectable.named('eeprom_file')
@Singleton
class EepromFile(object):
    """ Reads from and writes to the Master EEPROM. """

    BATCH_SIZE = 10

    @Inject
    def __init__(self, master_communicator=INJECTED):
        """
        Create an EepromFile.

        :param master_communicator: communicates with the master.
        :type master_communicator: master.master_communicator.MasterCommunicator
        """
        self._master_communicator = master_communicator
        self._bank_cache = {}

    def invalidate_cache(self):
        """ Invalidate the cache, this should happen when maintenance mode was used. """
        self._bank_cache = {}

    def activate(self):
        """
        Activate a change in the Eeprom. The master will read the eeprom
        and adjust the current settings.
        """
        logger.info("EEPROM - Activate")
        self._master_communicator.do_command(activate_eeprom(), {'eep': 0})

    def read(self, addresses):
        """
        Read data from the Eeprom.

        :param addresses: the addresses to read.
        :type addresses: list of master.eeprom_controller.EepromAddress
        :rtype: dict[master.eeprom_controller.EepromAddress, master.eeprom_controller.EepromData]
        """
        bank_data = self._read_banks({a.bank for a in addresses})
        return {a: EepromData(a, bank_data[a.bank][a.offset:a.offset + a.length]) for a in addresses}

    def _read_banks(self, banks):
        """
        Read a number of banks from the Eeprom.

        :param banks: a list of banks (integers).
        :returns: a dict mapping the bank to the data.
        """
        try:
            return_data = {}
            for bank in banks:
                if bank in self._bank_cache:
                    data = self._bank_cache[bank]
                else:
                    output = self._master_communicator.do_command(eeprom_list(), {'bank': bank})
                    data = output['data']
                    self._bank_cache[bank] = data
                return_data[bank] = data
            return return_data
        except Exception:
            # Failure reading, cache might be invalid
            self.invalidate_cache()
            raise

    def write(self, data):
        """
        Write data to the Eeprom.

        :param data: the data to write.
        :type data: list of master.eeprom_controller.EepromData
        """
        wrote_data = False

        # Read the data in the banks that we are trying to write
        bank_data = self._read_banks({d.address.bank for d in data})
        new_bank_data = bank_data.copy()

        for data_item in data:
            address = data_item.address
            data = new_bank_data[address.bank]
            new_bank_data[address.bank] = data[0:address.offset] + data_item.bytes + data[address.offset + address.length:]

        # Check what changed and write changes in batch
        try:
            for bank in bank_data.keys():
                old = bank_data[bank]
                new = new_bank_data[bank]

                i = 0
                while i < len(bank_data[bank]):
                    if old[i] != new[i]:
                        length = 1
                        j = 1
                        while j < EepromFile.BATCH_SIZE and i + j < len(old):
                            if old[i + j] != new[i + j]:
                                length = j + 1
                            j += 1

                        self._write(bank, i, new[i:i + length])
                        wrote_data = True
                        i += EepromFile.BATCH_SIZE
                    else:
                        i += 1

                self._bank_cache[bank] = new
            return wrote_data
        except Exception:
            # Failure reading, cache might be invalid
            self.invalidate_cache()
            raise

    def _write(self, bank, offset, to_write):
        """ Write a byte array to a specific location defined by the bank and the offset. """
        logger.info("EEPROM - Write: B{0} A{1} D[{2}]".format(bank, offset, ' '.join(['%3d' % ord(c) for c in to_write])))
        self._master_communicator.do_command(
            write_eeprom(), {'bank': bank, 'address': offset, 'data': to_write}
        )


class EepromAddress(object):
    """ Represents an address in the Eeprom, has a bank, an offset and a length. """

    def __init__(self, bank, offset, length, shared=False, name=None):
        self.bank = bank
        self.offset = offset
        self.length = length
        self.shared = shared
        self.name = name

    def __eq__(self, other):
        return self.bank == other.bank and self.offset == other.offset and self.length == other.length

    def __hash__(self):
        return self.bank + self.offset * 256 + self.length * 256 * 256

    def __str__(self):
        return '(B{0} A{1} L{2})'.format(self.bank, self.offset, self.length)

    def __repr__(self):
        return self.__str__()


class EepromData(object):
    """ A piece of Eeprom data, has an address and the actual data. """

    def __init__(self, address, data):
        """
        :type address: master.eeprom_controller.EepromAddress
        :type data: basestring
        """
        if address.length != len(data):
            raise TypeError('Length in the address ({0}) does not match the number of bytes ({1})'.format(address.length, len(data)))
        self.address = address
        self.bytes = data

    def __str__(self):
        hex_data = ' '.join(['%3d' % ord(c) for c in self.bytes])
        readable = ''.join([c if 32 < ord(c) <= 126 else '.' for c in self.bytes])
        return '{0}: {1} | {2}'.format(self.address, hex_data, readable)

    def __repr__(self):
        return self.__str__()


class EepromModel(object):
    """
    The EepromModel provides a generic way to model data in the eeprom by creating a child
    class of EepromModel with an optional EepromId and EepromDataTypes as class fields.
    """

    cache_fields = {}  # type: Dict[str,Any]
    cache_addresses = {}  # type: Dict[str,Any]
    cache_lock = Lock()

    def __init__(self, id=None):
        self.check_id(id)
        self.id = id
        self._fields = {'eeprom': [], 'eext': []}
        self._loaded_fields = []
        address_cache = self.__class__.get_address_cache(self.id)
        for field_name, field_type in self.__class__.get_field_dict(include_eeprom=True).items():
            setattr(self, '_{0}'.format(field_name), EepromDataContainer(field_type, address_cache[field_name]))
            self._add_property(field_name)
            self._fields['eeprom'].append(field_name)
        for field_name, field_type in self.__class__.get_field_dict(include_eext=True).items():
            setattr(self, '_{0}'.format(field_name), EextDataContainer(field_type))
            self._add_property(field_name)
            self._fields['eext'].append(field_name)

    def load_from_system(self, eeprom_file, eeprom_extension, fields=None):
        # type: (EepromFile, EepromExtension, List[str]) -> None
        expected_fields = [] if fields is None else fields[:]
        self._loaded_fields = []
        addresses = []
        for field_name in self._fields['eeprom']:
            if fields is not None:
                if field_name not in expected_fields:
                    continue
            field = getattr(self, '_{0}'.format(field_name))
            if field.composed is True:
                addresses += field.addresses
            else:
                addresses.append(field.address)
        data = eeprom_file.read(addresses)
        for field_name in self._fields['eeprom']:
            if fields is not None:
                if field_name not in expected_fields:
                    continue
                expected_fields.remove(field_name)
            field = getattr(self, '_{0}'.format(field_name))
            if field.composed is True:
                field.load_bytes([data[address] for address in field.addresses])
            else:
                field.load_bytes(data[field.address])
            self._loaded_fields.append(field_name)
        for field_name in self._fields['eext']:
            if fields is not None:
                if field_name not in expected_fields:
                    continue
                expected_fields.remove(field_name)
            data = eeprom_extension.read_data(self.__class__.__name__, self.id, field_name)
            if data is not None:
                field = getattr(self, '_{0}'.format(field_name))
                field.load_bytes(data)
            self._loaded_fields.append(field_name)
        if len(expected_fields) > 0:
            raise RuntimeError('Unknown fields: {0}'.format(', '.join(expected_fields)))

    def get_eeprom_data(self):
        data = []
        for field_name in self._fields['eeprom']:
            if field_name not in self._loaded_fields:
                continue
            field = getattr(self, '_{0}'.format(field_name))
            if field.read_only is False:
                if field.composed is True:
                    data += field.get_bytes()
                else:
                    data.append(field.get_bytes())
        return data

    def get_eext_data(self):
        data = []
        for field_name in self._fields['eext']:
            if field_name not in self._loaded_fields:
                continue
            field = getattr(self, '_{0}'.format(field_name))
            data.append((self.get_name(), self.id, field_name, field.get_bytes()))
        return data

    @classmethod
    def from_dict(cls, data_dict):
        return cls.deserialize(data_dict)

    @classmethod
    def deserialize(cls, data_dict):
        instance = cls(data_dict.get('id'))
        instance._deserialize(data_dict)
        return instance

    def _deserialize(self, data_dict):
        self._loaded_fields = []
        for field_name, value in data_dict.items():
            if field_name == 'id':
                continue
            self._loaded_fields.append(field_name)
            if not hasattr(self, '_{0}'.format(field_name)):
                raise TypeError('Field `{0}` is not available'.format(field_name))
            field = getattr(self, '_{0}'.format(field_name))
            field.deserialize(value, check_writability=False)

    def to_dict(self):
        return self.serialize()

    def serialize(self):
        data = {}
        if self.id is not None:
            data['id'] = self.id
        for field_name in self._loaded_fields:
            field = getattr(self, '_{0}'.format(field_name))
            data[field_name] = field.serialize()
        return data

    def _add_property(self, field_name):
        setattr(self.__class__, field_name, property(lambda s: s._get_property(field_name),
                                                     lambda s, v: s._set_property(field_name, v)))

    def _get_property(self, field_name):
        field = getattr(self, '_{0}'.format(field_name))
        return field.serialize()

    def _set_property(self, field_name, value):
        field = getattr(self, '_{0}'.format(field_name))
        field.deserialize(value)

    @classmethod
    def get_fields(cls, include_id=False, include_eeprom=False, include_eext=False):
        """ Get the fields defined by an EepromModel child. """
        if cls.__name__ not in EepromModel.cache_fields:
            EepromModel.cache_fields[cls.__name__] = {
                'id': inspect.getmembers(cls, lambda f: isinstance(f, EepromId)),
                'eeprom': inspect.getmembers(cls, lambda f: isinstance(f, EepromDataType) or isinstance(f, CompositeDataType)),
                'eext': inspect.getmembers(cls, lambda f: isinstance(f, EextDataType))
            }
        fields = []
        if include_id:
            fields += EepromModel.cache_fields[cls.__name__]['id']
        if include_eeprom:
            fields += EepromModel.cache_fields[cls.__name__]['eeprom']
        if include_eext:
            fields += EepromModel.cache_fields[cls.__name__]['eext']
        return fields

    @classmethod
    def get_field_dict(cls, include_id=False, include_eeprom=False, include_eext=False):
        """
        Get a dict from the field name to the field type for each field defined by the
        EepromModel child.
        """
        class_field_dict = {}
        for name, field_type in cls.get_fields(include_id, include_eeprom, include_eext):
            class_field_dict[name] = field_type
        return class_field_dict

    @classmethod
    def get_id_field(cls):
        """ Get the name of the EepromId field. None if not included. """
        ids = cls.get_fields(include_id=True)
        if len(ids) == 0:
            return None
        if len(ids) == 1:
            return ids[0][0]
        raise TypeError('Found more than 1 EepromId for {0}'.format(cls.__name__))

    @classmethod
    def has_id(cls):
        """ Check if the EepromModel has an id. """
        return cls.get_id_field() is not None

    @classmethod
    def get_name(cls):
        """ Get the name of the EepromModel. """
        return cls.__name__

    @classmethod
    def check_id(cls, id):
        """ Check if the id is valid for this EepromModel. """
        has_id = cls.has_id()

        if id is None and has_id:
            raise TypeError('{0} has an id, but no id was given.'.format(cls.__name__))
        if id is not None:
            if not has_id:
                raise TypeError('{0} doesn\'t have an id, but id was given.'.format(cls.__name__))
            id_fields = cls.get_fields(include_id=True)
            max_id = id_fields[0][1].get_max_id()
            if id > max_id:
                raise TypeError('The maximum id for {0} is {1}, {2} was provided.'.format(cls.__name__, max_id, id))

    @classmethod
    def get_address_cache(cls, id):
        if cls.__name__ in EepromModel.cache_addresses:
            class_cache = EepromModel.cache_addresses[cls.__name__]
        else:
            with EepromModel.cache_lock:
                class_cache = EepromModel.cache_addresses.setdefault(cls.__name__, {})
        if id in class_cache:
            return class_cache[id]
        with EepromModel.cache_lock:
            cache = {}
            for field_name, field_type in cls.get_fields(include_eeprom=True):
                if isinstance(field_type, CompositeDataType):
                    cache[field_name] = field_type.get_addresses(id, field_name)
                else:
                    cache[field_name] = field_type.get_address(id, field_name)
            class_cache[id] = cache
        return cache

    @classmethod
    def get_max_id(cls, eeprom_file):
        """
        :type eeprom_file: master.eeprom_controller.EepromFile
        """
        if not cls.has_id():
            raise TypeError('EepromModel {0} does not contain an id'.format(cls.get_name()))
        else:
            eeprom_id = cls.get_fields(include_id=True)[0][1]
            if not eeprom_id.has_address():
                return eeprom_id.get_max_id()
            address = eeprom_id.get_address()
            if address.length != 1:
                raise TypeError('Length of max id address in EepromModel {0} is not 1'.format(cls.get_name()))
            eeprom_data = eeprom_file.read([address])
            amount_of_modules = ord(eeprom_data[address].bytes[0])
            if amount_of_modules == 255:
                amount_of_modules = 0
            return amount_of_modules * eeprom_id.get_multiplier() - 1


class EepromId(object):
    """ Represents an id in an EepromModel. """

    def __init__(self, amount_of_modules, address=None, multiplier=None):
        """
        :param amount_of_modules: The amount of modules
        :type amount_of_modules: int
        :param address: the EepromAddress where the dynamic maximum for the id is located.
        :type address: master.eeprom_controller.EepromAddress
        :param multiplier: if an address is provided, the multiplier can be used to multiply the value located at that address.
        :type multiplier: int
        """
        self._max_id = amount_of_modules - 1
        self._address = address
        if multiplier is not None and self._address is None:
            raise TypeError('A multiplier was specified without an address')
        else:
            self._multiplier = multiplier if multiplier is not None else 1

    def get_max_id(self):
        """ Get the static maximum id. """
        return self._max_id

    def has_address(self):
        """ Check if the EepromId has a dynamic maximum. """
        return self._address is not None

    def get_address(self):
        """ Get the EepromAddress. """
        return self._address

    def get_multiplier(self):
        """ Return the multiplier for the Eeprom value (at the defined EepromAddress). """
        return self._multiplier


class CompositeDataType(object):
    """
    Defines a composite data type in an EepromModel, the composite structure contains multiple
    EepromDataTypes and defines a name for each child data type.
    """

    def __init__(self, data_types, read_only=False):
        """ Create a new composite data type using a list of tuples (name, EepromDataType). """
        self.data_types = data_types
        for data_type in self.data_types:
            data_type[1].read_only |= read_only
        self.read_only = read_only

    def get_addresses(self, id, field_name):
        """ Get all EepromDataType addresses in the composite data type. """
        return {t[0]: t[1].get_address(id, '{0}.{1}'.format(field_name, t[0])) for t in self.data_types}

    def get_name(self):
        """ Get the name of the EepromDataType. To be implemented in the subclass. """
        return '[{0}]'.format(','.join(['{0}({1})'.format(t[0], t[1].get_name()) for t in self.data_types]))


class EepromDataContainer(object):
    """
    Defines a field in an EepromModel. It contains the data, contains functionality to
    convert data to and from a string of bytes and contains the address(generator).
    """

    def __init__(self, data_type, address):
        """
        :type data_type: master.eeprom_controller.EepromDataType
        :type address: master.eeprom_controller.EepromAddress or dict[basestring, master.eeprom_controller.EepromAddress]
        """
        if isinstance(data_type, CompositeDataType):
            self.composed = True
            self.addresses = []
            self._composed_data = {}
            self._composed_fields = []
            self.read_only = data_type.read_only
            for data_type in data_type.data_types:
                field_name, field_type = data_type
                self._composed_fields.append(field_name)
                self._composed_data[field_name] = EepromDataContainer(field_type, address[field_name])
                self.addresses.append(address[field_name])
        else:
            self.composed = False
            self.address = address
            self._data = None
            self._data_type = data_type
            self.read_only = data_type.read_only

    def load_bytes(self, data):
        """
        :type data: master.eeprom_controller.EepromData or list of master.eeprom_controller.EepromData
        """
        if self.composed is True:
            if not isinstance(data, list):
                raise RuntimeError('Parameter `data` should be: list of EepromData')
            for item in data:
                for field_name in self._composed_fields:
                    container = self._composed_data[field_name]
                    if container.address == item.address:
                        container.load_bytes(item)
        else:
            self._data = data

    def get_bytes(self):
        if self.composed is True:
            return [self._composed_data[field_name].get_bytes() for field_name in self._composed_fields]
        return self._data

    def serialize(self):
        if self.composed is True:
            return [self._composed_data[field_name].serialize() for field_name in self._composed_fields]
        return self._data_type.decode(self._data.bytes)

    def deserialize(self, data, check_writability=True):
        if self.composed is True:
            for i in range(len(data)):
                self._composed_data[self._composed_fields[i]].deserialize(data[i], check_writability=check_writability)
        else:
            if check_writability is True:
                self._data_type.check_writable()
            self._data = EepromData(self.address, self._data_type.encode(data))


class EepromDataType(object):
    """
    Defines a data type in an EepromModel, and provides functions to_bytes and from_bytes to
    convert this data type from and to a string of bytes.  Besides these functions, the data type
    also contains the address, or the address generator (in case the model has an id).
    """

    def __init__(self, addr_gen, read_only=False, shared=False):
        """
        Create an instance of an EepromDataType with an address or an address generator.

        :type addr_gen: Tuple[int, int] or (int) => Tuple[int, int]
        """
        self.read_only = read_only
        self._shared = shared
        self._addr_tuple = None
        self._addr_func = None
        self._data = None

        if isinstance(addr_gen, tuple):
            self._addr_tuple = addr_gen
        elif isinstance(addr_gen, types.FunctionType):
            args = inspect.getargspec(addr_gen).args
            if len(args) == 1:
                self._addr_func = addr_gen
            else:
                raise TypeError('Parameter `addr_gen` should be a function that takes an id and returns the same tuple.')
        else:
            raise TypeError('Parameter `addr_gen` should be a tuple (bank, address) or a function that takes an id and returns the same tuple.')

    def check_writable(self):
        """ Raises a TypeError if the EepromDataType is not writable. """
        if self.read_only:
            raise TypeError('EepromDataType is not writable')

    def get_address(self, id, field_name):
        """
        Calculate the address for this data type.
        :rtype: master.eeprom_controller.EepromAddress
        """
        length = self.get_length()
        if id is None:
            if self._addr_tuple is None:
                raise TypeError('EepromDataType `{0}` expects an id'.format(field_name))
            bank, address = self._addr_tuple
        else:
            if self._addr_func is None:
                raise TypeError('EepromDataType `{0}` did not expect an id'.format(field_name))
            bank, address = self._addr_func(id)
        return EepromAddress(bank, address, length, self._shared, field_name)

    def get_name(self):
        """ Get the name of the EepromDataType. To be implemented in the subclass. """
        raise NotImplementedError()

    def decode(self, data):
        """ Convert a string of bytes to the desired type. To be implemented in the subclass. """
        raise NotImplementedError()

    def encode(self, field):
        """ Convert the field data to a string of bytes. To be implemented in the subclass. """
        raise NotImplementedError()

    def get_length(self):
        """ Get the length of the data type. """
        raise NotImplementedError()


def remove_tail(byte_str, delimiter='\xff'):
    """ Returns a new string where all instance of the delimiter at the end of the string are removed. """
    while len(byte_str) >= len(delimiter) and byte_str[-len(delimiter):] == delimiter:
        byte_str = byte_str[:-len(delimiter)]
    return byte_str


def append_tail(byte_str, length, delimiter='\xff'):
    """ Returns a new string with the given length by adding instances of the delimiter at the end
    of the string.
    """
    if len(byte_str) < length:
        return str(byte_str) + delimiter * ((length - len(byte_str)) // len(delimiter))
    return str(byte_str)


class EepromString(EepromDataType):
    """ A string with a given length. """

    def __init__(self, length, addr_gen, read_only=False, shared=False):
        super(EepromString, self).__init__(addr_gen, read_only, shared)
        self._length = length

    def get_name(self):
        return 'String[{0}]'.format(self._length)

    def decode(self, data):
        dirty = str(remove_tail(data))
        return ''.join([i if ord(i) < 128 else ' ' for i in dirty])

    def encode(self, field):
        if field is None:
            field = ''
        return append_tail(field, self._length)

    def get_length(self):
        return self._length


class EepromByte(EepromDataType):
    """ A byte. """

    def __init__(self, addr_gen, read_only=False):
        super(EepromByte, self).__init__(addr_gen, read_only)

    def get_name(self):
        return 'Byte'

    def decode(self, data):
        return ord(data[0])

    def encode(self, field):
        if field is None:
            field = 255
        return str(chr(field))

    def get_length(self):
        return 1


class EepromWord(EepromDataType):
    """ A word (2 bytes, converted to an integer). """

    def __init__(self, addr_gen, read_only=False):
        super(EepromWord, self).__init__(addr_gen, read_only)

    def get_name(self):
        return 'Word'

    def decode(self, data):
        return ord(data[1]) * 256 + ord(data[0])

    def encode(self, field):
        if field is None:
            field = 65535
        return ''.join([chr(int(field) % 256), chr(int(field) // 256)])

    def get_length(self):
        return 2


class EepromTemp(EepromDataType):
    """ A temperature (1 byte, converted to a float). """

    def __init__(self, addr_gen, read_only=False):
        super(EepromTemp, self).__init__(addr_gen, read_only)

    def get_name(self):
        return 'Temp'

    def decode(self, data):
        value = ord(data[0])
        if value == 255:
            return None
        return float(value) / 2 - 32

    def encode(self, field):
        if field is None:
            value = 255
        else:
            value = int((float(field) + 32) * 2)
            value = max(min(value, 255), 0)
        return str(chr(value))

    def get_length(self):
        return 1


class EepromSignedTemp(EepromDataType):
    """ A signed temperature (1 byte, converted to a float, from -7.5 to +7.5). """

    def __init__(self, addr_gen, read_only=False):
        super(EepromSignedTemp, self).__init__(addr_gen, read_only)

    def get_name(self):
        return 'SignedTemp(-7.5 to 7.5 degrees)'

    def decode(self, data):
        value = ord(data)
        if value == 255:
            return 0.0
        else:
            multiplier = 1 if value & 128 == 0 else -1
            return multiplier * float(value & 15) / 2.0

    def encode(self, field):
        if field is None or field == 0.0:
            return str(chr(255))
        elif field < -7.5 or field > 7.5:
            raise ValueError('SignedTemp should be in [-7.5, 7.5], was {0}'.format(field))
        else:
            offset = 0 if field > 0 else 128
            value = int(abs(field) * 2)
            return str(chr(offset + value))

    def get_length(self):
        return 1


class EepromTime(EepromDataType):
    """ A time (1 byte, converted into string HH:MM). """

    def __init__(self, addr_gen, read_only=False):
        super(EepromTime, self).__init__(addr_gen, read_only)

    def get_name(self):
        return 'Time'

    def decode(self, data):
        value = ord(data[0])
        hours = value / 6
        minutes = (value % 6) * 10
        return "{0:02d}:{1:02d}".format(hours, minutes)

    def encode(self, field):
        if field is None:
            return str(chr(255))
        split = [int(x) for x in field.split(':')]
        if len(split) != 2:
            raise ValueError('Time is not in HH:MM format: {0}'.format(field))
        field = (split[0] * 6) + (split[1] / 10)
        return str(chr(field))

    def get_length(self):
        return 1


class EepromCSV(EepromDataType):
    """ A list of bytes with a given length (converted to a string of comma separated integers).
    """

    def __init__(self, length, addr_gen, read_only=False):
        super(EepromCSV, self).__init__(addr_gen, read_only)
        self._length = length

    def get_name(self):
        return 'CSV[{0}]'.format(self._length)

    def decode(self, data):
        return ','.join([str(ord(b)) for b in remove_tail(data, '\xff')])

    def encode(self, field):
        actions = ''
        if field is not None and len(field) > 0:
            actions = ''.join([chr(int(x)) for x in field.split(",")])
        return append_tail(actions, self._length, '\xff')

    def get_length(self):
        return self._length


class EepromActions(EepromDataType):
    """
    A list of basic actions with a given length (2 bytes each, converted to a string of comma
    separated integers).
    """

    def __init__(self, length, addr_gen, read_only=False):
        super(EepromActions, self).__init__(addr_gen, read_only)
        self._length = length

    def get_name(self):
        return 'Actions[{0}]'.format(self._length)

    def decode(self, data):
        return ','.join([str(ord(b)) for b in remove_tail(data, '\xff\xff')])

    def encode(self, field):
        actions = ''
        if field is not None and len(field) > 0:
            actions = ''.join([chr(int(x)) for x in field.split(',')])
        return append_tail(actions, 2 * self._length, '\xff\xff')

    def get_length(self):
        return 2 * self._length


class EepromIBool(EepromDataType):
    """ A boolean that is encoded in a byte where value 255 is False and values < 255 are True. """

    def __init__(self, addr_gen, read_only=False):
        super(EepromIBool, self).__init__(addr_gen, read_only)

    def get_name(self):
        return 'Boolean'

    def decode(self, data):
        return ord(data[0]) < 255

    def encode(self, field):
        value = 0 if field is True else 255
        return str(chr(value))

    def get_length(self):
        return 1


class EepromEnum(EepromDataType):
    """ A enum value that is encoded into a byte. """

    def __init__(self, addr_gen, enum_values, read_only=False):
        super(EepromEnum, self).__init__(addr_gen, read_only)
        self._enum_values = enum_values

    def get_name(self):
        return 'Enum'

    def decode(self, data):
        index = ord(data[0])
        if index in self._enum_values.keys():
            return self._enum_values[index]
        return 'UNKNOWN'

    def encode(self, field):
        for key, value in self._enum_values.items():
            if field == value:
                return str(chr(key))
        return str(chr(255))

    def get_length(self):
        return 1


class EextDataContainer(object):
    """ Data container instance """

    def __init__(self, data_type):
        self._data = None
        self._data_type = data_type

    def load_bytes(self, data):
        self._data = data

    def get_bytes(self):
        return self._data

    def serialize(self):
        if self._data is None:
            return self._data_type.default_value()
        return self._data_type.decode(self._data)

    def deserialize(self, data, check_writability=True):
        _ = check_writability
        self._data = self._data_type.encode(data)


class EextDataType(object):
    """ Classes that are eeprom extensions should inherit from EextDataType. """

    def get_name(self):
        """ Get the name of the EextDataType. To be implemented in the subclass. """
        raise NotImplementedError()

    def default_value(self):
        """ Get the default value for this data type. To be implemented in the subclass. """
        raise NotImplementedError()

    def decode(self, value):
        """ Deserializes the database string value into the appropriate data type. To be implemented in the subclass. """
        raise NotImplementedError()

    def encode(self, value):
        """ Serializes the data type into the database string value. To be implemented in the subclass. """
        raise NotImplementedError()


class EextByte(EextDataType):
    """ An byte field, stored in the eeprom extension database. """

    def get_name(self):
        return 'Byte'

    def default_value(self):
        return 255

    def decode(self, value):
        return int(value)

    def encode(self, value):
        if value is None:
            value = self.default_value()
        return str(value)


class EextWord(EextDataType):
    """ An word field, stored in the eeprom extension database. """

    def get_name(self):
        return 'Word'

    def default_value(self):
        return 65535

    def decode(self, value):
        return int(value)

    def encode(self, value):
        if value is None:
            value = self.default_value()
        return str(value)


class EextString(EextDataType):
    """ An string field, stored in the eeprom extension database. """

    def get_name(self):
        return 'String'

    def default_value(self):
        return ''

    def decode(self, value):
        return value

    def encode(self, value):
        if value is None:
            value = self.default_value()
        return value


class EextBool(EextDataType):
    """ A Boolean field, stored in the eeprom extension database. """

    def get_name(self):
        return 'Boolean'

    def default_value(self):
        return False

    def decode(self, value):
        return value == 'True'

    def encode(self, value):
        if value is None:
            value = self.default_value()
        return str(value)
