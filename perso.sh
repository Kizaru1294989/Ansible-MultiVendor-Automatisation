#!/bin/bash

echo "🔧 Configuration MLAG interactive"

read -p "VLAN MLAG: " vlan
read -p "Netmask (ex: 31): " netmask
read -p "Port-channel ID: " pcid
read -p "Interface 1 (ex: Ethernet1): " iface1
read -p "Interface 2 (ex: Ethernet2): " iface2
read -p "IP coeur1: " ip1
read -p "IP coeur2: " ip2

cat <<EOF > user_vars.yml
mlag_vlan: $vlan
mlag_netmask: $netmask
port_channel_id: $pcid
interfaces:
  - $iface1
  - $iface2
mlag_ip_map:
  coeur1: $ip1
  coeur2: $ip2
mlag_peer_map:
  coeur1: coeur2
  coeur2: coeur1
EOF

echo "✅ user_vars.yml généré"

ansible-playbook -i inventories/production/hosts playbooks/deploy.yml -e @user_vars.yml
