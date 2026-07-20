# Cloudflare Sandbox Worker Cookbook

Deploy Sandcastle's Cloudflare Sandbox Worker from `cf-sandbox-worker/`.
The Worker provides `/health` and `/run` endpoints and starts isolated
Cloudflare Sandbox containers for agent runner scripts.

## 1. Install and log in

```sh
cd cf-sandbox-worker
npm ci
npx wrangler login
```

Pick the Cloudflare account that will own the Worker. `wrangler whoami` should
return that account.

## 2. Configure the worker

`cf-sandbox-worker/wrangler.jsonc` declares the `Sandbox` container, Durable
Object binding, and migration. Keep the `Sandbox` class and binding names in
sync; they match the binding used by `src/index.ts`. Adjust `name` and
`containers[0].max_instances` for your deployment as needed.

To require authentication on `/run`, store the optional shared secret:

```sh
npx wrangler secret put SANDBOX_SHARED_SECRET
```

The secret is scoped to the Worker and is never written to the Container
image.

## 3. wrangler deploy

```sh
npx wrangler deploy
```

`wrangler` builds `./Dockerfile` for the Containers runtime, uploads the
image, and binds the `Sandbox` Durable Object class. First deploy
takes ~2 minutes; subsequent layer-cached deploys are seconds.

## 4. Configure Sandcastle and verify

Set Sandcastle to use the deployed Worker URL:

```sh
SANDBOX_BACKEND=cloudflare
CLOUDFLARE_WORKER_URL=https://sandbox.your-domain.workers.dev
```

Confirm the deployment responds to its health endpoint:

```sh
curl https://sandbox.your-domain.workers.dev/health
```

It should return `{"ok":true}`.
