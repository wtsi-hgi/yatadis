#!/usr/bin/env python3
################################################################################
# Copyright (c) 2017 Genome Research Ltd.
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
import json
import os
import re
import sys

from jinja2 import Template
from jinja2 import exceptions as jinja_exc

DEFAULT_ANSIBLE_INVENTORY_NAME_TEMPLATE='{{ resource_name }}'
DEFAULT_ANSIBLE_HOST_VARS_TEMPLATE="""host_name={{ resource.primary.attributes.access_ip_v6
                                                | default(resource.primary.attributes.access_ip_v4, true)
                                                | default(resource.primary.attributes["network.0.floating_ip"], true)
                                                | default(resource.primary.attributes["network.0.fixed_ip_v6"], true)
                                                | default(resource.primary.attributes["network.0.fixed_ip_v4"], true)}},
                                      {% set comma = joiner(",") %}
                                      {% for attr, value in resource.primary.attributes.items() %}
                                        {{ comma() }}tf_{{ attr }}={{ value }}
                                      {% endfor %}
                                      """
DEFAULT_ANSIBLE_GROUPS_TEMPLATE='all'
DEFAULT_ANSIBLE_RESOURCE_FILTER_TEMPLATE='{{ resource.type in ["aws_instance","azure_instance","clc_server","digitalocean_droplet","google_compute_instance","openstack_compute_instance_v2","softlayer_virtualserver","triton_machine","ucs_service_profile","vsphere_virtual_machine"] }}'

TEMPLATE_KWARGS={'trim_blocks': True, 'lstrip_blocks': True, 'autoescape': False}

def process_tfstate(args, tf_state):
    tfstate_data = {}
    groups = {}
    hosts = {}
    for module in tf_state['modules']:
        args.debug and print("Processing module path %s" % (module['path']), file=sys.stderr)
        outputs = module['outputs']
        path = module['path']
        depends_on = module['depends_on']
        resources = module['resources']
        for resource_name in resources:
            args.debug and print("Processing resource name %s" % (resource_name), file=sys.stderr)
            host_vars = {}
            resource = {
                'resource_name': resource_name,
                'resource': resources[resource_name],
            }
            filter_value = args.ansible_resource_filter_template.render(resource)
            if filter_value == "False":
                continue
            elif filter_value != "True":
                raise ValueError("Unexpected value returned from ansible_resource_filter_template: %s (template was [%s])" % (filter_value, args.ansible_resource_filter_template.source()))
            inventory_name = args.ansible_inventory_name_template.render(resource)
            args.debug and print("Rendered ansible_inventory_name_template as '%s' for %s" % (inventory_name, resource_name), file=sys.stderr)
            group_names = re.split(',\s*', args.ansible_groups_template.render(resource))
            args.debug and print("Rendered ansible_groups_template as '%s' for %s" % (group_names, resource_name), file=sys.stderr)
            for group_name in group_names:
                if group_name not in groups:
                    groups[group_name] = {}
                    groups[group_name]['hosts'] = []
                args.debug and print("'%s' added to group '%s' for %s" % (inventory_name, group_name, resource_name), file=sys.stderr)
                groups[group_name]['hosts'].append(inventory_name)
            host_var_key_values = re.split(',\s*', args.ansible_host_vars_template.render(resource))
            args.debug and print("Rendered ansible_host_vars_template as '%s' for %s" % (host_var_key_values, resource_name), file=sys.stderr)
            for key_value in host_var_key_values:
                key_value = key_value.strip()
                if key_value == "":
                    continue
                key, value = key_value.split('=')
                key = key.strip()
                value = value.strip()
                host_vars[key] = value
                args.debug and print("host_var '%s' set to '%s' for %s" % (key, value, resource_name), file=sys.stderr)
            if inventory_name not in hosts:
                hosts[inventory_name] = host_vars
            else:
                sys.exit("inventory_name was not unique across terraform resources: '%s' was a duplicate" % (inventory_name))
    tfstate_data['groups'] = groups
    tfstate_data['hosts'] = hosts
    return tfstate_data


def list_groups(tf_state_data):
    meta = {"hostvars": tf_state_data['hosts']}
    list_with_meta = tf_state_data['groups']
    list_with_meta['_meta'] = meta
    return list_with_meta

def get_host(tf_state_data, inventory_name):
    return tf_state_data['hosts'].get(inventory_name, {})

