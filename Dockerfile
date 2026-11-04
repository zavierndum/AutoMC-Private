# AutoMC image (devops/AutoMC_Workspace_Plan.md, WD-5). Build from this folder (AutoMC/):
#
#   docker build -t automc:0.8.0 .                                          with Visual-Parser (knowledge bases of PDFs and images;
#                                                                           torch and Nougat make it several GB larger)
#   docker build -t automc:0.8.0-lite --build-arg VISUAL_PARSER=no .        without it: everything else works
#   python docker/check_image.py automc:0.8.0                               refuses an image that holds restricted data, devops/,
#                                                                           a workspace, keys or a personal identifier
#
# Run it with docker compose (docker-compose.yml): `docker compose run --rm automc chat`. What the image never holds: the restricted
# facility packs (mounted at run time, at NETL only: docker-compose.netl.yml), the nuclear data (mounted), the keys (the keys file is
# mounted read only), your projects (mounted at /host). .dockerignore lets in only src/, library_snapshots/, docker/requirements.txt and
# CHANGELOG.md.
FROM condaforge/miniforge3:24.11.3-2

ARG VISUAL_PARSER=yes
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# OpenMC and the scientific stack from conda-forge, as in the automc environment (SETUP.md section 2)
RUN conda create -y -n automc -c conda-forge --override-channels \
        "python=3.11.16" "openmc=0.15.3=*nompi*" "numpy=2.4.6" "h5py=3.16.0" "pandas=3.0.6" "scipy=1.17.1" "lxml=6.1.3" \
        "uncertainties=3.2.3" matplotlib \
    && conda clean -afy
ENV PATH=/opt/conda/envs/automc/bin:$PATH

COPY docker/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Visual-Parser at AutoSAM's pin; torch from the cu130 index, which also covers the newest GPUs (AutoSAM's Dockerfile explains why).
# One layer, so the default torch wheels never stay behind in an earlier one.
RUN if [ "$VISUAL_PARSER" = "yes" ]; then \
        python -m pip install --no-cache-dir "visual-parser==2.2.4" \
        && python -m pip install --no-cache-dir --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu130; \
    fi

# A user without root: the files AutoMC writes in your mounted projects belong to you (docker-compose.yml passes your uid). The base
# image already has a user 1000 (Ubuntu's own); it gives way to automc.
RUN if id -u 1000 >/dev/null 2>&1; then userdel -r "$(id -nu 1000)" 2>/dev/null || userdel "$(id -nu 1000)"; fi \
    && useradd -m -u 1000 -U automc
COPY --chown=automc:automc src/ /opt/automc/src/
COPY --chown=automc:automc library_snapshots/ /opt/automc/library_snapshots/
COPY --chown=automc:automc CHANGELOG.md /opt/automc/

ENV AUTOMC_IN_CONTAINER=1 \
    AUTOMC_WORKSPACE=/host/workspace \
    AUTOMC_FACILITY_PACKS=/packs \
    AUTOMC_ENV_FILE=/config/keys.env \
    AUTOMC_USER_SETTINGS=/config/settings.yaml \
    OPENMC_CROSS_SECTIONS=/data/nuclear/cross_sections.xml \
    OLLAMA_HOST=http://host.docker.internal:11434 \
    MPLBACKEND=Agg \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER automc
WORKDIR /opt/automc/src
LABEL org.opencontainers.image.title="AutoMC" org.opencontainers.image.version="0.8.0" \
      org.opencontainers.image.description="An agent that builds, runs and reads OpenMC simulations (TRIGA Input Builder, job service)"

ENTRYPOINT ["python", "-m", "automc"]
CMD ["chat"]
