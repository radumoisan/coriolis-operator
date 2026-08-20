#!/usr/bin/env python3
"""Validate the initial-runtime destination image pulls serially in Kubernetes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Sequence

APP_TAG = "2603.4"
SUPPORT_TAG = "2023.1-ubuntu-jammy"
DESTINATION_PREFIX = "cr.virtomat.io/virtomat/coriolis"
DEFAULT_SECRET = "coriolis-appliance-registry"
DEFAULT_TIMEOUT = 300
POLL_INTERVAL = 5

FAILURE_REASONS = frozenset(("ErrImagePull", "ImagePullBackOff", "InvalidImageName"))

INVENTORY: tuple[tuple[str, str], ...] = tuple(
    (name, f"{DESTINATION_PREFIX}/{name}:{tag}")
    for tag, names in (
        (
            APP_TAG,
            (
                "coriolis-api",
                "coriolis-compressor",
                "coriolis-conductor",
                "coriolis-deployer-manager",
                "coriolis-minion-manager",
                "coriolis-scheduler",
                "coriolis-transfer-cron",
                "coriolis-web",
                "coriolis-web-proxy",
                "coriolis-worker",
            ),
        ),
        (
            SUPPORT_TAG,
            (
                "barbican-api",
                "barbican-keystone-listener",
                "barbican-worker",
                "keystone",
                "keystone-fernet",
                "keystone-ssh",
                "kolla-toolbox",
                "mariadb-server",
                "memcached",
                "rabbitmq",
            ),
        ),
    )
    for name in names
) + (("step-ca", f"{DESTINATION_PREFIX}/step-ca:{APP_TAG}"),)


def pod_name(image_name: str) -> str:
    return f"coriolis-pull-check-{image_name}"


def base_command(context: str, namespace: str, *tail: str) -> list[str]:
    return [
        "kubectl",
        "--context",
        context,
        "--namespace",
        namespace,
        *tail,
    ]


def run(
    command: Sequence[str], *, input_data: str | None = None, timeout: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        input=input_data,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def get_secret_type(
    context: str, namespace: str, secret: str, timeout: int
) -> str | None:
    result = run(
        base_command(
            context,
            namespace,
            "get",
            "secret",
            secret,
            "--output=jsonpath={.type}",
        ),
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot inspect secret {secret}: {result.stderr.strip()}")
    return result.stdout.strip()


def poll_pod(context: str, namespace: str, name: str, timeout: int) -> str | None:
    deadline = time.monotonic() + timeout
    while True:
        result = run(
            base_command(
                context,
                namespace,
                "get",
                "pod",
                name,
                "--output=jsonpath={.status.containerStatuses[0].imageID}",
            ),
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(f"cannot get pod {name}: {result.stderr.strip()}")
        image_id = result.stdout.strip()
        if image_id:
            return image_id

        status = run(
            base_command(
                context,
                namespace,
                "get",
                "pod",
                name,
                "--output=jsonpath="
                "{.status.containerStatuses[0].state.waiting.reason}|"
                "{.status.containerStatuses[0].state.waiting.message}",
            ),
            timeout=timeout,
        )
        if status.returncode != 0:
            raise RuntimeError(f"cannot get pod {name}: {status.stderr.strip()}")
        reason, message = status.stdout.split("|", 1)
        if reason in FAILURE_REASONS:
            raise RuntimeError(
                f"pod {name} cannot pull image: {reason} - {message or 'no message'}"
            )
        if time.monotonic() >= deadline:
            raise RuntimeError(f"timed out after {timeout}s waiting for pod {name}")
        time.sleep(POLL_INTERVAL)


def validate_pull(
    context: str,
    namespace: str,
    secret: str,
    name: str,
    reference: str,
    *,
    timeout: int,
) -> str:
    manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name(name),
            "labels": {
                "app.kubernetes.io/part-of": "coriolis",
                "coriolis.cloudbase.it/pull-validation": "true",
            },
        },
        "spec": {
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "imagePullSecrets": [{"name": secret}],
            "containers": [
                {
                    "name": "pull",
                    "image": reference,
                    "imagePullPolicy": "Always",
                    "command": ["/bin/sh", "-c", "sleep 600"],
                }
            ],
        },
    }

    created = run(
        base_command(context, namespace, "create", "-f", "-"),
        input_data=json.dumps(manifest),
        timeout=timeout,
    )
    if created.returncode != 0:
        raise RuntimeError(
            f"cannot create pod {pod_name(name)}: {created.stderr.strip()}"
        )

    image_id = poll_pod(context, namespace, pod_name(name), timeout)

    deleted = run(
        base_command(
            context,
            namespace,
            "delete",
            "pod",
            pod_name(name),
            "--wait=true",
            "--timeout",
            "60s",
        ),
        timeout=timeout,
    )
    if deleted.returncode != 0:
        raise RuntimeError(
            f"pod {pod_name(name)} validated but deletion failed: "
            f"{deleted.stderr.strip()}"
        )
    return image_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True, help="kubectl context")
    parser.add_argument("--namespace", required=True, help="kubernetes namespace")
    parser.add_argument("--secret", default=DEFAULT_SECRET, help="image pull secret")
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT, help="per-pod wait timeout (s)"
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=[item[0] for item in INVENTORY],
        help="image name(s) to validate",
    )
    parser.add_argument("--dry-run", action="store_true", help="print selection only")
    args = parser.parse_args()

    by_name = dict(INVENTORY)
    if args.only:
        selected = tuple((name, by_name[name]) for name in args.only)
    else:
        selected = INVENTORY

    if args.dry_run:
        for index, (name, reference) in enumerate(selected, start=1):
            print(f"[{index}/{len(selected)}] {name}: would validate {reference}")
        return 0

    secret_type = get_secret_type(
        args.context, args.namespace, args.secret, args.timeout
    )
    if secret_type != "kubernetes.io/dockerconfigjson":
        print(
            f"error: secret {args.secret} has type {secret_type}, "
            "expected kubernetes.io/dockerconfigjson",
            file=sys.stderr,
        )
        return 1

    for index, (name, reference) in enumerate(selected, start=1):
        try:
            image_id = validate_pull(
                args.context,
                args.namespace,
                args.secret,
                name,
                reference,
                timeout=args.timeout,
            )
        except RuntimeError as error:
            print(
                f"[{index}/{len(selected)}] {name}: FAILED "
                f"(pod {pod_name(name)} left in place for diagnosis)",
                file=sys.stderr,
            )
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(f"[{index}/{len(selected)}] {name}: pulled {image_id}", flush=True)

    print(
        f"validated {len(selected)} image pull(s) "
        f"(context={args.context}, namespace={args.namespace}, secret={args.secret})",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
