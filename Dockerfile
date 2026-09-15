# syntax=docker/dockerfile:1.8-labs
FROM archlinux:base-devel AS ombs_base

RUN pacman-key --init && \
    pacman -Sy --noconfirm archlinux-keyring && \
    pacman -Su --noconfirm --needed \
    python python-pip \
    arch-install-scripts rsync \
    aarch64-linux-gnu-gcc aarch64-linux-gnu-binutils aarch64-linux-gnu-glibc aarch64-linux-gnu-linux-api-headers \
    git sudo \
    android-tools openssh inetutils \
    parted

RUN sed -i "s/EUID == 0/EUID == -1/g" "$(which makepkg)"

RUN yes | pacman -Scc

RUN sed -i "s/SigLevel.*/SigLevel = Never/g" /etc/pacman.conf
RUN useradd -m -g users omarchy
RUN echo "omarchy ALL=(ALL) NOPASSWD: ALL" | tee /etc/sudoers.d/omarchy

ENV OMBOOTSTRAP_WRAPPED=DOCKER
ENV PATH=/app/bin:/src/local/bin:/usr/local/bin:/usr/bin
WORKDIR /src

ADD src pyproject.toml requirements.txt Makefile ./
RUN python3 -m venv /app
RUN --mount=type=bind,source=src/ombootstrap,target=/src/src/ombootstrap  /app/bin/pip3 install -e '.[all]'
RUN mkdir -p /app/local/bin
RUN ln -sfr /app/bin/ombootstrap_wrapper_su_helper /app/bin/wrapper_su_helper


FROM ombs_base AS ombs_with_src
WORKDIR /src
ADD . .

FROM ombs_with_src AS ombs_fully_installed
WORKDIR /
RUN /app/bin/python3 -c "from ombootstrap.distro import distro; print(distro.get_kupfer_local(arch=None,in_chroot=False).repos_config_snippet())" | tee -a /etc/pacman.conf


FROM ombs_fully_installed AS ombs_with_git
ADD --link .git .gitignore ./
