"""Build the Sandcastle Docker runner image for the Docker sandbox backend.

Usage:
    python build_docker_image.py

Requires:
    - Docker daemon running locally
"""

import subprocess
import sys
from pathlib import Path


def main() -> None:
    dockerfile = Path(__file__).parent / "Dockerfile.runner"
    if not dockerfile.exists():
        print(f"Error: {dockerfile} not found", file=sys.stderr)
        sys.exit(1)

    image_name = "sandcastle-runner:latest"
    print(f"Building Docker image: {image_name}")
    print(f"Using Dockerfile: {dockerfile}")
    print()

    result = subprocess.run(
        [
            "docker", "build",
            "-t", image_name,
            "-f", str(dockerfile),
            str(dockerfile.parent),
        ],
        check=False,
    )

    if result.returncode != 0:
        print(f"\nBuild failed with exit code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)

    print()
    print(f"Image built successfully: {image_name}")
    print()
    print("Set in your .env:")
    print("  SANDBOX_BACKEND=docker")
    print(f"  DOCKER_IMAGE={image_name}")


if __name__ == "__main__":
    main()
