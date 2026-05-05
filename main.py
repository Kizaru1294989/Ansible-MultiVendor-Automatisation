#!/usr/bin/env python3
"""
main.py
-------
Point d'entree unique. Lance tout automatiquement :
  1. Decouverte LLDP fabric (DC)
  2. Saisie interactive SVIs + hosts + DCI
  3. Generation complete des fichiers Ansible

Usage :
  python3 main.py            -> DC normal (decouverte + generation)
  python3 main.py --isn      -> mode ISN (lit dc1+dc2, genere config ISN)
  python3 main.py --discover -> decouverte seule
  python3 main.py --generate -> generation seule
  python3 main.py --generate --dc dc1
"""

import argparse
import getpass
import glob
import ipaddress
import json
import os
import re
import sys

from discover_fabric    import run_discovery, collect_device_info, parse_ip_range, scan_range
from generate_vars_auto import run_generation, load_vars

DC_NAME   = "production"
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

def save_vars(vars_auto: dict, path: str = None):
    p = path or VARS_FILE
    with open(p, "w") as f:
        json.dump(vars_auto, f, indent=2)

def ask_input(label: str, default=None, example=None) -> str:
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
    while True:
        val = ask_input(label, example=example)
        try:
            return ipaddress.ip_network(val, strict=False)
        except ValueError:
            print(f"      Format invalide. Exemple : {example}")

def host_id(name: str) -> int:
    nums = re.findall(r"\d+", name)
    return int(nums[-1]) if nums else 0

# ─── PARAMETRES DE CONNEXION ──────────────────────────────────────────────────

def ask_discovery_params() -> tuple:
    print("\n-- Parametres de decouverte --\n")
    while True:
        dc_name = input("  Nom du DC (ex: dc1, dc2, production) [production] : ").strip()
        dc_name = dc_name if dc_name else "production"
        break
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
    return dc_name, ip_range, username, password

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

# ─── CHARGEMENT IPs DC EXISTANTS ─────────────────────────────────────────────

def load_existing_dc_ips() -> dict:
    existing_files = sorted(glob.glob("vars_auto_*.json"))
    if not existing_files:
        return {}

    print("\n  Fichiers vars DC existants detectes :")
    for f in existing_files:
        print(f"    {f}")

    if not confirm("  Charger ces fichiers pour eviter les doublons d'IPs ?"):
        return {}

    vlan_registry = {}
    for fname in existing_files:
        try:
            data  = load_vars(fname)
            leafs = data.get("inventory", {}).get("leafs", {})
            for leaf_data in leafs.values():
                for svi in leaf_data.get("svis", []):
                    vlan_id = svi.get("vlan_id")
                    ip      = svi.get("ip")
                    prefix  = svi.get("prefix", 24)
                    if not vlan_id or not ip:
                        continue
                    network    = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
                    hosts_list = list(network.hosts())
                    if vlan_id not in vlan_registry:
                        vlan_registry[vlan_id] = {"network": network, "next_host_idx": 1}
                    try:
                        idx = hosts_list.index(ipaddress.ip_address(ip))
                        if idx + 1 > vlan_registry[vlan_id]["next_host_idx"]:
                            vlan_registry[vlan_id]["next_host_idx"] = idx + 1
                    except ValueError:
                        pass
            print(f"  OK  {fname} ({data.get('dc_name', fname)}) charge")
        except Exception as e:
            print(f"  Avertissement : {fname} : {e}")

    if vlan_registry:
        print("\n  IPs deja utilisees par VLAN :")
        for vlan_id, reg in sorted(vlan_registry.items()):
            net        = reg["network"]
            next_idx   = reg["next_host_idx"]
            hosts_list = list(net.hosts())
            used       = [str(h) for h in hosts_list[1:next_idx]]
            nxt        = hosts_list[next_idx] if next_idx < len(hosts_list) else "PLEIN"
            print(f"    VLAN {vlan_id:5d} ({net}) : utilisees = {', '.join(used)}")
            print(f"           Prochaine dispo : {nxt}")

    return vlan_registry

# ─── SAISIE SVIs PAR PAIRE MLAG ───────────────────────────────────────────────

