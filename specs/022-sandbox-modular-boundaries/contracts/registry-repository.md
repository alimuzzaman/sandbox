# Contract: Registry Repository

- Resolve identity by canonical root plus label.
- List, put, and remove records under the existing lock semantics.
- Preserve supported legacy/current schemas and compatible unknown fields.
- Never apply runtime defaults, infer capabilities, or execute lifecycle behavior.
- Write through a sibling temporary file and atomic replacement.
- Leave the previous valid state recoverable on failed writes.
- Provide an in-memory implementation with identical observable behavior.
- Reject unsupported future schemas without rewriting the source.
