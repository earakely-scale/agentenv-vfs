FROM python:3.12-slim
RUN pip install --no-cache-dir 'agentenv-framework-protocol[agent]>=0.1.652,<0.2'
COPY server.py /opt/notes/server.py
ENV MCP_PORT=18765
EXPOSE 18765
CMD ["python", "/opt/notes/server.py"]
