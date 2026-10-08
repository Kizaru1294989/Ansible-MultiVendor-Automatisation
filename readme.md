<div align="center">

# ⚡ Arista Fabric Automation

### From bare switches to a multi-datacenter VXLAN EVPN fabric — in minutes, not days.

**Zero manual cabling maps. Zero IP spreadsheets. Zero copy-paste configs.**
Point it at a management range, answer a few business questions, deploy.

![Python](https://img.shields.io/badge/Python-3.x-3776AB?logo=python&logoColor=white)
![Ansible](https://img.shields.io/badge/Ansible-arista.eos-EE0000?logo=ansible&logoColor=white)
![Jinja2](https://img.shields.io/badge/Jinja2-templates-B41717?logo=jinja&logoColor=white)
![Arista](https://img.shields.io/badge/Arista-EOS%20eAPI-1E3A5F)
![VXLAN EVPN](https://img.shields.io/badge/VXLAN-EVPN-8A2BE2)
![Multi-DC](https://img.shields.io/badge/Multi--DC-ISN%20%2F%20DCI-orange)

</div>

---

## 🎯 Why this project?

Building a VXLAN EVPN fabric by hand means:
- mapping every cable,
- planning hundreds of /31s, loopbacks, ASNs, RDs and VNIs in a spreadsheet,
- writing dozens of near-identical configs,
- keeping two datacenters consistent.

One typo and an entire tenant goes dark.

**This toolkit replaces all of that with a single workflow.** The network describes itself through LLDP, the addressing plan is computed deterministically, and Ansible pushes validated, templated configurations, all the way to the inter-DC interconnect.

| Manual approach | With Arista Fabric Automation |
|---|---|
| Hours spent mapping cables | **Auto-discovered via LLDP** in seconds |
| IP plan in Excel | **Computed** from a handful of base ranges |
| Per-device configs written by hand | **Generated** from Jinja2 templates per role |
| VNI / IP drift between DCs | **Enforced consistency** across datacenters |
| Risky changes, no rollback | **Automatic backups**, one-command restore |

---

## 🗺️ Target topology

![Multi-DC VXLAN EVPN topology](docs/topology.png)

Two datacenters, each built as a **Spine/Leaf fabric** with **MLAG leaf pairs**, **border leaves** and an **ISN** (Inter-Site Network) carrying EVPN between sites.

- **Underlay:** eBGP over point-to-point /31s, one ASN for the spines and one per leaf pair
- **Overlay:** MP-BGP EVPN between loopbacks, with spines as transit
- **Data plane:** VXLAN with an anycast VTEP shared by each MLAG pair
- **Services:** L2VNI per VLAN, L3VNI per VRF, anycast gateway on every leaf
- **Inter-DC:** border leaves peer with the ISN (underlay + EVPN) to stretch tenants across sites

---

## 🏗️ Architecture

![Project architecture](docs/architecture.png)

---

## ✨ Key features

### 🔍 Zero-touch discovery
- Parallel TCP scan of the management range
- Arista **eAPI (JSON-RPC)** collection of hostname, chassis MAC and LLDP neighbors
- Automatic role detection: **spine / leaf / host**
- Auto-detection of **MLAG peer-links**, **server ports** and **border leaves / DCI links**
- Chassis-MAC deduplication, so hostname variations (`leaf-1` vs `leaf1`) never break the graph

### 🧮 Deterministic addressing engine
Everything is derived from the device ID and a few base ranges:

| Element | Computed automatically |
|---|---|
| Spine↔Leaf links | Sequential /31s |
| Loopback0 (router-id / EVPN) | Per device |
| Loopback1 (VTEP) | Shared per MLAG pair |
| BGP ASNs | Spines + one per leaf pair |
| MLAG peer addressing | /31 per pair |
| Route distinguishers | Per pair |
| Anycast gateway MAC | Per pair |
| L2VNI / L3VNI | `vni_base + vlan_id`, reused across DCs |
| SVI IPs | VIP + next free addresses, collision-aware across DCs |

### 🌍 Built for multi-datacenter
- VNIs and SVI subnets are **automatically reused** from existing DCs: no drift, no overlap
- Dedicated **ISN mode** discovers inter-site routers, matches them to border leaves, and **regenerates every DC** with full EVPN peering

### 🚀 Modular deployment
Deploy layer by layer and validate each step:

```bash
./run.sh mlag dc1    # MLAG domain & peer-link
./run.sh bgp  dc1    # eBGP underlay
./run.sh evpn dc1    # VXLAN EVPN overlay
./run.sh evpn isn    # Inter-site network
```

### 🛡️ Day-2 operations included
- **Backups** of running-configs before changes
- **One-command restore** and **reset**
- **Multi-device CLI**: run `show` commands on a group or a single device
- Interactive **backup manager**

### 🗺️ Live topology map
An interactive HTML map generated straight from LLDP, with a hierarchical layout, per-device details, interface labels on hover, search and PNG export.

---

## 🧰 Tech stack

| Technology | Purpose |
|---|---|
| **Python 3** | Discovery engine, addressing logic, file generation |
| **Arista eAPI** | Structured JSON data straight from EOS |
| **LLDP** | Source of truth for physical topology |
| **Ansible** (`arista.eos` / `httpapi`) | Configuration push, backup, restore |
| **Jinja2** | Role-based EOS templates (spine, leaf, border leaf, host, ISN) |
| **YAML** | Generated inventories, `group_vars`, `host_vars` |
| **Bash** | Operator wrappers (`run.sh`, `manage_backups.sh`) |
| **vis.js** | Interactive topology visualization |

---

## ⚙️ How it works

1. **Discover**: `main.py` scans the range, queries eAPI and builds the fabric graph from LLDP.
2. **Confirm**: the operator validates border leaves and enters business intent only (VLANs, subnets, DCI ASN).
3. **Compute**: `generate_vars_auto.py` derives the full addressing plan.
4. **Generate**: Ansible inventory, `group_vars` and `host_vars` are written per datacenter.
5. **Deploy**: `run.sh` pushes each layer through role-based Jinja2 templates.
6. **Operate**: backups, restore, CLI and topology map.

---

## 🚀 Quick start

### Requirements

```bash
pip install requests pyyaml ansible
ansible-galaxy collection install arista.eos
```

eAPI enabled on the switches:

```
management api http-commands
   no shutdown
```

### Single datacenter

```bash
python3 main.py          # discover + input + generate
./run.sh mlag dc1
./run.sh bgp  dc1
./run.sh evpn dc1
```

### Two datacenters + ISN

```bash
python3 main.py          # DC1
python3 main.py          # DC2 (VNIs and IPs reused automatically)
python3 main.py --isn    # ISN discovery + border-leaf EVPN update
./run.sh evpn dc1
./run.sh evpn dc2
./run.sh evpn isn
```

### `main.py` modes

| Flag | Action |
|---|---|
| *(none)* | Full workflow: discovery, input, generation |
| `--discover` | Discovery only, writes `vars_auto_<dc>.json` |
| `--generate --dc dc1` | Regenerate inventory from existing JSON |
| `--isn` | Configure ISN and complete border-leaf EVPN peering |

### `run.sh` actions

| Action | Description |
|---|---|
| `mlag` | MLAG domain, VLAN 4094, peer-link |
| `bgp` | eBGP underlay |
| `mlag-bgp` | MLAG + BGP |
| `evpn` | VXLAN EVPN overlay (or ISN when `dc=isn`) |
| `cli` | Run show commands on a group or a host |
| `reset` | Reset devices to base config |
| `restore` | Restore the latest backup |
| `backups` | Interactive backup manager |

### Topology map

```bash
python3 lldp_mapper.py -i inventory-lldp.json -o network_map_output.html
python3 lldp_mapper.py --cache    # reuse cached LLDP data
```

---

## 📁 Project structure

```
.
├── main.py                  # Entry point: DC / ISN workflow
├── discover_fabric.py       # Scan + eAPI + LLDP analysis
├── generate_vars_auto.py    # Addressing engine + YAML generation
├── lldp_mapper.py           # HTML topology map
├── run.sh                   # Deployment & operations wrapper
├── manage_backups.sh        # Backup manager
├── inventories/             # Generated per DC (dc1, dc2, isn)
├── playbooks/               # deploy, isn_deploy, cli, restore
├── roles/arista/
│   ├── arista_mlag/
│   ├── arista_bgp/
│   ├── arista_vxlan-evpn_l3/
│   └── arista_restore/
├── conf-example/            # Reference EOS configs
└── docs/                    # Diagrams
```

---

## 🛣️ Roadmap

- [ ] Web UI and API backend (an NDFC-like controller)
- [ ] Persistent allocation state for fully idempotent re-runs
- [ ] Multi-vendor support (Cisco Nexus)
- [ ] Pre/post-deployment validation (BGP, EVPN, MLAG health checks)

---

## 🔐 Security

Never commit eAPI credentials. Use **ansible-vault** or environment variables, and keep generated `vars_auto_*.json` and `inventories/` out of version control.

---

<div align="center">

**Built for network engineers who'd rather design fabrics than type configs.**

</div>
