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

Les sous-reseaux d'interconnexion sont calcules automatiquement
depuis interconnect_base_network + interconnect_links.
"""

import ipaddress
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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        if header:
            f.write(f"# {header}\n\n")
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"  OK  {path}")

def load_vars(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)

# ─── CALCUL DES INTERCONNEXIONS ───────────────────────────────────────────────

def compute_interconnects(bgp: dict) -> list:
    """
    Depuis interconnect_base_network et interconnect_links,
    calcule automatiquement les IPs et reseaux de chaque lien.

    Pour chaque /31 :
      - spine_ip = premiere IP (.0)
      - leaf_ip  = deuxieme IP (.1)
    """
    base        = ipaddress.ip_network(bgp["interconnect_base_network"], strict=True)
    prefix_len  = base.prefixlen
    links       = bgp["interconnect_links"]

    # Genere autant de sous-reseaux que necessaire depuis la base
    subnets = list(base.supernet(new_prefix=prefix_len - 0).subnets(new_prefix=prefix_len))

    # On part directement du reseau de base et on avance de 2^(32-prefix_len) a chaque fois
    step        = 2 ** (32 - prefix_len)
    base_int    = int(base.network_address)

    interconnects = []
    for i, link in enumerate(links):
        subnet      = ipaddress.ip_network(f"{ipaddress.ip_address(base_int + i * step)}/{prefix_len}")
        hosts       = list(subnet.hosts()) if prefix_len < 31 else [subnet[0], subnet[1]]
        spine_ip    = str(hosts[0])
        leaf_ip     = str(hosts[1])
        network     = str(subnet)

        interconnects.append({
            "spine":     link["spine"],
            "leaf":      link["leaf"],
            "network":   network,
            "spine_ip":  spine_ip,
            "leaf_ip":   leaf_ip,
            "spine_eth": link["spine_eth"],
            "leaf_eth":  link["leaf_eth"],
        })

        print(f"    lien spine{link['spine']} <-> leaf{link['leaf']} : {network}  spine={spine_ip}  leaf={leaf_ip}")

    return interconnects

# ─── GENERATEURS ──────────────────────────────────────────────────────────────

def generate_group_vars(data: dict):
    print("\ngroup_vars/all/")

    # mlag.yml
    write_yaml(
        os.path.join(GROUP_VARS, "mlag.yml"),
        data["mlag"],
        "MLAG - Variables globales"
    )

    # bgp.yml — on calcule les interconnects automatiquement
    bgp = data["bgp"].copy()
    bgp["leafs_asn"] = {int(k): v for k, v in bgp["leafs_asn"].items()}

    print("\n  Calcul des sous-reseaux d'interconnexion :")
    bgp["interconnects"] = compute_interconnects(bgp)

    # On retire les cles brutes qui ne doivent pas aller dans le YAML Ansible
    bgp.pop("interconnect_base_network", None)
    bgp.pop("interconnect_links", None)

    write_yaml(
        os.path.join(GROUP_VARS, "bgp.yml"),
        bgp,
        "BGP - Variables globales + matrice d'interconnexion (generee automatiquement)"
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
    print("\nhosts")

    inv    = data["inventory"]
    groups = inv["groups"]
    av     = inv["arista_vars"]

    os.makedirs(OUTPUT_BASE, exist_ok=True)
    lines = []

    for group_name, members in groups.items():
        lines.append(f"[{group_name}]")
        for hostname, hostvars in members.items():
            vars_str = " ".join(f"{k}={v}" for k, v in hostvars.items())
            lines.append(f"{hostname} {vars_str}")
        lines.append("")

    lines.append("[arista:children]")
    for group_name in groups:
        lines.append(group_name)
    lines.append("")

    lines.append("[arista:vars]")
    for k, v in av.items():
        lines.append(f"{k}={str(v).lower() if isinstance(v, bool) else v}")

    with open(HOSTS_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")

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