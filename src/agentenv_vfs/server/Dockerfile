# agentenv-vfs: an agent-env env server (Python) driving a Mirage daemon (Node 24, Mirage's published npm packages).
FROM node:24.15-bookworm-slim AS node
FROM python:3.12-slim
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
WORKDIR /opt/agentenv-vfs
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund --loglevel=error
RUN pip install --no-cache-dir 'agentenv-framework-protocol[agent]>=0.1.652,<0.2' 'httpx>=0.27'
COPY server.py listing.py preload.ts cli.ts ./
ENV AGENTENV_VFS_HOME=/opt/agentenv-vfs MCP_PORT=18765
EXPOSE 18765
CMD ["python", "/opt/agentenv-vfs/server.py"]
