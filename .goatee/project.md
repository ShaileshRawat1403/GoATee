# GoATee Project

Project-Name: GoATee
Tech-Stack: Python >=3.10

## Verification Commands

test: python -m unittest discover tests
build: python -m compileall goatee
lint: 

## Permanent Constraints

- Standard library only at runtime; no external runtime dependencies without prior architectural approval.
- Maintain minimal persistent artifact model: project.md, change.md, receipt.md, and review.md when human attestation exists.
- Verification must not mutate the repository state.
- Human attestation must never modify receipt.md.
