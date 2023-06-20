# Dashboards built with panel

Run published image:
```
docker run --rm -ti --platform linux/amd64 -p 80:5006 ghcr.io/swarm-disc/dashboards:main
```

Create and use persistent directory used by diskcache between runs
```
mkdir cache
chmod 777 cache
```
then pass that as a volume to the container:
```
docker run --rm -ti --platform linux/amd64 -p 80:5006 -v ./cache:/home/panel-user/cache ghcr.io/swarm-disc/dashboards:main
```

Build and run locally:
```
podman build . -t dashboards
podman run --rm -it -p 5006:5006 -v ./src:/home/panel-user/src dashboards panel serve src/geomagnetic-model-explorer.ipynb --allow-websocket-origin=* --warm
```

To update `environment-exact.txt`:
```
mamba env create --name dashboards --file environment.yml
mamba list --name dashboards --explicit > environment-exact.txt
```
