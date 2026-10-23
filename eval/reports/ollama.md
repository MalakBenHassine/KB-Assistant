## Evaluation — profile `ollama`

**Gate: passed**

| Models | |
|---|---|
| embedder | `nomic-embed-text` |
| chat | `qwen3:4b` |
| prompt | `answer-v1` |
| chunker | `structure-v2` |

88 questions: 56 answerable, 3 chitchat, 6 out of scope, 13 restricted, 10 unanswerable

| Metric | Value |
|---|---|
| hit@1 | 100.0 % |
| hit@3 | 100.0 % |
| hit@5 | 100.0 % |
| MRR | 1.000 |
| Answer accuracy | 92.9 % |
| Citation precision | 100.0 % |
| Faithfulness (lexical) | 91.1 % |
| Abstention accuracy | 98.9 % |
| Abstention recall | 100.0 % |
| False abstention rate | 1.8 % |
| Leaked chunks | 0 |
| Facts from the wrong source | 0 |
| Latency p50 / p95 | 27527 ms / 35551 ms |

### Threshold calibration

Configured `min_score` = 0.07; recommended 0.35 (best accuracy 94.1 % for every value in [0.0, 0.71]). The configured value is calibrated.

| min_score | Accuracy | Answer rate | False answer rate |
|---|---|---|---|
| 0.00 | 94.1 % | 91.1 % | 0.0 % |
| 0.05 | 94.1 % | 91.1 % | 0.0 % |
| 0.10 | 94.1 % | 91.1 % | 0.0 % |
| 0.15 | 94.1 % | 91.1 % | 0.0 % |
| 0.20 | 94.1 % | 91.1 % | 0.0 % |
| 0.25 | 94.1 % | 91.1 % | 0.0 % |
| 0.30 | 94.1 % | 91.1 % | 0.0 % |
| 0.35 | 94.1 % | 91.1 % | 0.0 % |
| 0.40 | 94.1 % | 91.1 % | 0.0 % |
| 0.45 | 94.1 % | 91.1 % | 0.0 % |
| 0.50 | 94.1 % | 91.1 % | 0.0 % |
| 0.55 | 94.1 % | 91.1 % | 0.0 % |
| 0.60 | 94.1 % | 91.1 % | 0.0 % |
| 0.65 | 94.1 % | 91.1 % | 0.0 % |
| 0.70 | 94.1 % | 91.1 % | 0.0 % |
| 0.75 | 91.8 % | 87.5 % | 0.0 % |
| 0.80 | 72.9 % | 58.9 % | 0.0 % |
| 0.85 | 54.1 % | 30.4 % | 0.0 % |
| 0.90 | 37.6 % | 5.4 % | 0.0 % |
| 0.95 | 34.1 % | 0.0 % | 0.0 % |
| 1.00 | 34.1 % | 0.0 % | 0.0 % |

### Incorrect replies (4)

| Question | Expected | Got | Why |
|---|---|---|---|
| `acme-hr-02` | answer | answer | missing 5 unused days |
| `acme-prod-03` | answer | answer | missing 14-day |
| `acme-admin-01` | answer | abstain | unsupported_answer |
| `globex-prod-02` | answer | answer | missing 30-day |
