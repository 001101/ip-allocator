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


def write_unit_file(host_network):
    dummy0_unit = textwrap.dedent('''
        [Match]
        Name=dummy0

        [Address]
        Address=%(address)s
    ''' % {
        'address': host_network
    })
    with open('/target/units/dummy0.network', 'w') as fobj:
        fobj.write(dummy0_unit)


def write_docker_opts_file(pod_network):
    opts_file = textwrap.dedent('''
        DOCKER_OPT_BIP=--ipv6 --fixed-cidr-v6=%(address)s
        DOCKER_OPT_IPMASQ=--ip-masq=false
    ''' % {
        'address': pod_network
    })
    with open('/target/opts/ip-allocator-docker-opts.env', 'w') as fobj:
        fobj.write(opts_file)


def main(argv):
    _, base_address = argv

    host_network, pod_network = compute_networks(base_address)
    write_unit_file(host_network)
    write_docker_opts_file(pod_network)


if __name__ == '__main__':
    main(sys.argv)
