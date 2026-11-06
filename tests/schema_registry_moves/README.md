# Schema registry moves

Overlay files let a refactor move a schema producer to another module before the main registry in
`tests/test_schema_registry.py` is updated. Each `*.txt` file in this directory holds one line per moved producer:

    new_module:symbol <- old_module:symbol

The test maps the code keys back to the old key before comparing. Comments start with `#`. Once the registry
itself is updated to the new key, the overlay line is removed. A line whose new key is not produced by the code,
whose old key is not in the registry, or whose old key still exists in the code fails the test.
