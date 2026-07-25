FROM python:3.12-slim

# Review commands are installed at image-build time. Runtime networking remains
# disabled by ContainerWorkspaceRuntime, so a Skill cannot install dependencies.
RUN pip install --no-cache-dir pytest ruff

