#!/usr/bin/python3

from wand import juju, run, status, wait, bootstrapped
import json
import yaml
import ipaddress
import sys


def maas(cmd):
    print('maas maas ' + cmd)
    out = run('maas maas ' + cmd, quiet=True)
    return json.loads(out)


class VLAN:
    def __init__(self, name, cidr, vid, interface):
        self.name = name
        self.network = ipaddress.IPv4Network(cidr)
        self.vid = vid
        self.interface = interface

    @property
    def network_address(self):
        return self.network.network_address

    @property
    def dynamic_start(self):
        return self.network_address + 10

    @property
    def dynamic_end(self):
        return self.network_address + 99

    @property
    def static_start(self):
        return self.network_address + 100

    @property
    def static_end(self):
        return self.network_address + 200

    @property
    def address(self):
        return self.network_address + 1

    @property
    def netmask(self):
        return self.network.netmask


def maas_setup(vlans, bundle_charm):
    with open(bundle_charm) as f:
        bundle = yaml.load(f)
        all_spaces = set()

        bundle_machines = []
        for name, service in bundle['services'].items():
            num_units = service['num_units']
            service_spaces = []

            for relation, space in service['bindings'].items():
                if space not in service_spaces:
                    service_spaces.append(space)
                    all_spaces.add(space)

            for i in range(num_units):
                bundle_machines.append(service_spaces)

    node_groups = maas('node-groups list')
    nodes = maas('nodes list')

    cluster_master_uuid = node_groups[0]['uuid']
    interface_list = maas('node-group-interfaces list ' + cluster_master_uuid)
    interface_template = ' '.join([
        'name={0.interface}',
        'interface={0.interface}',
        'ip={0.address}',
        'management=2',
        'router_ip={0.address}',
        'subnet_mask={0.netmask}',
        'ip_range_low={0.dynamic_start}',
        'ip_range_high={0.dynamic_end}',
        'static_ip_range_low={0.static_start}',
        'static_ip_range_high={0.static_end}'])

    maas_managed_network_cidr = '192.168.1.0/24'

    existing_interface_names = [i['name'] for i in interface_list]

    for vlan in vlans:
        if vlan.interface not in existing_interface_names:
            print('creating interface:', vlan.interface)
            maas('node-group-interfaces new ' + cluster_master_uuid + ' ' +
                 interface_template.format(vlan))

    # Expect a fabric named 'managed'. When we find it, add the VLANs to it
    fabrics = maas('fabrics read')
    found_managed_fabric = False
    for fabric in fabrics:
        if fabric['name'] == 'managed':
            found_managed_fabric = True
            existing_vlan_names = [v['name'] for v in fabric['vlans']]

            for vlan in vlans:
                if vlan.name not in existing_vlan_names:
                    print('Creating VLAN', vlan.name)
                    maas('vlans create {id}'.format(**fabric) +
                         ' name={0.name} vid={0.vid}'.format(vlan))

    if not found_managed_fabric:
        exit('Unable to find a MAAS fabric called "managed". You need to set '
             'it up before running this script.')

    spaces = maas('spaces read')
    space_names = [s['name'] for s in spaces]
    # Rename the MAAS default space from 'space-0' to 'default'.
    for space in spaces:
        if space['name'] == 'space-0':
            maas('space update {id} name=default'.format(**space))

    # Create a space per VLAN that we just created
    for vlan in vlans:
        if vlan.name not in space_names:
            print('Creating space', vlan.name)
            maas('spaces create name={0.name}'.format(vlan))

    fabrics = maas('fabrics read')
    spaces = maas('spaces read')
    spaces_by_name = {s['name']: s for s in spaces}

    vlans_by_name = {v.name: v for v in vlans}
    for fabric in fabrics:
        if fabric['name'] == 'managed':
            for v in fabric['vlans']:
                if vlans_by_name.get(v['name']):
                    vlans_by_name[v['name']].id = v['id']
            break

    subnets = maas('subnets read')
    subnets_by_cidr = {s['cidr']: s for s in subnets}

    for vlan in vlans:
        subnet = subnets_by_cidr[str(vlan.network)]
        space = spaces_by_name[vlan.name]
        if subnet['vlan']['name'] != vlan.name:
            maas('subnet update {subnet_id} '
                  'vlan={vlan.id} space={space_id} name={vlan.name}'.format(
                      subnet_id=subnet['id'], space_id=space['id'], vlan=vlan))

    subnets = maas('subnets read')
    subnets_by_cidr = {s['cidr']: s for s in subnets}
    subnets_by_space = {s['space']: s for s in subnets}

    managed_subnet_id = subnets_by_cidr[maas_managed_network_cidr]['id']
    bootstrap_node_name = None

    for i in range(len(bundle_machines)+1):
        node = nodes[i]

        # Make one node have all spaces on it, so we can use it as the
        # bootstrap node. We do this by configuring a machine with all spaces
        # on it before taking care of the specific requirements for the bundle.
        machine = ([list(all_spaces)] + bundle_machines)[i]
        if i == 0:
            bootstrap_node_name = node['hostname']
            continue # See if the bootstrap node can be in just the default space...

        node_interfaces = maas('interfaces read {system_id}'.format(**node))
        node_vlans = [n['vlan']['id'] for n in node_interfaces]

        interface_id = None
        for interface in node['interface_set']:
            for link in interface['links']:
                if link.get('subnet') and link['subnet']['id'] == managed_subnet_id:
                    interface_id = interface['id']

        if interface_id is None:
            # If we couldn't find the subnet we were looking for, this is an
            # error.
            sys.exit(
                'Could not find the MAAS managed network on node {}'.format(
                node['system_id']))

        for space in machine:
            vlan = vlans_by_name[space]

            if vlan.id not in node_vlans:
                maas('interfaces create-vlan '
                     '{system_id} parent={interface_id} vlan={vlan.id}'.format(
                        system_id=node['system_id'], interface_id=interface_id,
                        vlan=vlan))
    for node in nodes:
        node_interfaces = maas('interfaces read {system_id}'.format(**node))

        vlan_interfaces = [n for n in node_interfaces if n['type'] == 'vlan']
        for vif in vlan_interfaces:

            already_exists = False
            for link in vif.get('links', []):
                subnet = link.get('subnet', {})
                if subnet.get('name', '') == vif['vlan']['name']:
                    already_exists = True

            if already_exists:
                continue

            maas('interface link-subnet {system_id} '
                 '{vlan_interface_id} mode=auto subnet={subnet_id}'.format(
                    system_id=node['system_id'],
                    vlan_interface_id=vif['id'],
                    subnet_id=subnets_by_space[vif['vlan']['name']]['id']))

    if bootstrap_node_name is None:
        exit('Bootstrap node not configured')

    return bootstrap_node_name


