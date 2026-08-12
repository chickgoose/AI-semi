# Owner path fixtures

These three triplets are deliberately real regular files used to exercise the
v2 source, binding, and runner path/digest contract. Test setup computes one
Git-blob digest and two byte SHA-256 digests per owner, then materializes seven
separate bound run artifacts with `materialize_owner_fixture()`.

They are not candidate implementations or evidence that RTL was simulated.
Their purpose is to ensure provenance attacks traverse the same filesystem and
artifact-loading code as an owner submission instead of a dict-only mock.
