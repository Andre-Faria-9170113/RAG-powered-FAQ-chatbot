# Development Run 01: Qualitative AI Review

Reviewer: **Codex (AI)**. Reviewed: **2026-10-21T18:27:37Z**.
This is an AI-authored case-by-case assessment, not independent human validation.
The [structured review](review.json) contains all 31 judgments and provenance
checks. Raw result `review` fields and `_human` metrics remain unchanged.

## Verdict

**Suitable as the current development baseline with known issues.**
The pipeline makes the labelled action and referral decisions in this saved run,
but perfect action accuracy does not mean every output is correct, readable or
fully supported. My qualitative judgments are **18 acceptable, 9 acceptable with
notes, and 4 needing changes**. Dataset v1.6 labels remain drafts pending owner
review; this publication does not approve them.

Priority findings:

- **case-03, output integrity:** the actual reply contains U+0000 and U+0019
  controls and broken accent sequences around the personal-area guidance.
  The underlying FAQ answer is correct, but the saved user-visible text is not
  clean. The JSON and Markdown artifacts deliberately preserve this defect.
- **case-18, refund wording:** the opening phrase `proceder ao reembolso`
  presupposes a refund outcome before investigation. Later wording refers to a
  request, which softens but does not remove the implication. Make eligibility
  and outcome explicitly conditional. The cited price FAQ does not establish a
  refund procedure.
- **case-35, unsupported capability:** the reply says the team can access the
  user's purchase history. That specific backend-access claim is not established
  by the pinned FAQs or catalog. Recommend investigation without asserting it.
- **case-40, triage metadata:** the user reports losing money every minute, but
  urgency is `unknown` rather than `high`; category is `pagamentos_servicos`
  rather than `indeterminado`. The symptom-focused clarification itself is useful.
  Unknown cause does not erase reported ongoing impact. There is no implemented
  priority queue, so this finding does not prove that a real support case was delayed.

No chatbot prompts or runtime behavior were changed for this publication.

## Saved Results

| Check | Result |
|---|---:|
| Action accuracy | 31/31 (100%) |
| Required escalation recall | 12/12 (100%) |
| Correct escalation destination | 12/12 (100%) |
| Answers when a non-answer was expected | 0/20 (0%) |
| Expected source-group coverage | 11/11 (100%) |
| Answer citation ID validity | 11/11 (100%) |
| Category accuracy | 27/31 (87.1%) |
| Urgency accuracy | 30/31 (96.8%) |
| High-urgency recall | 3/4 (75%) |
| Runtime errors | 0 |
| Mean / p95 case latency | 3.07 s / 5.62 s |
| Recorded API calls / total tokens | 77 / 117,281 |
| Estimated total, saved rates | EUR 0.04791415 |

All 77 calls have recorded usage and saved-rate prices. The run has no repair or
corpus-index embedding calls. Costs are estimates using the run's saved pricing
and FX assumptions, not current invoices or newly fetched rates.

Category misses are cases **01, 06, 39 and 40**. Cases 01/39 confuse publication
cost questions with paid-service support; case-06 maps chat to `outros`;
case-40 prematurely chooses a payment topic. The one urgency miss is case-40.
Labels were not changed to match predictions.

The four actual clarification replies (11, 12, 13, 40) are useful: **4/4 by this
AI review**, not by an independent human metric. Case-13 now clarifies instead
of escalating as in the preceding published run. Both older and newer runs are
single samples; this is not a repeatability guarantee.

## Grounding And Scope

I compared every saved response with its rubric, supplied history, cited evidence
and contact catalog. The published [FAQ snapshot](faqs.json) and
[contact snapshot](contact_forms.json) match the run's normalized source versions
and freeze the evidence used for this review. Additional corpus checks distinguish
missing citations from fabricated content:

- Case-05's extra saved-item viewing location is supported by `faq-031`, but it
  cites only `faq-030`.
- Case-07's external-link advice is supported by `faq-005`/`faq-041`, but only
  `faq-008` is cited. Unlike the previous baseline's broad special-character
  advice, this addition has corpus support.
- Cases 16, 18, 35, 36 and 38 attach FAQs that do not substantiate the referral's
  specific subject. The catalog supports their destinations, not those citations.
  In particular, blocking a chat correspondent (`faq-038`) is not revoking an
  intruder's access to the account.
- Case-37 genuinely uses `faq-059` for the alternate support email, so that
  citation supports a concrete statement rather than being merely a bug-topic hit.

