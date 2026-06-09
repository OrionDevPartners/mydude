# Reaching Local LLM Nodes over Cloudflare Mesh

MyDude can route inference to local model servers (Ollama, MLX) running on a
separate machine, reachable through Cloudflare's WARP-to-WARP Mesh. This gives
a cloud-deployed MyDude a stable, private path to your local GPU box without
opening public ports or running a VPN/bastion.

**Everything below runs on your hardware, not on Replit.**

---

## How it works

Each machine enrolled in Cloudflare WARP (with WARP-to-WARP enabled in your
Zero Trust policy) gets a stable private address in the `100.96.x.x` range.
MyDude reaches the local model server over TCP on that address, the same way it
would reach localhost — but over the Mesh tunnel instead.

The only two things that change in MyDude's config are:

1. The base URL for the local provider (`OLLAMA_BASE_URL` / `MLX_BASE_URL`):
   point it at the node's Mesh IP instead of localhost.
2. The TCP probe timeout (`OLLAMA_PROBE_TIMEOUT` / `LOCAL_PROBE_TIMEOUT`):
   raise it to accommodate cross-network latency (Mesh adds tens of
   milliseconds; the localhost default of 0.5 s is too tight).

---

## Step 1 — Enroll the model-server machine as a Mesh node

Install the Cloudflare WARP client on the machine that runs Ollama or MLX.
This is the headless ("warp-cli") path for Linux servers; macOS and Windows
have a GUI installer that works equivalently.

### Linux (Debian/Ubuntu)

```bash
# Add the Cloudflare package repo
curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg

echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] \
  https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/cloudflare-client.list

sudo apt update && sudo apt install cloudflare-warp

# Register with your Zero Trust organisation
warp-cli registration new

# When prompted, authenticate via the browser URL it prints.
# On a headless server, copy the URL to a desktop browser.

# Connect
warp-cli connect
```

### macOS

Download and install from <https://1.1.1.1/> or your Zero Trust dashboard, or:

```bash
brew install --cask cloudflare-warp
```

Open the WARP app, click the menu → Preferences → Account → Login with
Cloudflare Zero Trust, enter your team name.

### Confirm the tunnel is up

```bash
warp-cli status
# Expected: Status update: Connected
```

---

## Step 2 — Enable WARP-to-WARP in Zero Trust (one-time, admin)

In the Cloudflare Zero Trust dashboard:

1. Go to **Settings → WARP Client → Device settings**.
2. Edit (or create) the default profile.
3. Under **WARP-to-WARP**, toggle it **on**.
4. Save and deploy.

This allows enrolled devices to reach each other over the `100.96.x.x` range.

---

## Step 3 — Find the node's Mesh IP

On the model-server machine:

```bash
warp-cli tunnel ip
# Output: 100.96.x.y/32
```

Or:

```bash
ip addr show CloudflareWARP
# Look for the inet 100.96.x.y line
```

Note this IP — you will paste it into MyDude's environment variables.

---

## Step 4 — Start the model server on the node

### Ollama

```bash
# Install: https://ollama.com/download
ollama serve
# Listens on 0.0.0.0:11434 by default
```

Pull the model you want MyDude to use:

```bash
ollama pull llama3.2:3b
```

### MLX (Apple Silicon only)

```bash
pip install mlx-lm
mlx_lm.server --model mlx-community/Qwen3-14B-8bit --port 11435 --host 0.0.0.0
```

Confirm the server is reachable from the Replit side (or any other enrolled
Mesh node) by running this on another machine in the same Mesh:

```bash
curl http://100.96.x.y:11434/v1/models   # Ollama
curl http://100.96.x.y:11435/v1/models   # MLX
```

---

## Step 5 — Set the MyDude environment variables

In Replit Secrets (or your deployment environment), set:

| Variable | Value | Purpose |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://100.96.x.y:11434/v1` | Points MyDude at the Mesh node |
| `MLX_BASE_URL` | `http://100.96.x.y:11435/v1` | Points MyDude at the Mesh node |
| `LOCAL_PROBE_TIMEOUT` | `2.0` | Shared probe timeout for all local providers |
| `OLLAMA_PROBE_TIMEOUT` | `2.0` | Per-provider override (takes precedence over shared) |
| `MLX_PROBE_TIMEOUT` | `2.0` | Per-provider override |

You only need to set the variables for providers you are actually using.
`LOCAL_PROBE_TIMEOUT` covers both if you set it once.

A timeout of 2.0 s is conservative. If the Mesh round-trip is consistently
under 100 ms you can use 0.5 s; if you are seeing false-down probes, raise it.

---

## Step 6 — Verify in the MyDude dashboard

1. Open **Governance Center** (`/governance`).
2. Under **Jurisdiction Routing**, find the `ollama` and `mlx` rows.
3. The **Endpoint** column shows the configured Mesh URL.
4. The **Secret / server** column shows **server up** (green) or **server down** (red).

If the status shows **server down** despite the node running:
- Confirm `warp-cli status` is **Connected** on the node.
- Confirm the model server is bound to `0.0.0.0` (not `127.0.0.1`).
- Try `curl http://<mesh-ip>:<port>/v1/models` from another Mesh node.
- Raise `LOCAL_PROBE_TIMEOUT` to `3.0` or `5.0` if the link is slow.

---

## Graceful behaviour when no Mesh node is configured

When `OLLAMA_BASE_URL` / `MLX_BASE_URL` are not set, the defaults
(`http://localhost:11434/v1` and `http://localhost:11435/v1`) are used. The TCP
probe will fail immediately (nothing is listening on localhost in a cloud
deployment), so these providers are silently excluded from the swarm fanout —
the same behaviour as before this feature was added. Cloud routing is unaffected.

When the Mesh node goes down (probe fails), the same exclusion applies: the
provider is dropped from the current fanout and cloud providers handle the
request. The Governance Center shows **server down** so you can see the link
status without inferring it from missing results.

---

## Security notes

- Cloudflare Mesh traffic is encrypted in transit by WARP's WireGuard tunnel.
- The model server port (`11434`, `11435`) is not exposed publicly — it is only
  reachable on the `100.96.x.x` overlay network by other enrolled devices.
- MyDude does not open any inbound port on the Replit side; it only initiates
  outbound TCP connections to the Mesh IP.
- Do not set `OLLAMA_BASE_URL` to a public IP unless you have independent
  authentication in front of the model server.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Governance shows **server down** | `warp-cli status` on node; server bound to `0.0.0.0`? |
| Probe passes but inference fails | Firewall rule blocking port 11434/11435 on node? |
| Mesh IP changes after reboot | Add a static WARP device profile in Zero Trust dashboard |
| Both local providers always blocked | `cloud_shift` is OFF — check `/governance` kill switch state |
| High probe latency causing timeouts | Raise `LOCAL_PROBE_TIMEOUT` to `3.0` or higher |
