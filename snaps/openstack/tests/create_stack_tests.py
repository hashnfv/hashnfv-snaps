# Copyright (c) 2017 Cable Television Laboratories, Inc. ("CableLabs")
#                    and others.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at:
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import time

import pkg_resources
from heatclient.exc import HTTPBadRequest
from snaps import file_utils
from snaps.openstack.create_flavor import OpenStackFlavor, FlavorSettings
from snaps.openstack.create_image import OpenStackImage

try:
    from urllib.request import URLError
except ImportError:
    from urllib2 import URLError

import logging
import unittest
import uuid

from snaps.openstack import create_stack
from snaps.openstack.create_stack import (
    StackSettings, StackSettingsError, StackCreationError)
from snaps.openstack.tests import openstack_tests, create_instance_tests
from snaps.openstack.tests.os_source_file_test import OSIntegrationTestCase
from snaps.openstack.utils import heat_utils, neutron_utils, nova_utils

__author__ = 'spisarski'

logger = logging.getLogger('create_stack_tests')


class StackSettingsUnitTests(unittest.TestCase):
    """
    Tests the construction of the StackSettings class
    """

    def test_no_params(self):
        with self.assertRaises(StackSettingsError):
            StackSettings()

    def test_empty_config(self):
        with self.assertRaises(StackSettingsError):
            StackSettings(**dict())

    def test_name_only(self):
        with self.assertRaises(StackSettingsError):
            StackSettings(name='foo')

    def test_config_with_name_only(self):
        with self.assertRaises(StackSettingsError):
            StackSettings(**{'name': 'foo'})

    def test_config_minimum_template(self):
        settings = StackSettings(**{'name': 'stack', 'template': 'foo'})
        self.assertEqual('stack', settings.name)
        self.assertEqual('foo', settings.template)
        self.assertIsNone(settings.template_path)
        self.assertIsNone(settings.env_values)
        self.assertEqual(create_stack.STACK_COMPLETE_TIMEOUT,
                         settings.stack_create_timeout)

    def test_config_minimum_template_path(self):
        settings = StackSettings(**{'name': 'stack', 'template_path': 'foo'})
        self.assertEqual('stack', settings.name)
        self.assertIsNone(settings.template)
        self.assertEqual('foo', settings.template_path)
        self.assertIsNone(settings.env_values)
        self.assertEqual(create_stack.STACK_COMPLETE_TIMEOUT,
                         settings.stack_create_timeout)

    def test_minimum_template(self):
        settings = StackSettings(name='stack', template='foo')
        self.assertEqual('stack', settings.name)
        self.assertEqual('foo', settings.template)
        self.assertIsNone(settings.template_path)
        self.assertIsNone(settings.env_values)
        self.assertEqual(create_stack.STACK_COMPLETE_TIMEOUT,
                         settings.stack_create_timeout)

    def test_minimum_template_path(self):
        settings = StackSettings(name='stack', template_path='foo')
        self.assertEqual('stack', settings.name)
        self.assertEqual('foo', settings.template_path)
        self.assertIsNone(settings.template)
        self.assertIsNone(settings.env_values)
        self.assertEqual(create_stack.STACK_COMPLETE_TIMEOUT,
                         settings.stack_create_timeout)

    def test_all(self):
        env_values = {'foo': 'bar'}
        settings = StackSettings(name='stack', template='bar',
                                 template_path='foo', env_values=env_values,
                                 stack_create_timeout=999)
        self.assertEqual('stack', settings.name)
        self.assertEqual('bar', settings.template)
        self.assertEqual('foo', settings.template_path)
        self.assertEqual(env_values, settings.env_values)
        self.assertEqual(999, settings.stack_create_timeout)

    def test_config_all(self):
        env_values = {'foo': 'bar'}
        settings = StackSettings(
            **{'name': 'stack', 'template': 'bar', 'template_path': 'foo',
               'env_values': env_values, 'stack_create_timeout': 999})
        self.assertEqual('stack', settings.name)
        self.assertEqual('bar', settings.template)
        self.assertEqual('foo', settings.template_path)
        self.assertEqual(env_values, settings.env_values)
        self.assertEqual(999, settings.stack_create_timeout)


