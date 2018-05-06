#!/usr/bin/env python3
################################################################################
# Copyright (c) 2017, 2018 Genome Research Ltd.
#
# Author: Joshua C. Randall <jcrandall@alum.mit.edu>
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <http://www.gnu.org/licenses/>.
################################################################################

import argparse
import ast
import json
import os
import re
import sys
import types

from jinja2 import Template
from jinja2 import exceptions as jinja_exc

from jinjath import TemplateWithSource, JinjaTemplateAction, set_template_kwargs

###############################################################################
# Default inventory name template:
# names the ansible `inventory_name` after the (guaranteed unique) Terraform
# `name`
###############################################################################
DEFAULT_ANSIBLE_INVENTORY_NAME_TEMPLATE='{{ name }}'

###############################################################################
# Default groups template:
# assign all resources to the `all` group
###############################################################################
DEFAULT_ANSIBLE_GROUPS_TEMPLATE='all'

###############################################################################
# Default resource filter:
# include all supported Terraform providers of compute instance/machines
###############################################################################
# List of providers with link to documentation:
# alicloud_instance: https://www.terraform.io/docs/providers/alicloud/r/instance.html
# aws_instance: https://www.terraform.io/docs/providers/aws/r/instance.html
# clc_server: https://www.terraform.io/docs/providers/clc/r/server.html
# cloudstack_instance: https://www.terraform.io/docs/providers/cloudstack/r/instance.html
# digitalocean_droplet: https://www.terraform.io/docs/providers/do/r/droplet.html
# docker_container: https://www.terraform.io/docs/providers/docker/r/container.html
# google_compute_instance: https://www.terraform.io/docs/providers/google/r/compute_instance.html
# azurem_virtual_machine: https://www.terraform.io/docs/providers/azurerm/r/virtual_machine.html
# azure_instance: https://www.terraform.io/docs/providers/azure/r/instance.html
# openstack_compute_instance_v2: https://www.terraform.io/docs/providers/openstack/r/compute_instance_v2.html
# profitbricks_server: https://www.terraform.io/docs/providers/profitbricks/r/profitbricks_server.html
# scaleway_server: https://www.terraform.io/docs/providers/scaleway/r/server.html
# softlayer_virtual_guest: https://www.terraform.io/docs/providers/softlayer/r/virtual_guest.html
# triton_machine: https://www.terraform.io/docs/providers/triton/r/triton_machine.html
# vsphere_virtual_machine: https://www.terraform.io/docs/providers/vsphere/r/virtual_machine.html
###############################################################################
DEFAULT_ANSIBLE_RESOURCE_FILTER_TEMPLATE="""{{ type in [
                                               "alicloud_instance",
                                               "aws_instance",
                                               "clc_server",
                                               "cloudstack_instance",
                                               "digitalocean_droplet",
                                               "docker_container",
                                               "google_compute_instance",
                                               "azurem_virtual_machine",
                                               "azure_instance",
                                               "openstack_compute_instance_v2",
                                               "profitbricks_server",
                                               "scaleway_server",
                                               "softlayer_virtual_guest",
                                               "triton_machine",
                                               "vsphere_virtual_machine"] }}"""

