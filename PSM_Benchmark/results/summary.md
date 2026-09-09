# PSMBench results

## Run provenance

Each protocol's row comes from exactly one namespace. Runs made under different prompt hashes are not comparable and are never mixed in one table.

- **BGP**: `9b76d7d997` (prompt_template `546cbc9f`)
- **DCCP**: `a400bf1f76` (prompt_template `546cbc9f`)
- **DHCP**: `b2bc939a5b` (prompt_template `546cbc9f`)
- **FTP**: `b406c1c8a0` (prompt_template `546cbc9f`)
- **IMAP**: `3fe6991ddc` (prompt_template `546cbc9f`)
- **MQTT**: `177c362fdd` (prompt_template `546cbc9f`)
- **NNTP**: `7abe5d83af` (prompt_template `546cbc9f`)
- **POP3**: `9d07692d30` (prompt_template `546cbc9f`)
- **PPP**: `fc7d193a55` (prompt_template `546cbc9f`)
- **PPTP**: `41ca31f695` (prompt_template `546cbc9f`)
- **RTSP**: `bfcd910c32` (prompt_template `546cbc9f`)
- **SIP**: `15771e160d` (prompt_template `546cbc9f`)
- **SMTP**: `0bba7f4d49` (prompt_template `546cbc9f`)
- **TCP**: `2cd85b6f01` (prompt_template `546cbc9f`)

## Results

The A2ABreak pipeline on all 14 PSMBench protocols, scored with the benchmark's unmodified evaluator (threshold 0.5, `if_partial=False`). Each row is the export exactly as the pipeline produced it. Cost is the cost of producing that protocol's run.

| Protocol | Variant | states m/GT (ext) | S-P | S-R | S-F1 | transitions m/GT (ext) | T-P | T-R | T-F1 | Run cost (USD) |
|---|---|---|---|---|---|---|---|---|---|---|
| BGP | pipeline output | 6/6 (6) | 1.000 | 1.000 | 1.000 | 24/26 (109) | 0.220 | 0.923 | 0.356 | 15.81 |
| DCCP | pipeline output | 9/9 (9) | 1.000 | 1.000 | 1.000 | 16/25 (40) | 0.400 | 0.640 | 0.492 | 19.49 |
| DHCP | pipeline output | 8/8 (10) | 0.800 | 1.000 | 0.889 | 14/19 (30) | 0.467 | 0.737 | 0.571 | 12.31 |
| FTP | pipeline output | 8/10 (24) | 0.333 | 0.800 | 0.470 | 2/24 (52) | 0.038 | 0.083 | 0.053 | 11.64 |
| IMAP | pipeline output | 4/5 (9) | 0.444 | 0.800 | 0.571 | 5/11 (53) | 0.094 | 0.455 | 0.156 | 22.90 |
| MQTT | pipeline output | 12/12 (37) | 0.324 | 1.000 | 0.489 | 4/17 (57) | 0.070 | 0.235 | 0.108 | 9.01 |
| NNTP | pipeline output | 7/10 (27) | 0.259 | 0.700 | 0.378 | 0/16 (60) | 0.000 | 0.000 | 0.000 | 21.00 |
| POP3 | pipeline output | 3/3 (4) | 0.750 | 1.000 | 0.857 | 8/18 (20) | 0.400 | 0.444 | 0.421 | 5.72 |
| PPP | pipeline output | 10/10 (15) | 0.667 | 1.000 | 0.800 | 20/27 (138) | 0.145 | 0.741 | 0.242 | 11.42 |
| PPTP | pipeline output | 9/9 (11) | 0.818 | 1.000 | 0.900 | 9/19 (37) | 0.243 | 0.474 | 0.321 | 7.34 |
| RTSP | pipeline output | 3/3 (4) | 0.750 | 1.000 | 0.857 | 12/33 (30) | 0.400 | 0.364 | 0.381 | 40.39 |
| SIP | pipeline output | 5/5 (22) | 0.227 | 1.000 | 0.370 | 17/20 (65) | 0.262 | 0.850 | 0.400 | 44.66 |
| SMTP | pipeline output | 5/7 (13) | 0.385 | 0.714 | 0.500 | 1/22 (38) | 0.026 | 0.045 | 0.033 | 21.06 |
| TCP | pipeline output | 10/11 (11) | 0.909 | 0.909 | 0.909 | 15/20 (75) | 0.200 | 0.750 | 0.316 | 22.52 |

### Aggregates

Macro is the mean of the per-protocol values; micro pools the counts across protocols before computing P/R/F1, so it weights larger state machines more heavily.

| Variant | Scope | S-P | S-R | S-F1 | T-P | T-R | T-F1 |
|---|---|---|---|---|---|---|---|
| pipeline output | macro (n=14) | 0.619 | 0.923 | 0.714 | 0.212 | 0.481 | 0.275 |
| pipeline output | micro (n=14) | 0.490 | 0.917 | 0.639 | 0.183 | 0.495 | 0.267 |

## Comparison with PSMBench baselines

The nine baseline rows are the FSMs **shipped with PSMBench**, scored here by the same unmodified evaluator on the same ground truth. They are not models we ran: no prompt, model version or decoding setting of theirs is ours to report, and any difference in those is a difference between the published artifacts and our pipeline, not a controlled comparison. Macro values, all 14 protocols.

