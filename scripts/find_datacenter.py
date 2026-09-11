#!/usr/bin/env python3
"""Find which RunPod datacenter to deploy this project's pod in, and
optionally create a network volume there.

Why this exists: RunPod GPU stock is per-datacenter and changes
minute-to-minute, but pod_up.py's own --dry-run ranking queries GLOBAL
stock (no dataCenterId) -- accurate for an unpinned deploy, but
misleading once a network volume pins the deploy to one specific
datacenter (see README.md's Troubleshooting section, "GPU shows in
stock but every deploy is refused", investigated 2026-09-11). This
script queries each *network-volume-capable* datacenter directly via
`lowestPrice(input:{dataCenterId:...})`, so what it reports is what a
real pinned deploy would actually see.

Only some datacenters support network volumes at all -- attempting to
create one in an unsupported datacenter fails outright. This is
discovered dynamically via `dataCenters { storageSupport }`, not
hardcoded, since RunPod has changed this list before (see the design
doc for the 2026-09-11 investigation).

Usage:
    python3 scripts/find_datacenter.py
        List volume-capable datacenters with real >=MIN_VRAM GPU stock
        right now, cheapest first.

    python3 scripts/find_datacenter.py --create-volume EU-SE-1
        Create a network volume in that datacenter (fails immediately,
        clearly, if it doesn't support volumes) and print the next
        steps (populate weights, point NETWORK_VOLUME_ID at it).

    python3 scripts/find_datacenter.py --list-volumes
        List this account's existing network volumes.

    python3 scripts/find_datacenter.py --delete-volume <id>
        Delete a network volume permanently (e.g. a failed/unverified
        one from a prior --create-volume). Asks for confirmation.

Env knobs (same defaults as pod_up.py): MIN_VRAM, MAX_PRICE, GPU_MATCH,
ACCOUNT_KEY_FILE.
"""
import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import pod_up  # noqa: E402


def list_volume_capable_datacenters(account_key):
    data = pod_up.gql(account_key, "query { dataCenters { id storageSupport } }")
    return [d["id"] for d in data["data"]["dataCenters"] if d["storageSupport"]]


def stock_in_datacenter(account_key, dc, min_vram, max_price, gpu_match):
    """Real per-DC >=min_vram GPU listings, cheapest first. Same filter
    logic as pod_up.rank_gpus, just with dataCenterId pinned in the
    GraphQL query itself instead of ranking a global aggregate."""
    query = (
        "query { gpuTypes { id memoryInGb "
        'lowestPrice(input:{gpuCount:1,secureCloud:true,dataCenterId:"%s"})'
        "{stockStatus uninterruptablePrice} } }" % dc
    )
    data = pod_up.gql(account_key, query)
    out = []
    for g in data["data"]["gpuTypes"]:
        vram = g.get("memoryInGb")
        lp = g.get("lowestPrice") or {}
        stock = lp.get("stockStatus")
        price = lp.get("uninterruptablePrice")
        if not vram or vram < min_vram or not stock:
            continue
        if price is None or price > max_price:
            continue
        if gpu_match and not re.search(gpu_match, g["id"], re.IGNORECASE):
            continue
        out.append({"id": g["id"], "vram": vram, "price": price, "stock": stock})
    out.sort(key=lambda g: g["price"])
    return out


def create_volume(account_key, dc, name, size_gb):
    mut = (
        "mutation{createNetworkVolume(input:{name:%s, size:%d, dataCenterId:%s})"
        "{id name size dataCenterId}}" % (json.dumps(name), size_gb, json.dumps(dc))
    )
    return pod_up.gql(account_key, mut)


def list_volumes(account_key):
    data = pod_up.gql(account_key, "query { myself { networkVolumes { id name size dataCenterId } } }")
    return data["data"]["myself"]["networkVolumes"]