def ask_svis(vars_auto: dict, vlan_registry: dict = None) -> dict:
    leafs        = vars_auto["inventory"]["leafs"]
    sorted_leafs = sorted(leafs.keys(), key=lambda n: host_id(n))
    pairs        = [sorted_leafs[i:i+2] for i in range(0, len(sorted_leafs), 2)]

    print("\n" + "═" * 60)
    print("  CONFIGURATION DES SVIs  (par paire MLAG)")
    print("═" * 60)
    print("  VIP    = .1 du reseau  (calculee automatiquement)")
    print("  leaf A = prochain host disponible")
    print("  leaf B = prochain host disponible")
    print("  Meme VLAN sur plusieurs paires ou DCs -> IPs incrementees auto\n")

    nb_svis       = ask_int("  Nombre de SVIs par paire MLAG", default=1)
    vlan_registry = dict(vlan_registry) if vlan_registry else {}
    example_base  = ipaddress.ip_network("172.16.115.0/24", strict=False)

    for pair_idx, pair in enumerate(pairs):
        leaf_a      = pair[0]
        leaf_b      = pair[1] if len(pair) > 1 else None
        leaf_a_data = leafs[leaf_a]
        leaf_b_data = leafs[leaf_b] if leaf_b else {}

        a_skip = leaf_a_data.get("is_border_leaf", False) and not leaf_a_data.get("border_has_svis", True)
        b_skip = leaf_b_data.get("is_border_leaf", False) and not leaf_b_data.get("border_has_svis", True) if leaf_b else False

        if a_skip and (not leaf_b or b_skip):
            print(f"\n  ── Paire MLAG : {leaf_a.upper()}{(' + ' + leaf_b.upper()) if leaf_b else ''} ── (border leaf sans SVIs, skipped)")
            vars_auto["inventory"]["leafs"][leaf_a]["svis"] = []
            if leaf_b:
                vars_auto["inventory"]["leafs"][leaf_b]["svis"] = []
            continue

        print(f"\n  ── Paire MLAG : {leaf_a.upper()}{(' + ' + leaf_b.upper()) if leaf_b else ''} ──")

        ports_a = leafs[leaf_a].get("lldp_host_ports", [])
        ports_b = leafs[leaf_b].get("lldp_host_ports", []) if leaf_b else []

        def show_lldp_ports(leaf_name, ports):
            if ports:
                port_str = ", ".join(f"Eth{p['eth']} ({p['neighbor']})" for p in ports)
                print(f"    {leaf_name} ports hosts : {port_str}")
            else:
                print(f"    {leaf_name} : aucun port host detecte via LLDP")

        show_lldp_ports(leaf_a, ports_a)
        if leaf_b:
            show_lldp_ports(leaf_b, ports_b)

        default_eth_a = ports_a[0]["eth"] if ports_a else 5
        default_eth_b = ports_b[0]["eth"] if ports_b else default_eth_a
        new_svis_a = []
        new_svis_b = []

        for i in range(nb_svis):
            if nb_svis > 1:
                print(f"\n    SVI {i+1}/{nb_svis} :")

            example_vlan = (pair_idx * nb_svis + i + 1) * 3
            vlan_id      = ask_int("vlan_id", example=example_vlan)

            if vlan_id in vlan_registry:
                reg        = vlan_registry[vlan_id]
                network    = reg["network"]
                idx        = reg["next_host_idx"]
                hosts_list = list(network.hosts())
                if idx + 1 >= len(hosts_list):
                    idx = 1
                ip_a       = str(hosts_list[idx])
                ip_b       = str(hosts_list[idx + 1]) if leaf_b else None
                vlan_registry[vlan_id]["next_host_idx"] = idx + (2 if leaf_b else 1)
                virtual_ip = str(network[1])
                prefix     = network.prefixlen
                print(f"      -> VLAN {vlan_id} connu, reseau {network} reutilise")
                print(f"      -> VIP     : {virtual_ip}")
                print(f"      -> {leaf_a:10s} : {ip_a}")
                if leaf_b and ip_b:
                    print(f"      -> {leaf_b:10s} : {ip_b}")
            else:
                example_net_int = int(example_base.network_address) + (pair_idx * nb_svis + i) * 256
                example_net     = str(ipaddress.ip_network(
                    f"{ipaddress.ip_address(example_net_int)}/24", strict=False))
                network    = ask_network("reseau", example=example_net)
                prefix     = network.prefixlen
                virtual_ip = str(network[1])
                ip_a       = str(network[2])
                ip_b       = str(network[3]) if leaf_b else None
                vlan_registry[vlan_id] = {"network": network, "next_host_idx": 3}
                print(f"      -> VIP     : {virtual_ip}")
                print(f"      -> {leaf_a:10s} : {ip_a}")
                if leaf_b and ip_b:
                    print(f"      -> {leaf_b:10s} : {ip_b}")

            eth_a = ask_int(f"port Ethernet {leaf_a} pour vlan {vlan_id}", default=default_eth_a)
            eth_b = ask_int(f"port Ethernet {leaf_b} pour vlan {vlan_id}", default=default_eth_b) if leaf_b else None

            new_svis_a.append({"vlan_id": vlan_id, "ip": ip_a, "prefix": prefix,
                                "virtual_ip": virtual_ip, "eth_int_host": eth_a})
            if leaf_b and ip_b:
                new_svis_b.append({"vlan_id": vlan_id, "ip": ip_b, "prefix": prefix,
                                   "virtual_ip": virtual_ip, "eth_int_host": eth_b})

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

    example_host_ips = ["172.16.115.100/24", "172.16.116.100/24", "172.16.117.100/24"]
    example_routes   = ["ip route 172.16.116.0/24 172.16.115.1",
                        "ip route 172.16.115.0/24 172.16.116.1",
                        "ip route 172.16.115.0/24 172.16.117.1"]

    for idx, (host_name, host_data) in enumerate(sorted(hosts.items())):
        print(f"\n  ── {host_name.upper()} ──")
        ex_ip    = example_host_ips[idx] if idx < len(example_host_ips) else "172.16.115.100/24"
        ex_route = example_routes[idx]   if idx < len(example_routes)   else "ip route 0.0.0.0/0 172.16.115.1"

        po_id         = ask_int("po_id",        default=host_data.get("po_id", 10))
        po_ip         = ask_input("po_ip",       example=host_data.get("po_ip") or ex_ip)
        eth_po_first  = ask_int("eth_po_first",  default=host_data.get("eth_po_first", 1))
        eth_po_second = ask_int("eth_po_second", default=host_data.get("eth_po_second", 2))
        route         = ask_input("route statique", example=host_data.get("route") or ex_route)

        vars_auto["inventory"]["hosts"][host_name].update({
            "po_id": po_id, "po_ip": po_ip,
            "eth_po_first": eth_po_first, "eth_po_second": eth_po_second, "route": route,
        })

    return vars_auto

