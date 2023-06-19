# Dashboards built with panel

To build the image and deploy:
```
sudo docker build . -t dashboards
sudo docker-compose up
```

To run locally with podman:
```
podman build . -t dashboards
podman run --rm -it -p 5006:5006 -v ./src:/home/panel-user/src dashboards panel serve src/geomagnetic-model-explorer.ipynb --allow-websocket-origin=* --warm
```

To update `environment-exact.txt`:
```
mamba env create --name dashboards --file environment.yml
mamba list --name dashboards --explicit > environment-exact.txt
```