###############################################################################
# Default host vars template:
# set all primary attributes as host_vars prefixed by 'tf_' and set `host_name`
# based on IP (v6 if available, otherwise v4; public if available, otherwise
# private/other).
###############################################################################
# IP address attributes for each provider, according to terraform docs:
# alicloud_instance: public_ip, private_ip
# aws_instance: public_ip, private_ip
# clc_server: (attribute undocumented, so this is based on arguments) private_ip_address
# cloudstack_instance: (attribute undocumented, so this is based on arguments) ip_address
# digitalocean_droplet: ipv4_address, ipv6_address, ipv6_address_private, ipv4_address_private
# docker_container: ip_address
# google_compute_instance: network_interface.0.access_config.0.assigned_nat_ip, network_interface.0.address
# azurem_virtual_machine: UNDOCUMENTED
# azure_instance: vip_address, ip_address
# openstack_compute_instance_v2: access_ip_v6, access_ip_v4, network/floating_ip, network/fixed_ip_v6, network/fixed_ip_v4
# profitbricks_server: UNDOCUMENTED
# scaleway_server: public_ip, private_ip
# softlayer_virtual_guest: (attribute undocumented, so this is based on arguments) ipv4_address, ipv4_address_private
# triton_machine: primaryip
# vsphere_virtual_machine: network_interface/ipv6_address, network_interface/ipv4_address
###############################################################################
DEFAULT_ANSIBLE_HOST_VARS_TEMPLATE="""ansible_host={{ primary.attributes.access_ip_v6
                                                | default(primary.attributes.ipv6_address, true)
                                                | default(primary.attributes.access_ip_v4, true)
                                                | default(primary.attributes["network.0.floating_ip"], true)
                                                | default(primary.attributes["network_interface.0.access_config.0.assigned_nat_ip"], true)
                                                | default(primary.attributes.ipv4_address, true)
                                                | default(primary.attributes.public_ip, true)
                                                | default(primary.attributes.ipaddress, true)
                                                | default(primary.attributes.vip_address, true)
                                                | default(primary.attributes.primaryip, true)
                                                | default(primary.attributes.ip_address, true)
                                                | default(primary.attributes["network_interface.0.ipv6_address"], true)
                                                | default(primary.attributes.ipv6_address_private, true)
                                                | default(primary.attributes.private_ip, true)
                                                | default(primary.attributes["network_interface.0.ipv4_address"], true)
                                                | default(primary.attributes.private_ip_address, true)
                                                | default(primary.attributes.ipv4_address_private, true)
                                                | default(primary.attributes["network_interface.0.address"], true)
                                                | default(primary.attributes["network.0.fixed_ip_v6"], true)
                                                | default(primary.attributes["network.0.fixed_ip_v4"], true)}}
                                      {% set newline = joiner("\n") -%}
                                      {% for attr, value in primary.expanded_attributes.items() -%}
                                        {{ newline() }}tf_{{ attr }}={{ value }}
                                      {%- endfor -%}
                                      """

###############################################################################
# Default inventory name template for terraform outputs:
# names the ansible `inventory_name` after the Terraform output name
###############################################################################
DEFAULT_ANSIBLE_OUTPUT_INVENTORY_NAME_TEMPLATE='{{ name }}'

###############################################################################
# Default groups template for terraform outputs:
# assign all outputs to the `all` group
###############################################################################
DEFAULT_ANSIBLE_OUTPUT_GROUPS_TEMPLATE='all'

###############################################################################
# Default resource filter for terraform outputs:
# exclude all outputs from ansible inventory
###############################################################################
DEFAULT_ANSIBLE_OUTPUT_RESOURCE_FILTER_TEMPLATE='False'

###############################################################################
# Default host vars template for terraform outputs:
# don't set any host_vars based on terraform outputs
###############################################################################
DEFAULT_ANSIBLE_OUTPUT_HOST_VARS_TEMPLATE=''

set_template_kwargs({'trim_blocks': True, 'lstrip_blocks': True, 'autoescape': False})

def process_tfstate(args, tf_state):
    tfstate_data = {}
    groups = {}
    hosts = {}
    for module in tf_state['modules']:
        args.debug and print("Processing module path %s" % (module['path']), file=sys.stderr)
        outputs = module['outputs']
        for output_name in outputs:
            output = Output(output_name, outputs[item_name])
            (output_groups, output_hosts) = process_item_with_templates(item=output, item_type="output", item_name=output_name, filter_template=args.ansible_output_filter_template, inventory_name_template=args.ansible_output_inventory_name_template, groups_template=args.ansible_output_groups_template, host_vars_template=args.ansible_output_host_vars_template, debug_p=args.debug)
        path = module['path']
        depends_on = module['depends_on']
        resources = module['resources']
        for resource_name in resources:
            resource = Resource(resource_name, resources[item_name])
            (resource_groups, resource_hosts) = process_item_with_templates(item=resource, item_type="resource", item_name=resource_name, filter_template=args.ansible_resource_filter_template, inventory_name_template=args.ansible_inventory_name_template, groups_template=args.ansible_groups_template, host_vars_template=args.ansible_host_vars_template, debug_p=args.debug)

    # merge groups and hosts from outputs and resources
    tfstate_data['groups'] = merge_groups(output_groups, resource_groups)
    tfstate_data['hosts'] = merge_hosts(output_hosts, resource_hosts)
    return tfstate_data

