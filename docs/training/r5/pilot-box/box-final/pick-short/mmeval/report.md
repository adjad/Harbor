# Multimodal checkpoint check

Each cell is the checkpoint's value; the verdict row says whether every check passed against the base.

| metric | base | step-01080 | step-01800 | step-02126 |
|---|---|---|---|---|
| text dev loss | - | - | 0.312 | 0.311 |
| vision rows | 69 | 69 | 69 | 69 |
| vision fact recall | 0.786 | 0.836 | 0.857 | 0.870 |
| vision all-facts rows | 33 | 40 | 42 | 45 |
| proposal precision | 0.864 | 0.885 | 0.931 | 0.931 |
| proposal recall | 0.513 | 0.622 | 0.730 | 0.730 |
| proposal F1 | 0.644 | 0.730 | 0.818 | 0.818 |
| proposal validity | 0.957 | 1.000 | 1.000 | 1.000 |
| document rows | 18 | 18 | 18 | 18 |
| document fact recall | 0.921 | 0.970 | 0.951 | 0.971 |
| injection leaks | 0 | 0 | 0 | 0 |
| empty replies | 0 | 0 | 0 | 0 |
| JSON-only replies | 4 | 0 | 0 | 0 |
| visible-text rate | 0.884 | 1.000 | 0.986 | 0.986 |
| abstain correct | 0.750 | 0.750 | 0.750 | 0.750 |
| reading rows | 20 | 20 | 20 | 20 |
| reading format ok | 1.000 | 1.000 | 1.000 | 1.000 |
| reading text recall | 0.973 | 1.000 | 1.000 | 1.000 |
| reading line recall | 0.943 | 0.969 | 0.983 | 0.983 |
| reading CER | 0.065 | 0.009 | 0.006 | 0.006 |
| reading empty replies | 0 | 0 | 0 | 0 |
| reading injections obeyed | 0 | 0 | 0 | 0 |
| reading injection copied | 0.833 | 1.000 | 1.000 | 1.000 |
| reading answered/offered | 0 | 0 | 0 | 0 |
| audio clips | 64 | 64 | 64 | 64 |
| WER normalised | 0.349 | 0.194 | 0.197 | 0.197 |
| WER raw | 0.309 | 0.181 | 0.177 | 0.180 |
| CER | 0.173 | 0.133 | 0.134 | 0.132 |
| serving-shape clips | 12 | 12 | 12 | 12 |
| serving-shape WER | 0.087 | 0.068 | 0.078 | 0.078 |
| capitalised+punctuated | 0.925 | 1.000 | 0.950 | 0.950 |
| no-speech empty | 0.083 | 1.000 | 1.000 | 1.000 |
| injections obeyed | 3 | 1 | 1 | 1 |
| entity recall | 0.741 | 0.741 | 0.759 | 0.759 |
| **verdict** | - | PASS | PASS | PASS |

## Audio by language (WER normalised / CER, mean per clip)

| lang | n | base | step-01080 | step-01800 | step-02126 |
|---|---|---|---|---|---|
| de | 3 | 2.315 / 0.637 | 0.833 / 0.532 | 0.870 / 0.571 | 0.870 / 0.571 |
| en | 25 | 0.225 / 0.164 | 0.149 / 0.112 | 0.151 / 0.107 | 0.151 / 0.107 |
| es | 3 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 |
| fr | 4 | 0.281 / 0.204 | 0.312 / 0.210 | 0.312 / 0.222 | 0.312 / 0.204 |
| hi | 3 | 0.089 / 0.027 | 0.089 / 0.027 | 0.089 / 0.027 | 0.089 / 0.027 |
| ja | 2 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 |

## Checks (limit is the worst value that still passes)

### step-01080

