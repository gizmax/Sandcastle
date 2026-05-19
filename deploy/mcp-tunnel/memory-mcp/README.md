# memory-mcp Helm chart + docker-compose

Deploys the Sandcastle **Memory MCP server** behind a **Cloudflare MCP tunnel**
(Anthropic Managed Agents, feature shipped 2026-05-19). The tunnel is
*outbound-only*, so no inbound ingress, public IP, or LB is required, which
makes this safe to install inside an EU enterprise VPC.

Stack:

- `memory-mcp-server` - the MCP server container (Memory tools backed by Qdrant)
- `cloudflared` - sidecar that opens an outbound tunnel to the Anthropic edge
- `qdrant` - persistent vector store (StatefulSet with a 10Gi PVC by default)

## 1. Request MCP tunnels access

MCP tunnels are gated behind the Managed Agents waitlist:

  https://claude.com/form/claude-managed-agents

Wait for the activation email before continuing.

## 2. Create a tunnel

Two options:

a) Anthropic Console -> Settings -> MCP Tunnels -> **New tunnel** -> copy the
   `tunnel_id` and (for manual auth) the bootstrap token.

b) CLI:

   ```
   ant beta:tunnel create --name memory-mcp-prod
   # prints: tunnel_id=cf-tnl_01J...   token=eyJhbG...
   ```

Store the token in a Secret (manual mode) or set up Workload Identity
Federation (WIF) for the projected service-account token (recommended).

## 3a. Helm install

```
# Workload Identity Federation (recommended):
helm install memory-mcp ./memory-mcp \
  --namespace sandcastle --create-namespace \
  --set cloudflared.tunnelId=cf-tnl_01J... \
  --set cloudflared.auth.mode=wif \
  --set cloudflared.auth.wif.audience=anthropic-mcp-tunnel

# Or manual token:
kubectl -n sandcastle create secret generic anthropic-tunnel-token \
  --from-literal=token=eyJhbG...
kubectl -n sandcastle create secret generic memory-mcp-env \
  --from-literal=SANDCASTLE_ENV_KEY=$(openssl rand -hex 32)
helm install memory-mcp ./memory-mcp \
  --namespace sandcastle \
  --set cloudflared.tunnelId=cf-tnl_01J... \
  --set cloudflared.auth.mode=manual
```

## 3b. docker-compose (single host)

```
export SANDCASTLE_ENV_KEY=$(openssl rand -hex 32)
export ANTHROPIC_TUNNEL_TOKEN=eyJhbG...
docker compose up -d
docker compose ps
```

## 4. Verify

```
kubectl -n sandcastle port-forward svc/memory-mcp 8080:8080
curl -fsS http://localhost:8080/healthz   # -> {"status":"ok"}
kubectl -n sandcastle logs deploy/memory-mcp -c cloudflared | grep "Registered tunnel"
```

The Anthropic Console shows tunnel state as **connected** within ~30 seconds.

## 5. Use from a Sandcastle workflow

```yaml
mcp_tunnel:
  servers:
    memory:
      tunnel_id: cf-tnl_01J...
      tools: ["memory.search", "memory.upsert"]

steps:
  - id: recall
    type: tool
    tool: memory.search
    args:
      query: "{{ inputs.topic }}"
```

The agent reaches the server through Anthropic's edge over your private tunnel;
no inbound ports are opened on your cluster.

## Notes

- `networkPolicy.enabled=true` restricts egress to Cloudflare edge ranges
  (`198.41.192.0/19`, `2606:4700:a0::/44`) on TCP+UDP `7844`, plus `:443` to
  `api.anthropic.com`.
- Qdrant takes hourly snapshots; back the PVC with your usual volume-snapshot
  schedule for off-site retention.
- All containers run with `runAsNonRoot`, `readOnlyRootFilesystem`, and all
  capabilities dropped. Adjust `containerSecurityContext` only if a tool needs
  to write outside `/tmp`.
