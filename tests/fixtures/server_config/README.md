# Server-config source fixtures

These are synthetic, non-secret parser inputs for Feature 053 unit tests. They do not
prove that an active nginx or OpenLiteSpeed image accepts or activates the content.

Each server directory contains:

- `valid.conf`: the planned minimum cache subset;
- `invalid.conf`: syntax or authority that must be refused;
- `conflict.conf`: ownership that conflicts when combined with another fragment;
- `boundary.conf`: protected-route or external-authority input that must be refused.

OpenLiteSpeed fixture names describe the planned vhost-local grammar. T004 exact-image
feasibility remains a separate authorized live gate.