def deploy(controller_name, cloud, bootstrap_node_name, bundle_charm):
    if not bootstrapped():
        juju('bootstrap {} {} --upload-tools --to "{}"'.format(
            controller_name, cloud, bootstrap_node_name))
    wait()
    
    juju('deploy ' + bundle_charm)
    wait()


def service_address(unit, relation):
    network_get_output = juju(
        'run --unit {unit} "network-get {relation} --primary-address"'.format(
            unit=unit, relation=relation),
        silent=True).rstrip()
    for line in network_get_output.split('\n'):
        try:
            return ipaddress.IPv4Address(line)
        except:
            continue
    return None


def check(vlans, bundle_charm):
    with open(bundle_charm) as f:
        bundle = yaml.load(f)

        for name, service in bundle['services'].items():
            for relation, space in service['bindings'].items():
                unit = name + '/0'
                address = service_address(unit, relation)
                found = False
                for vlan in vlans:
                    if address in vlan.network.hosts():
                        print(unit, relation, address, 'in', vlan.name)
                        found = True
                        continue

                if not found:
                    print(unit, relation, address, 'not in a VLAN')


def main():
    vlans = [
        VLAN('internal', '192.168.10.0/24', 10, 'enp2s0.10'),
        VLAN('public', '192.168.11.0/24', 11, 'enp2s0.11'),
        VLAN('db', '192.168.12.0/24', 12, 'enp2s0.12'),
    ]

    bundle_charm = 'charms/mediawiki/bundle.yaml'

    bootstrap_node = maas_setup(vlans, bundle_charm)
    deploy('maas', 'maas', bootstrap_node, bundle_charm)
    wait()
    check(vlans, bundle_charm)

if __name__ == '__main__':
    main()