| System | n | states F1 | trans P | trans R | trans F1 |
|---|---|---|---|---|---|
| **A2ABreak** | 14 | 0.714 | 0.212 | 0.481 | 0.275 |
| deepseek-chat | 14 | 0.757 | 0.356 | 0.415 | 0.367 |
| deepseek-reasoner | 14 | 0.699 | 0.342 | 0.409 | 0.354 |
| claude-3-7-sonnet-20250219 | 14 | 0.559 | 0.211 | 0.356 | 0.249 |
| qwen3:32b | 14 | 0.649 | 0.292 | 0.120 | 0.168 |
| gemini-2.0-flash | 14 | 0.513 | 0.116 | 0.376 | 0.167 |
| gpt-4o-mini | 14 | 0.436 | 0.139 | 0.226 | 0.155 |
| qwq | 14 | 0.532 | 0.212 | 0.071 | 0.104 |
| gemma3:27b | 14 | 0.481 | 0.102 | 0.116 | 0.103 |
| mistral-small3.1 | 14 | 0.378 | 0.098 | 0.104 | 0.089 |

## Semantic label normalization, re-scored with the benchmark evaluator (both numbers kept)

The benchmark evaluator compares label strings. To measure the score without the wording barrier, a copy of each of our exports is made in which every state and transition the semantic judge matched to the ground truth by meaning (confidence >= 0.6) is renamed to the ground truth's wording. Nothing is added or removed: every unmatched state and edge stays exactly as extracted, so extra edges still count against precision. The copies are then scored with the unmodified evaluator. The original exports and their official scores are unchanged and reported above; the baselines are not modified. Normalized copies, scores and the list of every changed label are in `results/supplementary/normalized/` (`label_changes.md`).

| Protocol | states m/GT (ext) | S-F1 raw | S-F1 norm. | transitions m/GT (ext) | T-F1 raw | m norm. | T-P norm. | T-R norm. | T-F1 norm. | states renamed | edges relabelled |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TCP | 10/11 (11) | 0.909 | 1.000 | 15/20 (75) | 0.316 | 20/20 | 0.267 | 1.000 | 0.421 | 11 | 20 |
| DCCP | 9/9 (9) | 1.000 | 1.000 | 16/25 (40) | 0.492 | 17/25 | 0.425 | 0.680 | 0.523 | 9 | 15 |
| BGP | 6/6 (6) | 1.000 | 1.000 | 24/26 (109) | 0.356 | 24/26 | 0.220 | 0.923 | 0.356 | 6 | 20 |
| PPP | 10/10 (15) | 0.800 | 0.800 | 20/27 (138) | 0.242 | 25/27 | 0.181 | 0.926 | 0.303 | 10 | 26 |
| DHCP | 8/8 (10) | 0.889 | 0.889 | 14/19 (30) | 0.571 | 15/19 | 0.500 | 0.789 | 0.612 | 8 | 15 |
| PPTP | 9/9 (11) | 0.900 | 0.900 | 9/19 (37) | 0.321 | 12/19 | 0.324 | 0.632 | 0.429 | 8 | 11 |
| IMAP | 4/5 (9) | 0.571 | 0.715 | 5/11 (53) | 0.156 | 11/11 | 0.208 | 1.000 | 0.344 | 5 | 11 |
| POP3 | 3/3 (4) | 0.857 | 0.857 | 8/18 (20) | 0.421 | 12/18 | 0.600 | 0.667 | 0.632 | 3 | 13 |
| SIP | 5/5 (22) | 0.370 | 0.370 | 17/20 (65) | 0.400 | 20/20 | 0.308 | 1.000 | 0.471 | 5 | 20 |
| RTSP | 3/3 (4) | 0.857 | 0.857 | 12/33 (30) | 0.381 | 24/33 | 0.800 | 0.727 | 0.762 | 3 | 24 |
| MQTT | 12/12 (36) | 0.489 | 0.500 | 4/17 (57) | 0.108 | 7/17 | 0.123 | 0.412 | 0.189 | 10 | 8 |
| SMTP | 5/7 (13) | 0.500 | 0.600 | 1/22 (38) | 0.033 | 9/22 | 0.237 | 0.409 | 0.300 | 6 | 9 |
| NNTP | 7/10 (27) | 0.378 | 0.432 | 0/16 (60) | 0.000 | 3/16 | 0.050 | 0.188 | 0.079 | 6 | 6 |
| FTP | 8/10 (24) | 0.470 | 0.529 | 2/24 (52) | 0.053 | 11/24 | 0.212 | 0.458 | 0.289 | 8 | 13 |

Matched over 14 protocols: states 99/108 raw → 104/108 normalized; transitions 147/297 raw → 210/297 normalized. Macro over 14 protocols: states F1 0.714 raw → 0.746 normalized; transitions P 0.212 → 0.318, R 0.481 → 0.701, F1 0.275 → 0.408.

## Semantic judge (supplementary metric, not the benchmark evaluator)

A different question from the tables above: an Opus 4.6 judge at temperature 0 decides, one call per protocol, whether each extracted state or transition MEANS the same thing as a ground-truth one, matched one-to-one at confidence ≥ 0.6. It is reported because the official metric scores string similarity, and an extracted edge that is correct but worded unlike the diagram is counted as a false positive. These numbers are not comparable to the official ones and do not replace them; the judge prompt is `judge_prompt.txt`. Fixed in METHOD.md §5 before any of these numbers were seen. Official PSMBench results are never altered by anything in this section; each analysis is reported separately so a reader can accept or discard it on its own terms.

| System | states F1 | trans P | trans R | trans F1 |
|---|---|---|---|---|
| ours | 0.721 | 0.322 | 0.710 | 0.413 |
