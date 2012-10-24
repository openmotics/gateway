'''
Tests for the passthrough module.

Created on Sep 24, 2012

@author: fryckbos
'''
import unittest
import time

import master_api
from serial_mock import SerialMock, sout, sin
from master_communicator import MasterCommunicator
from passthrough import PassthroughService

class PassthroughServiceTest(unittest.TestCase):
    """ Tests for :class`PassthroughService`. """

    def test_passthrough(self):
        """ Test the passthrough. """
        master_mock = SerialMock([
                        sout("data for the passthrough"), sin("response"),
                        sout("more data"), sin("more response") ])
        
        passthrough_mock = SerialMock([
                        sin("data for the passthrough"), sout("response"),
                        sin("more data"), sout("more response") ])        
        
        master_communicator = MasterCommunicator(master_mock, init_master=False)
        master_communicator.start()
        
        passthrough = PassthroughService(master_communicator, passthrough_mock)
        passthrough.start()
        
        time.sleep(0.1)
        
        self.assertEquals(33, master_communicator.get_bytes_read())
        self.assertEquals(21, master_communicator.get_bytes_written())
        
        self.assertEquals(33, master_mock.bytes_read)
        self.assertEquals(21, master_mock.bytes_written)
        
        self.assertEquals(21, passthrough_mock.bytes_read)
        self.assertEquals(33, passthrough_mock.bytes_written)
        
        passthrough.stop()


if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()