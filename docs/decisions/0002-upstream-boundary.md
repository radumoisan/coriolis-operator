# 0002: Preserve the Upstream Boundary

## Context

The appliance is composed from existing Coriolis components.

## Decision

Treat existing Coriolis component repositories and images as immutable upstream inputs. Retain the current appliance runtime shape.

## Consequences

This project manages and configures the appliance without modifying or rebuilding upstream component artifacts.
