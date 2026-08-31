# Test instructions

- Describe public behavior rather than copying the implementation's control
  flow or private structure.
- Put a short comment immediately below every newly added test function to
  describe the scenario.
- Prioritize error paths, cancellation, resource release, ordering, and boundary
  conditions in addition to the main success path.
- A regression test must demonstrate that the previous implementation fails
  the scenario before the fix is accepted.
- Do not add assertion-free tests, meaningless assertions, or execution-only
  tests merely to increase coverage.
