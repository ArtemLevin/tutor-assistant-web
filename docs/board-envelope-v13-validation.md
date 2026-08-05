# Board envelope v1.3 validation

The release gate covers runtime contract parsing for envelopes `1.0`, `1.2` and `1.3`, SQLite and PostgreSQL migrations, actor-scoped Lamport ordering, idempotent retries, mixed-version recovery, clock-skew scenarios, unified local stack startup and production container checks.

Legacy command batches persist `NULL / NULL` Lamport ranges. Ordered batches persist positive `lamport_min / lamport_max` values.

The unified local vertical smoke passed with a healthy gateway, TutorBoard build readiness, authenticated board creation, ordered envelope `1.3` append, snapshot recovery, GeometryOS access, collaboration transport and the remaining lesson/material delivery workflow.