class CreateStackSuccessTests(OSIntegrationTestCase):
    """
    Tests for the CreateStack class defined in create_stack.py
    """

    def setUp(self):
        """
        Instantiates the CreateStack object that is responsible for downloading
        and creating an OS stack file within OpenStack
        """
        super(self.__class__, self).__start__()

        self.guid = self.__class__.__name__ + '-' + str(uuid.uuid4())

        self.heat_creds = self.admin_os_creds
        self.heat_creds.project_name = self.admin_os_creds.project_name

        self.heat_cli = heat_utils.heat_client(self.heat_creds)
        self.stack_creator = None

        self.image_creator = OpenStackImage(
            self.heat_creds, openstack_tests.cirros_image_settings(
                name=self.guid + '-image',
                image_metadata=self.image_metadata))
        self.image_creator.create()

        # Create Flavor
        self.flavor_creator = OpenStackFlavor(
            self.admin_os_creds,
            FlavorSettings(name=self.guid + '-flavor-name', ram=256, disk=10,
                           vcpus=1))
        self.flavor_creator.create()

        self.network_name = self.guid + '-net'
        self.subnet_name = self.guid + '-subnet'
        self.vm_inst_name = self.guid + '-inst'

        self.env_values = {
            'image_name': self.image_creator.image_settings.name,
            'flavor_name': self.flavor_creator.flavor_settings.name,
            'net_name': self.network_name,
            'subnet_name': self.subnet_name,
            'inst_name': self.vm_inst_name}

        self.heat_tmplt_path = pkg_resources.resource_filename(
            'snaps.openstack.tests.heat', 'test_heat_template.yaml')

    def tearDown(self):
        """
        Cleans the stack and downloaded stack file
        """
        if self.stack_creator:
            try:
                self.stack_creator.clean()
            except:
                pass

        if self.image_creator:
            try:
                self.image_creator.clean()
            except:
                pass

        if self.flavor_creator:
            try:
                self.flavor_creator.clean()
            except:
                pass

        super(self.__class__, self).__clean__()

    def test_create_stack_template_file(self):
        """
        Tests the creation of an OpenStack stack from Heat template file.
        """
        # Create Stack
        # Set the default stack settings, then set any custom parameters sent
        # from the app
        stack_settings = StackSettings(
            name=self.__class__.__name__ + '-' + str(self.guid) + '-stack',
            template_path=self.heat_tmplt_path,
            env_values=self.env_values)
        self.stack_creator = create_stack.OpenStackHeatStack(self.heat_creds,
                                                             stack_settings)
        created_stack = self.stack_creator.create()
        self.assertIsNotNone(created_stack)

        retrieved_stack = heat_utils.get_stack_by_id(self.heat_cli,
                                                     created_stack.id)
        self.assertIsNotNone(retrieved_stack)
        self.assertEqual(created_stack.name, retrieved_stack.name)
        self.assertEqual(created_stack.id, retrieved_stack.id)
        self.assertEqual(0, len(self.stack_creator.get_outputs()))

    def test_create_stack_short_timeout(self):
        """
        Tests the creation of an OpenStack stack from Heat template file.
        """
        # Create Stack
        # Set the default stack settings, then set any custom parameters sent
        # from the app
        stack_settings = StackSettings(
            name=self.__class__.__name__ + '-' + str(self.guid) + '-stack',
            template_path=self.heat_tmplt_path,
            env_values=self.env_values, stack_create_timeout=0)

        self.stack_creator = create_stack.OpenStackHeatStack(self.heat_creds,
                                                             stack_settings)
        with self.assertRaises(StackCreationError):
            self.stack_creator.create()

    def test_create_stack_template_dict(self):
        """
        Tests the creation of an OpenStack stack from a heat dict() object.
        """
        # Create Stack
        # Set the default stack settings, then set any custom parameters sent
        # from the app
        template_dict = heat_utils.parse_heat_template_str(
            file_utils.read_file(self.heat_tmplt_path))
        stack_settings = StackSettings(
            name=self.__class__.__name__ + '-' + str(self.guid) + '-stack',
            template=template_dict,
            env_values=self.env_values)
        self.stack_creator = create_stack.OpenStackHeatStack(self.heat_creds,
                                                             stack_settings)
        created_stack = self.stack_creator.create()
        self.assertIsNotNone(created_stack)

        retrieved_stack = heat_utils.get_stack_by_id(self.heat_cli,
                                                     created_stack.id)
        self.assertIsNotNone(retrieved_stack)
        self.assertEqual(created_stack.name, retrieved_stack.name)
        self.assertEqual(created_stack.id, retrieved_stack.id)
        self.assertEqual(0, len(self.stack_creator.get_outputs()))

    def test_create_delete_stack(self):
        """
        Tests the creation then deletion of an OpenStack stack to ensure
        clean() does not raise an Exception.
        """
        # Create Stack
        template_dict = heat_utils.parse_heat_template_str(
            file_utils.read_file(self.heat_tmplt_path))
        stack_settings = StackSettings(
            name=self.__class__.__name__ + '-' + str(self.guid) + '-stack',
            template=template_dict,
            env_values=self.env_values)
        self.stack_creator = create_stack.OpenStackHeatStack(self.heat_creds,
                                                             stack_settings)
        created_stack = self.stack_creator.create()
        self.assertIsNotNone(created_stack)

        retrieved_stack = heat_utils.get_stack_by_id(self.heat_cli,
                                                     created_stack.id)
        self.assertIsNotNone(retrieved_stack)
        self.assertEqual(created_stack.name, retrieved_stack.name)
        self.assertEqual(created_stack.id, retrieved_stack.id)
        self.assertEqual(0, len(self.stack_creator.get_outputs()))
        self.assertEqual(create_stack.STATUS_CREATE_COMPLETE,
                         self.stack_creator.get_status())

        # Delete Stack manually
        heat_utils.delete_stack(self.heat_cli, created_stack)

        end_time = time.time() + 90
        deleted = False
        while time.time() < end_time:
            status = heat_utils.get_stack_status(self.heat_cli,
                                                 retrieved_stack.id)
            if status == create_stack.STATUS_DELETE_COMPLETE:
                deleted = True
                break

        self.assertTrue(deleted)

        # Must not throw an exception when attempting to cleanup non-existent
        # stack
        self.stack_creator.clean()
        self.assertIsNone(self.stack_creator.get_stack())

    def test_create_same_stack(self):
        """
        Tests the creation of an OpenStack stack when the stack already exists.
        """
        # Create Stack
        template_dict = heat_utils.parse_heat_template_str(
            file_utils.read_file(self.heat_tmplt_path))
        stack_settings = StackSettings(
            name=self.__class__.__name__ + '-' + str(self.guid) + '-stack',
            template=template_dict,
            env_values=self.env_values)
        self.stack_creator = create_stack.OpenStackHeatStack(self.heat_creds,
                                                             stack_settings)
        created_stack1 = self.stack_creator.create()

        retrieved_stack = heat_utils.get_stack_by_id(self.heat_cli,
                                                     created_stack1.id)
        self.assertIsNotNone(retrieved_stack)
        self.assertEqual(created_stack1.name, retrieved_stack.name)
        self.assertEqual(created_stack1.id, retrieved_stack.id)
        self.assertEqual(0, len(self.stack_creator.get_outputs()))

        # Should be retrieving the instance data
        stack_creator2 = create_stack.OpenStackHeatStack(self.heat_creds,
                                                         stack_settings)
        stack2 = stack_creator2.create()
        self.assertEqual(created_stack1.id, stack2.id)

    def test_retrieve_network_creators(self):
        """
        Tests the creation of an OpenStack stack from Heat template file and
        the retrieval of the network creator.
        """
        stack_settings = StackSettings(
            name=self.__class__.__name__ + '-' + str(self.guid) + '-stack',
            template_path=self.heat_tmplt_path,
            env_values=self.env_values)
        self.stack_creator = create_stack.OpenStackHeatStack(self.heat_creds,
                                                             stack_settings)
        created_stack = self.stack_creator.create()
        self.assertIsNotNone(created_stack)

        net_creators = self.stack_creator.get_network_creators()
        self.assertIsNotNone(net_creators)
        self.assertEqual(1, len(net_creators))
        self.assertEqual(self.network_name, net_creators[0].get_network().name)

        neutron = neutron_utils.neutron_client(self.os_creds)
        net_by_name = neutron_utils.get_network(
            neutron, network_name=net_creators[0].get_network().name)
        self.assertEqual(net_creators[0].get_network(), net_by_name)
        self.assertIsNotNone(neutron_utils.get_network_by_id(
            neutron, net_creators[0].get_network().id))

        self.assertEqual(1, len(net_creators[0].get_subnets()))
        subnet = net_creators[0].get_subnets()[0]
        subnet_by_name = neutron_utils.get_subnet(
            neutron, subnet_name=subnet.name)
        self.assertEqual(subnet, subnet_by_name)

        subnet_by_id = neutron_utils.get_subnet_by_id(neutron, subnet.id)
        self.assertIsNotNone(subnet_by_id)
        self.assertEqual(subnet_by_name, subnet_by_id)

    def test_retrieve_vm_inst_creators(self):
        """
        Tests the creation of an OpenStack stack from Heat template file and
        the retrieval of the network creator.
        """
        stack_settings = StackSettings(
            name=self.__class__.__name__ + '-' + str(self.guid) + '-stack',
            template_path=self.heat_tmplt_path,
            env_values=self.env_values)
        self.stack_creator = create_stack.OpenStackHeatStack(self.heat_creds,
                                                             stack_settings)
        created_stack = self.stack_creator.create()
        self.assertIsNotNone(created_stack)

        vm_inst_creators = self.stack_creator.get_vm_inst_creators()
        self.assertIsNotNone(vm_inst_creators)
        self.assertEqual(1, len(vm_inst_creators))
        self.assertEqual(self.vm_inst_name,
                         vm_inst_creators[0].get_vm_inst().name)

        nova = nova_utils.nova_client(self.admin_os_creds)
        vm_inst_by_name = nova_utils.get_server(
            nova, server_name=vm_inst_creators[0].get_vm_inst().name)
        self.assertEqual(vm_inst_creators[0].get_vm_inst(), vm_inst_by_name)
        self.assertIsNotNone(nova_utils.get_server_object_by_id(
            nova, vm_inst_creators[0].get_vm_inst().id))


