"""Summary-file schema: what a research agent hands to the dashboard's governing agent (research/summaries/<slug>.json)."""
import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"


class Source(BaseModel):
    slug: str
    title: str
    url: str


class CellResult(BaseModel):
    strategy_id: str
    venue: str
    assets: List[str]
    interval: str
    oos_return_pct: float
    oos_sharpe: float
    oos_max_drawdown_pct: float
    oos_bars: int
    oos_last_30d_return_pct: float
    benchmark_oos_return_pct: float          # buy-and-hold of the same assets over the same OOS bars
    benchmark_oos_max_drawdown_pct: float = 0.0
    turnover_per_day: float
    deflated_sharpe_prob: Optional[float] = None
    folds: List[Dict[str, Any]] = Field(default_factory=list)
    best_full_sample: Dict[str, Any] = Field(default_factory=dict)   # descriptive only (in-sample)
    regime_performance: List[Dict[str, Any]] = Field(default_factory=list)
    modal_params: Dict[str, Any] = Field(default_factory=dict)
    window_72h_pct: Dict[str, float] = Field(default_factory=dict)  # distribution of OOS 72h returns


class RecommendedTrial(BaseModel):
    strategy_id: str
    venue: str
    assets: List[str]
    interval: str
    params: Dict[str, Any]
    expected_72h_return_pct: float
    expected_72h_std_pct: float
    expected_72h_p10_pct: float
    favorable_regimes: List[str] = Field(default_factory=list)
    unfavorable_regimes: List[str] = Field(default_factory=list)
    costs_bps: Dict[str, float] = Field(default_factory=dict)


class StrategySummary(BaseModel):
    schema_version: str = SCHEMA_VERSION
    source: Source
    strategy_ids: List[str]
    run: Dict[str, Any]                      # period, venues, assets, intervals, costs, trials, code commit, timestamps
    results: List[CellResult]
    factor_attribution: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    data_quality: List[Dict[str, Any]] = Field(default_factory=list)
    skipped: List[Dict[str, str]] = Field(default_factory=list)
    viable: bool
    viability_reasons: List[str] = Field(default_factory=list)
    recommended_trial: Optional[RecommendedTrial] = None
    caveats: List[str] = Field(default_factory=list)


def save_summary(s: StrategySummary, path: str) -> None:
    with open(path, "w") as f:
        json.dump(json.loads(s.model_dump_json()), f, indent=2)


def load_summary(path: str) -> StrategySummary:
    with open(path) as f:
        return StrategySummary.model_validate(json.load(f))
