# Image builds

Build on the workstation, publish to Docker Hub, and deploy manually with [Guide 3](../guides/3-daily-usage.md). The VM runs published images and keeps only the Athenaeum source checkout for Compose and host tools.

## Repositories and tags

| Build project | Source recipe | Moving images | Retained version examples |
| --- | --- | --- | --- |
| `athenaeum` | `athenaeum/docker/compose.yaml` | `valentemath/athenaeum:latest`, `:latest-edge` | `:v0.1.0`, `:v0.1.0-edge` |
| `quacktuaries` | `quacktuaries/docker/compose.yaml` | `valentemath/quacktuaries:latest`, `:latest-athenaeum` | `:v0.1.0`, `:v0.1.0-athenaeum` |

The projects have independent version counters. Each build publishes both of its image lines at one version. Quacktuaries' standalone image serves at the root path; the hosted variant defaults to production mode and `/quacktuaries`. Secrets and proxy trust are runtime configuration.

## Shared Fish builder

The workstation's `build` function reads `~/.config/builder/builds.yaml` and records successfully published versions in the neighboring `versions.txt`. Each app has its own entry. These entries use the shared `compose-release` recipe and `semver` resolver:

```yaml
projects:
  athenaeum:
    description: Athenaeum ARM64 site and edge images
    directory: athenaeum/docker
    recipe: compose-release
    image: valentemath/athenaeum
    primary_component: app
    components:
      app:
        resolver: semver
        default_bump: patch
        build_arg: ATHENAEUM_VERSION
        build_transform: strip-v
    outputs:
      site:
        source_tag: latest
        tags: latest {app}
      edge:
        source_tag: latest-edge
        tags: latest-edge {app}-edge
  quacktuaries:
    description: Quacktuaries standalone and Athenaeum image lines
    directory: quacktuaries/docker
    recipe: compose-release
    image: valentemath/quacktuaries
    primary_component: app
    components:
      app:
        resolver: semver
        default_bump: patch
        build_arg: QUACKTUARIES_VERSION
        build_transform: strip-v
    outputs:
      standalone:
        source_tag: latest
        tags: latest {app}
      athenaeum:
        source_tag: latest-athenaeum
        tags: latest-athenaeum {app}-athenaeum
```

Merge these entries into the shared registry's existing `projects` mapping. Directories are resolved by the shared builder's development-directory lookup. Tailgate uses the same multiple-output pattern. The shared build function/configuration is workstation tooling, separate from this repository.

## Build and publish

Create or reuse the public Docker Hub repositories above. In your normal workstation Fish shell:

```fish
docker login --username valentemath
build --dry-run --version v0.1.0 athenaeum
build --version v0.1.0 athenaeum
build --dry-run --version v0.1.0 quacktuaries
build --version v0.1.0 quacktuaries
```

Use unused versions for first releases; subsequent `build athenaeum` or `build quacktuaries` increments only that project's patch version. `--no-push --version v0.1.0` tests locally without publication or changing recorded version state. `--rebuild` reuses the recorded version for a retry. Preserve published versions referenced by backups.

The release recipes target ARM64. On AMD64, Quacktuaries executes ARM Python during build and needs QEMU/binfmt support. If `/proc/sys/fs/binfmt_misc/qemu-aarch64` is absent, register it for this boot:

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

Quacktuaries can target AMD64 with `QUACKTUARIES_PLATFORM=linux/amd64 build quacktuaries`. These recipes publish single-architecture tags; do not overwrite Oracle's tags with a different architecture. [Docker cross-platform builds](https://docs.docker.com/build/building/multi-platform/).

Build inputs include current uncommitted files. Run the relevant [site tests](site-development.md) and [local classroom workflow](local-stack.md), commit source, and finish all pushes before deploying. Record source commits and image digests in private recovery notes. The builder advances its version record only after every push for that project succeeds.

## Build without the Fish helper

The Compose recipes also work directly. For example, from the Athenaeum checkout in Bash, using a new release version:

```bash
ATHENAEUM_VERSION=0.1.0 docker compose -f docker/compose.yaml build --build-arg ATHENAEUM_VERSION=0.1.0
docker tag valentemath/athenaeum:latest valentemath/athenaeum:v0.1.0
docker tag valentemath/athenaeum:latest-edge valentemath/athenaeum:v0.1.0-edge
docker push valentemath/athenaeum:latest
docker push valentemath/athenaeum:v0.1.0
docker push valentemath/athenaeum:latest-edge
docker push valentemath/athenaeum:v0.1.0-edge
```

For Quacktuaries, run its own `docker/compose.yaml` with `QUACKTUARIES_VERSION`, then tag/push both `latest` and `latest-athenaeum` with the matching version suffixes. Direct Docker commands do not update the shared Fish version state; reconcile that state before returning to the helper. During disaster recovery, prefer pulling the retained image digest that matches the backup over rebuilding historical dependencies.