class CreateStackFloatingIpTests(OSIntegrationTestCase):
    """
    Tests for the CreateStack class defined in create_stack.py
    """

    def setUp(self):
        """
        Instantiates the CreateStack object that is responsible for downloading
        and creating an OS stack file within OpenStack
        """
        super(self.__class__, self).__start__()

        self.guid = self.__class__.__name__ + '-' + str(uuid.uuid4())

        self.heat_creds = self.admin_os_creds
        self.heat_creds.project_name = self.admin_os_creds.project_name

        self.heat_cli = heat_utils.heat_client(self.heat_creds)
        self.stack_creator = None

        self.image_creator = OpenStackImage(
            self.heat_creds, openstack_tests.cirros_image_settings(
                name=self.guid + '-image',
                image_metadata=self.image_metadata))
        self.image_creator.create()

        self.network_name = self.guid + '-net'
        self.subnet_name = self.guid + '-subnet'
        self.flavor1_name = self.guid + '-flavor1'
        self.flavor2_name = self.guid + '-flavor2'
        self.sec_grp_name = self.guid + '-sec_grp'
        self.vm_inst1_name = self.guid + '-inst1'
        self.vm_inst2_name = self.guid + '-inst2'
        self.keypair_name = self.guid + '-kp'

        self.env_values = {
            'image1_name': self.image_creator.image_settings.name,
            'image2_name': self.image_creator.image_settings.name,
            'flavor1_name': self.flavor1_name,
            'flavor2_name': self.flavor2_name,
            'net_name': self.network_name,
            'subnet_name': self.subnet_name,
            'inst1_name': self.vm_inst1_name,
            'inst2_name': self.vm_inst2_name,
            'keypair_name': self.keypair_name,
            'external_net_name': self.ext_net_name,
            'security_group_name': self.sec_grp_name}

        self.heat_tmplt_path = pkg_resources.resource_filename(
            'snaps.openstack.tests.heat', 'floating_ip_heat_template.yaml')

    def tearDown(self):
        """
        Cleans the stack and downloaded stack file
        """
        if self.stack_creator:
            try:
                self.stack_creator.clean()
            except:
                pass

        if self.image_creator:
            try:
                self.image_creator.clean()
            except:
                pass

        super(self.__class__, self).__clean__()

    def test_connect_via_ssh_heat_vm(self):
        """
        Tests the creation of an OpenStack stack from Heat template file and
        the retrieval of two VM instance creators and attempt to connect via
        SSH to the first one with a floating IP.
        """
        stack_settings = StackSettings(
            name=self.__class__.__name__ + '-' + str(self.guid) + '-stack',
            template_path=self.heat_tmplt_path,
            env_values=self.env_values)
        self.stack_creator = create_stack.OpenStackHeatStack(
            self.heat_creds, stack_settings,
            [self.image_creator.image_settings])
        created_stack = self.stack_creator.create()
        self.assertIsNotNone(created_stack)

        vm_inst_creators = self.stack_creator.get_vm_inst_creators(
            heat_keypair_option='private_key')
        self.assertIsNotNone(vm_inst_creators)
        self.assertEqual(2, len(vm_inst_creators))

        for vm_inst_creator in vm_inst_creators:
            if vm_inst_creator.get_vm_inst().name == self.vm_inst1_name:
                self.assertTrue(
                    create_instance_tests.validate_ssh_client(vm_inst_creator))
            else:
                vm_settings = vm_inst_creator.instance_settings
                self.assertEqual(0, len(vm_settings.floating_ip_settings))


