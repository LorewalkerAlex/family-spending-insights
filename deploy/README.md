# Docker deployment

This deployment keeps one authoritative API process and one Nginx web container:

```text
Internet
  -> host Nginx/Caddy (HTTPS + access control)
  -> 127.0.0.1:8080
  -> web container
       /api/* -> api:8765
       other  -> Desktop Web static files
                    |
                    -> ./data bind mount (single writer)
```

The Compose port is loopback-only by default. Do not change it to `0.0.0.0` until HTTPS and
authentication are in place. The current application API does not yet implement user login.

## 1. Server prerequisites

- Docker Engine with the Docker Compose plugin
- A host reverse proxy already terminating HTTPS for the domain
- One Linux user that owns the checkout and household data

The API must remain a single replica. Do not use `docker compose up --scale api=...`, and do not
run `jobs run-due` from a second container while the API is running.

## 2. Prepare configuration and data

From the repository directory on the server:

```bash
cp .env.example .env
id -u
id -g
```

Put those numeric IDs into `PUID` and `PGID` in `.env`. Also set the private email credentials if
email acquisition is enabled. Keep `.env` mode-restricted and never commit it.

> Current limitation: the production `serve` composition does not yet instantiate a concrete IMAP
> connector or start `SourceSupervisor`. The email variables are reserved, but this Docker stack
> does not yet download new statements automatically. Stored CMB evidence remains fully available.
> This is separate from Scheduled Input, whose continuous scheduler is active in this deployment.

Copy the complete Canonical data directory to the server before the first start. Its top level must
contain `manifest.json`, `evidence/`, `state/`, and optionally `derived/`:

```bash
rsync -a ./data/ user@example-server:/opt/family-spending/data/
ssh user@example-server 'chmod -R u=rwX,go= /opt/family-spending/data'
```

Run the transfer while no local or server process is writing the source data. Do not synchronize
this directory bidirectionally after cutover; the server becomes the only writable copy.

## 3. Build and start

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 api web
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/api/health
```

`docker compose up -d --build` is the normal deployment command: it builds missing/changed images,
creates both services, publishes the configured Web port, and leaves the stack supervised in the
background. `docker compose run --rm api ...` is reserved for optional one-off operator commands
such as `diagnose state`; it is not the long-running deployment entrypoint.

The API startup validates the storage manifest, rebuilds runtime state, and catches up all Scheduled
Input occurrences through the server's current date. The in-process scheduler then checks every 300
seconds. `TZ=Asia/Shanghai` is the default and should remain aligned with the household timezone.

## 4. Host reverse proxy

Proxy the HTTPS virtual host to the loopback-only Compose port. A minimal Nginx location is:

```nginx
location / {
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_pass http://127.0.0.1:8080;
}
```

Add access control at this host proxy before exposing the domain. HTTP Basic Auth is acceptable for
the private Desktop Web over HTTPS, but it is not a safe identity design for a released Mini Program.
The Mini Program should remain unpublished until WeChat login/session authorization is implemented.

Do not open ports `8765` or `8080` in the Alibaba Cloud security group. Only the host reverse
proxy's HTTPS port needs public ingress.

## 5. Updates

```bash
git pull --ff-only
docker compose up -d --build
docker compose ps
```

Compose replaces the application containers while preserving the bind-mounted `./data` directory.
The data is deliberately excluded from every image build.

## 6. Consistent backups

The filesystem unit of work can change several files during one logical mutation, so do not archive
the directory while the API is writing. For the current small dataset, use a brief stop around the
snapshot:

```bash
docker compose stop api
tar -C /opt/family-spending -czf /secure-backups/family-spending-$(date +%F-%H%M%S).tar.gz data
docker compose start api
```

Encrypt and copy backups to a separate storage account or Alibaba Cloud OSS, enable retention, and
periodically test restoration into a disposable directory. A backup is not a second live writer.
