#!/usr/bin/env python3
"""
main.py
-------
Point d'entree unique. Lance tout automatiquement :
  1. Decouverte LLDP
  2. Saisie interactive des SVIs par paire MLAG + hosts
  3. Generation complete des fichiers Ansible

Usage :
  python3 main.py            -> tout en un
  python3 main.py --discover -> decouverte seule
  python3 main.py --generate -> generation seule
"""

import argparse
import getpass
import ipaddress
import json
import os
import re
import sys

from discover_fabric    import run_discovery
from generate_vars_auto import run_generation, load_vars

VARS_FILE = "vars_auto.json"

BANNER = """
╔══════════════════════════════════════════════════════╗
║         Arista Fabric Automation                     ║
║         Decouverte LLDP + Generation Ansible         ║
╚══════════════════════════════════════════════════════╝
"""

# ─── HELPERS SAISIE ───────────────────────────────────────────────────────────

def confirm(message: str) -> bool:
    while True:
        rep = input(f"{message} [o/n] : ").strip().lower()
        if rep in ("o", "oui", "y", "yes"):
            return True
        if rep in ("n", "non", "no"):
            return False

def save_vars(vars_auto: dict):
    with open(VARS_FILE, "w") as f:
        json.dump(vars_auto, f, indent=2)

def ask_input(label: str, default=None, example=None) -> str:
    """
    Demande une valeur a l'user.
    - default fourni  : Entree = default affiche entre []
    - example fourni  : Entree = example (valeur par defaut depuis l'exemple)
    - ni l'un ni l'autre : obligatoire
    """
    if default is not None:
        val = input(f"    {label} [{default}] : ").strip()
        return val if val else str(default)
    if example is not None:
        val = input(f"    {label} [{example}] : ").strip()
        return val if val else str(example)
    while True:
        val = input(f"    {label} : ").strip()
        if val:
            return val
        print("      Valeur obligatoire.")

def ask_int(label: str, default=None, example=None) -> int:
    while True:
        try:
            return int(ask_input(label, default=default, example=example))
        except ValueError:
            print("      Entrez un nombre entier.")

def ask_network(label: str, example: str = "172.16.115.0/24") -> ipaddress.IPv4Network:
    """Demande un reseau CIDR. Entree = example si vide."""
    while True:
        val = ask_input(label, example=example)
        try:
            return ipaddress.ip_network(val, strict=False)
        except ValueError:
            print(f"      Format invalide. Exemple : {example}")

def host_id(name: str) -> int:
    m = re.sub(r"[^0-9]", "", name)
    return int(m) if m else 0

# ─── PARAMETRES DE CONNEXION ──────────────────────────────────────────────────

def ask_discovery_params() -> tuple:
    print("\n-- Parametres de decouverte --\n")
    while True:
        ip_range = input("  Range IP mgmt (ex: 192.168.28.1-50) : ").strip()
        if ip_range:
            break
    while True:
        username = input("  Username eAPI                        : ").strip()
        if username:
            break
    while True:
        password = getpass.getpass("  Password                             : ")
        if password:
            break
    return ip_range, username, password

# ─── PLAGES RESEAU OPTIONNELLES ───────────────────────────────────────────────

def ask_fabric_overrides(vars_auto: dict) -> dict:
    fab = vars_auto["fabric"]
    print("\n-- Plages reseau du fabric (Entree = valeur par defaut) --\n")
    fields = [
        ("loopback0_base",           "Loopback0 base"),
        ("loopback1_base",           "Loopback1 base (paires MLAG)"),
        ("loopback_test_base",       "Loopback test base"),
        ("mlag_spine_base",          "MLAG spines base (/31)"),
        ("mlag_leaf_base",           "MLAG leafs base"),
        ("interconnect_base",        "Interconnect base"),
        ("interconnect_prefix",      "Interconnect prefix length"),
        ("asn_spines",               "ASN spines"),
        ("asn_leafs_base",           "ASN leafs base"),
        ("vni_base",                 "VNI base"),
        ("route_distinguisher_base", "Route-distinguisher base"),
    ]
    for key, label in fields:
        current = fab[key]
        val = input(f"  {label:35s} [{current}] : ").strip()
        if val:
            fab[key] = int(val) if isinstance(current, int) else val
    vars_auto["fabric"] = fab
    return vars_auto