def delete_volume(account_key, volume_id):
    pod_up.gql(account_key, "mutation{deleteNetworkVolume(input:{id:%s})}" % json.dumps(volume_id))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--create-volume", metavar="DATACENTER_ID",
                         help="Create a network volume in this datacenter")
    parser.add_argument("--size", type=int, default=30, help="Volume size in GB (default 30)")
    parser.add_argument("--name", default="witch-avatar-echomimicv3-weights",
                         help="Volume name (default matches this project's existing volumes)")
    parser.add_argument("--list-volumes", action="store_true", help="List existing network volumes")
    parser.add_argument("--delete-volume", metavar="VOLUME_ID", help="Delete a network volume (asks to confirm)")
    args = parser.parse_args()

    account_key = pod_up.load_account_key()

    if args.list_volumes:
        volumes = list_volumes(account_key)
        if not volumes:
            print("No network volumes on this account.")
        for v in volumes:
            print(f"  {v['id']:15s} {v['dataCenterId']:10s} {v['size']}GB  {v['name']}")
        return

    if args.delete_volume:
        confirm = input(f"Delete network volume {args.delete_volume}? This is permanent. [y/N] ")
        if confirm.strip().lower() != "y":
            print("Aborted.")
            return
        delete_volume(account_key, args.delete_volume)
        print(f"Deleted {args.delete_volume}.")
        return

    if args.create_volume:
        print(f"Creating a {args.size}GB volume named '{args.name}' in {args.create_volume} ...")
        res = create_volume(account_key, args.create_volume, args.name, args.size)
        if res.get("errors"):
            sys.exit(f"FAILED: {res['errors'][0]['message']}")
        vol = res["data"]["createNetworkVolume"]
        print(f"Created: id={vol['id']} dataCenterId={vol['dataCenterId']} size={vol['size']}GB")
        print()
        print("Next steps:")
        print(f"  1. Populate it with weights (see scripts/populate_echomimicv3_volume.sh --")
        print(f"     deploy a temporary pod with NETWORK_VOLUME_ID={vol['id']} and run that")
        print(f"     script over SSH on it, same as this project's other volumes).")
        print(f"  2. Point deploys at it: export NETWORK_VOLUME_ID={vol['id']}")
        print(f"     (or edit DEFAULT_NETWORK_VOLUME_ID in scripts/pod_up.py once confirmed working).")
        print(f"  3. Once confirmed working end-to-end, delete any other unused volume with")
        print(f"     --delete-volume to avoid paying for idle storage.")
        return

    min_vram = float(pod_up.env("MIN_VRAM") or str(pod_up.DEFAULT_MIN_VRAM))
    max_price = float(pod_up.env("MAX_PRICE") or str(pod_up.DEFAULT_MAX_PRICE))
    gpu_match = pod_up.env("GPU_MATCH", pod_up.DEFAULT_GPU_MATCH)

    dcs = list_volume_capable_datacenters(account_key)
    print(f"Checking {len(dcs)} network-volume-capable datacenters for "
          f">= {min_vram:g}GB GPUs under ${max_price:g}/hr matching /{gpu_match or '.*'}/ ...")
    print("(RunPod stock changes minute-to-minute -- this is a snapshot, not a guarantee.)")
    print()

    all_rows = []
    for dc in dcs:
        try:
            rows = stock_in_datacenter(account_key, dc, min_vram, max_price, gpu_match)
        except Exception as e:
            print(f"  {dc}: query failed ({e})")
            continue
        for r in rows:
            all_rows.append((dc, r))

    if not all_rows:
        print("No in-stock GPU matching your filter in any volume-capable datacenter right now.")
        print("Try again shortly, or raise MAX_PRICE / relax GPU_MATCH.")
        return

    all_rows.sort(key=lambda t: t[1]["price"])
    print(f"{'Datacenter':<12} {'GPU':<45} {'VRAM':>6} {'Price/hr':>10} {'Stock':>7}")
    for dc, r in all_rows:
        print(f"{dc:<12} {r['id']:<45} {r['vram']:>4}GB ${r['price']:>8.2f} {r['stock']:>7}")

    print()
    best_dc = all_rows[0][0]
    print(f"Cheapest right now: {best_dc}. To use it:")
    print(f"  python3 scripts/find_datacenter.py --create-volume {best_dc}")


if __name__ == "__main__":
    main()
