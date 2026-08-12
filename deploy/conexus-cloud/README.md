# AlphaLab Conexus cloud deployment

This deployment runs beside the existing server workspace and does not share
its container, project directory, volume, port, or administrator session.

- Public origin: `https://alphalab.43.154.239.41.nip.io`
- Compose project: `conexus-alphalab`
- Server directory: `/opt/conexus/alphalab`
- Loopback Web Host port: `3100`
- Loopback AlphaLab tunnel port: `18000`
- Local tunnel key: `%USERPROFILE%\.ssh\alphalab_conexus_ed25519`

The public Harness is anonymous and publisher-funded. AlphaLab's FastAPI
backend remains local because it owns the local database and RiceQuant data
connection. Start the restricted reverse SSH tunnel explicitly when the cloud
Tool runtime needs to call that API without exposing port 8000 publicly.
Publisher-funded model calls use the server-side OpenRouter key and are pinned
to `openai/gpt-oss-120b`; the key is never packaged in the Harness or frontend.
The Agent has no host-imposed iteration, output-token, or Harness deadline
ceiling; it runs until it calls `complete` or the user cancels the run.

Cloud-connected workstation startup (use two terminals):

```powershell
cd E:\quant-framework
powershell -ExecutionPolicy Bypass -File .\scripts\start_alphalab_conexus_tunnel.ps1
```

```powershell
cd E:\quant-framework
powershell -ExecutionPolicy Bypass -File .\dashboard\start.ps1
```

Server status and logs:

```bash
sudo docker compose \
  --env-file /opt/conexus/alphalab/.env \
  -f /opt/conexus/alphalab/docker-compose.yml ps
sudo docker logs --tail 100 conexus-alphalab-web-1
```

Secrets remain in the server-side mode-600 `.env` and are not part of this
directory. The existing `conexus-digui` Compose project is not a dependency of
this deployment.

## Hosted Linux workstation

The hosted AlphaLab workstation keeps browser access and Conexus tool access on
separate loopback listeners:

- `127.0.0.1:8100`: browser-facing deployment app with Basic authentication.
- `127.0.0.1:8101`: private FastAPI listener for Conexus tools.
- Hong Kong `127.0.0.1:18000`: reverse-tunnel endpoint forwarded to local port
  `8101` over the authenticated SSH connection.

Install `alphalab-conexus-api.service` and `alphalab-hk-tunnel.service` as user
services. Never point the tool tunnel at port `8100`; doing so sends internal
tool requests through the browser authentication boundary and returns HTTP 401.
