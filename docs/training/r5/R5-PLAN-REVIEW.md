# Ask LoRA r5: plan and evidence for review

**Status on 2026-11-06, commit `2eb0f3d1` on `dev/ask-lora-brev-2caec5`:** r5 hasn't trained. Its data, node kit and gate scorer are ready, and the GPU node hasn't been started. Three earlier tuned models (r2, r3 and r4) failed their gates, so untuned Gemma is still the shipped model.

**What r5 tries to do differently:** keep untuned Gemma's voice by having it write nearly every final answer, and add tool-calling rows, mainly for reading several sources. Training prompts also now match what the engine serves. They didn't in any earlier round.

## Where a review would help most

1. **Will this data keep the voice?** Earlier rounds gained tool skill and lost the voice. See [What went wrong before](#what-went-wrong-before) and [The r5 data](#the-r5-data).
2. **Cost.** Current estimates are about $120–250 before $60 of credits, or about $325 if batching gives no gain. Node speeds are unmeasured. See [Time and cost](#time-and-cost).
3. **Gate bars.** Are they strict enough without blocking on noise? A test with identical prompts on both sides still reached p=.002 on a sub-metric. See [How r5 is judged](#how-r5-is-judged).
4. **Thought share.** An earlier target of at least 75% can't be met by this build. See [Open questions](#open-questions).

## Background

Harbor is a local macOS assistant. Its Ask feature runs Gemma 4 E4B (4-bit, with an MTP speculative-decoding head) on an M5 Pro with 24 GB, served by oMLX. A "round" is one LoRA fine-tune of that model.

| Term | Meaning |
|---|---|
| Base | Untuned Gemma, the comparison for every gate |
| Toolbench | Offline test of Ask's real agent path against a synthetic world, with 378 to 466 cases depending on the date |
| Gate | The full test of one candidate against base on the same pinned code, on the Mac with Harbor closed |
| Picker | Scores saved checkpoints by behaviour on the GPU node, since r3's lowest-loss checkpoint wasn't its best |
| Unsafe call | A call a test case forbids, or a write that ran with no confirmation card |
| Final | The answer step of a turn, as opposed to a tool-call step |
| Thought | Gemma's reasoning block before an answer or call |

## What the owner wants from r5

The owner's words, 2026-11-06: "same base gemma talking style/formatting, ttft(excluding thinking) can be the same idc about it, better tool calling(already decent enough rn) but more would be nice - using all of my sources would be better."

And 2026-11-05: "everything in that debug log + variations of the questions I asked to work and look good in terms of style."

| Priority | Requirement |
|---|---|
| 1 | Base's voice and formatting: lists and bold where they fit, and no tables because the notch is too narrow |
| 2 | Better tool calling, above all reading every relevant source |
| 3 | No speed gain needed; it only blocks if more than 10% worse |

The owner also relaxed the safety bar: "i personally have never ran into a safety issue since switting to gemma." Unsafe calls with real effects must still stay at or below base's. Unsafe read-only calls are reported but don't fail the gate.

## Results from earlier rounds

Each gate compares a candidate with base on its own pinned code and case set, so compare within a row, not across rows. r1 was a reads-only dataset that never trained.

| | r2 | r3 | r4 (step 1840) |
|---|---|---|---|
| Date of gate | 2026-11-01 | 2026-11-02 | 2026-11-06 |
| Training rows | 1,743 | 3,254 | 6,098 |
| Hardware | 1x A6000 | 8x MI355X | 1x A6000 |
| Training run | 436 steps, 628 min, about $11 in all | 992 steps, 47 min | about 51 h and $30 (estimate) |
| Toolbench trials | 1 trial, 378 cases | 3 trials, 1,127 paired | 3 trials, 1,397 each |
| **Toolbench pass, base → tuned** | 64.6 → 69.9% (p=.059) | 68.5 → 73.4% (p=.001) | 68.4 → 72.9% (p=.0006) |
| Writes, base → tuned | 49.6 → 64.7% | 51.7 → 68.2% | 53.2 → 60.4% |
| **Unsafe calls, base → tuned** | 5 → 16 | 15 → 20 | 12 → 27 |
| Turn time p50, base → tuned | 7.8 → 3.3 s | 7.2 → 3.1 s | 8.0 → 4.2 s |
| Thought length p50, base → tuned | 1,479 → 69 chars | 1,320 → 2 chars | see note below |
| Chat judge, base vs tuned | 12 vs 28 | 43 vs 87 | 78 vs 69 |
| Median chat reply, base → tuned | not comparable | 393 → 124 chars | 427 → 180 chars |
| Emoji in replies, base → tuned | 54 → 0% | 49 → 0% | 53 → 24% |
| Vision proposals right, base → tuned | 3/6 → 0/6 | 6/11 → 3/11 | 8/11 → 5/11 |
| **Verdict** | Failed | Failed | Failed |

Thought length for r4 comes from earlier checkpoints only: on the full toolbench, step 1150 had 194 chars against base's 1,580. Its gate reports post-read thought share instead: 0.978 against base's 0.999.

**The blind judge and the owner disagreed on r3.** The judge preferred r3 87–43, but after using it the owner called it "monotonous and boring". A judge win alone now never passes.

### r4's gate in detail

r4 improved toolbench pass, tool choice (78 → 85%), and "not there" claims with no read (49 → 16). The private real-use sets rose from 231/341 to 261/341. It still failed these bars, some of which r5 has since dropped or relaxed:

| Bar | Base | r4 | Limit at the time |
|---|---|---|---|
| Unsafe calls | 12 | 27 | at or below base |
| `safety` category | 58/69 | 47/69 (p=.02) | at or above base |
| `compose` category | 40/75 | 29/75 (p=.03) | no significant loss |
| `day`, `notes` | 54/62, 33/39 | 49/62, 31/39 | none down more than 3 points |
| `session` | 113/168 | 113/168 | base +10 points |
| Writes | 53.2% | 60.4% | base +10 points |
| Blind chat judge | 78 | 69, 9 ties | r4 wins |
| Judge, multi-turn only | 30 | 14, 1 tie | report |
| Non-replies, all / multi-turn | 2 / 1 | 4 / 4 | at or below base, 0 multi-turn |
| Median reply | 427 chars | 180 chars (0.42x) | at least 0.8x |
| Replies with an emoji | 53% | 24% | at least 0.5x |
| Vision document facts | 1.000 | 0.944 | at or above base |
| Proposals right | 8/11 | 5/11 | at or above base |
| Picture-turn right call | 0.85 | 0.75 | at or above base |

Explicit chat checks also fell: all checks passed in 27/45 conversations against 36/45, recall 7/12 against 11/12, emotional 1/6 against 5/6.

Unsafe calls split by real effect: base has 3 writes and 9 reads, and r4 has 23 and 4. So r4 would still fail the relaxed bar.

### Mid-run checks on r4

These were single-trial spot checks, not gates.

| Full toolbench, 466 cases | Base | Step 1150 |
|---|---|---|
| Pass | 312 (67%) | 335 (72%) |
| Right tool | 78% | 86% |
| Right tool and arguments | 77% | 83% |
| No call when one was needed | 59 | 19 |
| Wrong tool | 34 | 37 |
| Bad arguments | 8 | 14 |
| Unsafe calls | 4 | 5 |
| Answers with bullets | 30% | 8% |
| Answers with bold | 66% | 14% |

### What went wrong before

- **Tuned models answer short and lose warmth.** Every round cut reply length and emoji. Thoughts also shrank sharply in r2, r3 and r4's earlier checkpoints. r3's finals had a p50 of 75 chars, and the model generalized that.
- **Template-written data reads flat.** In r4's rows after a tool result:

  | Thought on the row | Rows | Mean answer | Emoji | Bold | "!" |
  |---|---|---|---|---|---|
  | Base style | 1,074 | 369 chars | 70% | 89% | 79% |
  | Template | 657 | 205 chars | 0% | 0% | 0% |
  | None | 549 | 68 chars | 0% | 0% | 1% |

  Thought and answer come from the same row, so this shows a split by source, not that the thought causes warmth.
- **Acting more eagerly raises writes and unsafe calls together.** Unsafe calls rose in all three rounds.
- **Vision falls when training on mostly text.** Each round lost proposals. r3 also quoted planted text with its payload.
- **Chat turned into memory lookups in r4.** "Rough day" got "nothing saved" replies. `session_recall_r4` rows likely taught this. The new scorer counts 22 of 333 against base's 0 using the builder's pattern; the plan's 29 against 1 came from a broader hand-written pattern.
- **"And N more" endings.** These caused 9 of r4's 13 debug-log losses, and its data had 51 such finals.

### Other data worth knowing

- **Temperature was noise.** Untuned at 1.0 scored 58.0% against 59.5% at 0.8 (p=.21).
- **Thinking must be listed for background calls.** A tuned model missing from `no_thinking_capable` failed 4 of 4 long-email digests, and all 4 finished once listed. The toolbench can't see this, so `background_probe.py` is a separate check.
- **Other base models.** Nemotron Bolt lost its comparison and Gemma stayed: toolbench 69 vs 65% (p=.15), with unsafe calls 7 vs 4.

## A training bug found this round

**Through r4, training prompts differed from served prompts.** oMLX changes each request before applying the chat template, and the training renderer didn't:

- Adjacent system messages are joined into one turn.
- Tool schemas get sorted keys and added descriptions, and a `description` parameter becomes `param_description`.
- Tool results fold onto the calling turn, and JSON results become objects.

All 300 prompts sampled at first differed, by a median of 39 tokens from the schema changes alone. Whether this hurt earlier rounds is unmeasured.

**Now:** all 8,873 r5 prompts are byte-identical to oMLX's own code, and prompt sizes from the running engine matched on 14 of 14 rows. Those live rows check token counts, not bytes.

## The thoughts test was inconclusive

Google's guide says to keep thoughts between tool calls within a turn, and Harbor drops them. A test on base compared the two, but the engine discards `reasoning_content` on purpose in `extract_gemma4_messages`, so the "kept" side sent nothing extra. An 80-repeat thought changed the prompt by 0 tokens.

| 97 cases, 3 trials, 290 paired | Control | "Kept" |
|---|---|---|
| Pass | 134 (46.2%) | 147 (50.7%), p=.124 |
| Right tool (only one side right) | 18 | 43, p=.002 |
| Right arguments (only one side right) | 20 | 42, p=.007 |
| Turn p50 | 11.4 s | 11.4 s |

Those gaps can't be credited to thoughts. The sides ran under different conditions: control spanned AC power, a 6-hour pause and an engine restart, and "kept" ran on battery. The first 66 trials were level at 31 and 30. A real test with an engine patch is running as a separate task, and r5 doesn't wait for it. r5's rows have no earlier thoughts, matching what's served today.

## The r5 data

Built on main `7e442f2d`: 8,873 text rows in 43 families, of which 8,106 are for training and 767 for checking. The rows are 4,660 finals and 4,213 tool steps. Training rows average about 6,900 tokens (400-row sample of a dry build).

| Who writes the finals | Count |
|---|---|
| Reused from r4's samples of the served base model, same history | 1,435 |
| Base writes on the GPU node | 3,225 |
| **Total** | **4,660** |

A final keeps base's answer unless it fails a check, such as a cut list or a memory lookup in answer to chat. 424 rows have only a placeholder until base writes them, and they're dropped if no sample passes.

**Largest families:**

| Family | Rows | Purpose |
|---|---|---|
| `tool_choice_r5` | 772 | New: neighbouring tools, arguments, writes, each direct write with a contrast that doesn't act |
| `debug_log_r5` | 750 | New: the owner's own questions and variations |
| `day_chat_r4` | 491 | Day questions |
| `gather_then_send_r4` | 469 | Read first, then send |
| `compose_r3` | 419 | Composing messages |
| `instructions_r4` | 358 | Standing instructions |
| `session_recall_r4` | 331 | Recall within a chat |
| `safe_restraint` | 322 | Not acting |
| `restraint_r4` | 318 | Not acting |
| `all_sources_r5` | 318 | New: 149 turns, of which 120 read 2+ sources |

Other new families: `chat_contrast_r5` (179), `long_email_r5` (80), `picture_turn_r5` (56).

**Sources gap:** before `all_sources_r5`, only 50 of 4,650 turns read two or more sources.

**Multimodal rows:** 625 vision rows (536 train, 89 check) and 360 audio rows (296 train, 64 check). The 20 new `doc5_*` rows answer whole-page questions, aimed at r4's 76-character syllabus answer.

**Privacy rules:**

- The owner's own question wording may enter training, with names and addresses replaced.
- Tool results and answers are synthetic. No real email, message or calendar content reaches training or the node.
- A scan against private logs found no emails or real names. The hits were the synthetic persona, public places, common phrases and timestamps.

**Dry run on the Mac:** choosing, final build, validation and packing all passed, using only the 1,435 reused answers. Row counts will change once base writes the rest. The dry build had 8,430 rows after dropping the 424 unwritten rows and 19 rows from 12 questions too close to toolbench cases.

## Training setup

| Setting | r5 | Google's guide (gemma.ai/tuning) | Why they differ |
|---|---|---|---|
| Base weights | bf16 | 4-bit NF4 | The guide uses 4-bit to save memory |
| Loss | Completion only | Same | |
| LoRA | r=16, alpha=16, dropout 0.05 | Same (text); audio page uses 32/64 | One adapter serves text, vision and audio |
| Learning rate | 2e-4 | Same | |
| Schedule | Cosine, 3% warmup | QLoRA constant, audio page cosine | Follows the audio page |
| Epochs | 2 | 3 or 4 | Google's validation loss rises after epoch 1 or 2 in every table it prints |
| Grad clip, optimizer | 0.3, fused AdamW | Same (vision page) | |
| Checkpoint choice | Behaviour picker | Best eval loss (audio page) | r3's lowest-loss checkpoint wasn't its best |
| Effective batch | 8 | | |
| Mix | Vision 0.12, audio 0.06, anchor KL 0.5 | | KL to base protects multimodal behaviour |
| Attention on the node | `--attn math` | | gfx950's fused SDPA fails the LoRA gradient check |
| torch | 2.14.1+rocm7.2 | | AMD's 2.11.0+rocm7.13.0 gave `use_cache`-dependent losses |

**Left out on purpose:** `modules_to_save=["lm_head","embed_tokens"]`. Google lists it for text and vision with no stated reason. It would train the full 262,144-token embedding table, which the MTP head shares.

**Not in r5:** QAT weights, a higher image token budget, DPO.

## Steps

1. The owner starts the 8x MI355X node.
2. Setup, then a selfcheck that batched and single-sequence generation agree, then an 8-row speed run.
3. Base writes the finals. The node's output comes back to the Mac.
4. On the Mac: `finish_r5.py`, then validation (must exit 0), then pack and upload.
5. On the node: preflight, smoke, then training, with a check at step 400 before paying for the rest.
6. Picker, merge, package and download. The owner stops the node.
7. Quantize on the Mac, then the gate.

Full commands: [`mi355x/README.md`](mi355x/README.md).

## Time and cost

The node costs $24 an hour ($3 per GPU-hour) and bills while idle. $60 of credits apply.

| Stage | Estimate | Basis |
|---|---|---|
| Setup | 0.5–1 h | Judgment |
| Base writes 3,225 finals | 1–3 h with `BATCH=8`; 4.25 h unbatched plus retries | 38 s per generation, measured on an A6000 and spread over 8 GPUs; the batching gain is unmeasured |
| Training | about 2 h | r3's measured speed: 2,470 steps at 2.8 s, or 112M tokens at 15,464 tok/s |
| Picker, 8 at a time | about 1.5–3 h | 600 generations per model is 75 groups at 1–2.5 min each (unmeasured); about 6.3 h one at a time |
| **Total before credits** | **about $120–250** | About $325 if batching gives no gain |
| Mac gate | about 21 h, $0, Harbor closed | Estimate |

**The earlier "under 1 h" picker estimate and "$110–170" total weren't supported by these numbers.** The generation rate on this node is unmeasured, and the A6000's limit was one CPU thread (GPU about 20% busy), so a faster GPU may not help. A batch keeps running until its longest answer ends, up to 384 tokens, so expect less than an 8x gain.

**Options to bring it down further:**

- **Trim the picker,** with fewer dev steps or fewer checkpoints.
- **Write finals on the Mac for $0.** The plan's "38 s on the Mac" was mislabelled; that number was measured on the A6000. The Mac's own rate for this job isn't measured here, and Harbor must be closed while it runs.

## How r5 is judged

Base and r5 run on the same pin, and the 2026-11-05 plan also runs r4 for reference. r4's required gains are gone: r5 must match base's style, regress nowhere, and show a gain on multi-source reads.

| Area | Bar | Hard? |
|---|---|---|
| Voice | Owner's blind rating of about 20 conversation pairs, and a look in the notch | Yes, above any count |
| Judge | Blind judge no worse than base | Yes |
| Length | Median reply 0.8 to 1.25x base's | Yes |
| Emoji, bold | Within 10 points of base's ("close" in the plan; this reading is mine) | Yes |
| Non-replies, memory stall | At or below base's | Yes |
| Tables | None | Yes |
| Toolbench | Pass rate at or above base's | Yes |
| Multi-source | Above base on reading every expected source, and on using them in the answer | Yes |
| `safety` category | At or above base's | Yes |
| Unsafe writes | At or below base's | Yes |
| Unsafe reads | Reported | No |
| Speed | Report only, unless more than 10% worse | Conditional |
| Owner's questions | All 34 messages pass, at least 90% of held-out variations pass | Proposed 2026-11-05, not restated since |
| Background calls | Every call finishes, valid JSON, no failed digest, digest recall within 5 points of base | Yes |
| Vision, audio | Multimodal gate unchanged, audio with the owner's looser limits | Yes |

**Test sets beyond the toolbench:**

- **Held-out multi-source set:** 40 cases, sealed, with no results yet. Nine questions were reworded to stay apart from training.
- **Debug-log set:** rebuilt from every export before each gate. On 2026-11-05 that was 69 logs and 221 cases. Questions in training are reported apart from unseen ones. r4's baseline on unseen wording was 64/76, against base's 48/76.
- **976-prompt corpus:** has no tool labels, so it shows regressions and answers to read.
- **Compose tests:** fixed to accept valid day-plan reads, moving base from 40 to 46 and r4 from 29 to 37 of 75. Report both old and new scores.

## Risks

| Risk | Why | How it's limited |
|---|---|---|
| Tool gains shrink | r5 restarts from base and relearns them from data | Tool-call rows kept; toolbench at step 400 and at the gate |
| Unsafe writes stay high | They rose every round, and r5 adds write rows | Contrast rows; confirmation cards stay; a hard bar |
| Voice still drifts | Base writes the finals, but tool steps and some scripted finals remain | Style bars; the owner's rating |
| Fits the owner's chats too narrowly | 750 rows come from the owner's own questions and their variations | Held-out variations; unseen logs; full toolbench |
| Vision or audio regress | They did every round | KL anchors; `doc5_*` and picture rows; the gate |
| Base's answers misread results | Base-written finals aren't all correct | Checks at the choose step, though they can't catch every wrong fact |
| Noise passes for signal | Identical prompts reached p=.002 on a sub-metric | Don't rely on one sub-metric's p-value |
| A false pass on sources | The set is new and has never run | Report it next to the toolbench; read the transcripts |

## Open questions

1. **Thought share.** A 2026-11-04 target asked for at least 75% of rows with a thought. Today 44% have one, and the most this build can reach is 70%, if every final gets one. The target came from a guide with no evidence behind it, and the 2026-11-06 aim doesn't repeat it. Drop it, or add thoughts to tool steps?
2. **Fewer sources rows than planned.** `all_sources_r5` has 149 turns, about half the roughly 300 first asked for. Is that enough for a measurable gain?
3. **Picker size.** Batching is in, but its speed is unmeasured. See [Time and cost](#time-and-cost).
4. **Reinforcement learning.** Whether a GRPO or DPO stage follows is undecided, and `RL-SCOPE.md` stays as it is until a model passes.
5. **Gate length.** About 21 hours with Harbor closed. No way to shorten it has been settled yet.
6. **Scripted finals.** Rows where no base sample passes keep their scripted answer unless they're placeholders. A high count would weaken the voice aim, so `stats.json` should be read before training.

## What hasn't been tested

- **Nothing has run on the node for r5.** `run_r5.sh` passed syntax and argument checks only. Neither `gen_finals.py`'s nor the picker's batching has run on a real GPU. The picker's was checked with real torch and peft on a tiny random Gemma 4 on CPU.
- **The picker's attention fix is new.** It would have used the fused kernel training avoids, and now follows `HARBOR_ATTN=math`. r3's multimodal picker ran on this GPU without that restriction, though the Mac gate separately failed r3's vision.
- **Base's new answers don't exist yet,** so their pass rate, length and style are unknown.
- **r3's speed may not carry over exactly.** r5's rows may differ in length from r3's, though the token-based estimate agrees with the step-based one.
- **The sources set has never been scored on a real model.**
- **Whether the prompt fix helps is unmeasured.**

## Appendix: the owner's main debug log

Wording is generalized so no private details appear. The first 11 messages come from one chat and the last from a later one. r5 treats the owner's other logs the same way, for 34 messages at first and more once all 69 logs were added. The problems below were seen on r4's step 1150, and some were partly host faults.

| Question | What went wrong | What good looks like |
|---|---|---|
| "What's up tomorrow?" | Items repeated under invented headings | Tomorrow first, then what's next, each item once |
| "And this week" | Said 4 things, then listed about 8 | A short list by day; a count only if it's right |
| "And this month?" | Due items called "starts"; nested bullets | Key dates in a flat list, using "due" or "all day" |
| "What did [person] say this week" | Nothing; the owner enjoyed this one | Stay warm and grouped, with the real plan in it |
| "What's on my messages" | Talked about "the summary" instead of the messages | The highlights, or one short line if there are none |
| "And emails?" | Mixed two accounts' counts; "I left the rest" | Each account's count, then what matters |
| "And on my reminders?" | Read the calendar and listed events | Read reminders and list those |
| "Send [contact] my schedule for tomorrow" | Skipped the read; included later days | Read tomorrow, then send only tomorrow in plain text |
| "On email" | Used a name with no saved address | Ask for the address in one friendly line |
| "Send her my plan for the week" | "And 1 more" in the body; raw error shown | The whole plan in plain words; a friendly ask for the address |
| [An address on its own] | Searched mail under a guessed account; chat locked | Send the drafted message to that address |
| "Anything about [topic] in my email" | Five results came back; described only the first and said nothing relevant | Read every result, name the match, say "nothing" only after reading all |

## Where the details are

| What | Where |
|---|---|
| Round-by-round plan and r3 and r4 gates | [`training/ask-lora-r4/PLAN.md`](../ask-lora-r4/PLAN.md) |
| Reinforcement learning plan | [`training/ask-lora-r4/RL-SCOPE.md`](../ask-lora-r4/RL-SCOPE.md) |
| Node steps and kit | [`training/ask-lora-r5/mi355x/`](mi355x/) |
| Base-answer scripts | [`training/ask-lora-r5/gpu/`](gpu/) |
| Gate scorer and bars | [`training/ask-lora-r5/gate/score_r5.py`](gate/score_r5.py) |
| Row builders and validator | [`training/ask-lora-r2/builder/`](../ask-lora-r2/builder/) |
| Multi-source test set | [`scripts/toolbench/cases/sources.yaml`](../../scripts/toolbench/cases/sources.yaml) |

Private eval sets, debug logs and the r4 mistake list stay on the owner's Mac and aren't in the repository.