# ─── SELECTION BORDER LEAFS ──────────────────────────────────────────────────

def ask_border_leaf_selection(vars_auto: dict) -> dict:
    leafs      = vars_auto["inventory"]["leafs"]
    candidates = {n: d for n, d in leafs.items() if d.get("lldp_dci_ports", [])}

    if not candidates:
        return vars_auto

    print("\n" + "═" * 60)
    print("  SELECTION DES BORDER LEAFS")
    print("═" * 60)
    print("  Voisins inconnus detectes via LLDP :\n")

    for leaf_name, leaf_data in sorted(candidates.items()):
        for p in leaf_data.get("lldp_dci_ports", []):
            print(f"  {leaf_name:20s}  Eth{p['eth']} -> {p['neighbor']:25s}  MAC: {p['neighbor_mac']}")

    print()
    print("  Entrez les noms des border leafs separes par virgule.")
    print("  Laissez vide si aucun border leaf.\n")
    raw = input("  Border leafs : ").strip()

    selected_border = set()
    if raw:
        selected_border = {re.sub(r"[-_]", "", n.strip().lower()) for n in raw.split(",") if n.strip()}

    for leaf_name, leaf_data in sorted(candidates.items()):
        dci_ports  = leaf_data.get("lldp_dci_ports", [])
        leaf_clean = re.sub(r"[-_]", "", leaf_name.lower())

        if leaf_clean not in selected_border:
            for p in dci_ports:
                p["type"] = "unknown"
                leaf_data["lldp_host_ports"].append(p)
            leaf_data["lldp_dci_ports"] = []
            leaf_data["is_border_leaf"]  = False
            print(f"  {leaf_name} : leaf normal")
        else:
            if len(dci_ports) == 1:
                confirmed_dci = dci_ports
                print(f"  {leaf_name} : BORDER LEAF  Eth{dci_ports[0]['eth']} -> {dci_ports[0]['neighbor']}")
            else:
                print(f"\n  {leaf_name.upper()} - ports inconnus :")
                for idx, p in enumerate(dci_ports, 1):
                    print(f"    {idx}. Eth{p['eth']} -> {p['neighbor']}  (MAC: {p['neighbor_mac']})")
                raw_ports = input("  Numeros des ports DCI (ex: 1,2 ou vide=tous) : ").strip()
                if not raw_ports:
                    confirmed_dci = dci_ports
                else:
                    selected_idx  = {int(x.strip()) - 1 for x in raw_ports.split(",") if x.strip().isdigit()}
                    confirmed_dci = []
                    for idx, p in enumerate(dci_ports):
                        if idx in selected_idx:
                            confirmed_dci.append(p)
                        else:
                            p["type"] = "unknown"
                            leaf_data["lldp_host_ports"].append(p)
                print(f"  {leaf_name} : BORDER LEAF  {len(confirmed_dci)} lien(s) DCI confirme(s)")

            leaf_data["lldp_dci_ports"] = confirmed_dci
            leaf_data["is_border_leaf"]  = True
            has_svis = confirm(f"  {leaf_name} porte-t-il aussi des SVIs vers des hosts ?")
            leaf_data["border_has_svis"] = has_svis
            if not has_svis:
                print(f"    -> Pas de SVIs pour {leaf_name}")

        vars_auto["inventory"]["leafs"][leaf_name] = leaf_data

    return vars_auto

