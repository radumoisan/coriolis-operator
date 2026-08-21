# Coriolis template source

The six non-sensitive `.j2` configuration templates, `coriolis.conf.j2`, and
the 16 `providers/*.conf.j2` fragments are immutable verbatim copies of
`coriolis-docker/coriolis_ansible/roles/coriolis/common/templates/`.
They are licensed under Apache License 2.0; the localized license text is in
`LICENSE.apache-2.0`. The upstream `coriolis-docker` distribution has no
`NOTICE` file.

`kubernetes/coriolis.conf.j2` and `kubernetes/wsgi-coriolis.conf.j2` are
explicit Kubernetes-derived variants. Their only policy delta is internal
plaintext transport: RabbitMQ uses port 5672 with `ssl = False` and no CA file;
Keystone uses HTTP on port 5000 with no CA files; Apache listens and advertises
HTTP without loading or configuring TLS. All other upstream content is retained.
