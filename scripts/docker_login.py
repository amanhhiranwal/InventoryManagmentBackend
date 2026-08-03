# scripts/docker_login.py
import os
import subprocess
import sys


def main() -> int:
    username = os.getenv("DOCKERHUB_USERNAME")
    token = os.getenv("DOCKERHUB_TOKEN")

    if not username or not token:
        print("Missing DOCKERHUB_USERNAME or DOCKERHUB_TOKEN")
        return 1

    result = subprocess.run(
        ["docker", "login", "-u", username, "--password-stdin"],
        input=token.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    print(result.stdout.decode("utf-8", errors="replace"))

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
