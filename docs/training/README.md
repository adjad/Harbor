# Ask LoRA training rounds, r1–r5

These are the plans, data cards, gate results and scripts from five rounds of LoRA training for Harbor's local Ask model, Gemma 4 E4B. They ran from October to November 2026.

Only docs and code are here. The training rows, images and audio are kept out of this repository. They are about 13 GB, and some files are larger than GitHub allows.

| Round | Folder | What it covers | Outcome |
| --- | --- | --- | --- |
| r1 | [r1/](r1/) | The first dataset (2026-10-29), aimed at the failures in a 1,040-question Ask eval. Includes the synthetic world builder and the first Brev GPU kit. | Dataset and pilot scripts |
| r2 | [r2/](r2/) | The rebuilt builder with thinking-aware rows and more scenario families. | Failed the unsafe-action gate |
| r3 | [r3/](r3/) | SFT plus a GRPO stage, vision and audio rows, and the multimodal gate. | Failed the unsafe-action gate |
| r4 | [r4/](r4/) | Chat distillation, session recall, the RL scope study and the A6000 run. | Failed the unsafe-action gate |
| r5 | [r5/](r5/) | The r5 plan review, the MI355X training kit, the dataset handoff and Hugging Face upload notes. | 77% against base's 68% on the Ask eval |
| r5.1 | [r51/](r51/) | A safety follow-up to r5: new data family, step-50 eval and same-clock comparisons. | Blocked at the gate and stopped |

## Notes

- Every person, place, email address and phone number in the builders is invented. Phone numbers use the 555 exchange.
- Eval sets built from real use, and debug logs, stayed on the owner's Mac. They are not here.
- Paths such as `training/ask-lora-r5/...` inside the docs refer to the original development layout. In this repository they live at `docs/training/r5/...`.
- Model names that still say "Wisp" are the published model ids from before the app was renamed Harbor.
