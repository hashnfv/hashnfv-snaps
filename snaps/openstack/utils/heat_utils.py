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

import yaml
from heatclient.client import Client
from heatclient.common.template_format import yaml_loader
from novaclient.exceptions import NotFound
from oslo_serialization import jsonutils

from snaps import file_utils
from snaps.domain.stack import Stack, Resource, Output

from snaps.openstack.utils import keystone_utils, neutron_utils, nova_utils, \
    cinder_utils

__author__ = 'spisarski'

logger = logging.getLogger('heat_utils')


def heat_client(os_creds):
    """
    Retrieves the Heat client
    :param os_creds: the OpenStack credentials
    :return: the client
    """
    logger.debug('Retrieving Nova Client')
    return Client(os_creds.heat_api_version,
                  session=keystone_utils.keystone_session(os_creds),
                  region_name=os_creds.region_name)


def get_stack(heat_cli, stack_settings=None, stack_name=None):
    """
    Returns the first domain Stack object found. When stack_setting
    is not None, the filter created will take the name attribute. When
    stack_settings is None and stack_name is not, stack_name will be used
    instead. When both are None, the first stack object received will be
    returned, else None
    :param heat_cli: the OpenStack heat client
    :param stack_settings: a StackSettings object
    :param stack_name: the name of the heat stack to return
    :return: the Stack domain object else None
    """

    stack_filter = dict()
    if stack_settings:
        stack_filter['stack_name'] = stack_settings.name
    elif stack_name:
        stack_filter['stack_name'] = stack_name

    stacks = heat_cli.stacks.list(**stack_filter)
    for stack in stacks:
        return Stack(name=stack.identifier, stack_id=stack.id)


def get_stack_by_id(heat_cli, stack_id):
    """
    Returns a domain Stack object for a given ID
    :param heat_cli: the OpenStack heat client
    :param stack_id: the ID of the heat stack to retrieve
    :return: the Stack domain object else None
    """
    stack = heat_cli.stacks.get(stack_id)
    return Stack(name=stack.identifier, stack_id=stack.id)


def get_stack_status(heat_cli, stack_id):
    """
    Returns the current status of the Heat stack
    :param heat_cli: the OpenStack heat client
    :param stack_id: the ID of the heat stack to retrieve
    :return:
    """
    return heat_cli.stacks.get(stack_id).stack_status


def get_stack_status_reason(heat_cli, stack_id):
    """
    Returns the current status of the Heat stack
    :param heat_cli: the OpenStack heat client
    :param stack_id: the ID of the heat stack to retrieve
    :return: reason for stack creation failure
    """
    return heat_cli.stacks.get(stack_id).stack_status_reason


def create_stack(heat_cli, stack_settings):
    """
    Executes an Ansible playbook to the given host
    :param heat_cli: the OpenStack heat client object
    :param stack_settings: the stack configuration
    :return: the Stack domain object
    """
    args = dict()

    if stack_settings.template:
        args['template'] = stack_settings.template
    else:
        args['template'] = parse_heat_template_str(
            file_utils.read_file(stack_settings.template_path))
    args['stack_name'] = stack_settings.name

    if stack_settings.env_values:
        args['parameters'] = stack_settings.env_values

    stack = heat_cli.stacks.create(**args)

    return get_stack_by_id(heat_cli, stack_id=stack['stack']['id'])


def delete_stack(heat_cli, stack):
    """
    Deletes the Heat stack
    :param heat_cli: the OpenStack heat client object
    :param stack: the OpenStack Heat stack object
    """
    heat_cli.stacks.delete(stack.id)


def __get_os_resources(heat_cli, stack):
    """
    Returns all of the OpenStack resource objects for a given stack
    :param heat_cli: the OpenStack heat client
    :param stack: the SNAPS-OO Stack domain object
    :return: a list
    """
    return heat_cli.resources.list(stack.id)


def get_resources(heat_cli, stack, res_type=None):
    """
    Returns all of the OpenStack resource objects for a given stack
    :param heat_cli: the OpenStack heat client
    :param stack: the SNAPS-OO Stack domain object
    :param res_type: the type name to filter
    :return: a list of Resource domain objects
    """
    os_resources = __get_os_resources(heat_cli, stack)

    if os_resources:
        out = list()
        for os_resource in os_resources:
            if ((res_type and os_resource.resource_type == res_type)
                    or not res_type):
                out.append(Resource(
                    resource_type=os_resource.resource_type,
                    resource_id=os_resource.physical_resource_id))
        return out


