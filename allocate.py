#!/usr/bin/env python3

import etcd
import netaddr
import netifaces
import os
import sys
import textwrap

ETCD_VIP = '[fd65:7b9c:569:680:98eb:c508:ea6b:b0b2]'


class Client(object):

    def __init__(self, host=ETCD_VIP, port=4001):
        self.client = etcd.Client(host=host, port=port)

    def get_config(self, key):
        return self.client.read("/kubermesh.github.io/ip-allocator/config/%s" % key).value

    def allocate_address(self, machine_identity):
        network = netaddr.IPNetwork(self.get_config('ipv4-base-network'))
        try:
            response = self.client.read('/kubermesh.github.io/ip-allocator/ipv4/', recursive=True)
        except etcd.EtcdKeyNotFound:
            pass
        else:
            for child in response.children:
                if child.value == machine_identity:
                    return child.key.replace('/kubermesh.github.io/ip-allocator/ipv4/', '')

        for address in network:
            try:
                self.client.write("/kubermesh.github.io/ip-allocator/ipv4/%s" % address, machine_identity, prevExist=False)
                return netaddr.IPAddress(address)
            except etcd.EtcdAlreadyExist:
                pass


def mac_addresses():
    # Lower priority is better (so we can just sort this list)
    for interface_name in netifaces.interfaces():
        for mac_object in netifaces.ifaddresses(interface_name)[netifaces.AF_LINK]:
            mac = mac_object['addr']
            try:
                eui=netaddr.EUI(mac)
            except netaddr.core.AddrFormatError:
                continue
            else:
                if not eui.value:
                    # Ignore
                    continue
                local_mac = bool(eui.words[0] & 0b10) # See https://stackoverflow.com/a/38877492/14044
                # Also https://en.wikipedia.org/wiki/MAC_address#Universal_vs._local
                yield (
                    # Something that's univeral is best
                    1 if local_mac else 0,
                    # eth* preferred to other names
                    0 if interface_name.startswith('eth') else 1,
                    # Lowest mac is best
                    eui
                )


def compute_networks(base_address):
    node_network = netaddr.IPNetwork(base_address)

    lowest_mac = sorted(mac_addresses())[0][2]

    # Combine base address with mac
    node_network.prefixlen += 48
    node_network._value |= (lowest_mac.value << (128 - node_network.prefixlen))

    host_network, pod_network = node_network.subnet(node_network.prefixlen + 1)
    vip_network, cluster_interface_network = list(host_network.subnet(123))[:2]
    return vip_network, list(cluster_interface_network.subnet(126)), pod_network


def write_dummy_netdev_unit_file():
    dummy0_unit = textwrap.dedent('''
        [NetDev]
        Name=dummy0
        Kind=dummy
    ''')
    with open('/target/units/dummy0.netdev', 'w') as fobj:
        fobj.write(dummy0_unit)


def write_network_unit_file(interface_name, ipv4_address=None, ipv6_address=None, dhcp='both', preferred_lifetime='forever'):
    if ipv4_address:
        unit = textwrap.dedent('''
            [Match]
            Name=%(interface_name)s

            [Address]
            Address=%(ipv4_address)s/32

            [Address]
            Address=%(ipv6_address)s/128
            PreferredLifetime=%(preferred_lifetime)s

            [Network]
            DHCP=%(dhcp)s
            IPForward=ipv6
        ''' % {
            'dhcp': dhcp,
            'interface_name': interface_name,
            'ipv4_address': ipv4_address,
            'ipv6_address': ipv6_address,
            'preferred_lifetime': preferred_lifetime,
        })
    else:
        unit = textwrap.dedent('''
            [Match]
            Name=%(interface_name)s

            [Address]
            Address=%(ipv6_address)s
            PreferredLifetime=%(preferred_lifetime)s

            [Network]
            DHCP=%(dhcp)s
            IPForward=ipv6
            IPv6AcceptRA=true
        ''' % {
            'dhcp': dhcp,
            'interface_name': interface_name,
            'ipv4_address': ipv4_address,
            'ipv6_address': ipv6_address,
            'preferred_lifetime': preferred_lifetime,
        })
    with open("/target/units/%s.network" % interface_name, 'w') as fobj:
        fobj.write(unit)


def write_dnsmasq_opts_file(vip_ip, interface_networks):
    with open('/target/opts/dnsmasq-opts.env', 'w') as fobj:
        fobj.write("VIP_IP=%s\n" % vip_ip)
        for index, interface_network in enumerate(interface_networks):
            opts_file = textwrap.dedent('''
                CLUSTER%(index)d_INTERFACE_IP=%(interface_ip)s
                CLUSTER%(index)d_RANGE_START=%(range_start)s
                CLUSTER%(index)d_RANGE_END=%(range_end)s
                CLUSTER%(index)d_RANGE_NETMASK=%(range_netmask)s
            ''' % {
                'index': index,
                'interface_ip': interface_network[0],
                'range_start': interface_network[1],
                'range_end': interface_network[-1],
                'range_netmask': interface_network.prefixlen,
            })
            fobj.write(opts_file)


def write_v4_overlay_opts_file(vip_ip):
    with open('/target/opts/v4-overlay-opts.env', 'w') as fobj:
        fobj.write("VIP_IP=%s\n" % vip_ip)


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

    client = Client(host=os.getenv('ETCD_HOST', ETCD_VIP),
                    port=int(os.getenv('ETCD_PORT', 4001)))
    vip_network, cluster_networks, pod_network = compute_networks(client.get_config('ipv6-base-network'))
    ipv4_address = client.allocate_address(machine_identity)

    host_interface = vip_network[0]
    v4_overlay_interface = vip_network[1]

    assert host_interface
    assert pod_network
    assert ipv4_address

    write_dummy_netdev_unit_file()
    write_network_unit_file('dummy0', ipv4_address, host_interface, dhcp='no')
    for index, cluster_network in enumerate(cluster_networks):
        write_network_unit_file("cluster%d" % index, ipv4_address=None, ipv6_address=cluster_network, dhcp='yes', preferred_lifetime=0)
    write_dnsmasq_opts_file(host_interface, cluster_networks)
    write_v4_overlay_opts_file(v4_overlay_interface)
    write_docker_opts_file(pod_network)
    write_kubelet_opts_file(ipv4_address)


if __name__ == '__main__':
    main(sys.argv)
