FROM python:3.7-buster

ENV DEBIAN_FRONTEND=noninteractive
ENV VIRTUALENV_PYTHON=/usr/bin/python3.7

ARG DEV_PACKAGES="python3.7-dev build-essential libgeos-dev"

RUN apt-get update && \
  apt-get install --yes --no-install-recommends \
    python3-pip \
    python3-venv \
    virtualenv \
    ${DEV_PACKAGES} \
    zsh \
    postgresql-client

ENV SHELL /bin/zsh

RUN wget https://github.com/robbyrussell/oh-my-zsh/raw/master/tools/install.sh -O - | zsh || true

# keep container running until killed - For DEV use only
CMD [ "tail", "-f", "/dev/null" ]
