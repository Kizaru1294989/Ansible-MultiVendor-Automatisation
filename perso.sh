#!/bin/bash

echo "=== Génération du fichier hosts Ansible ==="

# Valeurs par défaut
default_mgmt_ip1="192.168.28.150"
default_mgmt_ip2="192.168.28.151"
default_mgmt_gateway="192.168.28.254"

# Choix utilisateur
read -p "🔧 Voulez-vous modifier les IP management et la passerelle ? (o/n) : " choix_mgmt

if [[ "$choix_mgmt" =~ ^[Oo]$ ]]; then
  read -p "🌍 IP Management coeur1 (par défaut: $default_mgmt_ip1) : " mgmt_ip1
  read -p "🌍 IP Management coeur2 (par défaut: $default_mgmt_ip2) : " mgmt_ip2
  read -p "🌐 Passerelle Management (par défaut: $default_mgmt_gateway) : " mgmt_gateway

  # Utiliser valeurs par défaut si vide
  mgmt_ip1=${mgmt_ip1:-$default_mgmt_ip1}
  mgmt_ip2=${mgmt_ip2:-$default_mgmt_ip2}
  mgmt_gateway=${mgmt_gateway:-$default_mgmt_gateway}
else
  mgmt_ip1=$default_mgmt_ip1
  mgmt_ip2=$default_mgmt_ip2
  mgmt_gateway=$default_mgmt_gateway
fi

# Saisie des valeurs MLAG
read -p "🖥️  VLAN MLAG : " mlag_vlan
read -p "🔗 Nom du peer MLAG : " mlag_peer_name
read -p "🌐 Masque MLAG (ex: 31) : " mlag_netmask
read -p "🧠 Réseau MLAG (ex: 172.16.56.0/31) : " mlag_network

# Extraction des IPs
mlag_base_ip=$(echo "$mlag_network" | cut -d'/' -f1)
IFS='.' read -r o1 o2 o3 o4 <<< "$mlag_base_ip"
mlag_ip1="$o1.$o2.$o3.$o4"
mlag_ip2="$o1.$o2.$o3.$((o4 + 1))"

# Construction du fichier hosts temporaire
cat <<EOF > temp_hosts
coeur1 ansible_host=${mgmt_ip1} mgmt_ip=${mgmt_ip1} hostname=coeur1 mlag_ip=${mlag_ip1} mlag_peer_ip=${mlag_ip2}
coeur2 ansible_host=${mgmt_ip2} mgmt_ip=${mgmt_ip2} hostname=coeur2 mlag_ip=${mlag_ip2} mlag_peer_ip=${mlag_ip1}
EOF

# Écriture dans le fichier final
cat <<EOF > inventories/production/hosts
[arista]
$(cat temp_hosts)

[arista:vars]
ansible_user=admin
ansible_password=admin
ansible_connection=httpapi
ansible_network_os=eos
ansible_httpapi_use_ssl=true
ansible_httpapi_validate_certs=false
ansible_httpapi_port=443

# Config réseau partagée
mgmt_gateway=${mgmt_gateway}
mlag_vlan=${mlag_vlan}
mlag_peer_name=${mlag_peer_name}
mlag_netmask=${mlag_netmask}
EOF

rm temp_hosts
echo "✅ Fichier hosts généré avec succès dans inventories/production/hosts"