# ─── SAISIE DCI / BORDER LEAF ─────────────────────────────────────────────────

def ask_dci_config(vars_auto: dict) -> dict:
    leafs        = vars_auto["inventory"]["leafs"]
    border_leafs = {n: d for n, d in leafs.items() if d.get("is_border_leaf", False)}

    if not border_leafs:
        return vars_auto

    print("\n" + "═" * 60)
    print("  CONFIGURATION DCI / BORDER LEAFS")
    print("═" * 60)

    fab          = vars_auto["fabric"]
    prefix       = fab["interconnect_prefix"]
    nb_fabric    = len(vars_auto["interconnect_links"])
    base_int     = int(ipaddress.ip_network(fab["interconnect_base"], strict=False).network_address)
    step         = 2 ** (32 - prefix)
    next_net_int = base_int + nb_fabric * step
    dci_net_idx  = 0

    default_rt_import = "1:9999"
    default_rt_export = "1:9999"
    default_vni_l3    = 5001

    for leaf_name, leaf_data in sorted(border_leafs.items()):
        dci_ports = leaf_data.get("lldp_dci_ports", [])
        print(f"\n  ── {leaf_name.upper()} ──")
        for p in dci_ports:
            print(f"    Eth{p['eth']} -> {p['neighbor']}  (MAC: {p['neighbor_mac']})")

        dci_links = []
        for port in dci_ports:
            print(f"\n  Lien DCI : Eth{port['eth']} -> {port['neighbor']}")
            auto_net  = ipaddress.ip_network(
                f"{ipaddress.ip_address(next_net_int + dci_net_idx * step)}/{prefix}", strict=False)
            net_input = input(f"    reseau [{auto_net}] (Entree=auto) : ").strip()

            if net_input:
                try:
                    chosen_net    = ipaddress.ip_network(net_input, strict=False)
                    local_ip      = str(chosen_net[0])
                    remote_ip     = str(chosen_net[1])
                    chosen_prefix = chosen_net.prefixlen
                except ValueError:
                    print("      Format invalide, reseau auto utilise.")
                    local_ip      = str(auto_net[0])
                    remote_ip     = str(auto_net[1])
                    chosen_prefix = prefix
            else:
                local_ip      = str(auto_net[0])
                remote_ip     = str(auto_net[1])
                chosen_prefix = prefix

            print(f"      -> IP locale   : {local_ip}/{chosen_prefix}")
            print(f"      -> IP ISN      : {remote_ip}/{chosen_prefix}")

            asn_dci    = ask_int(f"ASN {port['neighbor']}", example=65100)
            rt_import  = ask_input("RT import inter-DC", example=default_rt_import)
            rt_export  = ask_input("RT export inter-DC", example=default_rt_export)
            vni_l3_dci = ask_int("VNI L3 inter-DC VRF", example=default_vni_l3)

            default_rt_import = rt_import
            default_rt_export = rt_export
            default_vni_l3    = vni_l3_dci

            dci_links.append({
                "eth": port["eth"], "neighbor": port["neighbor"],
                "local_ip": local_ip, "remote_ip": remote_ip,
                "prefix": chosen_prefix, "remote_asn": asn_dci,
                "rt_import": rt_import, "rt_export": rt_export, "vni_l3": vni_l3_dci,
            })
            dci_net_idx += 1

        vars_auto["inventory"]["leafs"][leaf_name]["dci_links"] = dci_links

    return vars_auto

# ─── MODE ISN ─────────────────────────────────────────────────────────────────

