# Deployment process

## Schedule

Production deployments happen on Tuesdays and Thursdays between 10:00 and 15:00 CET.
No production deployment is allowed on Fridays or on the day before a public holiday.
A deployment freeze runs every year from December 15 to January 5; only security fixes
approved by the head of engineering ship during the freeze.

## Change review

Every production change needs an approved change request linked to its pull request.
The change request lists the risk, the rollback plan and the person on call.

## Canary and rollback

Each release first goes to a canary that receives 5% of traffic for 30 minutes.
If the canary error rate exceeds the baseline by more than 1%, the release is rolled back.
A rollback must be completed within 15 minutes of the decision.
