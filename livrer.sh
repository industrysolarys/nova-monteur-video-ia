#!/usr/bin/env bash
# Livre des fichiers finis dans le dépôt de l'élève (« mes-videos-nova-ia », ouvert dans la même session) et affiche le lien de la vidéo.
# Usage : bash livrer.sh <video.mp4> [fiche.json controle.txt …]  → <dépôt élève>/livraisons/<AAAA-MM-JJ>/<nom de la vidéo>/
# Dépôt de l'élève : variable ADCLONER_LIVRAISONS, sinon le dépôt git voisin qui ne contient pas le skill.
set -e
[ $# -ge 1 ] || { echo "usage : bash livrer.sh <video.mp4> [autres fichiers]"; exit 1; }
for f in "$@"; do [ -f "$f" ] || { echo "fichier introuvable : $f"; exit 1; }; done
ici="$(cd "$(dirname "$0")" && pwd)"
cible="${ADCLONER_LIVRAISONS:-}"
if [ -z "$cible" ]; then
  for d in "$(dirname "$ici")"/*/; do
    d="${d%/}"; [ -d "$d/.git" ] && [ ! -f "$d/nova/SKILL.md" ] && { cible="$d"; break; }
  done
fi
if [ -z "$cible" ] || [ ! -d "$cible/.git" ]; then
  # Sur un Mac, sans dépôt de livraison : la vidéo finie est rangée dans ~/Movies/Nova
  if [ "$(uname)" = "Darwin" ]; then
    dest="$HOME/Movies/Nova/$(date +%Y-%m-%d)/$(basename "${1%.*}")"; mkdir -p "$dest"; cp "$@" "$dest/"
    echo "Livré sur ce Mac : $dest/$(basename "$1")"; exit 0
  fi
  echo "PAS DE DÉPÔT ÉLÈVE : la session doit aussi ouvrir le dépôt « mes-videos-nova-ia » de l'élève."; exit 2
fi
jour=$(date +%Y-%m-%d); nom=$(basename "${1%.*}"); dest="livraisons/$jour/$nom"
mkdir -p "$cible/$dest"
cp "$@" "$cible/$dest/"
cd "$cible"
branche=$(git symbolic-ref --short HEAD 2>/dev/null || echo main)
git add "$dest" && git commit -qm "Livraison $jour : $nom"
git push -q origin "HEAD:$branche"
slug=$(git remote get-url origin | sed -E 's#\.git$##; s#/+$##' | awk -F/ '{print $(NF-1)"/"$NF}')
echo "Livré dans $slug. Lien de téléchargement de la vidéo :"
echo "https://github.com/$slug/blob/$branche/$dest/$(basename "$1")"