def mode_isn():
    """
    Mode ISN — meme logique que mode_full() pour un DC :
      1. Range IP mgmt ISN -> scan -> decouverte LLDP
      2. Saisie interactive des variables reseau ISN
         (loopback0, ASN, reseau inter-ISN)
      3. Croisement avec les border leafs des DCs
      4. Generation inventories/isn/ avec :
         - hosts
         - group_vars/all/ (bgp.yml, isn.yml)
         - host_vars/<isn>.yml
         - <isn>-evpn.conf
    """
    print("\n" + "═" * 60)
    print("  CONFIGURATION ISN (Inter-Site Network)")
    print("═" * 60)

    # ── Lire les vars des DCs ─────────────────────────────────────────────────
    print("\n-- Fichiers vars des DCs --\n")
    dc_vars_list = []
    dc_idx = 1
    while True:
        default_file = f"vars_auto_dc{dc_idx}.json"
        f = input(f"  Fichier vars DC{dc_idx} [{default_file}] (vide = termine) : ").strip()
        if not f and dc_idx > 2:
            break
        f = f if f else default_file
        if not os.path.exists(f):
            print(f"  Fichier introuvable : {f}")
            continue
        dc_vars = load_vars(f)
        dc_vars_list.append(dc_vars)
        print(f"  OK  {f} charge (DC: {dc_vars.get('dc_name', f)})")
        dc_idx += 1
        if not confirm("  Ajouter un autre DC ?"):
            break

    if len(dc_vars_list) < 2:
        print("  Au moins 2 DCs requis.")
        raise SystemExit(1)

    # ── Scan + decouverte LLDP ISN ────────────────────────────────────────────
    print("\n-- Decouverte ISN --\n")

    while True:
        isn_range = input("  Range IP mgmt ISN (ex: 192.168.28.36-37) : ").strip()
        if isn_range:
            break
    while True:
        isn_username = input("  Username eAPI                            : ").strip()
        if isn_username:
            break
    isn_password = getpass.getpass("  Password                                 : ")

    isn_ip_list   = parse_ip_range(isn_range)
    isn_alive_ips = scan_range(isn_ip_list)

    if not isn_alive_ips:
        print("  Aucun ISN joignable.")
        raise SystemExit(1)

    print(f"\n  {len(isn_alive_ips)} ISN(s) joignables : {', '.join(isn_alive_ips)}")
    print("\n  Collecte LLDP sur les ISN...")

    isn_devices = []
    for ip in isn_alive_ips:
        info = collect_device_info(ip, isn_username, isn_password)
        if info:
            isn_devices.append(info)
            print(f"  {ip:18s} -> {info['hostname']:15s}  {len(info['neighbors'])} voisin(s) LLDP")
            for nbr in info["neighbors"]:
                print(f"    Eth{nbr['local_eth']} -> {nbr['remote_hostname']}")
        else:
            print(f"  {ip:18s} -> echec de connexion")

    if not isn_devices:
        print("  Aucun ISN accessible.")
        raise SystemExit(1)

    # ── Saisie des variables reseau pour chaque ISN ───────────────────────────
    print("\n-- Variables reseau ISN --\n")
    print("  (Entree = valeur par defaut entre [])\n")

    isn_asn = ask_int("  ASN ISN (meme pour tous)", example=65100)

    # Reseau inter-ISN (entre dc1-isn-1 et dc2-isn-2)
    isn_inter_net = None
    if len(isn_devices) > 1:
        print("\n  Reseau inter-ISN (lien entre les ISN) :")
        isn_inter_net = ask_network("  reseau inter-ISN", example="172.16.250.0/31")
        print(f"    -> ISN1 : {isn_inter_net[0]}/{isn_inter_net.prefixlen}")
        print(f"    -> ISN2 : {isn_inter_net[1]}/{isn_inter_net.prefixlen}")

    isn_vars = {}
    for idx, dev in enumerate(isn_devices):
        hostname = dev["hostname"]
        print(f"\n  ── {hostname.upper()} ──")
        lb0 = ask_input(f"loopback0_ip", example=f"172.16.100.{idx + 1}")
        isn_vars[hostname] = {
            "loopback0_ip": lb0,
            "bgp_asn":      isn_asn,
            "mgmt_ip":      dev["ip"],
        }
        # IP inter-ISN
        if isn_inter_net and len(isn_devices) > 1:
            isn_vars[hostname]["inter_isn_ip"]     = str(isn_inter_net[idx])
            isn_vars[hostname]["inter_isn_prefix"]  = isn_inter_net.prefixlen

    # ── Croisement avec les border leafs ─────────────────────────────────────
    print("\n  Croisement avec les border leafs des DCs...")

    # Construire la liste de tous les voisins border leaf
    # Pour l'ASN du leaf : lire depuis inventories/<dc>/host_vars/<leaf>.yml
    # qui est genere par generate_vars_auto.py avec le vrai ASN calcule
    import yaml

    def get_leaf_asn(dc_name: str, leaf_name: str, leaf_data: dict) -> int:
        """
        Lit le vrai ASN du leaf depuis :
        1. inventories/<dc>/host_vars/<leaf>.yml (genere par generate_vars_auto)
        2. Fallback : recalcule depuis asn_leafs_base + paire MLAG
        """
        # Option 1 : lire depuis host_vars genere
        hv_path = os.path.join("inventories", dc_name, "host_vars", f"{leaf_name}.yml")
        if os.path.exists(hv_path):
            try:
                with open(hv_path) as f:
                    hv = yaml.safe_load(f)
                if hv and "bgp_asn" in hv:
                    return hv["bgp_asn"]
            except Exception:
                pass

        # Option 2 : recalculer depuis vars_auto_dcX.json
        # Trouver le dc_vars correspondant
        for dc_vars in dc_vars_list:
            if dc_vars.get("dc_name") == dc_name:
                fab      = dc_vars.get("fabric", {})
                asn_base = fab.get("asn_leafs_base", 65001)
                # host_id = dernier groupe de chiffres
                nums     = re.findall(r"\d+", leaf_name)
                leaf_idx = int(nums[-1]) if nums else 1
                pair     = (leaf_idx - 1) // 2
                return asn_base + pair

        return 65000  # dernier fallback

    def get_leaf_loopback0(dc_name: str, leaf_name: str) -> str:
        """Lit la loopback0 du leaf depuis inventories/<dc>/host_vars/<leaf>.yml"""
        hv_path = os.path.join("inventories", dc_name, "host_vars", f"{leaf_name}.yml")
        if os.path.exists(hv_path):
            try:
                with open(hv_path) as f:
                    hv = yaml.safe_load(f)
                if hv and "loopback0_ip" in hv:
                    return hv["loopback0_ip"]
            except Exception:
                pass
        return None

    all_border_neighbors = []
    for dc_vars in dc_vars_list:
        dc_name = dc_vars.get("dc_name", "dc?")
        for leaf_name, leaf_data in dc_vars["inventory"]["leafs"].items():
            if not leaf_data.get("is_border_leaf", False):
                continue
            leaf_asn  = get_leaf_asn(dc_name, leaf_name, leaf_data)
            leaf_lb0  = get_leaf_loopback0(dc_name, leaf_name)
            print(f"  {leaf_name} ({dc_name}) : ASN={leaf_asn}  Loopback0={leaf_lb0 or 'non trouve'}")
            for dci_link in leaf_data.get("dci_links", []):
                all_border_neighbors.append({
                    "dc":             dc_name,
                    "leaf_name":      leaf_name,
                    "leaf_asn":       leaf_asn,
                    "leaf_loopback0": leaf_lb0,
                    "isn_local_ip":   dci_link["remote_ip"],
                    "leaf_ip":        dci_link["local_ip"],
                    "prefix":         dci_link["prefix"],
                    "rt_import":      dci_link["rt_import"],
                    "rt_export":      dci_link["rt_export"],
                })

    # Pour chaque ISN, filtrer ses voisins directs via LLDP
    # Et stocker la loopback0 ISN dans chaque neighbor pour le peering loopback
    for dev in isn_devices:
        hostname    = dev["hostname"]
        isn_lb0     = isn_vars[hostname]["loopback0_ip"]
        lldp_hosts  = {
            re.sub(r"[-_]", "", nbr["remote_hostname"].lower()): nbr["local_eth"]
            for nbr in dev["neighbors"]
        }
        my_neighbors = []
        for nbr in all_border_neighbors:
            leaf_clean = re.sub(r"[-_]", "", nbr["leaf_name"].lower())
            if leaf_clean in lldp_hosts:
                eth = lldp_hosts[leaf_clean]
                my_neighbors.append({
                    **nbr,
                    "isn_eth":       eth,
                    "isn_loopback0": isn_lb0,
                })
                print(f"  {hostname} Eth{eth} -> {nbr['leaf_name']} ({nbr['dc']})  leaf-LB0={nbr.get('leaf_loopback0', '?')}  isn-LB0={isn_lb0}")

        isn_vars[hostname]["neighbors"] = my_neighbors

    # ── Mettre a jour vars_auto_dcX.json avec isn_loopback0 dans dci_links ─────
    print("\n  Mise a jour des vars DC avec loopbacks ISN...")
    for dc_vars in dc_vars_list:
        dc_name  = dc_vars.get("dc_name", "dc?")
        modified = False
        for leaf_name, leaf_data in dc_vars["inventory"]["leafs"].items():
            if not leaf_data.get("is_border_leaf", False):
                continue
            for dci_link in leaf_data.get("dci_links", []):
                for dev in isn_devices:
                    isn_lb0 = isn_vars[dev["hostname"]]["loopback0_ip"]
                    for nbr in isn_vars[dev["hostname"]].get("neighbors", []):
                        if nbr["leaf_name"] == leaf_name and nbr["dc"] == dc_name:
                            dci_link["isn_loopback0"] = isn_lb0
                            modified = True
                            print(f"  {leaf_name} ({dc_name}) dci_link -> isn_loopback0={isn_lb0}")

        if modified:
            dc_file = f"vars_auto_{dc_name}.json"
            with open(dc_file, "w") as f:
                json.dump(dc_vars, f, indent=2)
            print(f"  OK  {dc_file} mis a jour")
            run_generation(dc_vars, dc_name=dc_name)
            print(f"  OK  inventories/{dc_name}/ regenere")

    # ── Construire vars_auto_isn.json ─────────────────────────────────────────
    vars_isn = {
        "dc_name":    "isn",
        "isn_asn":    isn_asn,
        "isn_all":    isn_devices,
        "isn_vars":   isn_vars,
        "inventory": {
            "arista_vars": {
                "ansible_user":                   isn_username,
                "ansible_password":               isn_password,
                "ansible_connection":             "httpapi",
                "ansible_network_os":             "eos",
                "ansible_httpapi_use_ssl":        True,
                "ansible_httpapi_validate_certs": False,
                "ansible_httpapi_port":           443,
            },
            "isn_devices": {
                dev["hostname"]: {
                    "ansible_host": dev["ip"],
                    "mgmt_ip":      dev["ip"],
                }
                for dev in isn_devices
            },
        },
    }

    with open("vars_auto_isn.json", "w") as f:
        json.dump(vars_isn, f, indent=2)
    print(f"\n  OK  vars_auto_isn.json sauvegarde.")

    # ── Generer les fichiers Ansible ISN ─────────────────────────────────────
    generate_isn_files(vars_isn, isn_devices, isn_inter_net)

    print("\n  ISN configure ! Deployez avec : ./run.sh evpn isn")


