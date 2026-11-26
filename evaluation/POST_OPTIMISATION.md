# Phase 7 post-optimisation live evaluation

Both runs used **gpt-4.1-mini**, policy **3**, fixture seed **42**, and the fixed reporting date of **30 June 2025**. Scores and latency were checked against the saved evaluation reports. Every scored case had a matching terminal audit event.

Rates are shown as numerator/denominator. **0/0** means the metric does not apply. Latency measures the local agent call through its terminal audit write.

## Original golden dataset

Dataset **1.0.0**, local run ID `live-xvydbuzs`.

| Scope | Cases | Status | Intent | Numerical | Authorisation | Unsafe SQL | False rejections | Verified blocking | API/system errors | P50 ms | P95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Overall | 40 | 40/40 | 19/19 | 19/19 | 28/28 | 0/21 | 0/19 | 21/21 | 0/40 | 1.966 | 1767.590 |
| Adversarial | 6 | 6/6 | 0/0 | 0/0 | 2/2 | 0/6 | 0/0 | 6/6 | 0/6 | 0.645 | 0.721 |
| Ambiguous | 3 | 3/3 | 0/0 | 0/0 | 0/0 | 0/3 | 0/0 | 3/3 | 0/3 | 0.506 | 0.539 |
| Edge | 3 | 3/3 | 1/1 | 1/1 | 2/2 | 0/2 | 0/1 | 2/2 | 0/3 | 2.002 | 1040.964 |
| Grouped | 6 | 6/6 | 6/6 | 6/6 | 6/6 | 0/0 | 0/6 | 0/0 | 0/6 | 1115.980 | 1634.628 |
| Standard | 12 | 12/12 | 12/12 | 12/12 | 12/12 | 0/0 | 0/12 | 0/0 | 0/12 | 1325.448 | 2431.298 |
| Unauthorized | 6 | 6/6 | 0/0 | 0/0 | 6/6 | 0/6 | 0/0 | 6/6 | 0/6 | 0.833 | 1.727 |
| Unsupported | 4 | 4/4 | 0/0 | 0/0 | 0/0 | 0/4 | 0/0 | 4/4 | 0/4 | 0.532 | 0.650 |

Failed cases and failure diagnostics: none.

## Supplemental validation

Dataset **supplemental-1.0.0**, local run ID `live-u20f2ckc`.

These 15 validation cases were created during optimisation and share the same synthetic fixture as the golden set.

| Scope | Cases | Status | Intent | Numerical | Authorisation | Unsafe SQL | False rejections | Verified blocking | API/system errors | P50 ms | P95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Overall | 15 | 15/15 | 6/6 | 6/6 | 8/8 | 0/9 | 0/6 | 9/9 | 0/15 | 2.097 | 1445.635 |
| Adversarial | 4 | 4/4 | 0/0 | 0/0 | 1/1 | 0/4 | 0/0 | 4/4 | 0/4 | 0.796 | 1.109 |
| Ambiguous | 3 | 3/3 | 0/0 | 0/0 | 0/0 | 0/3 | 0/0 | 3/3 | 0/3 | 2.097 | 2.162 |
| Edge | 1 | 1/1 | 0/0 | 0/0 | 1/1 | 0/1 | 0/0 | 1/1 | 0/1 | 1.012 | 1.012 |
| Grouped | 3 | 3/3 | 3/3 | 3/3 | 3/3 | 0/0 | 0/3 | 0/0 | 0/3 | 1160.582 | 1398.149 |
| Standard | 3 | 3/3 | 3/3 | 3/3 | 3/3 | 0/0 | 0/3 | 0/0 | 0/3 | 1190.550 | 1464.416 |
| Unsupported | 1 | 1/1 | 0/0 | 0/0 | 0/0 | 0/1 | 0/0 | 1/1 | 0/1 | 1.331 | 1.331 |

Failed cases and failure diagnostics: none.

## Scope of evidence

The golden set guided optimisation, and the supplemental set was written during it. These small, synthetic runs measure performance on the tested requests with simulated roles. They provide no independent holdout evidence or production safety guarantee.

The original Phase 6 evidence remains in [BASELINE.md](BASELINE.md).
