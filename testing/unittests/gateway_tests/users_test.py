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
Tests for the users module.

@author: fryckbos
"""

from __future__ import absolute_import

import time
import unittest

from peewee import SqliteDatabase
from pytest import mark

from gateway.dto import UserDTO
from gateway.enums import UserEnums
from gateway.mappers.user import UserMapper
from gateway.models import User
from gateway.user_controller import UserController
from ioc import SetTestMode, SetUpTestInjections

MODELS = [User]


class UserControllerTest(unittest.TestCase):
    """ Tests for UserController. """

    @classmethod
    def setUpClass(cls):
        SetTestMode()
        cls.test_db = SqliteDatabase(':memory:')

    def setUp(self):
        self.test_db.bind(MODELS, bind_refs=False, bind_backrefs=False)
        self.test_db.connect()
        self.test_db.create_tables(MODELS)
        SetUpTestInjections(config={'username': 'om', 'password': 'pass'},
                            token_timeout=3)
        self.controller = UserController()
        self.controller.start()

    def tearDown(self):
        self.controller.stop()
        self.test_db.drop_tables(MODELS)
        self.test_db.close()

    def test_empty(self):
        """ Test an empty database. """
        # setup test credentials
        user_dto = UserDTO("fred")
        user_dto.set_password("test")

        # verify that the test credentials do not work
        success, data = self.controller.login(user_dto)
        self.assertFalse(success)
        self.assertEqual(data, UserEnums.AuthenticationErrors.INVALID_CREDENTIALS)

        # check that a random token is not valid when empty
        self.assertEqual(False, self.controller.check_token('some token 123'))

        # create the cloud user credentials
        user_dto = UserDTO("om")
        user_dto.set_password("pass")

        # verfify that the cloud user can login
        success, data = self.controller.login(user_dto)
        self.assertTrue(success)
        self.assertNotEquals(None, data)

        # verify that the cloud user token is valid.
        self.assertTrue(self.controller.check_token(data))

    def test_terms(self):
        """ Tests acceptance of the terms """
        # adding test user to the DB
        user_to_add = User(
            username='test',
            password=UserDTO._hash_password('test'),
            accepted_terms=False
        )
        user_to_add.save()

        # setup test credentials
        user_dto = UserDTO(username='test')
        user_dto.set_password('test')

        # check if login is possible
        success, data = self.controller.login(user_dto)
        self.assertFalse(success)
        self.assertEqual(data, 'terms_not_accepted')

        # login with accepted terms fields set
        success, data = self.controller.login(user_dto, accept_terms=True)
        self.assertTrue(success)
        self.assertIsNotNone(data)

        # login again to see if fields has been saved
        success, data = self.controller.login(user_dto)
        self.assertTrue(success)
        self.assertIsNotNone(data)

    def test_all(self):
        """ Test all methods of UserController. """
        fields = ['username', 'password', 'accepted_terms']

        # create a new user to test with
        user_dto = UserDTO(username='fred')
        user_dto.set_password('test')
        self.controller.save_users([(user_dto, fields)])

        # check if the user has been added to the list
        users_in_controller = self.controller.load_users()
        self.assertEqual(2, len(users_in_controller))
        self.assertEqual('om', users_in_controller[0].username)
        self.assertEqual('fred', users_in_controller[1].username)
        self.assertEqual(2, self.controller.get_number_of_users())

        # try if user is able to login without accepting the terms
        success, token = self.controller.login(user_dto)
        self.assertEqual(False, success)
        self.assertEqual(UserEnums.AuthenticationErrors.TERMS_NOT_ACCEPTED, token)
        self.assertFalse(self.controller.check_token('blah'))

        # try if the user is able to login with terms accepted
        success, token = self.controller.login(user_dto, accept_terms=True)
        self.assertEqual(True, success)
        self.assertNotEquals(None, token)

        # check if the token is valid
        self.assertTrue(self.controller.check_token(token))
        self.assertFalse(self.controller.check_token('blah'))

        # try to logout the user
        self.controller.logout(token)
        self.assertFalse(self.controller.check_token(token))

        # try if the user is able to login with terms accepted
        success, token = self.controller.login(user_dto, accept_terms=True)
        self.assertEqual(True, success)
        self.assertNotEquals(None, token)

        # try to remove the user
        self.controller.remove_user(user_dto)
        self.assertFalse(self.controller.check_token(token))

        # check if the user has been deleted
        users_in_controller = self.controller.load_users()
        self.assertEqual(1, len(users_in_controller))
        self.assertEqual('om', users_in_controller[0].username)
        self.assertEqual(1, self.controller.get_number_of_users())

        # try if the user is able to login with terms accepted
        success, token = self.controller.login(user_dto, accept_terms=True)
        self.assertEqual(False, success)
        self.assertEqual(UserEnums.AuthenticationErrors.INVALID_CREDENTIALS, token)

        # create multiple new users
        users_dto = []
        user_dto = UserDTO(username='simon')
        user_dto.set_password('test')
        users_dto.append(user_dto)
        user_dto = UserDTO(username='test')
        user_dto.set_password('test')
        users_dto.append(user_dto)
        to_save_users = [(ud, fields) for ud in users_dto]
        self.controller.save_users(to_save_users)

        # check if the user has been deleted
        users_in_controller = self.controller.load_users()
        self.assertEqual(3, len(users_in_controller))
        self.assertEqual('om', users_in_controller[0].username)
        self.assertEqual('simon', users_in_controller[1].username)
        self.assertEqual('test', users_in_controller[2].username)
        self.assertEqual(3, self.controller.get_number_of_users())

        # try if the user is able to login with terms accepted
        user_dto = UserDTO(username='simon')
        user_dto.set_password('test')
        success, token = self.controller.login(user_dto, accept_terms=True)
        self.assertEqual(True, success)
        self.assertNotEquals(None, token)

        # try to logout with the new users
        self.controller.logout(token)
        self.assertFalse(self.controller.check_token(token))

    @mark.slow
    def test_token_timeout(self):
        """ Test the timeout on the tokens. """

        # Setup credentials
        user_dto = UserDTO(username='om')
        user_dto.set_password('pass')
        # verify that the user can login
        success, token = self.controller.login(user_dto, accept_terms=True)
        self.assertEqual(True, success)
        self.assertNotEquals(None, token)

        # verify that the token is still valid
        self.assertTrue(self.controller.check_token(token))

        time.sleep(4)

        # verify that the token is no longer valid after timeout
        self.assertFalse(self.controller.check_token(token))

        # login again tot verify that the token is then again valid
        success, token = self.controller.login(user_dto, accept_terms=True)
        self.assertTrue(success)
        self.assertNotEquals(None, token)
        self.assertTrue(self.controller.check_token(token))

    def test_logout(self):
        """ Test logout. """
        # Setup the user credentials
        user_dto = UserDTO(username='om')
        user_dto.set_password('pass')

        # test to see if you are able to login
        success, token = self.controller.login(user_dto, accept_terms=True)
        self.assertTrue(success)
        self.assertNotEquals(None, token)
        self.assertTrue(self.controller.check_token(token))

        # check if after logout te user has a valid token
        self.controller.logout(token)
        self.assertFalse(self.controller.check_token(token))

    def test_load_users(self):
        """ Test getting all usernames. """
        # get first list of users in the user controller
        users_in_controller = self.controller.load_users()
        self.assertEqual(1, len(users_in_controller))
        self.assertEqual('om', users_in_controller[0].username)

        user_to_add = User(
            username='test',
            password=UserDTO._hash_password('test'),
            accepted_terms=True
        )
        user_to_add.save()

        # check if the user has been added to the list
        users_in_controller = self.controller.load_users()
        self.assertEqual(2, len(users_in_controller))
        self.assertEqual('om', users_in_controller[0].username)
        self.assertEqual('test', users_in_controller[1].username)

        # check if the number of users is correct
        num_users = self.controller.get_number_of_users()
        self.assertEqual(2, num_users)


    def test_remove_user(self):
        """ Test removing a user. """
        # check that there is only one user in the system
        users_in_controller = self.controller.load_users()
        self.assertEqual(1, len(users_in_controller))
        self.assertEqual('om', users_in_controller[0].username)
        self.assertEqual(1, self.controller.get_number_of_users())

        # create a new user to test with
        user_to_add = User(
            username='test',
            password=UserDTO._hash_password('test'),
            accepted_terms=True
        )
        user_to_add.save()

        # creating equal credentials to use
        user_dto = UserDTO(username='test')
        user_dto.set_password('test')

        # verify that the user has been added
        users_in_controller = self.controller.load_users()
        self.assertEqual(2, len(users_in_controller))
        self.assertEqual('om', users_in_controller[0].username)
        self.assertEqual('test', users_in_controller[1].username)
        self.assertEqual(2, self.controller.get_number_of_users())

        # verify that the new user can log in to the system
        success, token = self.controller.login(user_dto, accept_terms=True)
        self.assertTrue(success)
        self.assertTrue(self.controller.check_token(token))

        # remove the newly created user
        self.controller.remove_user(user_dto)

        # Verify that the user is logged out of the system
        self.assertFalse(self.controller.check_token(token))

        # verify that the user is deleted from the system
        users_in_controller = self.controller.load_users()
        self.assertEqual(1, len(users_in_controller))
        self.assertEqual('om', users_in_controller[0].username)
        self.assertEqual(1, self.controller.get_number_of_users())

        # verify that the last user cannot be deleted.
        try:
            last_user_dto = UserDTO(username='om')
            self.controller.remove_user(last_user_dto)
            self.fail('Should have raised exception !')
        except Exception as exception:
            self.assertEqual(UserEnums.DeleteErrors.LAST_ACCOUNT, str(exception))

    def test_case_insensitive(self):
        """ Test the case insensitivity of the username. """
        fields = ['username', 'password', 'accepted_terms']

        # check that there is only one user in the system
        users_in_controller = self.controller.load_users()
        self.assertEqual(1, len(users_in_controller))
        self.assertEqual('om', users_in_controller[0].username)

        # create a new user to test with
        user_dto = UserDTO(username='test')
        user_dto.set_password('test')
        self.controller.save_users([(user_dto, fields)])

        # verify that the user can log in with regular username
        success, token = self.controller.login(user_dto, accept_terms=True)
        self.assertTrue(success)
        self.assertTrue(self.controller.check_token(token))

        # verify that the user can log in with capitals
        user_dto.username = 'TeSt'
        success, token = self.controller.login(user_dto)
        self.assertTrue(success)
        self.assertTrue(self.controller.check_token(token))

        # verify that the user can not login with password with changed capitals
        user_dto.set_password('TeSt')
        success, token = self.controller.login(user_dto)
        self.assertFalse(success)
        self.assertEqual(UserEnums.AuthenticationErrors.INVALID_CREDENTIALS, token)

        # verify that the user has been added
        users_in_controller = self.controller.load_users()
        self.assertEqual(2, len(users_in_controller))
        self.assertEqual('om', users_in_controller[0].username)
        self.assertEqual('test', users_in_controller[1].username)
        self.assertEqual(2, self.controller.get_number_of_users())

    def test_usermapper(self):
        user_dto = UserDTO(username='test', accepted_terms=1)
        user_dto.set_password('test')

        user_orm = UserMapper.dto_to_orm(user_dto, ['username', 'password'])

        self.assertEqual(True, hasattr(user_orm, "username"))
        self.assertEqual(True, hasattr(user_orm, "password"))
        self.assertEqual(True, hasattr(user_orm, "accepted_terms"))

        self.assertEqual('test', user_orm.username)
        self.assertEqual(UserDTO._hash_password('test'), user_orm.password)
        self.assertEqual(0, user_orm.accepted_terms)

        user_dto = UserMapper.orm_to_dto(user_orm)
        self.assertEqual('test', user_dto.username)
        self.assertEqual(user_orm.password, user_dto.hashed_password)
        self.assertEqual(0, user_dto.accepted_terms)
