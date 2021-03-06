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

import logging

import enum
from cinderclient.exceptions import NotFound

from snaps.openstack.openstack_creator import OpenStackVolumeObject
from snaps.openstack.utils import cinder_utils

__author__ = 'spisarski'

logger = logging.getLogger('create_qos')

IMAGE_ACTIVE_TIMEOUT = 600
POLL_INTERVAL = 3
STATUS_ACTIVE = 'active'


class OpenStackQoS(OpenStackVolumeObject):
    """
    Class responsible for managing an qos in OpenStack
    """

    def __init__(self, os_creds, qos_settings):
        """
        Constructor
        :param os_creds: The OpenStack connection credentials
        :param qos_settings: The qos settings
        :return:
        """
        super(self.__class__, self).__init__(os_creds)

        self.qos_settings = qos_settings
        self.__qos = None

    def initialize(self):
        """
        Loads the existing QoS
        :return: The QoS domain object or None
        """
        super(self.__class__, self).initialize()

        self.__qos = cinder_utils.get_qos(
            self._cinder, qos_settings=self.qos_settings)

        return self.__qos

    def create(self):
        """
        Creates the qos in OpenStack if it does not already exist and returns
        the domain QoS object
        :return: The QoS domain object or None
        """
        self.initialize()

        if not self.__qos:
            self.__qos = cinder_utils.create_qos(
                self._cinder, self.qos_settings)

            logger.info(
                'Created qos with name - %s', self.qos_settings.name)

        return self.__qos

    def clean(self):
        """
        Cleanse environment of all artifacts
        :return: void
        """
        if self.__qos:
            try:
                cinder_utils.delete_qos(self._cinder, self.__qos)
            except NotFound:
                pass

        self.__qos = None

    def get_qos(self):
        """
        Returns the domain QoS object as it was populated when create() was
        called
        :return: the object
        """
        return self.__qos


class Consumer(enum.Enum):
    """
    QoS Specification consumer types
    """
    front_end = 'front-end'
    back_end = 'back-end'
    both = 'both'


class QoSSettings:
    def __init__(self, **kwargs):
        """
        Constructor
        :param name: the qos's name (required)
        :param consumer: the qos's consumer type of the enum type Consumer
                         (required)
        :param specs: dict of key/values
        """

        self.name = kwargs.get('name')

        if kwargs.get('consumer'):
            self.consumer = map_consumer(kwargs['consumer'])
        else:
            self.consumer = None

        self.specs = kwargs.get('specs')
        if not self.specs:
            self.specs = dict()

        if not self.name or not self.consumer:
            raise QoSSettingsError(
                "The attributes name and consumer are required")


def map_consumer(consumer):
    """
    Takes a the protocol value maps it to the Consumer enum. When None return
    None
    :param consumer: the value to map to the Enum
    :return: the Protocol enum object
    :raise: Exception if value is invalid
    """
    if not consumer:
        return None
    elif isinstance(consumer, Consumer):
        return consumer
    else:
        proto_str = str(consumer)
        if proto_str == 'front-end':
            return Consumer.front_end
        elif proto_str == 'back-end':
            return Consumer.back_end
        elif proto_str == 'both':
            return Consumer.both
        else:
            raise QoSSettingsError('Invalid Consumer - ' + proto_str)


class QoSSettingsError(Exception):
    """
    Exception to be thrown when an qos settings are incorrect
    """

    def __init__(self, message):
        Exception.__init__(self, message)


class QoSCreationError(Exception):
    """
    Exception to be thrown when an qos cannot be created
    """

    def __init__(self, message):
        Exception.__init__(self, message)