def process_item_with_templates(item, item_type, item_name, filter_template, inventory_name_template, groups_template, host_vars_template, debug_p=False):
    debug_p and print("Processing %s item named %s" % (item_type, item_name), file=sys.stderr)
    host_vars = {}
    groups = {}
    hosts = {}
    try:
        filter_value = filter_template.render(item)
    except jinja_exc.UndefinedError as e:
        sys.exit("Error rendering filter template: %s (template was '%s')" % (e, filter_template.source()))
    if filter_value == "False":
        continue
    elif filter_value != "True":
        raise ValueError("Unexpected value returned from filter_template: %s (template was [%s])" % (filter_value, filter_template.source()))
    try:
        inventory_name = inventory_name_template.render(item)
    except jinja_exc.UndefinedError as e:
        sys.exit("Error rendering inventory name template: %s (template was '%s')" % (e, inventory_name_template.source()))
    debug_p and print("Rendered ansible_inventory_name_template as '%s' for %s" % (inventory_name, item_name), file=sys.stderr)
    try:
        group_names = re.split('\s*\n\s*', groups_template.render(resource))
    except jinja_exc.UndefinedError as e:
        sys.exit("Error rendering groups template: %s (template was '%s')" % (e, groups_template.source()))
    debug_p and print("Rendered ansible_groups_template as '%s' for %s" % (group_names, item_name), file=sys.stderr)
    for group_name in group_names:
        if group_name not in groups:
            groups[group_name] = {}
            groups[group_name]['hosts'] = []
        debug_p and print("'%s' added to group '%s' for %s" % (inventory_name, group_name, item_name), file=sys.stderr)
        groups[group_name]['hosts'].append(inventory_name)
    try:
        host_var_key_values = re.split('\s*\n\s*', host_vars_template.render(resource))
    except jinja_exc.UndefinedError as e:
        sys.exit("Error rendering host_vars template: %s (template was '%s')" % (e, host_vars_template.source()))
    debug_p and print("Rendered ansible_host_vars_template as '%s' for %s" % (host_var_key_values, item_name), file=sys.stderr)
    for key_value in host_var_key_values:
        key_value = key_value.strip()
        if key_value == "":
            continue
        key_value = key_value.split('=', 1)
        key = key_value[0].strip()
        if len(key_value) < 2:
            print("WARNING: no '=' in assignment '%s' rendered from ansible_host_vars_template [%s]" % (key_value, host_vars_template.source()), file=sys.stderr)
            value = ""
        else:
            value = key_value[1].strip()
            if value.startswith('['):
                value = ast.literal_eval(value)
            elif value.startswith('{'):
                value = ast.literal_eval(value)
        host_vars[key] = value
        debug_p and print("host_var '%s' set to '%s' for %s" % (key, value, item_name), file=sys.stderr)
    if inventory_name not in hosts:
        hosts[inventory_name] = host_vars
    else:
        sys.exit("inventory_name was not unique across terraform resources: '%s' was a duplicate" % (inventory_name))
    return (groups, hosts)

def merge_hosts(*hosts_list):
    hosts = {}
    for hosts_to_merge in hosts_list:
        for inventory_name in hosts_to_merge.keys():
            if inventory_name not in hosts:
                hosts[inventory_name] = hosts_to_merge[inventory_name]
            else:
                sys.exit("inventory_name was not unique across terraform resources & outputs: '%s' was a duplicate" % (inventory_name))
    return hosts

def merge_groups(*groups_list):
    groups = {}
    for groups_to_merge in groups_list:
        for group_name in groups_to_merge.keys():
            group = groups_to_merge[group_name]
            for group_key in group.keys():
                if group_key == 'hosts':
                    hosts_list = group[group_key]
                    if group_name not in groups:
                        groups[group_name] = {}
                        groups[group_name]['hosts'] = []
                    groups[group_name]['hosts'].extend(hosts_list)
                else:
                    sys.exit("don't know how to merge group key: %s" % (group_key))
    return groups

def list_groups(tf_state_data):
    meta = {"hostvars": tf_state_data['hosts']}
    list_with_meta = tf_state_data['groups']
    list_with_meta['_meta'] = meta
    return list_with_meta

