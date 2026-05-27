import os
import base64
import pulumi
from pulumi import automation as auto
from pulumi_azure_native import resources, network, compute

_DEFAULT_SSH_KEY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.ssh/id_rsa")


def _make_infrastructure(ssh_key_path: str):
    def _infrastructure():
        with open(f"{ssh_key_path}.pub") as f:
            ssh_public_key = f.read().strip()

        rg = resources.ResourceGroup("rg")

        vnet = network.VirtualNetwork(
            "vnet",
            resource_group_name=rg.name,
            address_space={"address_prefixes": ["10.0.0.0/16"]},
        )

        subnet = network.Subnet(
            "subnet",
            resource_group_name=rg.name,
            virtual_network_name=vnet.name,
            address_prefix="10.0.1.0/24",
        )

        public_ip = network.PublicIPAddress(
            "public-ip",
            resource_group_name=rg.name,
            public_ip_allocation_method=network.IPAllocationMethod.STATIC,
            sku={"name": network.PublicIPAddressSkuName.STANDARD},
        )

        nsg = network.NetworkSecurityGroup(
            "nsg",
            resource_group_name=rg.name,
            security_rules=[
                {
                    "name": "allow-http",
                    "priority": 100,
                    "protocol": "Tcp",
                    "access": "Allow",
                    "direction": "Inbound",
                    "source_address_prefix": "*",
                    "source_port_range": "*",
                    "destination_address_prefix": "*",
                    "destination_port_range": "80",
                },
                {
                    "name": "allow-https",
                    "priority": 110,
                    "protocol": "Tcp",
                    "access": "Allow",
                    "direction": "Inbound",
                    "source_address_prefix": "*",
                    "source_port_range": "*",
                    "destination_address_prefix": "*",
                    "destination_port_range": "443",
                },
                {
                    "name": "allow-ssh",
                    "priority": 120,
                    "protocol": "Tcp",
                    "access": "Allow",
                    "direction": "Inbound",
                    "source_address_prefix": "*",
                    "source_port_range": "*",
                    "destination_address_prefix": "*",
                    "destination_port_range": "22",
                },
            ],
        )

        nic = network.NetworkInterface(
            "nic",
            resource_group_name=rg.name,
            network_security_group={"id": nsg.id},
            ip_configurations=[
                {
                    "name": "ipconfig",
                    "subnet": {"id": subnet.id},
                    "public_ip_address": {"id": public_ip.id},
                }
            ],
        )

        def make_cloud_init(ip: str) -> str:
            script = """#!/bin/bash
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update && apt-get install -y caddy
mkdir -p /srv/files
chown azureuser:azureuser /srv/files
systemctl enable caddy
systemctl start caddy
"""
            return base64.b64encode(script.encode()).decode()

        cloud_init = public_ip.ip_address.apply(make_cloud_init)

        compute.VirtualMachine(
            "vm",
            resource_group_name=rg.name,
            hardware_profile={"vm_size": "Standard_D2s_v3"},
            opts=pulumi.ResourceOptions(ignore_changes=["osProfile"]),
            os_profile={
                "computer_name": "infra-lib-vm",
                "admin_username": "azureuser",
                "linux_configuration": {
                    "disable_password_authentication": True,
                    "ssh": {
                        "public_keys": [
                            {
                                "path": "/home/azureuser/.ssh/authorized_keys",
                                "key_data": ssh_public_key,
                            }
                        ]
                    },
                },
                "custom_data": cloud_init,
            },
            storage_profile={
                "image_reference": {
                    "publisher": "Canonical",
                    "offer": "0001-com-ubuntu-server-jammy",
                    "sku": "22_04-lts-gen2",
                    "version": "latest",
                },
                "os_disk": {
                    "create_option": compute.DiskCreateOptionTypes.FROM_IMAGE,
                    "managed_disk": {"storage_account_type": compute.StorageAccountTypes.STANDARD_LRS},
                },
            },
            network_profile={"network_interfaces": [{"id": nic.id, "primary": True}]},
        )

        pulumi.export("public_ip", public_ip.ip_address)
        pulumi.export("url", public_ip.ip_address.apply(lambda ip: f"http://{ip}"))

    return _infrastructure


def provision(location: str = "CentralUS", ssh_key_path: str = None) -> dict:
    ssh_key_path = os.path.abspath(ssh_key_path or _DEFAULT_SSH_KEY)
    stack = auto.create_or_select_stack(
        stack_name="dev",
        project_name="infra-lib",
        program=_make_infrastructure(ssh_key_path),
    )
    stack.set_config("azure-native:location", auto.ConfigValue(location))
    result = stack.up(on_output=print)
    return {k: v.value for k, v in result.outputs.items()}
