# Dashboards built with panel

Images published at https://github.com/Swarm-DISC/dashboards/pkgs/container/dashboards

## Usage

Create and use persistent directory used by diskcache between runs
```
mkdir cache
chmod 777 cache
```
then pass that as a volume to the container:
```
docker run --rm -ti --platform linux/amd64 -p 80:5006 -v ./cache:/home/panel-user/cache ghcr.io/swarm-disc/dashboards:main
```

Or copy the `docker-compose.yml` from here and modify it to control the behaviour and use `docker compose up` to start the service.

References:
- https://panel.holoviz.org/how_to/server/commandline.html
- https://docs.docker.com/engine/reference/commandline/compose/

## Development

Build and run locally:
```
podman build . -t dashboards
podman run --rm -it -p 5006:5006 -v ./cache:/home/panel-user/cache dashboards panel serve src/geomagnetic-model-explorer.ipynb --warm
```

To update `environment-exact.txt`:
```
mamba env create --name dashboards --file environment.yml
mamba list --name dashboards --explicit > environment-exact.txt
```
