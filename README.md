# Dashboards built with panel

To build the image and deploy:
```
docker build . -t dashboards
sudo docker-compose up
```

To run locally:
```
docker run --rm -it -p 5006:5006 -v ./src:/home/panel-user/src dashboards panel serve src/* --allow-websocket-origin=* --warm
```

To update `environment-exact.txt`:
```
mamba env create --name dashboards --file environment.yml
mamba list --name dashboards --explicit > environment-exact.txt
```
