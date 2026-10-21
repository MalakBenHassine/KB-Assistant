## Evaluation — profile `offline`

**Gate: passed**

| Models | |
|---|---|
| embedder | `local-hashing-1536` |
| chat | `local-extractive-v1` |
| prompt | `answer-v1` |
| chunker | `structure-v2` |

86 questions: 54 answerable, 3 chitchat, 6 out of scope, 13 restricted, 10 unanswerable

| Metric | Value |
|---|---|
| hit@1 | 98.1 % |
| hit@3 | 100.0 % |
| hit@5 | 100.0 % |
| MRR | 0.991 |
| Answer accuracy | 94.4 % |
| Citation precision | 93.5 % |
| Faithfulness (lexical) | 100.0 % |
| Abstention accuracy | 98.8 % |
| Abstention recall | 96.6 % |
| False abstention rate | 0.0 % |
| Leaked chunks | 0 |
| Facts from the wrong source | 0 |
| Latency p50 / p95 | 32 ms / 42 ms |

### Threshold calibration

Configured `min_score` = 0.07; recommended 0.07 (best accuracy 98.8 % for every value in [0.0, 0.14]). The configured value is calibrated.

| min_score | Accuracy | Answer rate | False answer rate |
|---|---|---|---|
| 0.00 | 98.8 % | 100.0 % | 3.4 % |
| 0.05 | 98.8 % | 100.0 % | 3.4 % |
| 0.10 | 98.8 % | 100.0 % | 3.4 % |
| 0.15 | 96.4 % | 96.3 % | 3.4 % |
| 0.20 | 94.0 % | 90.7 % | 0.0 % |
| 0.25 | 84.3 % | 75.9 % | 0.0 % |
| 0.30 | 71.1 % | 55.6 % | 0.0 % |
| 0.35 | 62.7 % | 42.6 % | 0.0 % |
| 0.40 | 55.4 % | 31.5 % | 0.0 % |
| 0.45 | 51.8 % | 25.9 % | 0.0 % |
| 0.50 | 43.4 % | 13.0 % | 0.0 % |
| 0.55 | 38.6 % | 5.6 % | 0.0 % |
| 0.60 | 34.9 % | 0.0 % | 0.0 % |
| 0.65 | 34.9 % | 0.0 % | 0.0 % |
| 0.70 | 34.9 % | 0.0 % | 0.0 % |
| 0.75 | 34.9 % | 0.0 % | 0.0 % |
| 0.80 | 34.9 % | 0.0 % | 0.0 % |
| 0.85 | 34.9 % | 0.0 % | 0.0 % |
| 0.90 | 34.9 % | 0.0 % | 0.0 % |
| 0.95 | 34.9 % | 0.0 % | 0.0 % |
| 1.00 | 34.9 % | 0.0 % | 0.0 % |

### Incorrect replies (4)

| Question | Expected | Got | Why |
|---|---|---|---|
| `acme-eng-06` | answer | answer | missing 5 business days |
| `acme-eng-09` | answer | answer | missing 400 |
| `acme-hr-04` | answer | answer | missing six months |
| `restricted-02` | abstain | answer |  |
