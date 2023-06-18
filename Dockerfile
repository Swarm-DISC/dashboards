FROM condaforge/mambaforge:22.11.1-4

ENV EOXMAGMOD_TAG_NAME=master

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
COPY wheels/spacepy-0.4.1-cp310-cp310-linux_x86_64.whl .
RUN /bin/bash -c 'source activate base && pip install spacepy-0.4.1-cp310-cp310-linux_x86_64.whl'

# (eoxmagmod) Magnetic Models and dependencies
RUN mkdir $HOME/build \
    && cd $HOME/build \
    && wget --no-verbose https://github.com/ESA-VirES/MagneticModel/archive/${EOXMAGMOD_TAG_NAME}.zip -O MagneticModel-${EOXMAGMOD_TAG_NAME}.zip \
    && unzip MagneticModel-${EOXMAGMOD_TAG_NAME}.zip \
    && mv MagneticModel-${EOXMAGMOD_TAG_NAME} MagneticModel \
    && mamba install --quiet --yes conda-build conda-verify \
    && mamba build MagneticModel/libcdf \
    && mamba build MagneticModel/qdipole \
    && mamba build purge \
    && mamba install --quiet --yes --use-local qdipole cdf \
    && mamba remove --quiet --yes --force conda-build conda-verify \
    && mamba clean --all -f -y \
    && /bin/bash -c 'source activate base && pip install --no-build-isolation MagneticModel/eoxmagmod' \
    && cd $HOME \
    && rm -fR $HOME/.cache/pip/ $HOME/build/

# Create user which runs the dashboards (instead of as root)
RUN useradd --create-home --shell /bin/bash panel-user
USER panel-user
WORKDIR /home/panel-user
# Fix for CDF installation location
ENV CDF_LIB=/opt/conda/lib/
