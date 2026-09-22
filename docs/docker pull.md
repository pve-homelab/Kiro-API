# Docker pull

The CI pipeline publishes two images to the configured internal registry:

| Compose configuration | Image repository | Pull command |
| --- | --- | --- |
| Standard (`docker-compose.yml`) | `<registry>/<project>/kiro-api` | `docker pull <registry>/<project>/kiro-api:latest` |
| Bundled (`docker-compose.bundle.yml`) | `<registry>/<project>/kiro-api-bundle` | `docker pull <registry>/<project>/kiro-api-bundle:latest` |

Replace `<registry>/<project>` with the Harbor project or Artifactory Docker
repository path. For example:

```bash
docker login harbor.example.com
docker pull harbor.example.com/kiro/kiro-api:latest
docker pull harbor.example.com/kiro/kiro-api-bundle:latest
```

To use the pulled standard image with Compose:

```bash
IMAGE_REPOSITORY=harbor.example.com/kiro/kiro-api \
IMAGE_TAG=latest \
docker compose pull
IMAGE_REPOSITORY=harbor.example.com/kiro/kiro-api \
IMAGE_TAG=latest \
docker compose up -d
```

To use the pulled bundled image:

```bash
IMAGE_REPOSITORY=harbor.example.com/kiro/kiro-api-bundle \
IMAGE_TAG=latest \
docker compose -f docker-compose.bundle.yml pull
IMAGE_REPOSITORY=harbor.example.com/kiro/kiro-api-bundle \
IMAGE_TAG=latest \
docker compose -f docker-compose.bundle.yml up -d
```

The default branch also publishes immutable commit tags. Replace `latest`
with the CI commit-short-SHA tag when a pinned deployment is required.