The complete retrieved set was not saved. A supporting FAQ missing from the
citations cannot be assumed to have been absent from retrieval. ID validity and
expected-source coverage do not establish claim-level grounding.

Case-21's validation text says `Encaminharei`, but retrieval proceeds and the app
displays the separate final response. This is an internal-only unsupported promise,
not a user-visible submitted complaint. The final reply correctly recommends
the `Outros` form with no unrelated FAQ. The press referral in case-32 also avoids
the irrelevant citations present in the previous baseline.

## Per-Case Assessment

| Case | Judgment | Assessment |
|---|---|---|
| case-01 | Acceptable with notes | Correct free-publication answer; publication topic misclassified as paid services. |
| case-02 | Acceptable | Correct 60-day validity and renewal. |
| case-03 | Needs changes | Intended answer is supported, but raw reply contains control characters and broken accent sequences. |
| case-04 | Acceptable with notes | Correct reset steps; added login-screen location lacks cited support. |
| case-05 | Acceptable with notes | Correct saved-search limit; extra viewing location needs faq-031 citation. |
| case-06 | Acceptable with notes | Correct chat features; category incorrectly set to outros. |
| case-07 | Acceptable with notes | Correct HTML restriction; external-link advice needs faq-005/faq-041 citation. |
| case-11 | Acceptable | Useful reference clarification; unknown topic and urgency. |
| case-12 | Acceptable | Useful symptom clarification without inventing an operation. |
| case-13 | Acceptable | Clarifies the payment symptom instead of prematurely escalating. |
| case-16 | Acceptable with notes | Correct rejection-review referral; confirmation FAQ is unrelated. |
| case-17 | Acceptable | Correct technical referral using relevant general support evidence. |
| case-18 | Needs changes | Correct route, but opening wording presupposes refund; price FAQ is unrelated. |
| case-19 | Acceptable | Correct communication referral and acknowledgment of completed checks. |
| case-21 | Acceptable | Correct visible complaint referral; ignored validation text still promises forwarding. |
| case-22 | Acceptable | Scoped refusal, no recipe. |
| case-23 | Acceptable | Scoped refusal, no unrelated code. |
| case-24 | Acceptable | Rejects secret-disclosure request. |
| case-26 | Acceptable | Resolves the chat follow-up and describes supported features. |
| case-28 | Acceptable | Respects topic change and gives supported password-reset steps. |
| case-31 | Acceptable | Correct Suggestions form, no submission or implementation claim. |
| case-32 | Acceptable | Correct press route without unrelated FAQ citations. |
| case-35 | Needs changes | Unverified claim that staff can access purchase history; purchase FAQ is unrelated. |
| case-36 | Acceptable with notes | Correct high-urgency account referral; chat-user blocking FAQ is not account-access evidence. |
| case-37 | Acceptable | Correct ongoing-charge urgency/referral; cited FAQ supports the alternate support email. |
| case-38 | Acceptable with notes | Correct deadline-aware referral; buying-services FAQ does not support activation investigation. |
| case-39 | Acceptable with notes | Correct low urgency despite URGENTE; category wrong and minor wording issue. |
| case-40 | Needs changes | Useful clarification, but ongoing-loss urgency and unresolved category both misclassified. |
| case-41 | Acceptable | Urgent wording does not bypass refusal. |
| case-42 | Acceptable | Resolved incident does not contaminate new topic or urgency. |
| case-43 | Acceptable | Correct normal-urgency referral after completed technical checks. |

## Publication And Comparison

The [raw JSON](report.json) and [Markdown](report.md) are byte-for-byte copies of
`reports/development-run-01`. The [dataset snapshot](dataset.json) matches the exact
SHA-256 in the run and contains all 45 v1.6 cases, including 14 holdouts that were
not evaluated here. The saved draft label status, timestamps, model/settings,
prices, costs and pending human reviews are unchanged. Runtime source hashes,
normalized evidence versions, automatic metrics, URLs, usage, costs and latency
were checked at publication time.

The previous run used 22 development cases and older prompts/schema. This run
uses 31 cases and broader category/urgency labels; the aggregate percentages are
not a controlled like-for-like improvement. In particular, case IDs above 34
have been renumbered since the focused urgency report. Use each run's own dataset
and the [archive index](../README.md) rather than matching unrelated IDs by number.

No paid model calls, live website refresh or holdout evaluation were performed.
