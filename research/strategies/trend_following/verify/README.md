Run-2 fix-verification scripts (run from the repo root: `PYTHONPATH=. python research/strategies/trend_following/verify/<script>.py`).
They hit PUBLIC read-only endpoints (or the lab's disk cache) and print the numbers quoted in research/reports/trend_following.md section 2.
Not collected by pytest (no test_ prefix). `dsr_recompute.py` and `independent_tsmom_ls.py` read research/summaries/trend_following.json.
