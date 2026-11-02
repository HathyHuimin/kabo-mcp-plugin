# Written analysis

Code writes each question's conclusion, its evidence and the data limits. The analysis adds
judgment in one JSON object keyed by the question IDs printed with the evidence:

```json
{"questions": {"<id>": {
  "interpretation": "what the conclusion means for this creator",
  "hypotheses": ["an unconfirmed explanation to investigate"],
  "need": {"items": ["data the creator should supply first"], "why": "what it would settle"},
  "next_steps": [{"do": "what to do", "check": "what to check, and when",
                  "then": "what to do with each result", "opening": "optional opening plan"}]
}}}
```

Every field is optional; a question left out keeps only the code-written parts.

- `interpretation`: judgment on the code-written conclusion printed with the evidence, never
  stronger or weaker than it; possible causes go in `hypotheses`. Cite computed numbers as the printed
  `{fact:…}` placeholders (append `|percent` for a fraction as a percentage). A typed number must be
  stated by this question's evidence: a printed fact (rounded, scaled or as a percentage; a change
  keeps its direction, so a 0.15 ratio is a fall of 85%, never a rise), a date in
  it, a metric of one of its records, the user's own request, or a retrieved title or caption quoted
  verbatim. A sentence typing any other number is left out.
- `hypotheses`: explanations to investigate, not findings. Public counts do not show penalties,
  private metrics or audience classification.
- `need`: only what the next step cannot start without.
- `next_steps`: one experiment or check per item. Compare like with like: the same format and the
  same time after publication. Durations and counts here are proposals, not account facts or
  platform rules, and may be typed; account numbers anywhere are cited as placeholders. `opening` follows
  [opening-experiments.md](opening-experiments.md).
