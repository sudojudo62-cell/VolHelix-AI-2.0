"""Default token universe for the pair ranker (canonical base assets; each venue adapter maps them to its own symbols).
Assets a venue does not list are dropped and reported, not fatal."""
DEFAULT_UNIVERSE = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT", "LTC", "BCH", "ATOM", "NEAR", "APT",
    "ARB", "OP", "SUI", "INJ", "TON", "UNI", "AAVE", "FIL", "ETC", "XLM",
]
