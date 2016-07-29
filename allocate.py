#!/usr/bin/env python3

import netaddr
import netifaces
import sys


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
    return host_network, pod_network


def main(argv):
    _, base_address = argv

    print(compute_networks(base_address))


if __name__ == '__main__':
    main(sys.argv)