def get_outputs(heat_cli, stack):
    """
    Returns all of the SNAPS-OO Output domain objects for the defined outputs
    for given stack
    :param heat_cli: the OpenStack heat client
    :param stack: the SNAPS-OO Stack domain object
    :return: a list of Output domain objects
    """
    out = list()

    os_stack = heat_cli.stacks.get(stack.id)

    outputs = None
    if os_stack:
        outputs = os_stack.outputs

    if outputs:
        for output in outputs:
            out.append(Output(**output))

    return out


def get_stack_networks(heat_cli, neutron, stack):
    """
    Returns a list of Network domain objects deployed by this stack
    :param heat_cli: the OpenStack heat client object
    :param neutron: the OpenStack neutron client object
    :param stack: the SNAPS-OO Stack domain object
    :return: a list of Network objects
    """

    out = list()
    resources = get_resources(heat_cli, stack, 'OS::Neutron::Net')
    for resource in resources:
        network = neutron_utils.get_network_by_id(
            neutron, resource.id)
        if network:
            out.append(network)

    return out


def get_stack_servers(heat_cli, nova, stack):
    """
    Returns a list of VMInst domain objects associated with a Stack
    :param heat_cli: the OpenStack heat client object
    :param nova: the OpenStack nova client object
    :param stack: the SNAPS-OO Stack domain object
    :return: a list of VMInst domain objects
    """

    out = list()
    resources = get_resources(heat_cli, stack, 'OS::Nova::Server')
    for resource in resources:
        try:
            server = nova_utils.get_server_object_by_id(nova, resource.id)
            if server:
                out.append(server)
        except NotFound:
            logger.warn('VmInst cannot be located with ID %s', resource.id)

    return out


def get_stack_volumes(heat_cli, cinder, stack):
    """
    Returns an instance of NetworkSettings for each network owned by this stack
    :param heat_cli: the OpenStack heat client object
    :param cinder: the OpenStack cinder client object
    :param stack: the SNAPS-OO Stack domain object
    :return: a list of Volume domain objects
    """

    out = list()
    resources = get_resources(heat_cli, stack, 'OS::Cinder::Volume')
    for resource in resources:
        try:
            server = cinder_utils.get_volume_by_id(cinder, resource.id)
            if server:
                out.append(server)
        except NotFound:
            logger.warn('Volume cannot be located with ID %s', resource.id)

    return out


def get_stack_volume_types(heat_cli, cinder, stack):
    """
    Returns an instance of NetworkSettings for each network owned by this stack
    :param heat_cli: the OpenStack heat client object
    :param cinder: the OpenStack cinder client object
    :param stack: the SNAPS-OO Stack domain object
    :return: a list of VolumeType domain objects
    """

    out = list()
    resources = get_resources(heat_cli, stack, 'OS::Cinder::VolumeType')
    for resource in resources:
        try:
            vol_type = cinder_utils.get_volume_type_by_id(cinder, resource.id)
            if vol_type:
                out.append(vol_type)
        except NotFound:
            logger.warn('VolumeType cannot be located with ID %s', resource.id)

    return out


def parse_heat_template_str(tmpl_str):
    """
    Takes a heat template string, performs some simple validation and returns a
    dict containing the parsed structure. This function supports both JSON and
    YAML Heat template formats.
    """
    if tmpl_str.startswith('{'):
        tpl = jsonutils.loads(tmpl_str)
    else:
        try:
            tpl = yaml.load(tmpl_str, Loader=yaml_loader)
        except yaml.YAMLError as yea:
            raise ValueError(yea)
        else:
            if tpl is None:
                tpl = {}
    # Looking for supported version keys in the loaded template
    if not ('HeatTemplateFormatVersion' in tpl or
            'heat_template_version' in tpl or
            'AWSTemplateFormatVersion' in tpl):
        raise ValueError("Template format version not found.")
    return tpl
