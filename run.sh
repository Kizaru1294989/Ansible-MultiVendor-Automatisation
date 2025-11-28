#!/bin/bash
set -e

ENV="production"
ACTION="$1"

if [[ -z "$ACTION" ]]; then
  echo "Usage: $0 [mlag|reset|restore|backups]"
  exit 1
fi

case "$ACTION" in
  mlag)
    echo ">>> Déploiement MLAG"
    ansible-playbook -i "inventories/$ENV/hosts" playbooks/deploy.yml
    ;;
  reset)
    echo ">>> Reset de la configuration via playbook reset"
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
    echo "Options : mlag | reset | restore | backups"
    exit 1
    ;;
esac
