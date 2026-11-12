# Design evidence

Existing local login is already reusable; hosted native WordPress login is one-use and finite, while login_service/share modes have separate contracts. These paths must not be conflated.

Chosen: separate permanent renderer with a fragment-to-POST bridge. This isolates new behavior and retains the existing renderer exactly. A smaller change that merely disabled expiry and use checks in the query-token renderer was rejected: a reusable credential would enter HTTP access logs.

The prior site-specific helper proved that `Referrer-Policy: no-referrer` makes the automatic form POST send `Origin:null`. Switching to same-origin retained strict origin checking and enabled two real sessions separated by logout. Reuse that policy, not a null-origin exception.

Existing redaction strips URL fragments. Keep deliberate issuance as the only key disclosure; do not add plaintext to host state. Existing replacement is the revocation mechanism. Removing manifest configuration alone does not revoke an installed helper.
