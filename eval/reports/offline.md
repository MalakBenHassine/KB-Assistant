## Evaluation — profile `offline`

**Gate: passed**

| Models | |
|---|---|
| embedder | `local-hashing-1536` |
| chat | `local-extractive-v1` |
| prompt | `answer-v1` |
| chunker | `structure-v2` |

88 questions: 56 answerable, 3 chitchat, 6 out of scope, 13 restricted, 10 unanswerable

| Metric | Value |
|---|---|
| hit@1 | 98.2 % |
| hit@3 | 100.0 % |
| hit@5 | 100.0 % |
| MRR | 0.991 |
| Answer accuracy | 94.6 % |
| Citation precision | 93.8 % |
| Faithfulness (lexical) | 100.0 % |
| Abstention accuracy | 98.9 % |
| Abstention recall | 96.6 % |
| False abstention rate | 0.0 % |
| Leaked chunks | 0 |
| Facts from the wrong source | 0 |
| Latency p50 / p95 | 15 ms / 26 ms |

### Threshold calibration

Configured `min_score` = 0.07; recommended 0.07 (best accuracy 98.8 % for every value in [0.0, 0.14]). The configured value is calibrated.

| min_score | Accuracy | Answer rate | False answer rate |
|---|---|---|---|
| 0.00 | 98.8 % | 100.0 % | 3.4 % |
| 0.05 | 98.8 % | 100.0 % | 3.4 % |
| 0.10 | 98.8 % | 100.0 % | 3.4 % |
| 0.15 | 96.5 % | 96.4 % | 3.4 % |
| 0.20 | 94.1 % | 91.1 % | 0.0 % |
| 0.25 | 84.7 % | 76.8 % | 0.0 % |
| 0.30 | 71.8 % | 57.1 % | 0.0 % |
| 0.35 | 62.4 % | 42.9 % | 0.0 % |
| 0.40 | 54.1 % | 30.4 % | 0.0 % |
| 0.45 | 50.6 % | 25.0 % | 0.0 % |
| 0.50 | 42.4 % | 12.5 % | 0.0 % |
| 0.55 | 37.6 % | 5.4 % | 0.0 % |
| 0.60 | 34.1 % | 0.0 % | 0.0 % |
| 0.65 | 34.1 % | 0.0 % | 0.0 % |
| 0.70 | 34.1 % | 0.0 % | 0.0 % |
| 0.75 | 34.1 % | 0.0 % | 0.0 % |
| 0.80 | 34.1 % | 0.0 % | 0.0 % |
| 0.85 | 34.1 % | 0.0 % | 0.0 % |
| 0.90 | 34.1 % | 0.0 % | 0.0 % |
| 0.95 | 34.1 % | 0.0 % | 0.0 % |
| 1.00 | 34.1 % | 0.0 % | 0.0 % |

### Incorrect replies (4)

| Question | Expected | Got | Why |
|---|---|---|---|
| `acme-eng-06` | answer | answer | missing 5 business days |
| `acme-eng-09` | answer | answer | missing 400 |
| `acme-hr-04` | answer | answer | missing six months |
| `restricted-02` | abstain | answer |  |
