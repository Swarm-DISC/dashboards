# Dashboards built with panel

Images published at https://github.com/Swarm-DISC/dashboards/pkgs/container/dashboards

## Usage

Create and use persistent directory used by diskcache between runs:
```
mkdir cache
chmod 777 cache
```

Create a environment file to store secrets and add an access token
```
# .env
VIRES_TOKEN=...
```

## Development

Build and run locally:
```
podman build . -t dashboards
podman run --rm -it -p 5006:5006 -v ./cache:/home/panel-user/cache --env-file .env dashboards bash -c "panel serve /root/dashboards/*ipynb --warm"
```

To update `environment-exact.txt`:
```
mamba env create --name dashboards --file environment.yml
mamba list --name dashboards --explicit > environment-exact.txt
```
