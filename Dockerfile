FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8

# Base OS packages and locale configuration (cleaned up in same layer)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        git \
        ca-certificates \
        python3 \
        python3-pip \
        jq \
        nodejs \
        npm \
        bash \
        xdg-utils \
        wl-clipboard \
        xclip \
        locales \
    && locale-gen en_US.UTF-8 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Astral uv
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/usr/local/bin" sh

# Install OpenCode and prepare for rootless execution
RUN curl -fsSL https://opencode.ai/install | bash \
    && mv /root/.opencode /opt/opencode \
    && chmod -R a+rX /opt/opencode \
    && ln -s /opt/opencode/bin/opencode /usr/local/bin/opencode

ENV PATH="/opt/opencode/bin:/usr/local/bin:${PATH}"
WORKDIR /workspace/project
ENTRYPOINT ["opencode"]