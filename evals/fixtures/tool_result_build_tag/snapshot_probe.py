"""Print the build tag from a deterministic snapshot fingerprint."""

import hashlib

_SNAPSHOT_FIELDS = ("batch:arc-17", "region:west", "epoch:34")


def main() -> None:
    fingerprint = hashlib.sha256("|".join(_SNAPSHOT_FIELDS).encode()).hexdigest()
    print(f"TR-91 build_tag=plume-{fingerprint[:8]}")


if __name__ == "__main__":
    main()
