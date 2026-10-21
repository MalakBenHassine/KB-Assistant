"""Offline evaluation of the question-answering workflow (docs/EVALUATION.md).

`dataset` loads the questions, `runner` sends them through the real graph,
`metrics` and `calibration` turn the results into numbers, `gate` compares the
numbers with the limits in `eval/gate.toml`, and `report` writes them down.
"""
