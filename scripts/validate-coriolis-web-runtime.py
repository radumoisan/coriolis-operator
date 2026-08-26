#!/usr/bin/env python3
"""Validate the constrained standalone Coriolis web runtime without secrets."""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/coriolis-web:2603.4"
    "@sha256:32ebc391ac46fe627185694b3fd252afd7587b152f526dff"
    "38ae0a5b887c0db1"
)
ENTRYPOINT = ("npm", "run", "start")
WORKDIR = "/root/coriolis-web"
PORT = "3000/tcp"
PREFIX = "oc-coriolis-web-evidence"
DEFAULT_TIMEOUT = 120
POLL_INTERVAL = 1.0
STABILITY_INTERVAL = 20.0
STOP_TIMEOUT = 15
# The observed upstream npm SIGTERM completion was 0.656s. Keep a generous,
# evidence-backed bound while still detecting a materially delayed stop.
STOP_COMPLETION_BOUND = 5.0
FORBIDDEN_ENV_NAMES = frozenset(
    (
        "CORIOLIS_URL",
        "CA_FINGERPRINT",
        "MOD_JSON",
        "CORIOLIS_LICENSING_BASE_URL",
        "DISCLAIMER_PATH",
    )
)
FORBIDDEN_ENV_PREFIXES = ("LOGGER", "STEP_CA")
FIRST_LAUNCH_BODY = '{"isFirstLaunch":false}'

CommandRunner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]
Reporter = Callable[[str], None]


class ValidationFailure(Exception):
    """A sanitized failure that identifies only a stable validation stage."""

    def __init__(self, stage: str) -> None:
        super().__init__(f"validation failed: {stage}")
        self.stage = stage


class Resources:
    def __init__(self, token: str) -> None:
        self.token = token

    @property
    def container(self) -> str:
        return f"{PREFIX}-{self.token}"


def _run(command: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), check=False, capture_output=True, text=True, timeout=timeout
    )


