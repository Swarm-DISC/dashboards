FROM condaforge/mambaforge:22.11.1-4

RUN apt-get update \
    && apt-get install unzip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# mamba environment
COPY environment-exact.txt .
RUN mamba install --file environment-exact.txt
# pip packages
COPY requirements.txt .
RUN /bin/bash -c 'source activate base && pip install -r requirements.txt'
# spacepy (had trouble installing it normally...)
COPY wheels/spacepy-0.2.3-cp310-cp310-linux_x86_64.whl .
RUN /bin/bash -c 'source activate base && pip install spacepy-0.2.3-cp310-cp310-linux_x86_64.whl'

ENV EOXMAGMOD_TAG_NAME=eoxmagmod-0.12.1

# (eoxmagmod) Magnetic Models and dependencies
RUN mkdir $HOME/build \
    && cd $HOME/build \
    && wget --no-verbose https://github.com/ESA-VirES/MagneticModel/archive/refs/tags/${EOXMAGMOD_TAG_NAME}.zip -O MagneticModel-${EOXMAGMOD_TAG_NAME}.zip \
    && unzip MagneticModel-${EOXMAGMOD_TAG_NAME}.zip \
    && mv MagneticModel-${EOXMAGMOD_TAG_NAME} MagneticModel \
    && mamba build MagneticModel/libcdf \
    && mamba build MagneticModel/qdipole \
    && mamba build purge \
    && mamba install --quiet --yes --use-local qdipole cdf \
    && /bin/bash -c 'source activate base && pip install --no-build-isolation MagneticModel/eoxmagmod' \
    && cd $HOME

# cleanup
RUN mamba remove --quiet --yes --force conda-build conda-verify
RUN mamba clean --all -f -y
RUN rm -fR $HOME/.cache/pip/ $HOME/build/

# Patch in diskcache
RUN mamba install -y diskcache

RUN /bin/bash -c 'source activate base && pip install viresclient'
# Copy the entrypoint script and set it
# NB: container must be supplied with $VIRES_TOKEN at runtime (e.g. via .env file)
COPY entrypoint.sh /root/entrypoint.sh
RUN chmod +x /root/entrypoint.sh
ENTRYPOINT ["/root/entrypoint.sh"]

# Fix for CDF installation location
ENV CDF_LIB=/opt/conda/lib/

# Add the dashboard code into the image
COPY src /root/dashboards

# Default command to run (might be replaced by docker-compose.yml)
# Publishes the dashboards at port 5006
# CMD panel serve /root/dashboards/vires-catalog.ipynb --allow-websocket-origin=* --warm --num-procs 1