# ─── SAISIE SVIs PAR PAIRE MLAG ───────────────────────────────────────────────

def ask_svis(vars_auto: dict) -> dict:
    """
    Par paire MLAG, l'user donne :
      - vlan_id
      - reseau CIDR (ex: 172.16.115.0/24)  -> Entree = exemple affiché

    Calcul automatique :
      - VIP    = .1
      - leaf A = .2
      - leaf B = .3
    """
    leafs = vars_auto["inventory"]["leafs"]
    sorted_leafs = sorted(leafs.keys(), key=lambda n: host_id(n))
    pairs = [sorted_leafs[i:i+2] for i in range(0, len(sorted_leafs), 2)]

    print("\n" + "═" * 60)
    print("  CONFIGURATION DES SVIs  (par paire MLAG)")
    print("═" * 60)
    print("  VIP    = .1 du reseau  (calculee automatiquement)")
    print("  leaf A = .2 du reseau  (calculee automatiquement)")
    print("  leaf B = .3 du reseau  (calculee automatiquement)\n")

    nb_svis = ask_int("  Nombre de SVIs par paire MLAG", default=1)

    # Exemple de reseau incrementé par paire pour que l'exemple soit pertinent
    example_base = ipaddress.ip_network("172.16.115.0/24", strict=False)

    for pair_idx, pair in enumerate(pairs):
        leaf_a = pair[0]
        leaf_b = pair[1] if len(pair) > 1 else None

        print(f"\n  ── Paire MLAG : {leaf_a.upper()}{(' + ' + leaf_b.upper()) if leaf_b else ''} ──")

        eth_a = leafs[leaf_a].get("svis", [{}])[0].get("eth_int_host", 5)
        print(f"    eth_int_host : {eth_a}  (detecte via LLDP)")

        new_svis_a = []
        new_svis_b = []

        for i in range(nb_svis):
            if nb_svis > 1:
                print(f"\n    SVI {i+1}/{nb_svis} :")

            # Exemple de vlan_id : 3, 6, 9...
            example_vlan = (pair_idx * nb_svis + i + 1) * 3
            # Exemple de reseau : 172.16.115.0/24, 172.16.116.0/24...
            example_net_int = int(example_base.network_address) + (pair_idx * nb_svis + i) * 256
            example_net = str(ipaddress.ip_network(
                f"{ipaddress.ip_address(example_net_int)}/24", strict=False
            ))

            vlan_id = ask_int("vlan_id", example=example_vlan)
            network = ask_network("reseau", example=example_net)
            prefix  = network.prefixlen

            virtual_ip = str(network[1])
            ip_a       = str(network[2])
            ip_b       = str(network[3])

            print(f"      -> VIP     : {virtual_ip}  (automatique)")
            print(f"      -> {leaf_a:8s} : {ip_a}  (automatique)")
            if leaf_b:
                print(f"      -> {leaf_b:8s} : {ip_b}  (automatique)")

            new_svis_a.append({
                "vlan_id":      vlan_id,
                "ip":           ip_a,
                "prefix":       prefix,
                "virtual_ip":   virtual_ip,
                "eth_int_host": eth_a,
            })

            if leaf_b:
                eth_b = leafs[leaf_b].get("svis", [{}])[0].get("eth_int_host", eth_a)
                new_svis_b.append({
                    "vlan_id":      vlan_id,
                    "ip":           ip_b,
                    "prefix":       prefix,
                    "virtual_ip":   virtual_ip,
                    "eth_int_host": eth_b,
                })

        vars_auto["inventory"]["leafs"][leaf_a]["svis"] = new_svis_a
        if leaf_b:
            vars_auto["inventory"]["leafs"][leaf_b]["svis"] = new_svis_b

    return vars_auto

# ─── SAISIE HOSTS ─────────────────────────────────────────────────────────────

