# Resuming an install reruns every step

`install` saves the intake answers to `installs.json` before step 1 and marks the install `in_progress`. Resuming reruns every step with the saved answers instead of tracking which steps finished, so every step must be idempotent and do nothing when its work is already done. Resume is then correct after an interruption at any point, including partway through a step, at the cost of re-checking finished steps.
