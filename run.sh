#!/bin/bash
set -e

ACTION="${1:-mlag}"
ENV="${2:-production}"

case "$ACTION" in
  mlag)
    echo "⚙️  Déploiement MLAG pour l'environnement : $ENV"
    ansible-playbook -i inventories/$ENV/hosts playbooks/deploy.yml
    ;;
  restore)
    echo "♻️  Restauration de la dernière configuration sauvegardée"
    ansible-playbook -i inventories/$ENV/hosts playbooks/restore.yml
    ;;
  *)
    echo "❌ Action non reconnue. Utilisez : mlag | restore"
    ;;
esac
