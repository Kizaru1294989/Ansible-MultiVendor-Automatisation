#!/bin/bash
set -e

ACTION="$1"
DC="${2:-production}"
ENV="inventories/$DC"

if [[ -z "$ACTION" ]]; then
  echo "Usage: $0 [mlag|bgp|evpn|mlag-bgp|cli|reset|restore|backups] [dc_name]"
  echo "  dc_name : nom du DC (defaut: production)"
  echo "  ex: $0 evpn dc1"
  echo "  ex: $0 evpn dc2"
  exit 1
fi

# Verifie que l'inventaire existe (seulement pour les actions qui en ont besoin)
check_inventory() {
  if [[ ! -f "$ENV/hosts" ]]; then
    echo "Inventaire introuvable : $ENV/hosts"
    echo "Lancez d'abord : python3 main.py"
    exit 1
  fi
}

case "$ACTION" in
  mlag)
    check_inventory
    echo ">>> Deploiement MLAG [$DC]"
    ansible-playbook -i "$ENV/hosts" playbooks/deploy.yml --tags mlag
    ;;

  bgp)
    check_inventory
    echo ">>> Deploiement BGP [$DC]"
    ansible-playbook -i "$ENV/hosts" playbooks/deploy.yml --tags bgp
    ;;

  evpn)
    check_inventory
    if [[ "$DC" == "isn" ]]; then
      echo ">>> Deploiement ISN"
      ansible-playbook -i "$ENV/hosts" playbooks/isn_deploy.yml
    else
      echo ">>> Deploiement Fabric VXLAN EVPN L3 [$DC]"
      ansible-playbook -i "$ENV/hosts" playbooks/deploy.yml --tags evpn
    fi
    ;;

  mlag-bgp)
    check_inventory
    echo ">>> Deploiement MLAG + BGP [$DC]"
    ansible-playbook -i "$ENV/hosts" playbooks/deploy.yml --tags mlag,bgp
    ;;

  cli)
    check_inventory
    echo ""
    echo "Cibles disponibles : arista (tous) | spines | leafs | hosts | <hostname>"
    read -rp "Cible [arista] : " TARGET
    TARGET="${TARGET:-arista}"

    echo ""
    echo "Entrez une ou plusieurs commandes separees par |"
    echo "  Note : commandes completes uniquement (show, pas sh)"
    echo ""
    echo "  Exemples :"
    echo "    show version"
    echo "    show mlag|show bgp summary|show vxlan address-table"
    echo ""
    read -rp "Commandes : " COMMANDS

    if [[ -z "$COMMANDS" ]]; then
      echo "Aucune commande saisie. Abandon."
      exit 1
    fi

    COMMANDS=$(echo "$COMMANDS" | sed \
      -e 's/\bsh vlan\b/show vlan/g' \
      -e 's/\bsh int\b/show interfaces/g' \
      -e 's/\bsh ip\b/show ip/g' \
      -e 's/\bsh bgp\b/show bgp/g' \
      -e 's/\bsh mlag\b/show mlag/g' \
      -e 's/\bsh lldp\b/show lldp/g' \
      -e 's/\bsh run\b/show running-config/g' \
      -e 's/\bsh ver\b/show version/g' \
      -e 's/\bsh\b/show/g' \
      -e 's/\bsho\b/show/g')

    echo ""
    echo ">>> Envoi CLI sur : $TARGET [$DC]"
    echo ">>> Commandes : $COMMANDS"
    echo ""

    TMPVARS=$(mktemp /tmp/cli_vars_XXXXXX.yml)
    cat > "$TMPVARS" << YAML
cli_target: "${TARGET}"
cli_commands: "${COMMANDS}"
YAML

    ansible-playbook \
      -i "$ENV/hosts" \
      playbooks/cli.yml \
      -e "@${TMPVARS}"

    rm -f "$TMPVARS"
    ;;

  reset)
    check_inventory
    echo ">>> Reset [$DC]"
    ansible-playbook -i "$ENV/hosts" playbooks/reset.yml
    ;;

  restore)
    # Si l'inventaire du DC existe, on l'utilise
    # Sinon on cherche le premier inventaire disponible
    if [[ -f "$ENV/hosts" ]]; then
      RESTORE_INV="$ENV/hosts"
    else
      RESTORE_INV=$(find inventories -name "hosts" | head -1)
      if [[ -z "$RESTORE_INV" ]]; then
        echo "Aucun inventaire trouve dans inventories/"
        echo "Usage: $0 restore <dc_name>"
        exit 1
      fi
      echo "  Inventaire utilise : $RESTORE_INV"
    fi
    # Determiner le bon groupe cible selon le DC
    if [[ "$DC" == "isn" ]]; then
      TARGET_GROUP="isn"
    else
      TARGET_GROUP="arista"
    fi
    echo ">>> Restauration depuis le dernier backup [$DC] (groupe: $TARGET_GROUP)"
    ansible-playbook -i "$RESTORE_INV" playbooks/restore.yml -e "target_group=$TARGET_GROUP"
    ;;

  backups)
    # backups ne depend pas de l'inventaire
    echo ">>> Gestion des backups"
    ./manage_backups.sh
    ;;

  *)
    echo "Action non reconnue : $ACTION"
    echo "Options : mlag | bgp | evpn | mlag-bgp | cli | reset | restore | backups"
    exit 1
    ;;
esac