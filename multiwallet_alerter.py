#!/usr/bin/env python3
"""
Multi-Wallet Buy Alerter
========================
Polls Helius Enhanced Transactions API for all tracked wallets.
When 2+ wallets buy the same token within the time window, posts an alert to Discord.

Required env vars:
  HELIUS_API_KEY       - Your Helius API key (https://dev.helius.xyz)
  DISCORD_CHANNEL_ID   - Discord channel ID to post alerts to
  WALLETS_FILE         - Path to wallets.json (default: data/wallets.json)
  CACHE_FILE           - Path to rolling buy cache (default: data/buy_cache.json)
"""

import json
import os
import time
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────────────────────
HELIUS_API_KEY     = os.environ.get("HELIUS_API_KEY", "YOUR_HELIUS_API_KEY")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "YOUR_DISCORD_CHANNEL_ID")
WALLETS_FILE       = os.environ.get("WALLETS_FILE", "data/wallets.json")
CACHE_FILE         = os.environ.get("CACHE_FILE", "data/buy_cache.json")

HELIUS_TXN_URL   = "https://api-mainnet.helius-rpc.com/v0/addresses/{address}/transactions"
HELIUS_TOKEN_URL = "https://api-mainnet.helius-rpc.com/v0/token-metadata"

# Stablecoins + wrapped assets to ignore
IGNORED_MINTS = {
    "So11111111111111111111111111111111111111112",    # SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", # USDT
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So", # mSOL
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs", # wETH
}

# ── LOAD CONFIG ──────────────────────────────────────────────────────────────────────────
def load_wallets():
    with open(WALLETS_FILE) as f:
        cfg = json.load(f)
    wallet_map = {w["address"]: w.get("label", w["address"][:8] + "...") for w in cfg["wallets"]}
    threshold  = cfg.get("min_wallets_threshold", 2)
    window_min = cfg.get("time_window_minutes", 5)
    return wallet_map, threshold, window_min

# ── CACHE ────────────────────────────────────────────────────────────────────────────
def load_cache():
    if Path(CACHE_FILE).exists():
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {"events": [], "alerted_tokens": []}

def save_cache(cache):
    Path(CACHE_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

def prune_cache(cache, window_min):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=window_min)).isoformat()
    cache["events"] = [e for e in cache["events"] if e["timestamp"] >= cutoff]
    return cache

# ── HELIUS: FETCH SWAPS ───────────────────────────────────────────────────────────────────
def fetch_recent_swaps(address, since_iso):
    url = HELIUS_TXN_URL.format(address=address)
    params = {"api-key": HELIUS_API_KEY, "type": "SWAP", "limit": 50}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        txns = resp.json()
    except Exception as e:
        print(f"[WARN] Failed to fetch txns for {address}: {e}")
        return []

    buys = []
    since_ts = datetime.fromisoformat(since_iso).timestamp()
    for tx in txns:
        ts = tx.get("timestamp", 0)
        if ts < since_ts:
            continue
        swap  = tx.get("events", {}).get("swap", {})
        token = swap.get("tokenBought", {})
        mint  = token.get("mint", "")
        if not mint or mint in IGNORED_MINTS:
            continue
        buys.append({
            "wallet":    address,
            "mint":      mint,
            "amount":    token.get("tokenAmount", 0),
            "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "signature": tx.get("signature", ""),
        })
    return buys

# ── HELIUS: TOKEN METADATA ──────────────────────────────────────────────────────────────────
def resolve_token_metadata(mints):
    if not mints:
        return {}
    try:
        resp = requests.post(
            f"{HELIUS_TOKEN_URL}?api-key={HELIUS_API_KEY}",
            json={"mintAccounts": list(mints)},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception as e:
        print(f"[WARN] Token metadata fetch failed: {e}")
        return {}

    meta = {}
    for item in results:
        mint    = item.get("account", "")
        onchain = item.get("onChainMetadata", {}).get("metadata", {}).get("data", {})
        offchn  = item.get("offChainMetadata", {}).get("metadata", {})
        name    = onchain.get("name") or offchn.get("name") or "Unknown"
        symbol  = onchain.get("symbol") or offchn.get("symbol") or "???"
        meta[mint] = {"name": name.strip(), "symbol": symbol.strip()}
    return meta

# ── CORRELATION ───────────────────────────────────────────────────────────────────────────
def find_signals(events, threshold, alerted_tokens):
    token_wallets = defaultdict(set)
    for e in events:
        token_wallets[e["mint"]].add(e["wallet"])
    signals = []
    for mint, wallets in token_wallets.items():
        if mint in alerted_tokens:
            continue
        if len(wallets) >= threshold:
            signals.append({"mint": mint, "wallets": list(wallets)})
    return signals

# ── FORMAT DISCORD MESSAGE ─────────────────────────────────────────────────────────────────
def format_discord_message(signal, meta, wallet_labels, window_min):
    mint   = signal["mint"]
    wallets = signal["wallets"]
    token  = meta.get(mint, {"name": "Unknown", "symbol": "???"})
    wallet_lines = "\n".join(
        f"  - `{wallet_labels.get(w, w[:8]+'...')}` ({w[:6]}...{w[-4:]})"
        for w in wallets
    )
    dex_link = f"https://dexscreener.com/solana/{mint}"
    bird_lnk = f"https://birdeye.so/token/{mint}?chain=solana"
    return (
        f"\U0001f6a8 **MULTI-WALLET BUY SIGNAL**\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"**Token:** {token['name']} ({token['symbol']})\n"
        f"**Contract:** `{mint}`\n"
        f"**Wallets that bought ({len(wallets)}) in last {window_min}m:**\n"
        f"{wallet_lines}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"[DexScreener]({dex_link}) | [Birdeye]({bird_lnk})"
    )

# ── MAIN ────────────────────────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().isoformat()}] Starting multi-wallet alerter run...")
    wallet_map, threshold, window_min = load_wallets()
    cache    = load_cache()
    cache    = prune_cache(cache, window_min)
    since_iso = (datetime.now(timezone.utc) - timedelta(minutes=window_min)).isoformat()
    known_sigs = {e["signature"] for e in cache["events"]}

    new_events = []
    for address in wallet_map:
        swaps = fetch_recent_swaps(address, since_iso)
        for swap in swaps:
            if swap["signature"] not in known_sigs:
                new_events.append(swap)
                known_sigs.add(swap["signature"])
        time.sleep(0.3)

    print(f"[INFO] {len(new_events)} new buy events across {len(wallet_map)} wallets.")
    cache["events"].extend(new_events)

    signals = find_signals(cache["events"], threshold, cache["alerted_tokens"])
    print(f"[INFO] {len(signals)} new signal(s) found.")

    discord_messages = []
    if signals:
        meta = resolve_token_metadata({s["mint"] for s in signals})
        for signal in signals:
            msg = format_discord_message(signal, meta, wallet_map, window_min)
            discord_messages.append({"mint": signal["mint"], "message": msg})
            cache["alerted_tokens"].append(signal["mint"])

    cache["alerted_tokens"] = cache["alerted_tokens"][-500:]
    save_cache(cache)

    summary = {
        "signals_found": len(signals),
        "new_events":    len(new_events),
        "wallets_tracked": len(wallet_map),
        "discord_messages": discord_messages,
        "signals": [
            {"mint": s["mint"], "wallet_count": len(s["wallets"]), "wallets": s["wallets"]}
            for s in signals
        ],
    }
    print(json.dumps(summary))
    return summary

if __name__ == "__main__":
    main()
