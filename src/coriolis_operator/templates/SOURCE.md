# Coriolis template source

The six non-sensitive `.j2` configuration templates, `coriolis.conf.j2`, and
the 16 `providers/*.conf.j2` fragments are immutable verbatim copies of
`coriolis-docker/coriolis_ansible/roles/coriolis/common/templates/`.
They are licensed under Apache License 2.0; the localized license text is in
`LICENSE.apache-2.0`. The upstream `coriolis-docker` distribution has no
`NOTICE` file.

`kubernetes/coriolis.conf.j2` and `kubernetes/wsgi-coriolis.conf.j2` are
explicit Kubernetes-derived variants. Their policy deltas are internal
plaintext transport: RabbitMQ uses port 5672 with `ssl = False` and no CA file;
Keystone uses HTTP on port 5000 with no CA files; Apache listens and advertises
HTTP without loading or configuring TLS. Kubernetes also enforces stderr-only
application logging: `kubernetes/coriolis.conf.j2` renders `debug` from the
per-appliance validated setting and sets `log_dir =` empty,
`use_syslog = false`, and `use_stderr = true`, so all core Coriolis services
log exclusively to stderr for the container log pipeline and never write
in-container log files. All other upstream content is retained.