def ask_hosts_config(vars_auto: dict) -> dict:
    hosts = vars_auto["inventory"]["hosts"]
    if not hosts:
        return vars_auto

    print("\n" + "═" * 60)
    print("  CONFIGURATION DES HOSTS")
    print("═" * 60)

    # Exemples d'IPs pour les hosts
    example_host_ips = [
        "172.16.115.100/24",
        "172.16.116.100/24",
        "172.16.117.100/24",
    ]
    example_routes = [
        "ip route 172.16.116.0/24 172.16.115.1",
        "ip route 172.16.115.0/24 172.16.116.1",
        "ip route 172.16.115.0/24 172.16.117.1",
    ]

    for idx, (host_name, host_data) in enumerate(sorted(hosts.items())):
        print(f"\n  ── {host_name.upper()} ──")

        ex_ip    = example_host_ips[idx] if idx < len(example_host_ips) else "172.16.115.100/24"
        ex_route = example_routes[idx]   if idx < len(example_routes)   else "ip route 0.0.0.0/0 172.16.115.1"

        po_id         = ask_int("po_id (Port-Channel ID)",   default=host_data.get("po_id", 10))
        po_ip         = ask_input("po_ip",                   example=host_data.get("po_ip") or ex_ip)
        eth_po_first  = ask_int("eth_po_first",              default=host_data.get("eth_po_first", 1))
        eth_po_second = ask_int("eth_po_second",             default=host_data.get("eth_po_second", 2))
        route         = ask_input("route statique",          example=host_data.get("route") or ex_route)

        vars_auto["inventory"]["hosts"][host_name].update({
            "po_id":         po_id,
            "po_ip":         po_ip,
            "eth_po_first":  eth_po_first,
            "eth_po_second": eth_po_second,
            "route":         route,
        })

    return vars_auto

# ─── MODES ────────────────────────────────────────────────────────────────────

def mode_full():
    # 1. Connexion
    ip_range, username, password = ask_discovery_params()

    # 2. Decouverte LLDP
    vars_auto = run_discovery(username, password, ip_range)

    # 3. Recapitulatif
    inv = vars_auto["inventory"]
    print("\n-- Recapitulatif de la decouverte --")
    print(f"  Spines : {', '.join(sorted(inv['spines'].keys()))}")
    print(f"  Leafs  : {', '.join(sorted(inv['leafs'].keys()))}")
    print(f"  Hosts  : {', '.join(sorted(inv['hosts'].keys()))}")
    print(f"  Liens  : {len(vars_auto['interconnect_links'])} interconnexion(s)")

    # 4. Plages reseau (optionnel)
    if confirm("\nVoulez-vous modifier les plages reseau du fabric ?"):
        vars_auto = ask_fabric_overrides(vars_auto)

    # 5. SVIs par paire MLAG
    vars_auto = ask_svis(vars_auto)

    # 6. Hosts
    vars_auto = ask_hosts_config(vars_auto)

    # 7. Sauvegarde
    save_vars(vars_auto)
    print(f"\n  OK  {VARS_FILE} sauvegarde.")

    # 8. Generation
    print("\n" + "═" * 60)
    run_generation(vars_auto)

def mode_discover_only():
    ip_range, username, password = ask_discovery_params()
    run_discovery(username, password, ip_range)
    print(f"\n  vars_auto.json genere.")
    print(f"  Lancez ensuite : python3 main.py --generate")

def mode_generate_only():
    if not os.path.exists(VARS_FILE):
        print(f"Fichier introuvable : {VARS_FILE}")
        print("Lancez d'abord : python3 main.py --discover")
        raise SystemExit(1)
    vars_auto = load_vars(VARS_FILE)
    run_generation(vars_auto)

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print(BANNER)

    parser = argparse.ArgumentParser(description="Arista Fabric Automation")
    parser.add_argument("--discover", action="store_true",
                        help="Decouverte LLDP uniquement")
    parser.add_argument("--generate", action="store_true",
                        help="Generation Ansible uniquement")
    args = parser.parse_args()

    try:
        if args.discover:
            mode_discover_only()
        elif args.generate:
            mode_generate_only()
        else:
            mode_full()

    except KeyboardInterrupt:
        print("\n\nInterruption. Au revoir.")
        sys.exit(0)

if __name__ == "__main__":
    main()