def get_host(tf_state_data, inventory_name):
    return tf_state_data['hosts'].get(inventory_name, {})

def main():
    parser = argparse.ArgumentParser(description='Terraform Ansible Inventory')
    parser.add_argument('--list', help='List inventory', action='store_true', default=False)
    parser.add_argument('--host', help='Get hostvars for a specific host', default=None)
    parser.add_argument('--debug', help='Print additional debugging information to stderr', action='store_true', default=False)
    parser.add_argument('--state', help="Location of Terraform .tfstate file (default: environment variable TF_STATE or 'terraform.tfstate' in the current directory)", type=argparse.FileType('r'), default=os.getenv('TF_STATE', 'terraform.tfstate'), dest='terraform_state')
    parser.add_argument('--ansible-inventory-name-template', help="A jinja2 template used to generate the ansible `host` (i.e. the inventory name) from a terraform resource. (default: environment variable TF_ANSIBLE_INVENTORY_NAME_TEMPLATE or `%s`)" % (DEFAULT_ANSIBLE_INVENTORY_NAME_TEMPLATE), default=get_template_default('TF_ANSIBLE_INVENTORY_NAME_TEMPLATE', default=DEFAULT_ANSIBLE_INVENTORY_NAME_TEMPLATE), action=JinjaTemplateAction)
    parser.add_argument('--ansible-host-vars-template', help="A jinja2 template used to generate a newline separated list (with optional whitespace before or after the newline, which will be stripped\
    ) of ansible host_vars settings (as '<key>=<value>' pairs) from a terraform resource. (default: environment variable TF_ANSIBLE_HOST_VARS_TEMPLATE or if not set, a template that maps all Terraform attributes to ansible host_vars prefixed by 'tf_' as well as setting 'ansible_host' to the IP address)", default=get_template_default('TF_ANSIBLE_HOST_VARS_TEMPLATE', default=DEFAULT_ANSIBLE_HOST_VARS_TEMPLATE), action=JinjaTemplateAction)
    parser.add_argument('--ansible-groups-template', help="A jinja2 template used to generate a newline separated list (with optional whitespace before or after the newline, which will be stripped) of ansible `group` names to which the resource should belong. (default: environment variable TF_ANSIBLE_GROUPS_TEMPLATE or `%s`])" % (DEFAULT_ANSIBLE_GROUPS_TEMPLATE), default=get_template_default('TF_ANSIBLE_GROUPS_TEMPLATE', default=DEFAULT_ANSIBLE_GROUPS_TEMPLATE), action=JinjaTemplateAction)
    parser.add_argument('--ansible-resource-filter-template', help="A jinja2 template used to filter terraform resources. This template is rendered for each resource and should evaluate to either the string 'True' to include the resource or 'False' to exclude it from the output.", default=get_template_default('TF_ANSIBLE_RESOURCE_FILTER_TEMPLATE', default=DEFAULT_ANSIBLE_RESOURCE_FILTER_TEMPLATE), action=JinjaTemplateAction)
    parser.add_argument('--ansible-output-inventory-name-template', help="A jinja2 template used to generate the ansible `host` (i.e. the inventory name) from a terraform output. (default: environment variable TF_ANSIBLE_OUTPUT_INVENTORY_NAME_TEMPLATE or `%s`)" % (DEFAULT_ANSIBLE_OUTPUT_INVENTORY_NAME_TEMPLATE), default=get_template_default('TF_ANSIBLE_OUTPUT_INVENTORY_NAME_TEMPLATE', default=DEFAULT_ANSIBLE_OUTPUT_INVENTORY_NAME_TEMPLATE), action=JinjaTemplateAction)
    parser.add_argument('--ansible-output-host-vars-template', help="A jinja2 template used to generate a newline separated list (with optional whitespace before or after the newline, which will be stripped\
    ) of ansible-output host_vars settings (as '<key>=<value>' pairs) from a terraform output. (default: environment variable TF_ANSIBLE_OUTPUT_HOST_VARS_TEMPLATE or if not set, a template that maps all Terraform attributes to ansible-output host_vars prefixed by 'tf_' as well as setting 'ansible-output_host' to the IP address)", default=get_template_default('TF_ANSIBLE_OUTPUT_HOST_VARS_TEMPLATE', default=DEFAULT_ANSIBLE_OUTPUT_HOST_VARS_TEMPLATE), action=JinjaTemplateAction)
    parser.add_argument('--ansible-output-groups-template', help="A jinja2 template used to generate a newline separated list (with optional whitespace before or after the newline, which will be stripped) of ansible-output `group` names to which the output record should belong. (default: environment variable TF_ANSIBLE_OUTPUT_GROUPS_TEMPLATE or `%s`])" % (DEFAULT_ANSIBLE_OUTPUT_GROUPS_TEMPLATE), default=get_template_default('TF_ANSIBLE_OUTPUT_GROUPS_TEMPLATE', default=DEFAULT_ANSIBLE_OUTPUT_GROUPS_TEMPLATE), action=JinjaTemplateAction)
    parser.add_argument('--ansible-output-filter-template', help="A jinja2 template used to filter terraform outputs. This template is rendered for each output and should evaluate to either the string 'True' to include the  or 'False' to exclude it from the output.", default=get_template_default('TF_ANSIBLE_OUTPUT_FILTER_TEMPLATE', default=DEFAULT_ANSIBLE_OUTPUT_FILTER_TEMPLATE), action=JinjaTemplateAction)
    args = parser.parse_args()

    args.debug and print("Parsing JSON from %s" % (args.terraform_state), file=sys.stderr)
    tf_state = json.load(args.terraform_state)
    ansible_data = {}
    args.debug and print("Processing tf_state data", file=sys.stderr)
    tf_state_data = process_tfstate(args, tf_state)
    if args.list:
        ansible_data = list_groups(tf_state_data)
    elif args.host is not None:
        ansible_data = get_host(tf_state_data, args.host)
    else:
        sys.exit("nothing to do (please specify either '--list' or '--host <INVENTORY_NAME>')")
    print(json.dumps(ansible_data))


