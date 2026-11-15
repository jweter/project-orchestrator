# Issue #710 — actual process-kill durable resume proof

Unlike the earlier simulated supervisor restart (#722, reopening Python
objects), `test_iron_god_force_kill_recovery.py` runs the existing local
acceptance system in a **separate Windows Python worker process**.

It initializes a disposable Git repository containing three independently
failing native tests. A child process accepts two individually tested source
edits through the existing protected Git ref, SQLite journal, exact-head
verification and local acceptance. The parent waits for the second accepted
milestone, then calls **Popen.kill()** to terminate that child without Python
cleanup. A fresh process must reopen exactly the two committed milestones.

The parent revokes owner authority and proves the next process cannot dispatch
the third edit. After a new owner grant, another fresh child continues only
the third edit. Its work must pass native tests and commit under the existing
exact-head rules. Re-invoking the child shows ALREADY_COMPLETE with **no
fourth commit, duplicate milestone or replayed acceptance**.

This is a stronger isolated crash-recovery integration test, but still uses
disposable local source produced deterministically, not Ollama-generated
product code. It does not modify the real Azathoth engine, local release pin,
product clone, supervisor or cloud-model service. Do not conflate its success
with #710's live engine restart authorization or full Product Reality proof.