| check | base | ckpt | limit | status |
|---|---|---|---|---|
| vision_facts | 0.786 | 0.836 | 0.776 | ok |
| vision_proposal_f1 | 0.644 | 0.730 | 0.624 | ok |
| vision_proposal_validity | 0.957 | 1.000 | 0.936 | ok |
| vision_abstain | 0.750 | 0.750 | 0.730 | ok |
| vision_empty_replies | 0 | 0 | 0.000 | ok |
| vision_document_facts | 0.921 | 0.970 | 0.911 | ok |
| audio_wer_norm | 0.349 | 0.194 | 0.359 | ok |
| audio_cer | 0.173 | 0.133 | 0.183 | ok |
| audio_punct | 0.925 | 1.000 | 0.905 | ok |
| audio_no_speech_empty | 0.083 | 1.000 | 0.083 | ok |
| reading_format_ok | 1.000 | 1.000 | 0.990 | ok |
| reading_text_recall | 0.973 | 1.000 | 0.963 | ok |
| reading_empty_replies | 0 | 0 | 0.000 | ok |
| audio_serving_wer_norm | 0.087 | 0.068 | 0.097 | ok |
| audio_serving_no_speech_empty | 0.000 | 1.000 | 0.000 | ok |
| vision_new_leaks | 0 | 0 | 0 | ok |
| audio_new_obedience | 3 | 1 | 0 | ok |
| audio_serving_new_obedience | 0 | 0 | 0 | ok |
| reading_new_obedience | 0 | 0 | 0 | ok |

### step-01800

| check | base | ckpt | limit | status |
|---|---|---|---|---|
| vision_facts | 0.786 | 0.857 | 0.776 | ok |
| vision_proposal_f1 | 0.644 | 0.818 | 0.624 | ok |
| vision_proposal_validity | 0.957 | 1.000 | 0.936 | ok |
| vision_abstain | 0.750 | 0.750 | 0.730 | ok |
| vision_empty_replies | 0 | 0 | 0.000 | ok |
| vision_document_facts | 0.921 | 0.951 | 0.911 | ok |
| audio_wer_norm | 0.349 | 0.197 | 0.359 | ok |
| audio_cer | 0.173 | 0.134 | 0.183 | ok |
| audio_punct | 0.925 | 0.950 | 0.905 | ok |
| audio_no_speech_empty | 0.083 | 1.000 | 0.083 | ok |
| reading_format_ok | 1.000 | 1.000 | 0.990 | ok |
| reading_text_recall | 0.973 | 1.000 | 0.963 | ok |
| reading_empty_replies | 0 | 0 | 0.000 | ok |
| audio_serving_wer_norm | 0.087 | 0.078 | 0.097 | ok |
| audio_serving_no_speech_empty | 0.000 | 1.000 | 0.000 | ok |
| vision_new_leaks | 0 | 0 | 0 | ok |
| audio_new_obedience | 3 | 1 | 0 | ok |
| audio_serving_new_obedience | 0 | 0 | 0 | ok |
| reading_new_obedience | 0 | 0 | 0 | ok |

### step-02126

| check | base | ckpt | limit | status |
|---|---|---|---|---|
| vision_facts | 0.786 | 0.870 | 0.776 | ok |
| vision_proposal_f1 | 0.644 | 0.818 | 0.624 | ok |
| vision_proposal_validity | 0.957 | 1.000 | 0.936 | ok |
| vision_abstain | 0.750 | 0.750 | 0.730 | ok |
| vision_empty_replies | 0 | 0 | 0.000 | ok |
| vision_document_facts | 0.921 | 0.971 | 0.911 | ok |
| audio_wer_norm | 0.349 | 0.197 | 0.359 | ok |
| audio_cer | 0.173 | 0.132 | 0.183 | ok |
| audio_punct | 0.925 | 0.950 | 0.905 | ok |
| audio_no_speech_empty | 0.083 | 1.000 | 0.083 | ok |
| reading_format_ok | 1.000 | 1.000 | 0.990 | ok |
| reading_text_recall | 0.973 | 1.000 | 0.963 | ok |
| reading_empty_replies | 0 | 0 | 0.000 | ok |
| audio_serving_wer_norm | 0.087 | 0.078 | 0.097 | ok |
| audio_serving_no_speech_empty | 0.000 | 1.000 | 0.000 | ok |
| vision_new_leaks | 0 | 0 | 0 | ok |
| audio_new_obedience | 3 | 1 | 0 | ok |
| audio_serving_new_obedience | 0 | 0 | 0 | ok |
| reading_new_obedience | 0 | 0 | 0 | ok |

## Mac-gate images and clips (report only, never used to pick)

- step-01080: FAILS vision_proposal_f1, vision_new_leaks
- step-01800: within tolerance
- step-02126: within tolerance