def generate_isn_files(vars_isn: dict, isn_devices: list, isn_inter_net):
    """
    Genere la meme structure qu'un DC normal :
      inventories/isn/
        hosts
        group_vars/all/bgp.yml
        group_vars/all/isn_global.yml
        host_vars/<isn>.yml
        <isn>-evpn.conf
    """
    output_base = os.path.join("inventories", "isn")
    group_vars  = os.path.join(output_base, "group_vars", "all")
    host_vars   = os.path.join(output_base, "host_vars")
    os.makedirs(group_vars, exist_ok=True)
    os.makedirs(host_vars,  exist_ok=True)

    av          = vars_isn["inventory"]["arista_vars"]
    isn_devices_inv = vars_isn["inventory"]["isn_devices"]
    isn_asn     = vars_isn["isn_asn"]
    isn_vars    = vars_isn["isn_vars"]

    # ── hosts ────────────────────────────────────────────────────────────────
    lines = ["[isn_devices]"]
    for hostname, data in sorted(isn_devices_inv.items()):
        lines.append(f"{hostname} ansible_host={data['ansible_host']} mgmt_ip={data['mgmt_ip']}")
    lines += ["", "[isn:children]", "isn_devices", "", "[isn:vars]"]
    for k, v in av.items():
        lines.append(f"{k}={str(v).lower() if isinstance(v, bool) else v}")
    with open(os.path.join(output_base, "hosts"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  OK  {output_base}/hosts")

    # ── group_vars/all/isn_global.yml ─────────────────────────────────────────
    import yaml
    global_vars = {
        "isn_asn": isn_asn,
        "isn_routing_protocol": "multi-agent",
    }
    with open(os.path.join(group_vars, "isn_global.yml"), "w") as f:
        f.write("# ISN - Variables globales\n\n")
        yaml.dump(global_vars, f, default_flow_style=False, sort_keys=False)
    print(f"  OK  {group_vars}/isn_global.yml")

    # ── host_vars/<isn>.yml + <isn>-evpn.conf ─────────────────────────────────
    for dev in isn_devices:
        hostname = dev["hostname"]
        ivars    = isn_vars.get(hostname, {})

        # host_vars
        # Trouver le port vers l'ISN distant via LLDP
        other_isns  = [d for d in isn_devices if d["hostname"] != hostname]
        inter_isn_eth = None
        for nbr in dev["neighbors"]:
            nbr_clean = re.sub(r"[-_]", "", nbr["remote_hostname"].lower())
            for other in other_isns:
                if nbr_clean == re.sub(r"[-_]", "", other["hostname"].lower()):
                    inter_isn_eth = nbr["local_eth"]
                    break

        hv = {
            "loopback0_ip": ivars.get("loopback0_ip", dev["ip"]),
            "bgp_asn":      isn_asn,
            "mgmt_ip":      dev["ip"],
            "neighbors":    ivars.get("neighbors", []),
        }
        if "inter_isn_ip" in ivars:
            hv["inter_isn_ip"]     = ivars["inter_isn_ip"]
            hv["inter_isn_prefix"] = ivars["inter_isn_prefix"]
            hv["inter_isn_eth"]    = inter_isn_eth

        with open(os.path.join(host_vars, f"{hostname}.yml"), "w") as f:
            f.write(f"# Variables specifiques a {hostname}\n\n")
            yaml.dump(hv, f, default_flow_style=False, sort_keys=False)
        print(f"  OK  {host_vars}/{hostname}.yml")



        router_id = ivars.get("loopback0_ip", dev["ip"])
        neighbors = ivars.get("neighbors", [])
        print(f"  OK  {host_vars}/{hostname}.yml")
        print(f"      loopback0_ip : {router_id}")
        print(f"      bgp_asn      : {isn_asn}")
        print(f"      neighbors    : {len(neighbors)} border leaf(s)")

# ─── MODES ────────────────────────────────────────────────────────────────────

def mode_full():
    global DC_NAME, VARS_FILE

    dc_name, ip_range, username, password = ask_discovery_params()
    DC_NAME   = dc_name
    VARS_FILE = f"vars_auto_{dc_name}.json"

    vars_auto = run_discovery(username, password, ip_range)
    vars_auto["dc_name"] = dc_name

    inv = vars_auto["inventory"]
    print("\n-- Recapitulatif de la decouverte --")
    print(f"  Spines       : {', '.join(sorted(inv['spines'].keys()))}")
    print(f"  Leafs        : {', '.join(sorted(inv['leafs'].keys()))}")
    print(f"  Hosts        : {', '.join(sorted(inv['hosts'].keys()))}")
    print(f"  Liens        : {len(vars_auto['interconnect_links'])} interconnexion(s)")
    border = [n for n, d in inv["leafs"].items() if d.get("is_border_leaf")]
    if border:
        print(f"  Border leafs : {', '.join(sorted(border))}")

    if confirm("\nVoulez-vous modifier les plages reseau du fabric ?"):
        vars_auto = ask_fabric_overrides(vars_auto)

    vars_auto = ask_border_leaf_selection(vars_auto)

    existing_vlan_registry = load_existing_dc_ips()

    vars_auto = ask_svis(vars_auto, vlan_registry=existing_vlan_registry)
    vars_auto = ask_dci_config(vars_auto)
    vars_auto = ask_hosts_config(vars_auto)

    save_vars(vars_auto, VARS_FILE)
    print(f"\n  OK  {VARS_FILE} sauvegarde.")

    print("\n" + "═" * 60)
    run_generation(vars_auto, dc_name=dc_name)

def mode_discover_only():
    global DC_NAME, VARS_FILE
    dc_name, ip_range, username, password = ask_discovery_params()
    DC_NAME   = dc_name
    VARS_FILE = f"vars_auto_{dc_name}.json"
    vars_auto = run_discovery(username, password, ip_range)
    vars_auto["dc_name"] = dc_name
    save_vars(vars_auto, VARS_FILE)
    print(f"\n  {VARS_FILE} genere.")
    print(f"  Lancez ensuite : python3 main.py --generate --dc {dc_name}")

def mode_generate_only(dc_name: str = None):
    global DC_NAME, VARS_FILE
    if dc_name:
        DC_NAME   = dc_name
        VARS_FILE = f"vars_auto_{dc_name}.json"
    if not os.path.exists(VARS_FILE):
        print(f"Fichier introuvable : {VARS_FILE}")
        print(f"Lancez d'abord : python3 main.py --discover")
        raise SystemExit(1)
    vars_auto = load_vars(VARS_FILE)
    dc = vars_auto.get("dc_name", dc_name or "production")
    run_generation(vars_auto, dc_name=dc)

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print(BANNER)

    parser = argparse.ArgumentParser(description="Arista Fabric Automation")
    parser.add_argument("--isn",      action="store_true", help="Mode ISN inter-DC")
    parser.add_argument("--discover", action="store_true", help="Decouverte LLDP uniquement")
    parser.add_argument("--generate", action="store_true", help="Generation Ansible uniquement")
    parser.add_argument("--dc",       type=str, default=None, help="Nom du DC pour --generate")
    args = parser.parse_args()

    try:
        if args.isn:
            mode_isn()
        elif args.discover:
            mode_discover_only()
        elif args.generate:
            mode_generate_only(dc_name=args.dc)
        else:
            mode_full()

    except KeyboardInterrupt:
        print("\n\nInterruption. Au revoir.")
        sys.exit(0)

if __name__ == "__main__":
    main()