#!/bin/bash
BACKUP_DIR="backups"
if [ ! -d "$BACKUP_DIR" ]; then
  echo "ERREUR : Le dossier de backup '$BACKUP_DIR' n'existe pas."
  exit 1
fi

while true; do
  echo "Liste des equipements avec backups :"
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
  echo
  echo " [0] Quitter"
  echo " [A] Nettoyer TOUS les dossiers (garder uniquement le plus ancien par equipement)"
  echo
  read -p "Selectionnez un equipement ou une option : " choix

  if [[ "$choix" == "0" ]]; then
    echo "Fin du script."
    exit 0

  elif [[ "${choix^^}" == "A" ]]; then
    echo
    echo "Nettoyage de tous les dossiers - conservation du plus ancien uniquement :"
    echo
    for dir in "$BACKUP_DIR"/*; do
      [ -d "$dir" ] || continue
      host=$(basename "$dir")
      mapfile -t backups < <(ls -t "$dir"/*.conf 2>/dev/null | tac)
      if [[ ${#backups[@]} -eq 0 ]]; then
        echo "  $host : aucun backup."
        continue
      fi
      oldest="${backups[0]}"
      deleted=0
      for f in "${backups[@]}"; do
        if [[ "$f" != "$oldest" ]]; then
          rm -f "$f"
          ((deleted++))
        fi
      done
      echo "  $host : $deleted fichier(s) supprime(s), conserve : $(basename "$oldest")"
    done
    echo
    read -p "Appuyez sur Entree pour revenir au menu..." _
    clear

  elif [[ "$choix" =~ ^[0-9]+$ ]] && (( choix > 0 && choix <= ${#equipements[@]} )); then
    host="${equipements[$((choix - 1))]}"
    host_path="$BACKUP_DIR/$host"

    mapfile -t backups < <(ls -t "$host_path"/*.conf 2>/dev/null | tac)

    echo
    echo "Backups pour $host (du plus ancien au plus recent) :"
    for idx in "${!backups[@]}"; do
      fname=$(basename "${backups[$idx]}")
      raw_date=$(echo "$fname" | grep -oP '\d{8}T\d{6}')
      readable_date=$(date -d "${raw_date:0:8} ${raw_date:9:2}:${raw_date:11:2}:${raw_date:13:2}" "+%d/%m/%Y %H:%M:%S" 2>/dev/null)
      if [[ $idx == 0 ]]; then
        echo "  [$((idx + 1))] $fname  | ${readable_date:-Invalide}  [PLUS ANCIEN]"
      else
        echo "  [$((idx + 1))] $fname  | ${readable_date:-Invalide}"
      fi
    done
    echo
    echo "  [0]   Retour au menu principal"
    echo "  [all] Supprimer tous les backups"
    echo "  [old] Supprimer tous sauf le plus ancien"
    echo
    read -p "Entrez les numeros a supprimer (ex: 1 2 4), 'all' ou 'old' : " delete_choice

    if [[ "$delete_choice" == "0" ]]; then
      clear
      continue
    elif [[ "$delete_choice" == "all" ]]; then
      rm -f "$host_path"/*.conf
      echo "Tous les backups pour $host ont ete supprimes."
    elif [[ "$delete_choice" == "old" ]]; then
      oldest="${backups[0]}"
      for f in "${backups[@]}"; do
        if [[ "$f" != "$oldest" ]]; then
          echo "Suppression : $(basename "$f")"
          rm -f "$f"
        fi
      done
      echo "Conserve : $(basename "$oldest")"
    else
      for num in $delete_choice; do
        index=$((num - 1))
        if [[ -f "${backups[$index]}" ]]; then
          echo "Suppression : $(basename "${backups[$index]}")"
          rm -f "${backups[$index]}"
        else
          echo "Numero $num invalide."
        fi
      done
    fi

    echo
    read -p "Appuyez sur Entree pour revenir au menu..." _
    clear

  else
    echo "Choix invalide. Essayez encore."
    echo
  fi
done