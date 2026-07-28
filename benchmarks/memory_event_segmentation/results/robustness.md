# Robustness to noise

F1 (higher better) / Pk (lower better) as backchannel+drift rise and fewer boundaries get a time gap.

## time_gap

| noise | F1 | Pk | WinDiff | segs |
|---|---|---|---|---|
| clean | 0.870 | 0.104 | 0.104 | 8.4 |
| low | 0.691 | 0.201 | 0.201 | 6.2 |
| medium | 0.665 | 0.223 | 0.223 | 6.0 |
| high | 0.599 | 0.264 | 0.264 | 5.2 |

## fixed_window

| noise | F1 | Pk | WinDiff | segs |
|---|---|---|---|---|
| clean | 0.176 | 0.449 | 0.449 | 11.6 |
| low | 0.271 | 0.423 | 0.423 | 11.6 |
| medium | 0.265 | 0.471 | 0.471 | 10.8 |
| high | 0.120 | 0.522 | 0.522 | 11.8 |

## embedding_drift

| noise | F1 | Pk | WinDiff | segs |
|---|---|---|---|---|
| clean | 1.000 | 0.000 | 0.000 | 10.6 |
| low | 0.970 | 0.023 | 0.023 | 10.8 |
| medium | 0.924 | 0.055 | 0.059 | 11.6 |
| high | 0.894 | 0.102 | 0.128 | 13.0 |

## surprise_em_llm

| noise | F1 | Pk | WinDiff | segs |
|---|---|---|---|---|
| clean | 1.000 | 0.000 | 0.000 | 10.6 |
| low | 0.846 | 0.096 | 0.147 | 13.8 |
| medium | 0.723 | 0.216 | 0.303 | 17.4 |
| high | 0.677 | 0.274 | 0.404 | 20.0 |