# A python implementation of the flatmap.Expand function in terraform:
# https://github.com/hashicorp/terraform/blob/master/flatmap/expand.go
def flatmap_expand(flatmap, key):
    if key in flatmap.keys():
        v = flatmap[key]
        if v == "true":
            return True
        elif v == "false":
            return False
        return v

    if key+'.#' in flatmap.keys():
        return flatmap_expand_array(flatmap, key)

    prefix = key+'.'
    for k in flatmap.keys():
        if k.startswith(prefix):
            return flatmap_expand_dict(flatmap, prefix)

    return None

def flatmap_expand_array(flatmap, prefix):
    num = int(flatmap[prefix+'.#'])
    key_set = set()
    for k in flatmap.keys():
        if not k.startswith(prefix+'.'):
            continue

        key = k[len(prefix)+1:]
        idx = key.find('.')
        if idx != -1:
            key = key[:idx]

        if key == '#':
            continue

        k = int(key)
        key_set.add(k)

    keys_list = []
    for key in key_set:
        keys_list.append(key)

    keys_list.sort()

    result = []
    for key in keys_list:
        pk = "%s.%d" % (prefix, key)
        result.append(flatmap_expand(flatmap, pk))

    return result

def flatmap_expand_dict(flatmap, prefix):
    result = {}
    for k in flatmap.keys():
        if not k.startswith(prefix):
            continue

        key = k[len(prefix):]
        idx = key.find(".")
        if idx != -1:
            key = key[:idx]
        if key in result:
            continue

        if key == '%':
            continue

        result[key] = flatmap_expand(flatmap, k[:len(prefix)+len(key)])

    return result

class Resource(dict):
    def __init__(self, resource_name, resource_dict):
        super().__init__(resource_dict)
        self['name'] = resource_name
        self._expand_primary_attributes()

    def _expand_primary_attributes(self):
        attributes = self['primary']['attributes']
        self['primary']['expanded_attributes'] = {}
        for prefix in set([attr.split('.')[0] for attr in attributes.keys()]):
            self['primary']['expanded_attributes'][prefix] = flatmap_expand(attributes, prefix)

def get_template_default(*env_vars, default=''):
    template_source = None
    for var in env_vars:
        value = os.getenv(var, None)
        if value is not None:
            template_source = value
            break
    if template_source is None:
        template_source = default
    return TemplateWithSource(template_source)

if __name__ == '__main__':
    main()
