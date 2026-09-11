# V8.4 Recovery Contract

A recovery decision is authoritative only for the exact pull-request head SHA it was computed from. Any head movement invalidates the snapshot and forces a fresh classification.

Automatic retry is limited to a single failed provider/infrastructure job and a bounded attempt budget. Multiple heterogeneous failures, permission problems, policy drift, exhausted retries, and ambiguous failures are escalated instead of retried.
