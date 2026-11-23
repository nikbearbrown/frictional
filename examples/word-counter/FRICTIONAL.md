# Frictional

Constructed example for testing Frictional. Not a real student's log.

## Dated entry

Date (mark retrospective entries): 2026-11-16
What I tried and expected: Count words by splitting on spaces; expected the tab test to pass.
What happened / what I found difficult or checked: The tab-and-newline test failed: split(" ") keeps "one\ttwo" as one word.
What I did next: Read the str.split docs and switched to split() with no argument.
What Claude or another person contributed: Claude explained the difference between split() and split(" "); I wrote the fix and the test.
What I understand now / still do not understand: Whitespace splitting is clear; I am not sure how punctuation should count.
Evidence, commit, and next step: next commit fixes it; add most_common.

## Dated entry

Date (mark retrospective entries): 2026-11-18
What I tried and expected: Strip punctuation and lowercase for most_common; expected "the" for the test sentence.
What happened / what I found difficult or checked: It passed first time; I checked an empty string by hand.
What I did next: Added an empty-text test.
What Claude or another person contributed: none
What I understand now / still do not understand: Ties between words are still decided by first appearance; I did not decide if that is right.
Evidence, commit, and next step: tests in tests/test_counter.py; next build the web page.

## Dated entry

Date (mark retrospective entries): 2026-11-21 (retrospective)
What I tried and expected: A Flask page with one form.
What happened / what I found difficult or checked: Inside a container the app was unreachable until I bound to 0.0.0.0.
What I did next: Added --host to app.py and put it in the README.
What Claude or another person contributed: Claude suggested argparse for the host and port.
What I understand now / still do not understand: Why 127.0.0.1 inside a container is not reachable from outside it.
Evidence, commit, and next step: app.py and README.md.
