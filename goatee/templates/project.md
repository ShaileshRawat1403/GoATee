# GoATee Project

Project-Name: {project_name}
Tech-Stack: Python >=3.10

## Verification Commands

test: python -m unittest discover tests
build: python -m compileall goatee
lint: 

## Permanent Constraints

- Standard library only at runtime; no external runtime dependencies without prior architectural approval.
- Maintain minimal persistent artifact model: project.md, change.md, receipt.md.
- Verification must not mutate the repository state.