class CreateStackVolumeTests(OSIntegrationTestCase):
    """
    Tests for the CreateStack class defined in create_stack.py
    """

    def setUp(self):
        """
        Instantiates the CreateStack object that is responsible for downloading
        and creating an OS stack file within OpenStack
        """
        super(self.__class__, self).__start__()

        self.guid = self.__class__.__name__ + '-' + str(uuid.uuid4())

        self.heat_creds = self.admin_os_creds
        self.heat_creds.project_name = self.admin_os_creds.project_name

        self.heat_cli = heat_utils.heat_client(self.heat_creds)
        self.stack_creator = None

        self.volume_name = self.guid + '-volume'
        self.volume_type_name = self.guid + '-volume-type'

        self.env_values = {
            'volume_name': self.volume_name,
            'volume_type_name': self.volume_type_name}

        self.heat_tmplt_path = pkg_resources.resource_filename(
            'snaps.openstack.tests.heat', 'volume_heat_template.yaml')

        stack_settings = StackSettings(
            name=self.__class__.__name__ + '-' + str(self.guid) + '-stack',
            template_path=self.heat_tmplt_path,
            env_values=self.env_values)
        self.stack_creator = create_stack.OpenStackHeatStack(
            self.heat_creds, stack_settings)
        self.created_stack = self.stack_creator.create()
        self.assertIsNotNone(self.created_stack)

    def tearDown(self):
        """
        Cleans the stack and downloaded stack file
        """
        if self.stack_creator:
            try:
                self.stack_creator.clean()
            except:
                pass

        super(self.__class__, self).__clean__()

    def test_retrieve_volume_creator(self):
        """
        Tests the creation of an OpenStack stack from Heat template file and
        the retrieval of an OpenStackVolume creator/state machine instance
        """
        volume_creators = self.stack_creator.get_volume_creators()
        self.assertEqual(1, len(volume_creators))

        creator = volume_creators[0]
        self.assertEqual(self.volume_name, creator.volume_settings.name)
        self.assertEqual(self.volume_name, creator.get_volume().name)
        self.assertEqual(self.volume_type_name,
                         creator.volume_settings.type_name)
        self.assertEqual(self.volume_type_name, creator.get_volume().type)
        self.assertEqual(1, creator.volume_settings.size)
        self.assertEqual(1, creator.get_volume().size)

    def test_retrieve_volume_type_creator(self):
        """
        Tests the creation of an OpenStack stack from Heat template file and
        the retrieval of an OpenStackVolume creator/state machine instance
        """
        volume_type_creators = self.stack_creator.get_volume_type_creators()
        self.assertEqual(1, len(volume_type_creators))

        creator = volume_type_creators[0]
        self.assertIsNotNone(creator)

        volume_type = creator.get_volume_type()
        self.assertIsNotNone(volume_type)

        self.assertEqual(self.volume_type_name, volume_type.name)
        self.assertTrue(volume_type.public)
        self.assertIsNone(volume_type.qos_spec)

        encryption = volume_type.encryption
        self.assertIsNotNone(encryption)
        self.assertIsNone(encryption.cipher)
        self.assertEqual('front-end', encryption.control_location)
        self.assertIsNone(encryption.key_size)
        self.assertEqual(u'nova.volume.encryptors.luks.LuksEncryptor',
                         encryption.provider)
        self.assertEqual(volume_type.id, encryption.volume_type_id)


