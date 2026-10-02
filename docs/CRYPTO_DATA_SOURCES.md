# Crypto data sources for the pair/token models

_Researched 2026-10-02 from web search results (links at the bottom). Free-tier limits change often and several figures come from
third-party summaries, so confirm them before depending on them. "Better and faster" is not a single answer: Allium is the broadest
and fastest managed option but is freemium/enterprise-priced; the free options below each cover a slice._

## What each layer needs, and the best free source for it

| Need | Best free option(s) | Notes / limits (as reported) |
|---|---|---|
| Exchange candles, tickers, order books, 100+ exchanges | **ccxt** (35k+ stars; Python/JS/Go/...; public data needs no key) | Unified schema across venues. Rate limits are per exchange. |
| Bulk exchange history (spot + futures klines, trades) | **data.binance.vision** + `binance-bulk-downloader` / `binance-historical-data` | Static files, no API limit; Binance only. Our sandbox is geo-blocked from Binance APIs, cloud agents may not be. |
| Token ↔ network ↔ contract ↔ exchange listings | **CoinPaprika** (plugin: 12k+ coins, 350+ exchanges, contract lookups, keyless) and **CoinGecko keyless API** | Good for the token/network/exchange map used as model features. |
| DEX pools, on-chain prices across networks | **GeckoTerminal API** (keyless; ~250 networks, ~1,900 DEXes) | Keyless limit reported ~10-30 calls/min: use for snapshots, not bulk history. |
| TVL, stablecoins (peg), bridges, DEX volume, yields | **DefiLlama free API** (keyless; ~500 req/5 min) | Bridge list only on the free tier; open-source adapters on GitHub. |
| Token / contract / holder details, explorer data | **Blockscout** (connector; keyless) | Per-chain explorers; slower than an indexer for aggregates. |
| Bulk on-chain SQL across chains | **Allium** (plugin, 135-150+ chains; freemium) | Fastest/broadest managed option; free-tier limits not published in what I found. |
| Self-hosted indexing (free, you run it) | **reth-indexer**, **DipDup** (Python), **SubQuery**, **The Graph subgraphs**; curated list: `o-az/awesome-evm-indexer` | No data fees but needs infra/ops. |
| Exchange websocket feeds | **ccxt** (websocket support) | `bmoscon/cryptofeed` is reported archived (Jul 2026): do not add it as a new dependency. |
| ETF flows / sentiment | **CryptoETF Flows** plugin | Needs enabling in claude.ai. |
| Hyperliquid whale/trader positioning | **Coinversa Pulse** connector | Needs an account. |

## Recommended free stack for this project
1. **ccxt** for the broad exchange universe (candles, tickers, books) - one code path for 100+ venues.
2. **CoinPaprika/CoinGecko** for the token->network->contract->exchange map; **DefiLlama** for stablecoin/bridge/TVL risk features;
   **GeckoTerminal** for DEX pool prices of wrapped/bridged tokens.
3. **Allium + Blockscout** (enabled by the user) for on-chain structure questions the free APIs cannot answer.
4. Self-hosted indexers only if a feature proves valuable and rate limits bind.

Everything is read-only public data. No source here is verified from this build sandbox (it has no market-data egress); cloud
research agents verify each one against live endpoints before a feature depends on it.

## Sources
- [Dune vs Allium](https://dune.com/alternatives/dune-vs-allium) · [Best onchain analytics platforms 2026](https://eco.com/support/en/articles/14800357-best-onchain-analytics-platforms-2026) · [Best blockchain indexing tools](https://www.spaceandtime.io/blog/best-blockchain-indexing-tools)
- [o-az/awesome-evm-indexer](https://github.com/o-az/awesome-evm-indexer) · [SaaSHub open-source indexer alternatives](https://www.saashub.com/open-source/blockchain-data-indexer-alternatives)
- [GeckoTerminal API guide](https://apiguide.geckoterminal.com) · [CoinGecko keyless public API](https://docs.coingecko.com/docs/keyless-public-api.md)
- [DefiLlama free TVL and analytics](https://eco.com/support/en/articles/14800367-defillama-free-tvl-and-defi-analytics) · [api-evangelist/defillama](https://github.com/api-evangelist/defillama)
- [bmoscon/cryptofeed](https://github.com/bmoscon/cryptofeed) (archived) · [ccxt (via registry summary)](https://tessl.io/registry/skills/github/HKUDS/Vibe-Trading/ccxt)
- [binance-bulk-downloader](https://github.com/aoki-h-jp/binance-bulk-downloader) · [Binance public data](https://docsearch.algolia.com/mcp/docs/repo/binance/binance-public-data)
- [Allium vs Zerion](https://www.allium.so/compare/allium-vs-zerion) · [Stablecoin depeg spread signals](https://www.elliptic.co/corpus/gen-3884/intermarket-spread/stablecoin-depeg-spread-signals.html)
