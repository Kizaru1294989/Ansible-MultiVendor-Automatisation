#!/bin/bash

BACKUP_DIR="backups"

if [ ! -d "$BACKUP_DIR" ]; then
  echo "❌ Le dossier de backup '$BACKUP_DIR' n'existe pas."
  exit 1
fi

while true; do
  echo "📁 Liste des équipements avec backups :"
  echo

  equipements=()
  i=1

  for dir in "$BACKUP_DIR"/*; do
    [ -d "$dir" ] || continue
    host=$(basename "$dir")
    equipements+=("$host")

    nb_backups=$(find "$dir" -name "*.conf" | wc -l)
    echo " [$i] $host ($nb_backups fichier(s))"
    ((i++))
  done

  echo " [0] Quitter"
  echo
  read -p "🔽 Sélectionnez un équipement pour voir ses backups : " choix

  if [[ "$choix" == "0" ]]; then
    echo "👋 Fin du script."
    exit 0
  elif [[ "$choix" =~ ^[0-9]+$ ]] && (( choix > 0 && choix <= ${#equipements[@]} )); then
    host="${equipements[$((choix - 1))]}"
    host_path="$BACKUP_DIR/$host"
    backups=("$host_path"/*.conf)

    echo
    echo "📄 Backups pour $host :"
    for idx in "${!backups[@]}"; do
      fname=$(basename "${backups[$idx]}")
      raw_date=$(echo "$fname" | grep -oP '\d{8}T\d{6}')
      readable_date=$(date -d "${raw_date:0:8} ${raw_date:9:2}:${raw_date:11:2}:${raw_date:13:2}" "+%d/%m/%Y %H:%M:%S" 2>/dev/null)
      echo "  [$((idx + 1))] $fname  📅 ${readable_date:-Invalide}"
    done
    echo "  [0] Retour au menu principal"
    echo

    read -p "🗑️  Entrez les numéros à supprimer (ex: 1 2 4) ou 'all' pour tout supprimer : " delete_choice

    if [[ "$delete_choice" == "0" ]]; then
      clear
      continue
    elif [[ "$delete_choice" == "all" ]]; then
      rm -f "$host_path"/*.conf
      echo "✅ Tous les backups pour $host ont été supprimés."
    else
      for num in $delete_choice; do
        index=$((num - 1))
        if [[ -f "${backups[$index]}" ]]; then
          echo "❌ Suppression : $(basename "${backups[$index]}")"
          rm -f "${backups[$index]}"
        else
          echo "⚠️  Numéro $num invalide."
        fi
      done
    fi

    echo
    read -p "Appuyez sur Entrée pour revenir au menu..." _
    clear
  else
    echo "❌ Choix invalide. Essayez encore."
    echo
  fi
done
