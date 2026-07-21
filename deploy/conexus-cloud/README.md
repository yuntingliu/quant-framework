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
connection. `dashboard\start.ps1` starts a restricted reverse SSH tunnel so the
cloud Tool runtime can call that API without exposing port 8000 publicly.
Publisher-funded model calls use the server-side OpenRouter key and are pinned
to `moonshotai/kimi-k3`; the key is never packaged in the Harness or frontend.
The Agent has no host-imposed iteration, output-token, or Harness deadline
ceiling; it runs until it calls `complete` or the user cancels the run.

Normal workstation startup:

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
