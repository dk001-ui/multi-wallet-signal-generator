# Multi-Wallet Signal Generator

Monitors multiple Solana wallets for synchronized swap activity and broadcasts buy signals to Discord.

## How It Works

1. Every 15 minutes, the system polls the [Helius Enhanced Transactions API](https://dev.helius.xyz) for recent SWAP events across all tracked wallets.
2. If 2 or more wallets buy the same token within the configured time window, a buy signal is triggered.
3. The alert is posted to a Discord channel with token info, wallet labels, amounts, and links to DexScreener / Birdeye.

## Setup

### 1. Add Wallets
Edit `data/wallets.json` and replace placeholder addresses with real Solana wallet addresses.

### 2. Environment Variables
| Variable | Description |
|---|---|
| `HELIUS_API_KEY` | Your Helius API key — get one free at [dev.helius.xyz](https://dev.helius.xyz) |
| `DISCORD_CHANNEL_ID` | The Discord channel ID to post alerts to |

### 3. Run Locally
```bash
pip install requests
export HELIUS_API_KEY=your_key_here
export DISCORD_CHANNEL_ID=your_channel_id
python multiwallet_alerter.py
```

## Configuration (`data/wallets.json`)

| Field | Default | Description |
|---|---|---|
| `min_wallets_threshold` | `2` | Minimum wallets that must buy the same token to trigger a signal |
| `time_window_minutes` | `5` | Time window to correlate buys across wallets |

## Alert Format

```
🚨 MULTI-WALLET BUY SIGNAL

Token: TOKEN_NAME (SYMBOL)
Mint: <address>

Wallets:
• Whale #1 — 10,000 SYMBOL ($250)
• Smart Money #1 — 5,000 SYMBOL ($125)

DexScreener | Birdeye
```

## Files

| File | Purpose |
|---|---|
| `multiwallet_alerter.py` | Core script |
| `data/wallets.json` | Wallet list and config |