def main(args):
    args.debug and print("Parsing JSON from %s" % (args.terraform_state), file=sys.stderr)
    tf_state = json.load(args.terraform_state)
    ansible_data = {}
    args.debug and print("Processing tf_state data", file=sys.stderr)
    tf_state_data = process_tfstate(args, tf_state)
    if args.list:
        ansible_data = list_groups(tf_state_data)
    elif args.host is not None:
        ansible_data = get_host(tf_state_data, args.host)
    print(json.dumps(ansible_data))

class TemplateWithSource(Template):
    def __new__(cls, source, **kwargs):
        rv = super().__new__(cls, source, **kwargs)
        rv._source = source
        return rv

    def source(self):
        return self._source

class JinjaTemplateAction(argparse.Action):
    def __init__(self, option_strings, dest, nargs=None, **kwargs):
        if nargs is not None:
            raise ValueError("nargs not allowed")
        super().__init__(option_strings, dest, **kwargs)
    def __call__(self, parser, namespace, source, option_string=None):
        try:
            template = TemplateWithSource(source, **TEMPLATE_KWARGS)
        except jinja_exc.TemplateSyntaxError as e:
            sys.exit("Syntax error in template specified by %s: %s (template source was: '%s')" % (option_string, e, source))
        setattr(namespace, self.dest, template)

def get_template_default(*env_vars, default=''):
    template_source = None
    for var in env_vars:
        value = os.getenv(var, None)
        if value is not None:
            template_source = value
            break
    if template_source is None:
        template_source = default
    return TemplateWithSource(template_source, **TEMPLATE_KWARGS)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Terraform Ansible Inventory')
    parser.add_argument('--list', help='List inventory', action='store_true', default=False)
    parser.add_argument('--host', help='Get hostvars for a specific host', default=None)
    parser.add_argument('--debug', help='Print additional debugging information to stderr', action='store_true', default=False)
    parser.add_argument('--state', help="Location of Terraform .tfstate file (default: environment variable TF_STATE or 'terraform.tfstate' in the current directory)", type=argparse.FileType('r'), default=os.getenv('TF_STATE', 'terraform.tfstate'), dest='terraform_state')
    parser.add_argument('--ansible-inventory-name-template', help="A jinja2 template used to generate the ansible `host` (i.e. the inventory name) from a terraform resource. (default: environment variable TF_ANSIBLE_INVENTORY_NAME_TEMPLATE or `%s`)" % (DEFAULT_ANSIBLE_INVENTORY_NAME_TEMPLATE), default=get_template_default('TF_ANSIBLE_INVENTORY_NAME_TEMPLATE', default=DEFAULT_ANSIBLE_INVENTORY_NAME_TEMPLATE), action=JinjaTemplateAction)
    parser.add_argument('--ansible-host-vars-template', help="A jinja2 template used to generate a comma-separated list (with optional whitespace after the comma, which will be stripped) of ansible host_vars settings (as '<key>=<value>' pairs) from a terraform resource. (default: environment variable TF_ANSIBLE_HOST_VARS_TEMPLATE or `%s`)" % (DEFAULT_ANSIBLE_HOST_VARS_TEMPLATE), default=get_template_default('TF_ANSIBLE_HOST_VARS_TEMPLATE', default=DEFAULT_ANSIBLE_HOST_VARS_TEMPLATE), action=JinjaTemplateAction)
    parser.add_argument('--ansible-groups-template', help="A jinja2 template used to generate a comma-separated list (with optional whitespace after the comma, which will be stripped) of ansible `group` names to which the resource should belong. (default: environment variable TF_ANSIBLE_GROUPS_TEMPLATE or `%s`])" % (DEFAULT_ANSIBLE_GROUPS_TEMPLATE), default=get_template_default('TF_ANSIBLE_GROUPS_TEMPLATE', default=DEFAULT_ANSIBLE_GROUPS_TEMPLATE), action=JinjaTemplateAction)
    parser.add_argument('--ansible-resource-filter-template', help="A jinja2 template used to filter terraform resources. This template is rendered for each resource and should evaluate to either the string 'True' to include the resource or 'False' to exclude it from the output.", default=get_template_default('TF_ANSIBLE_RESOURCE_FILTER_TEMPLATE', default=DEFAULT_ANSIBLE_RESOURCE_FILTER_TEMPLATE), action=JinjaTemplateAction)
    args = parser.parse_args()
    main(args)
