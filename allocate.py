#!/usr/bin/env python3

import etcd
import netaddr
import netifaces
import sys
import textwrap


def get_etcd_client():
    return etcd.Client(host='fd65:7b9c:569:680:98eb:c508:ea6b:b0b2')


def get_config(key):
    return get_etcd_client().read("/kubermesh.github.io/ip-allocator/config/%s" % key).value


def allocate_address_etcd(machine_identity):
    network = netaddr.IPNetwork(get_config('ipv4-base-network'))
    client = get_etcd_client()
    try:
        response = client.read('/kubermesh.github.io/ip-allocator/ipv4/', recursive=True)
    except etcd.EtcdKeyNotFound:
        pass
    else:
        for child in response.children:
            if child.value == machine_identity:
                return child.key.replace('/kubermesh.github.io/ip-allocator/ipv4/', '')

    for address in network:
        try:
            client.write("/kubermesh.github.io/ip-allocator/ipv4/%s" % address, machine_identity, prevExist=False)
            return netaddr.IPAddress(address)
        except etcd.EtcdAlreadyExist:
            pass


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


def write_network_unit_file(interface_name, ipv4_address, ipv6_address, dhcp='both'):
    unit = textwrap.dedent('''
        [Match]
        Name=%(interface_name)s

        [Address]
        Address=%(ipv4_address)s
        Address=%(ipv6_address)s

        [Network]
        DHCP=%(dhcp)s
    ''' % {
        'dhcp': dhcp,
        'interface_name': interface_name,
        'ipv4_address': ipv4_address,
        'ipv6_address': ipv6_address,
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


def write_kubelet_opts_file(address):
    opts_file = textwrap.dedent('''
        IPALLOC_HOST_IP=%(address)s
    ''' % {
        'address': address,
    })
    with open('/target/opts/ip-allocator-kubelet-opts.env', 'w') as fobj:
        fobj.write(opts_file)


def main(argv):
    _, machine_identity = argv

    host_network, pod_network = compute_networks(get_config('ipv6-base-network'))
    ipv4_address = allocate_address_etcd(machine_identity)

    assert host_network
    assert pod_network
    assert ipv4_address

    write_dummy_netdev_unit_file()
    write_network_unit_file('dummy0', ipv4_address, host_network)
    write_docker_opts_file(pod_network)
    write_kubelet_opts_file(ipv4_address)


if __name__ == '__main__':
    main(sys.argv)
