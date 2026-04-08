#!/usr/bin/env python3
"""
generate_vars.py
----------------
Lit vars.json et genere tous les fichiers Ansible :
  - inventories/production/group_vars/all/mlag.yml
  - inventories/production/group_vars/all/bgp.yml
  - inventories/production/group_vars/all/vxlan_evpn.yml
  - inventories/production/host_vars/<hostname>.yml  (pour chaque host)
  - inventories/production/hosts
"""

import json
import os
import yaml

# ─── CONFIG ───────────────────────────────────────────────────────────────────

VARS_FILE   = "vars.json"
OUTPUT_BASE = "inventories/production"
GROUP_VARS  = os.path.join(OUTPUT_BASE, "group_vars", "all")
HOST_VARS   = os.path.join(OUTPUT_BASE, "host_vars")
HOSTS_FILE  = os.path.join(OUTPUT_BASE, "hosts")

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def write_yaml(path: str, data: dict, header: str = ""):
    """Ecrit un dict en YAML avec un commentaire d'en-tete optionnel."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        if header:
            f.write(f"# {header}\n\n")
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"  OK  {path}")

def load_vars(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)

# ─── GENERATEURS ──────────────────────────────────────────────────────────────

def generate_group_vars(data: dict):
    print("\ngroup_vars/all/")

    # mlag.yml
    write_yaml(
        os.path.join(GROUP_VARS, "mlag.yml"),
        data["mlag"],
        "MLAG - Variables globales"
    )

    # bgp.yml
    bgp = data["bgp"].copy()
    bgp["leafs_asn"] = {int(k): v for k, v in bgp["leafs_asn"].items()}
    write_yaml(
        os.path.join(GROUP_VARS, "bgp.yml"),
        bgp,
        "BGP - Variables globales + matrice d'interconnexion"
    )

    # vxlan_evpn.yml
    write_yaml(
        os.path.join(GROUP_VARS, "vxlan_evpn.yml"),
        data["vxlan_evpn"],
        "VXLAN EVPN - Variables globales communes a tous les leafs"
    )

def generate_host_vars(data: dict):
    print("\nhost_vars/")
    for hostname, vars_ in data["hosts"].items():
        write_yaml(
            os.path.join(HOST_VARS, f"{hostname}.yml"),
            vars_,
            f"Variables specifiques a {hostname}"
        )

def generate_hosts_file(data: dict):
    """Genere le fichier hosts Ansible depuis la section inventory de vars.json."""
    print("\nhosts")

    inv    = data["inventory"]
    groups = inv["groups"]
    av     = inv["arista_vars"]

    os.makedirs(OUTPUT_BASE, exist_ok=True)

    lines = []

    # -- groupes et leurs hosts --
    for group_name, members in groups.items():
        lines.append(f"[{group_name}]")
        for hostname, hostvars in members.items():
            vars_str = " ".join(f"{k}={v}" for k, v in hostvars.items())
            lines.append(f"{hostname} {vars_str}")
        lines.append("")

    # -- groupe parent arista:children --
    lines.append("[arista:children]")
    for group_name in groups:
        lines.append(group_name)
    lines.append("")

    # -- arista:vars --
    lines.append("[arista:vars]")
    for k, v in av.items():
        lines.append(f"{k}={str(v).lower() if isinstance(v, bool) else v}")

    content = "\n".join(lines) + "\n"

    with open(HOSTS_FILE, "w") as f:
        f.write(content)

    print(f"  OK  {HOSTS_FILE}")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print(f"Lecture de {VARS_FILE}...")

    if not os.path.exists(VARS_FILE):
        print(f"Fichier introuvable : {VARS_FILE}")
        raise SystemExit(1)

    data = load_vars(VARS_FILE)

    print("Generation des fichiers Ansible...\n")
    generate_group_vars(data)
    generate_host_vars(data)
    generate_hosts_file(data)

    print("\nTous les fichiers ont ete generes avec succes !")
    print(f"Repertoire de sortie : {OUTPUT_BASE}/")

if __name__ == "__main__":
    main()