class Validator:
    def __init__(
        self,
        *,
        repository_root: Path,
        timeout: int,
        runner: CommandRunner = _run,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = time.sleep,
        report: Reporter = print,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.timeout = timeout
        self.runner = runner
        self.clock = clock
        self.sleeper = sleeper
        self.report = report
        self.resources = Resources(secrets.token_hex(8))

    def _docker(self, *arguments: str) -> list[str]:
        return ["docker", *arguments]

    def _checked(self, stage: str, command: Sequence[str]) -> None:
        try:
            result = self.runner(command, self.timeout)
        except (OSError, subprocess.SubprocessError):
            raise ValidationFailure(stage) from None
        if result.returncode != 0:
            raise ValidationFailure(stage)

    def _stage(self, name: str, action: Callable[[], None]) -> None:
        started = self.clock()
        action()
        self.report(f"PASS {name} {self.clock() - started:.3f}")

    def _inspect(self, stage: str) -> dict[str, object]:
        try:
            result = self.runner(
                self._docker("inspect", self.resources.container), self.timeout
            )
        except (OSError, subprocess.SubprocessError):
            raise ValidationFailure(stage) from None
        if result.returncode != 0:
            raise ValidationFailure(stage)
        try:
            payload = json.loads(result.stdout)
            if len(payload) != 1 or not isinstance(payload[0], dict):
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ValidationFailure(stage) from None
        return payload[0]

    def _verify_image_contract(self) -> None:
        stage = "image-contract"
        try:
            result = self.runner(self._docker("image", "inspect", IMAGE), self.timeout)
        except (OSError, subprocess.SubprocessError):
            raise ValidationFailure(stage) from None
        if result.returncode != 0:
            raise ValidationFailure(stage)
        try:
            payload = json.loads(result.stdout)
            if len(payload) != 1 or not isinstance(payload[0], dict):
                raise ValueError
            image = payload[0]
            config = image["Config"]
            if not isinstance(config, dict):
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ValidationFailure(stage) from None
        if (
            image.get("Os") != "linux"
            or image.get("Architecture") != "amd64"
            or config.get("User") not in (None, "", "root", "0")
            or tuple(config.get("Entrypoint") or ()) != ENTRYPOINT
            or config.get("Cmd") not in (None, [])
            or config.get("WorkingDir") != WORKDIR
            or config.get("ExposedPorts") != {PORT: {}}
            or config.get("Volumes")
            or config.get("Healthcheck")
        ):
            raise ValidationFailure(stage)

    def _runtime_arguments(self) -> list[str]:
        """Keep the upstream npm entrypoint: no command or entrypoint override."""
        return self._docker(
            "run",
            "--name",
            self.resources.container,
            "--detach",
            "--network",
            "none",
            "--user",
            "0:0",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--env",
            "BIND=0.0.0.0",
            IMAGE,
        )

    def _start(self) -> None:
        self._checked("start-runtime", self._runtime_arguments())

    def _environment_names(self, environment: object) -> set[str] | None:
        if not isinstance(environment, list) or not all(
            isinstance(item, str) for item in environment
        ):
            return None
        return {item.split("=", 1)[0].upper() for item in environment}

    def _inspect_runtime(self) -> None:
        stage = "inspect-runtime"
        container = self._inspect(stage)
        try:
            config = container["Config"]
            host = container["HostConfig"]
            if not isinstance(config, dict) or not isinstance(host, dict):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ValidationFailure(stage) from None
        names = self._environment_names(config.get("Env"))
        environment = config.get("Env")
        security_opt = host.get("SecurityOpt") or []
        mounts = container.get("Mounts") or []
        if (
            config.get("Image") != IMAGE
            or config.get("User") != "0:0"
            or tuple(config.get("Entrypoint") or ()) != ENTRYPOINT
            or config.get("Cmd") not in (None, [])
            or config.get("WorkingDir") != WORKDIR
            or not isinstance(names, set)
            or not isinstance(environment, list)
            or environment.count("BIND=0.0.0.0") != 1
            or names & FORBIDDEN_ENV_NAMES
            or any(name.startswith(FORBIDDEN_ENV_PREFIXES) for name in names)
            or host.get("Privileged") is not False
            or host.get("ReadonlyRootfs") is not False
            or host.get("CapDrop") != ["ALL"]
            or not isinstance(security_opt, list)
            or "no-new-privileges" not in security_opt
            or any(str(option).startswith("seccomp=") for option in security_opt)
            or host.get("NetworkMode") != "none"
            or host.get("PortBindings")
            or host.get("Binds")
            or host.get("Devices")
            or host.get("DeviceRequests")
            or host.get("VolumesFrom")
            or mounts
            or container.get("RestartCount") != 0
        ):
            raise ValidationFailure(stage)

    def _probe_source(self, expectation: str) -> str:
        """Return a fixed Node probe that emits nothing and never exposes bodies."""
        checks = {
            "root": "status === 200",
            "config-true": (
                "status === 200 && configServices(payload) && "
                "firstLaunch(payload) === true"
            ),
            "fingerprint": "status === 500",
            "first-launch": "status === 200",
            "config-false": (
                "status === 200 && configServices(payload) && "
                "firstLaunch(payload) === false"
            ),
        }
        path = {
            "root": "/",
            "config-true": "/api/config",
            "fingerprint": "/proxy/metal-hub/fingerprint",
            "first-launch": "/api/config/first-launch",
            "config-false": "/api/config",
        }[expectation]
        method = "POST" if expectation == "first-launch" else "GET"
        body = FIRST_LAUNCH_BODY if expectation == "first-launch" else ""
        condition = checks[expectation]
        if expectation in ("config-true", "config-false"):
            response_handler = (
                "let data='';res.on('data',chunk=>data+=chunk);"
                "res.on('end',()=>{let payload;"
                "try{payload=data?JSON.parse(data):null;}catch{process.exit(1);return;}"
                "const status=res.statusCode;process.exit("
                + f"{condition}?0:1"
                + ");});"
            )
        else:
            response_handler = (
                "res.resume();res.on('end',()=>{const status=res.statusCode;"
                "process.exit(" + f"{condition}?0:1" + ");});"
            )
        return (
            "const http=require('http');"
            "const body=" + json.dumps(body) + ";"
            "const configServices=value=>{const services=value&&value.config"
            "&&value.config.servicesUrls;if(!services||typeof services!=='object'"
            "||Array.isArray(services))return false;return services.keystone==="
            "'/identity'&&services.barbican==='/barbican'&&services.coriolis==="
            "'/coriolis'&&services.coriolisLogs==='/logs'&&services."
            "coriolisLogStreamBaseUrl===''&&services.coriolisLicensing==="
            "'/licensing'&&services.metalhub==='/metal-hub';};"
            "const firstLaunch=value=>value&&typeof value==='object'"
            "?value.isFirstLaunch:undefined;"
            "const req=http.request({host:'127.0.0.1',port:3000,path:"
            + json.dumps(path)
            + ",method:"
            + json.dumps(method)
            + ",headers:body?{'content-type':'application/json',"
            "'content-length':Buffer.byteLength(body)}:{}},res=>{"
            + response_handler
            + "});req.on('error',()=>process.exit(1));req.end(body);"
        )

    def _probe(self, stage: str, expectation: str) -> bool:
        try:
            result = self.runner(
                self._docker(
                    "exec",
                    self.resources.container,
                    "node",
                    "-e",
                    self._probe_source(expectation),
                ),
                self.timeout,
            )
        except (OSError, subprocess.SubprocessError):
            raise ValidationFailure(stage) from None
        return result.returncode == 0

    def _wait_for_probe(self, stage: str, expectation: str) -> None:
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            if self._probe(stage, expectation):
                return
            self.sleeper(POLL_INTERVAL)
        raise ValidationFailure(stage)

    def _stability(self) -> None:
        deadline = self.clock() + STABILITY_INTERVAL
        while self.clock() < deadline:
            container = self._inspect("runtime-stability")
            state = container.get("State")
            if (
                not isinstance(state, dict)
                or state.get("Running") is not True
                or container.get("RestartCount") != 0
            ):
                raise ValidationFailure("runtime-stability")
            self.sleeper(POLL_INTERVAL)
        self._inspect_runtime()

    def _stop(self) -> None:
        stage = "stop-runtime"
        started = self.clock()
        self._checked(
            stage,
            self._docker("stop", "--time", str(STOP_TIMEOUT), self.resources.container),
        )
        if self.clock() - started > STOP_COMPLETION_BOUND:
            raise ValidationFailure(f"{stage}-slow")
        container = self._inspect(stage)
        state = container.get("State")
        if (
            not isinstance(state, dict)
            or state.get("Running") is not False
            or state.get("ExitCode") != 1
            or container.get("RestartCount") != 0
        ):
            raise ValidationFailure(f"{stage}-exit-code")

    def _remove(self) -> None:
        self._checked(
            "remove-runtime", self._docker("rm", "-f", self.resources.container)
        )

    def _cleanup(self) -> None:
        try:
            self.runner(
                self._docker("container", "rm", "-f", self.resources.container),
                self.timeout,
            )
        except (OSError, subprocess.SubprocessError):
            self.report("CLEANUP container")

    def _verify_cleanup(self) -> None:
        for stage, command in (
            (
                "cleanup-container-leftovers",
                self._docker("ps", "-a", "--format={{.Names}}"),
            ),
            (
                "cleanup-network-leftovers",
                self._docker("network", "ls", "--format={{.Name}}"),
            ),
            (
                "cleanup-volume-leftovers",
                self._docker("volume", "ls", "--format={{.Name}}"),
            ),
        ):
            try:
                result = self.runner(command, self.timeout)
            except (OSError, subprocess.SubprocessError):
                raise ValidationFailure(stage) from None
            if result.returncode != 0 or any(
                line.startswith(PREFIX) for line in result.stdout.splitlines()
            ):
                raise ValidationFailure(stage)

    def _run_body(self) -> None:
        self._stage(
            "docker-cli-daemon",
            lambda: self._checked("docker-cli-daemon", self._docker("info")),
        )
        self._stage(
            "image-available",
            lambda: self._checked(
                "image-available", self._docker("image", "pull", IMAGE)
            ),
        )
        self._stage("image-contract", self._verify_image_contract)
        self._stage("start-runtime", self._start)
        self._stage("inspect-runtime", self._inspect_runtime)
        self._stage("get-root", lambda: self._wait_for_probe("get-root", "root"))
        self._stage(
            "get-config-first-launch",
            lambda: self._wait_for_probe("get-config-first-launch", "config-true"),
        )
        self._stage(
            "get-metal-hub-fingerprint",
            lambda: self._wait_for_probe("get-metal-hub-fingerprint", "fingerprint"),
        )
        self._stage(
            "post-first-launch",
            lambda: self._wait_for_probe("post-first-launch", "first-launch"),
        )
        self._stage(
            "get-config-configured",
            lambda: self._wait_for_probe("get-config-configured", "config-false"),
        )
        self._stage("runtime-stability", self._stability)
        self._stage("stop-runtime", self._stop)
        self._stage("remove-runtime", self._remove)
        self._stage("start-runtime-recreated", self._start)
        self._stage("inspect-runtime-recreated", self._inspect_runtime)
        self._stage(
            "get-config-recreated",
            lambda: self._wait_for_probe("get-config-recreated", "config-true"),
        )
        self._stage("stop-runtime-recreated", self._stop)

    def run(self) -> int:
        started = self.clock()
        failure: ValidationFailure | None = None
        try:
            self._run_body()
        except ValidationFailure as error:
            failure = error
        except Exception:
            failure = ValidationFailure("internal")
        finally:
            self._cleanup()
            try:
                self._verify_cleanup()
            except ValidationFailure as error:
                failure = error
        elapsed = self.clock() - started
        if failure is not None:
            self.report(f"FAIL {failure.stage}")
            self.report(f"SUMMARY runtime failed {elapsed:.3f}")
            return 1
        self.report(f"SUMMARY runtime passed {elapsed:.3f}")
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Disposable local Coriolis web runtime evidence."
    )
    parser.add_argument(
        "--run", action="store_true", help="perform disposable evidence"
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("--run is required; this performs disposable runtime evidence")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return Validator(
        repository_root=Path(__file__).resolve().parents[1], timeout=args.timeout
    ).run()


if __name__ == "__main__":
    sys.exit(main())
