#!/usr/bin/env python3

import netaddr
import netifaces
import sys
import textwrap


def mac_addresses():
    for interface_name in netifaces.interfaces():
        if interface_name.startswith('eth'):
            for mac_object in netifaces.ifaddresses(interface_name)[netifaces.AF_LINK]:
                yield mac_object['addr']


def compute_networks(base_address):
    node_network = netaddr.IPNetwork(base_address)

    lowest_mac = sorted(mac_addresses())[0]
    lowest_mac = netaddr.EUI(lowest_mac)

    # Combine base address with mac
    node_network.prefixlen += 48
    node_network._value |= (lowest_mac.value << (128 - node_network.prefixlen))

    host_network, pod_network = node_network.subnet(node_network.prefixlen + 1)
    host_network.prefixlen=128
    return host_network, pod_network


def write_dummy_netdev_unit_file():
    dummy0_unit = textwrap.dedent('''
        [NetDev]
        Name=dummy0
        Kind=dummy
    ''')
    with open('/target/units/dummy0.netdev', 'w') as fobj:
        fobj.write(dummy0_unit)


def write_network_unit_file(interface_name, address, dhcp='both'):
    unit = textwrap.dedent('''
        [Match]
        Name=%(interface_name)s

        [Address]
        Address=%(address)s

        [Network]
        DHCP=%(dhcp)s
    ''' % {
        'address': address,
        'dhcp': dhcp,
        'interface_name': interface_name,
    })
    with open("/target/units/%s.network" % interface_name, 'w') as fobj:
        fobj.write(unit)


def write_docker_opts_file(pod_network):
    opts_file = textwrap.dedent('''
        DOCKER_OPT_BIP=--ipv6 --fixed-cidr-v6=%(address)s
        DOCKER_OPT_IPMASQ=--ip-masq=false
    ''' % {
        'address': pod_network,
    })
    with open('/target/opts/ip-allocator-docker-opts.env', 'w') as fobj:
        fobj.write(opts_file)


def write_kubelet_opts_file(host_network):
    opts_file = textwrap.dedent('''
        IPALLOC_HOST_IP=%(address)s
    ''' % {
        'address': host_network.network,
    })
    with open('/target/opts/ip-allocator-kubelet-opts.env', 'w') as fobj:
        fobj.write(opts_file)


def main(argv):
    _, base_address = argv

    host_network, pod_network = compute_networks(base_address)
    write_dummy_netdev_unit_file()
    write_network_unit_file('dummy0', host_network)
    write_docker_opts_file(pod_network)
    write_kubelet_opts_file(host_network)


if __name__ == '__main__':
    main(sys.argv)
