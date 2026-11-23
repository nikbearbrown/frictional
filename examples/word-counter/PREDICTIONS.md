# Predictions

Constructed example for testing Frictional.

## Before the run
Date: 2026-11-16
Question: Will the tab-and-newline test pass with split(" ")?
Expected result: yes
Confidence and reason: 70%, split usually handles whitespace
## After the run — append, do not rewrite above
Actual output and evidence path: it failed, "one\ttwo" stayed one word
Difference from prediction: wrong
Prediction held: no

## Before the run
Date: 2026-11-18
Question: Will most_common return "the" for "The cat. the dog, THE end"?
Expected result: yes
Confidence and reason: 85%
## After the run — append, do not rewrite above
Actual output and evidence path: "the", tests pass
Difference from prediction: none
Prediction held: yes
