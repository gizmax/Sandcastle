# MCP Tunnels for Managed Agents

Anthropic's 2026-05-19 update added **MCP tunnels**, a gated research
preview that lets a hosted agent reach Model Context Protocol servers
running inside your private network without inbound firewall rules.

Anthropic spec: <https://platform.claude.com/docs/en/managed-agents/mcp-tunnels>

## What MCP tunnels solve

The classic problem: your MCP server (the Jira mirror, the data warehouse
gateway, the Memory MCP server filling the `memory_stores` gap) lives
behind a corporate firewall with no public ingress. Anthropic's agent
needs to invoke it, but you can't poke holes for arbitrary Anthropic
egress IPs and you can't put the MCP server on the open internet.

A tunnel is outbound-only: a Cloudflared sidecar inside your boundary
dials Anthropic's tunnel ingress and keeps the connection alive. Anthropic
multiplexes agent-to-MCP traffic over that connection. No inbound rule
ever opens.

## Access gating

The preview is gated. Sign up:
<https://claude.com/form/claude-managed-agents>. After Anthropic provisions
your tunnel, you receive a `tunnel_id` and either a workload-identity
federation (WIF) trust policy or a manual tunnel-token bundle.

Sandcastle refuses to spawn the cloudflared subprocess unless the operator
has set `SANDCASTLE_MCP_TUNNEL_ENABLED=1`. The gating function lives in
`src/sandcastle/engine/mcp_tunnel.py::assert_enabled`. This is intentional;
the beta header on the Messages API is a kill switch Anthropic can flip
and we want the failure mode to be obvious before the API starts 4xx-ing.

## Three-layer security model

Anthropic's threat model
(<https://platform.claude.com/docs/en/managed-agents/mcp-tunnels#security>)
stacks three independent layers. Sandcastle preserves all three.

| Layer    | Where           | What it protects                        | Configured via                  |
|----------|------------------|------------------------------------------|----------------------------------|
| Outer    | Cloudflared <-> Anthropic | Tunnel identity, mTLS               | `auth_mode` (WIF or manual cert)  |
| Inner    | Anthropic <-> MCP server  | Hop confidentiality, TLS            | Cloudflared TLS, no extra config |
| Upstream | MCP server <-> backend    | Per-server OAuth / API auth          | `MCPTunnelServer.auth_token_env` |

Two of the three are owned end-to-end by us; the middle hop is owned by
Cloudflare-as-Anthropic-subprocessor (see gotchas below).

## Auth mode comparison

| Mode                                  | When to use                            | Long-lived secrets? | Rotation                    |
|---------------------------------------|----------------------------------------|---------------------|-----------------------------|
| `workload_identity_federation` (WIF)  | Default; recommended                   | No                  | Automatic via your IdP      |
| `manual_cert`                         | Air-gapped clusters, no OIDC IdP       | Yes (tunnel token + customer CA) | Manual, you own it |

WIF maps a Kubernetes ServiceAccount or cloud-vendor identity to the
tunnel through your OIDC issuer; cloudflared exchanges its workload
identity for a short-lived Anthropic credential. No long-lived secret
lives on disk. Anthropic strongly prefers this mode and so do we.
Manual mode exists for environments without an OIDC issuer.
`validate_tunnel_config` in `mcp_tunnel.py` requires both
`tunnel_token_file` and `ca_cert_file` when `auth_mode = manual_cert`.

## Sandcastle wiring

Attach a tunnel to a workflow:

```yaml
mcp_tunnel:
  tunnel_id: tunnel_abc123
  auth_mode: workload_identity_federation
  servers:
    - name: memory
      hostname: memory.acme-tunnel.anthropic.cloud
      auth_token_env: SANDCASTLE_MEMORY_MCP_TOKEN
      allowed_tools:
        - add
        - search
        - forget
        - list_memories
    - name: internal-jira
      hostname: jira.acme-tunnel.anthropic.cloud
      auth_token_env: ACME_JIRA_BEARER
```

This parses into `MCPTunnelConfig` (`src/sandcastle/engine/mcp_tunnel.py`).
`build_mcp_servers_block(config)` produces the `mcp_servers` array Anthropic's
Messages API expects (spec:
<https://platform.claude.com/docs/en/api/messages#mcp_servers>). Each
`auth_token_env` is resolved against process env at request time; bearer
values never appear in YAML or git history. `build_cloudflared_args(config)`
emits the cloudflared subprocess arguments for the sidecar.

## Deployment via Helm

The reference chart lives in `deploy/mcp-tunnel/memory-mcp/`.

```bash
helm install memory-mcp-tunnel ./deploy/mcp-tunnel/memory-mcp \
  --set tunnelId=tunnel_abc123 \
  --set authMode=workload_identity_federation \
  --namespace sandcastle
```

The chart deploys two pods: the Memory MCP server itself and a
cloudflared sidecar. The sidecar uses WIF against your in-cluster OIDC
issuer (typically the Kubernetes API server). Both pods share a private
ClusterIP service; only the sidecar has the tunnel credential.

## The Memory MCP server pattern

Sandcastle ships a first-party Memory MCP server because of the
`memory_stores` gap in self-hosted sandboxes (see
[managed-agents-self-hosted.md](./managed-agents-self-hosted.md#the-4-hard-limits)).
Anthropic disclaims `memory_stores` whenever a session carries a
self-hosted environment block; the in-product Memory tool simply will not
bind. That's a regression if you were relying on Anthropic's hosted
memory.

The fix composes cleanly: run our Memory MCP server inside the same
boundary that runs your self-hosted sandboxes, expose it on the tunnel,
and let agents reach it via MCP. The server wraps Sandcastle's existing
mem0 + fastembed + Qdrant stack (see [memory-mcp-server.md](./memory-mcp-server.md)),
so the storage and retrieval semantics are the same as hosted memory
plus GDPR right-to-be-forgotten via the `forget` tool. The data never
leaves your jurisdiction.

End-to-end shape:

```
agent (Anthropic) -> mcp_servers[memory] -> tunnel -> cloudflared sidecar
   -> Memory MCP server -> mem0 -> Qdrant (your disk)
```

## Gotchas

- **Cert rotation.** WIF tokens auto-rotate via your IdP. Manual-mode
  tunnel tokens do not; rotate on the same cadence as your other
  long-lived production secrets (90 days is a sane default).
- **Cloudflare is an Anthropic subprocessor with no separate SLA.** The
  middle hop sits on Cloudflare's network under Anthropic's contract.
  There is no separate Cloudflare SLA you can lean on. For regulated
  EU workloads, document this in your DPIA. Cite:
  <https://www.anthropic.com/legal/subprocessors>.
- **Tunnel-token compromise = catastrophic.** A leaked manual tunnel
  token lets the holder impersonate your tunnel and intercept MCP
  traffic. Treat token loss the way you'd treat a root key compromise:
  revoke immediately via the Anthropic console, issue a new tunnel,
  rotate every upstream OAuth credential on every server behind it,
  audit the full request log Anthropic retains for the tunnel.
- **`allowed_tools` is enforced server-side at the agent layer**, not at
  the tunnel. The tunnel forwards everything the MCP server advertises.
  Don't rely on `allowed_tools` for security boundaries; rely on what
  the MCP server itself implements.
- **Hostname shape matters.** Cloudflared validates against the
  per-tunnel subdomain Anthropic issues. Plain hostnames with no dot are
  rejected (`validate_tunnel_config` catches this before spawn).
