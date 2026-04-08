#!/bin/bash
set -e
ENV="production"
ACTION="$1"

if [[ -z "$ACTION" ]]; then
  echo "Usage: $0 [mlag|bgp|evpn|mlag-bgp|reset|restore|backups]"
  exit 1
fi

case "$ACTION" in
  mlag)
    echo ">>> Déploiement MLAG (leafs uniquement)"
    ansible-playbook -i "inventories/$ENV/hosts" playbooks/deploy.yml --tags mlag
    ;;
  bgp)
    echo ">>> Déploiement BGP (spines + leafs)"
    ansible-playbook -i "inventories/$ENV/hosts" playbooks/deploy.yml --tags bgp
    ;;
  evpn)
    echo ">>> Déploiement Fabric VXLAN EVPN L3 (spines + leafs)"
    ansible-playbook -i "inventories/$ENV/hosts" playbooks/deploy.yml --tags evpn
    ;;
  mlag-bgp)
    echo ">>> Déploiement MLAG + BGP"
    ansible-playbook -i "inventories/$ENV/hosts" playbooks/deploy.yml --tags mlag,bgp
    ;;
  reset)
    echo ">>> Reset de la configuration"
    ansible-playbook -i "inventories/$ENV/hosts" playbooks/reset.yml
    ;;
  restore)
    echo ">>> Restauration depuis le dernier backup"
    ansible-playbook -i "inventories/$ENV/hosts" playbooks/restore.yml
    ;;
  backups)
    echo ">>> Gestion des backups"
    ./manage_backups.sh
    ;;
  *)
    echo "⚠️  Action non reconnue : $ACTION"
    echo "Options : mlag | bgp | mlag-bgp | reset | restore | backups"
    exit 1
    ;;
esac