class CreateStackNegativeTests(OSIntegrationTestCase):
    """
    Negative test cases for the CreateStack class
    """

    def setUp(self):
        super(self.__class__, self).__start__()

        self.heat_creds = self.admin_os_creds
        self.heat_creds.project_name = self.admin_os_creds.project_name

        self.stack_name = self.__class__.__name__ + '-' + str(uuid.uuid4())
        self.stack_creator = None
        self.heat_tmplt_path = pkg_resources.resource_filename(
            'snaps.openstack.tests.heat', 'test_heat_template.yaml')

    def tearDown(self):
        if self.stack_creator:
            self.stack_creator.clean()
        super(self.__class__, self).__clean__()

    def test_missing_dependencies(self):
        """
        Expect an StackCreationError when the stack file does not exist
        """
        stack_settings = StackSettings(name=self.stack_name,
                                       template_path=self.heat_tmplt_path)
        self.stack_creator = create_stack.OpenStackHeatStack(self.heat_creds,
                                                             stack_settings)
        with self.assertRaises(HTTPBadRequest):
            self.stack_creator.create()

    def test_bad_stack_file(self):
        """
        Expect an StackCreationError when the stack file does not exist
        """
        stack_settings = StackSettings(name=self.stack_name,
                                       template_path='foo')
        self.stack_creator = create_stack.OpenStackHeatStack(self.heat_creds,
                                                             stack_settings)
        with self.assertRaises(IOError):
            self.stack_creator.create()
