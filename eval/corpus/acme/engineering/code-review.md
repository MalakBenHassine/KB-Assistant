# Code review guidelines

## Approvals

Every pull request to the main branch needs two approvals, one of them from a code owner.
The continuous integration pipeline must pass before merging; nobody may bypass it.

## Size and speed

Pull requests should stay under 400 changed lines; larger changes are split.
Reviewers respond to a review request within one business day.

## What reviewers check

Reviewers check correctness, tests, security and readability, in that order.
Style questions are left to the formatter and the linter.
