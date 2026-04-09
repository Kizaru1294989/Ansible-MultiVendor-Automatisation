#!/usr/bin/env python3
"""
LLDP Network Mapper — Arista eAPI
Déduplique les nœuds via chassis MAC LLDP + normalisation hostname
"""

import json
import argparse
import sys
import re
from pathlib import Path

try:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    print("[!] pip install requests")
    sys.exit(1)

# ─── eAPI ─────────────────────────────────────────────────────────────────────

def eapi_call(host: dict, commands: list):
    url = f"https://{host['management_ip']}/command-api"
    payload = {
        "jsonrpc": "2.0", "method": "runCmds",
        "params": {"version": 1, "cmds": commands, "format": "json"},
        "id": 1
    }
    try:
        resp = requests.post(url, json=payload,
                             auth=(host["username"], host["password"]),
                             verify=False, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            print(f"  [!] eAPI error on {host['hostname']}: {data['error']['message']}")
            return None
        return data["result"]
    except requests.exceptions.ConnectTimeout:
        print(f"  [!] Timeout: {host['hostname']} ({host['management_ip']})")
    except requests.exceptions.ConnectionError as e:
        print(f"  [!] Cannot connect to {host['hostname']} ({host['management_ip']}): {e}")
    except Exception as e:
        print(f"  [!] Unexpected error on {host['hostname']}: {e}")
    return None

# ─── Normalisation hostname ────────────────────────────────────────────────────

def normalize_hostname(name: str) -> str:
    """Lowercase, supprime domaine FQDN, strip espaces."""
    return name.strip().split(".")[0].lower()

# ─── Collecte ─────────────────────────────────────────────────────────────────

def collect_device_info(host: dict):
    print(f"  → {host['hostname']} ({host['management_ip']})...")
    result = eapi_call(host, [
        "show version",
        "show lldp neighbors detail",
        "show lldp local-info",   # donne le chassis MAC local
    ])
    if not result or len(result) < 3:
        return None

    version_info = result[0]
    lldp_detail  = result[1]
    lldp_local   = result[2]

    # Chassis MAC local (ex: "00:1c:73:ab:cd:ef")
    chassis_mac = lldp_local.get("chassisId", "").lower().strip()

    neighbors = []
    for local_intf, data in lldp_detail.get("lldpNeighbors", {}).items():
        for nbr in data.get("lldpNeighborInfo", []):
            remote_hostname = normalize_hostname(nbr.get("systemName", ""))
            # Chassis MAC du voisin
            nbr_chassis_mac = nbr.get("chassisId", "").lower().strip()
            remote_intf = nbr.get("neighborInterfaceInfo", {}).get("interfaceId", "")
            if remote_hostname:
                neighbors.append({
                    "local_interface":   local_intf,
                    "remote_hostname":   remote_hostname,
                    "remote_interface":  remote_intf,
                    "remote_chassis_mac": nbr_chassis_mac,
                })

    return {
        "hostname":      normalize_hostname(host["hostname"]),
        "display_name":  host["hostname"],          # nom original pour affichage
        "management_ip": host["management_ip"],
        "model":         version_info.get("modelName", "N/A"),
        "eos_version":   version_info.get("version", "N/A"),
        "serial":        version_info.get("serialNumber", "N/A"),
        "chassis_mac":   chassis_mac,
        "group":         host.get("group", "unknown"),
        "neighbors":     neighbors,
    }

# ─── Déduplication + Graph builder ────────────────────────────────────────────

def build_graph(devices_info: list) -> dict:
    """
    Stratégie de déduplication :
    1. Les équipements de l'inventaire sont autoritaires (hostname normalisé = clé)
    2. Les voisins découverts via LLDP sont matchés par :
       a) chassis MAC  → si le MAC correspond à un équipement inventorié
       b) hostname normalisé → fallback
    3. Si aucun match → nœud "unknown" ajouté avec le hostname LLDP
    """

    # Index primaire : hostname normalisé → device
    nodes = {}
    # Index secondaire : chassis_mac → hostname normalisé (pour résoudre les alias)
    mac_to_hostname = {}

    # 1. Enregistrer tous les équipements inventoriés
    for d in devices_info:
        nodes[d["hostname"]] = {
            "id":            d["hostname"],
            "label":         d["display_name"],
            "group":         d["group"],
            "management_ip": d["management_ip"],
            "model":         d["model"],
            "eos_version":   d["eos_version"],
            "serial":        d["serial"],
            "chassis_mac":   d["chassis_mac"],
        }
        if d["chassis_mac"]:
            mac_to_hostname[d["chassis_mac"]] = d["hostname"]

    # 2. Résolution des voisins LLDP → trouver le vrai hostname
    def resolve_neighbor(remote_hostname: str, remote_mac: str) -> str:
        """Retourne le hostname canonique d'un voisin."""
        # Priorité 1 : match par chassis MAC
        if remote_mac and remote_mac in mac_to_hostname:
            return mac_to_hostname[remote_mac]
        # Priorité 2 : match direct par hostname normalisé
        if remote_hostname in nodes:
            return remote_hostname
        # Priorité 3 : recherche partielle (ex: "leaf-1" vs "leaf1")
        clean = re.sub(r'[^a-z0-9]', '', remote_hostname)
        for known in nodes:
            if re.sub(r'[^a-z0-9]', '', known) == clean:
                return known
        # Pas trouvé → nouveau nœud inconnu
        return remote_hostname

    edges = []
    seen_edges = set()

    for d in devices_info:
        for nbr in d["neighbors"]:
            canonical = resolve_neighbor(
                nbr["remote_hostname"],
                nbr["remote_chassis_mac"]
            )

            # Ajouter le nœud si vraiment inconnu (pas dans l'inventaire)
            if canonical not in nodes:
                nodes[canonical] = {
                    "id":            canonical,
                    "label":         nbr["remote_hostname"],   # nom brut LLDP
                    "group":         "unknown",
                    "management_ip": "N/A",
                    "model":         "N/A",
                    "eos_version":   "N/A",
                    "serial":        "N/A",
                    "chassis_mac":   nbr["remote_chassis_mac"],
                }
                if nbr["remote_chassis_mac"]:
                    mac_to_hostname[nbr["remote_chassis_mac"]] = canonical

            # Dédupliquer les liens
            edge_key = tuple(sorted([
                f"{d['hostname']}:{nbr['local_interface']}",
                f"{canonical}:{nbr['remote_interface']}"
            ]))
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append({
                    "from":             d["hostname"],
                    "to":               canonical,
                    "local_interface":  nbr["local_interface"],
                    "remote_interface": nbr["remote_interface"],
                })

    return {"nodes": list(nodes.values()), "edges": edges}

# ─── HTML generator ───────────────────────────────────────────────────────────

def generate_html(graph: dict, output_path: str):
    nodes_json = json.dumps(graph["nodes"], indent=2)
    edges_json = json.dumps(graph["edges"], indent=2)

    template_path = Path(__file__).parent / "network_map.html"
    if not template_path.exists():
        print(f"  [!] Template introuvable : {template_path}")
        return

    html = template_path.read_text(encoding="utf-8")
    html = html.replace("__NODES_DATA__", nodes_json)
    html = html.replace("__EDGES_DATA__", edges_json)
    Path(output_path).write_text(html, encoding="utf-8")
    print(f"\n✅ Carte réseau générée : {output_path}")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LLDP Network Mapper — Arista eAPI → HTML vis.js")
    parser.add_argument("-i", "--inventory", default="inventory-lldp.json")
    parser.add_argument("-o", "--output",    default="network_map_output.html")
    parser.add_argument("--cache", action="store_true",
                        help="Réutilise le cache JSON sans re-interroger les équipements")
    args = parser.parse_args()

    try:
        with open(args.inventory) as f:
            inventory = json.load(f)
    except FileNotFoundError:
        print(f"[!] Inventaire introuvable : {args.inventory}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"[!] JSON invalide : {e}")
        sys.exit(1)

    hosts = inventory.get("hosts", [])
    if not hosts:
        print("[!] Aucun hôte dans l'inventaire.")
        sys.exit(1)

    cache_file = ".lldp_cache.json"

    if args.cache and Path(cache_file).exists():
        print("[*] Chargement depuis le cache...")
        with open(cache_file) as f:
            devices_info = json.load(f)
    else:
        print(f"[*] Collecte LLDP sur {len(hosts)} équipement(s)...\n")
        devices_info = []
        for host in hosts:
            info = collect_device_info(host)
            if info:
                devices_info.append(info)
                print(f"  ✓ {info['display_name']} — MAC:{info['chassis_mac']} — {len(info['neighbors'])} voisin(s)\n")

        if not devices_info:
            print("[!] Aucune donnée collectée.")
            sys.exit(1)

        with open(cache_file, "w") as f:
            json.dump(devices_info, f, indent=2)
        print(f"[*] Cache sauvegardé : {cache_file}")

    print("[*] Construction du graphe (déduplication MAC + hostname)...")
    graph = build_graph(devices_info)

    # Résumé déduplication
    unknown = [n for n in graph["nodes"] if n["group"] == "unknown"]
    known   = [n for n in graph["nodes"] if n["group"] != "unknown"]
    print(f"    {len(known)} nœud(s) inventoriés, {len(unknown)} nœud(s) inconnu(s), {len(graph['edges'])} lien(s)")
    if unknown:
        print(f"    ⚠ Nœuds non résolus (absents de l'inventaire) :")
        for u in unknown:
            print(f"      - {u['label']} (MAC: {u['chassis_mac'] or 'inconnue'})")

    print("[*] Génération HTML...")
    generate_html(graph, args.output)

if __name__ == "__main__":
    main()