#!/usr/bin/env python3
"""Mirror the approved Coriolis image inventory one image at a time."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

SOURCE_REGISTRY = "registry.cloudbase.it"
DESTINATION_PREFIX = "cr.virtomat.io/virtomat/coriolis"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Image:
    name: str
    source_repository: str
    source_digest: str
    destination_tag: str

    @property
    def source(self) -> str:
        return f"{self.source_repository}@sha256:{self.source_digest}"

    @property
    def destination(self) -> str:
        return f"{DESTINATION_PREFIX}/{self.name}:{self.destination_tag}"


def appliance_image(name: str, digest: str) -> Image:
    return Image(
        name=name,
        source_repository=f"{SOURCE_REGISTRY}/appliance/{name}",
        source_digest=digest,
        destination_tag="2603.4",
    )


def support_image(name: str, digest: str) -> Image:
    return Image(
        name=name,
        source_repository=f"{SOURCE_REGISTRY}/appliance/{name}",
        source_digest=digest,
        destination_tag="2023.1-ubuntu-jammy",
    )


IMAGES = (
    appliance_image(
        "coriolis-api",
        "fce6369f07ef777b5174d3a4f849d4eac914256a20a47ffa0cd1c98081be2705",
    ),
    appliance_image(
        "coriolis-common",
        "e0baa5094d651992253cc419f40411f2529a1a1236e87eda90809b235aaf235a",
    ),
    appliance_image(
        "coriolis-compressor",
        "af2cf9d2eb3ca153b56b3eb928045092f904be03a381371ff73efacaf7feb842",
    ),
    appliance_image(
        "coriolis-conductor",
        "27495f44fbb8b320098d0aa04cd9dcb2a4b432e57aa17417606efc5403ac09c7",
    ),
    appliance_image(
        "coriolis-console-editor",
        "c944df5b208a2b91d317ee2deb636e6bbc3cf278d181766943b7e1a08e589429",
    ),
    appliance_image(
        "coriolis-deployer-manager",
        "a2a7091daf8e172b96fa0b48d19ffad285d7bfaad42fc7e8cd44a688f06f36aa",
    ),
    appliance_image(
        "coriolis-licensing-server",
        "09d8332b1d271824300e9e210c2623251b432bfc46ca6e2500ced8ed2f8d2e6b",
    ),
    appliance_image(
        "coriolis-logger",
        "aafdad52913518d55a2c44d8e437b96f7cc079a79e4437c2ce0c396ed178cb4f",
    ),
    appliance_image(
        "coriolis-metal-hub",
        "e51ce9624312ef6a2e3b39dbd62f3d7d1b5059b40a11cfe8ba351330e45fa698",
    ),
    appliance_image(
        "coriolis-minion-manager",
        "1ea016dd967ce249a45cf9937701a45880f3b42f8146a93d1f5eb4f1d84e1fb9",
    ),
    appliance_image(
        "coriolis-scheduler",
        "45bea9e0bab4cac0fdddee6d3eac52006d12cf7de1e798e2949dd9ebc2a73c41",
    ),
    appliance_image(
        "coriolis-transfer-cron",
        "3a44d3b40ba92dff9217b8e7d6a7ca3e7a202efa2641c771ce9b2a3552b3ea9c",
    ),
    appliance_image(
        "coriolis-web",
        "32ebc391ac46fe627185694b3fd252afd7587b152f526dff38ae0a5b887c0db1",
    ),
    appliance_image(
        "coriolis-web-proxy",
        "649a4fa9ceb91effdd0f3d782e7ac593d2e099ac93ffe8d1c8b6629eba6be762",
    ),
    appliance_image(
        "coriolis-worker",
        "ff30999d6e43709411f197b1b6b80dbce1d7e5498a27f869df93a061626ab2c9",
    ),
    support_image(
        "barbican-api",
        "a142a57761f708b241358383d6445ac5da4e05ae26a284369081cfb15cca8a60",
    ),
    support_image(
        "barbican-keystone-listener",
        "cc6ee5067f336a578e761a031116b32b60a08ba323d1c33f0758d0e1c43ba0cb",
    ),
    support_image(
        "barbican-worker",
        "ed907de778900b08f2645c9eeb82d48d8202ce6517cdb543d42db2e88ea642b5",
    ),
    support_image(
        "keystone",
        "7c57962762f5e6fdb1a109097e8f3e2e5f6218ad9c09f10a585adb67ed245cf0",
    ),
    support_image(
        "keystone-fernet",
        "2f10e712c99f8c9bb78cdc9a33452d9994e228f46c00aaeb2d45b1806e3ed03f",
    ),
    support_image(
        "keystone-ssh",
        "a3ab792cb4375c6aa4eab3930486ec536629fee45ff4c9285a5e23c2b4fed60c",
    ),
    support_image(
        "kolla-toolbox",
        "b0952a70fad1df6ed8351ff522b1e86b77148d52efc77d85b048a517574e0bff",
    ),
    support_image(
        "mariadb-server",
        "22cb109d23d1aa6a6acb17e54657b5b9cd753837b01345b52fc3c35cbbd9981e",
    ),
    support_image(
        "memcached",
        "746b93082a4f6d07f464e93d4b14f5e30510abf17a9ae0a4af20e111408c8f1e",
    ),
    support_image(
        "rabbitmq",
        "a595bf6f306ded2b6ad01f068ef69255df72eb73d471ba73ce9bbf0470d15d8a",
    ),
    Image(
        name="step-ca",
        source_repository="docker.io/smallstep/step-ca",
        source_digest=(
            "e9e8fa3262bf37b130962ffddbf6a64ac188f0bbb80959cf3ddc04c6bf294c3d"
        ),
        destination_tag="2603.4",
    ),
)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        parsed = shlex.split(raw_value, comments=False, posix=True)
        if len(parsed) != 1:
            raise RuntimeError(f"Invalid value for {key.strip()} in {path}")
        values[key.strip()] = parsed[0]
    return values


def source_auth_file(env_path: Path) -> int:
    env = read_env(env_path)
    username = env.get("REGISTRY_USER")
    password = env.get("REGISTRY_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            f"REGISTRY_USER and REGISTRY_PASSWORD are required in {env_path}"
        )

    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    payload = json.dumps(
        {"auths": {SOURCE_REGISTRY: {"auth": encoded}}}, separators=(",", ":")
    ).encode()
    auth_fd = os.memfd_create("coriolis-source-registry-auth", flags=0)
    os.write(auth_fd, payload)
    return auth_fd


def run_skopeo(
    arguments: list[str], *, auth_fd: int | None = None
) -> subprocess.CompletedProcess[str]:
    if auth_fd is not None:
        os.lseek(auth_fd, 0, os.SEEK_SET)
    return subprocess.run(
        ["skopeo", *arguments],
        check=False,
        capture_output=True,
        text=True,
        pass_fds=() if auth_fd is None else (auth_fd,),
    )


def inspect_digest(reference: str, *, auth_fd: int | None = None) -> str | None:
    arguments = ["inspect", "--format", "{{.Digest}}"]
    if auth_fd is not None:
        arguments.extend(("--authfile", f"/proc/self/fd/{auth_fd}"))
    result = run_skopeo([*arguments, f"docker://{reference}"], auth_fd=auth_fd)
    if result.returncode == 0:
        return result.stdout.strip()

    error = result.stderr.lower()
    if any(
        marker in error
        for marker in ("manifest unknown", "name unknown", "not found", "404")
    ):
        return None
    raise RuntimeError(f"Cannot inspect {reference}: {result.stderr.strip()}")


def inspect_digest_with_unauthorized_retry(
    reference: str, *, attempts: int = 3, delay: float = 2.0
) -> str | None:
    for attempt in range(1, attempts + 1):
        try:
            return inspect_digest(reference)
        except RuntimeError as error:
            if "unauthorized" not in str(error).lower() or attempt == attempts:
                raise
            time.sleep(delay)
    raise RuntimeError(f"Cannot inspect {reference}: retry attempts exhausted")


def mirror(image: Image, auth_fd: int, *, dry_run: bool) -> str:
    expected_digest = f"sha256:{image.source_digest}"
    source_digest = inspect_digest(image.source, auth_fd=auth_fd)
    if source_digest != expected_digest:
        raise RuntimeError(
            f"Source digest mismatch for {image.name}: "
            f"expected {expected_digest}, found {source_digest}"
        )

    destination_digest = inspect_digest(image.destination)
    if destination_digest == expected_digest:
        return "already-present"
    if destination_digest is not None:
        raise RuntimeError(
            f"Refusing to overwrite {image.destination}: "
            f"expected {expected_digest}, found {destination_digest}"
        )
    if dry_run:
        return "would-copy"

    result = run_skopeo(
        [
            "copy",
            "--quiet",
            "--preserve-digests",
            "--src-authfile",
            f"/proc/self/fd/{auth_fd}",
            f"docker://{image.source}",
            f"docker://{image.destination}",
        ],
        auth_fd=auth_fd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Copy failed for {image.name}: {result.stderr.strip()}")

    destination_digest = inspect_digest_with_unauthorized_retry(image.destination)
    if destination_digest != expected_digest:
        raise RuntimeError(
            f"Destination digest mismatch for {image.name}: "
            f"expected {expected_digest}, found {destination_digest}"
        )
    return "copied"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", choices=tuple(image.name for image in IMAGES))
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = tuple(image for image in IMAGES if args.only in (None, image.name))
    auth_fd = source_auth_file(args.env_file)
    try:
        for index, image in enumerate(selected, start=1):
            status = mirror(image, auth_fd, dry_run=args.dry_run)
            print(
                f"[{index}/{len(selected)}] {image.name}: {status} "
                f"sha256:{image.source_digest}",
                flush=True,
            )
    finally:
        os.close(auth_fd)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
