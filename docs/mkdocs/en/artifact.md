# Artifact Storage

Artifacts are versioned files produced or consumed by an agent. The artifact
API separates identity and metadata from the `Part` payload and supports both
temporary in-memory storage and durable local filesystem storage.

## Architecture

`ArtifactServiceABC` defines the asynchronous storage contract:

- `save_artifact` creates a new immutable version.
- `load_artifact` loads a specific version or the latest version.
- `list_artifact_keys` lists artifacts visible in a scope.
- `list_versions` and `list_artifact_versions` expose version history.
- `get_artifact_version` reads metadata without returning the payload.
- `delete_artifact` removes all versions of one artifact.

The SDK provides two implementations:

- `InMemoryArtifactService` is useful for tests and short-lived processes.
- `FileArtifactService` persists artifacts below a local directory and
  survives process restarts.

`FileArtifactService` stores each complete version as an independent JSON
record containing the serialized `Part` and `ArtifactVersion`. It writes and
syncs a temporary file before atomically replacing the final version record.
Exclusive reservation files prevent concurrent writers from selecting the
same version.

## Basic usage

```python
from google.genai.types import Part
from trpc_agent_sdk.abc import ArtifactId
from trpc_agent_sdk.artifacts import FileArtifactService

service = FileArtifactService("./artifact_data")
artifact_id = ArtifactId(
    app_name="reporting",
    user_id="alice",
    session_id="session-1",
    filename="report.txt",
)

version = await service.save_artifact(
    artifact_id=artifact_id,
    artifact=Part(text="Quarterly report"),
    metadata={"status": "draft"},
)
latest = await service.load_artifact(artifact_id=artifact_id)
specific = await service.load_artifact(
    artifact_id=artifact_id,
    version=version,
)
keys = await service.list_artifact_keys(artifact_id=artifact_id)
versions = await service.list_artifact_versions(artifact_id=artifact_id)
await service.delete_artifact(artifact_id=artifact_id)
```

Text, inline binary data, external file references, and artifact references are
preserved using the existing `google.genai.types.Part` representation.

## Scope and version semantics

An artifact identity consists of `app_name`, `user_id`, optional `session_id`,
and `filename`.

- A normal filename is session-scoped. It is visible only to the matching app,
  user, and session.
- A filename beginning with `user:` is user-scoped. The session ID is ignored
  for storage, and the artifact is visible across that user's sessions in the
  same app.
- `list_artifact_keys` returns the current session's artifacts together with
  user-scoped artifacts. With no session ID, it returns user-scoped artifacts
  only.
- Versions begin at `0` and increase monotonically for an artifact. Saves never
  modify existing versions.
- Omitting `version` loads the highest complete version. A missing artifact or
  version returns `None`.
- Deletion removes every version of the selected artifact but does not affect
  neighboring scopes or filenames.

Metadata is stored per version in `ArtifactVersion.custom_metadata`. MIME type,
creation timestamp, version number, and canonical `artifact://` URI are also
persisted per version.

## Persistence and concurrency

Create a new `FileArtifactService` with the same root directory to access data
after a process restart. Atomic replacement prevents readers from observing a
partially written record. Concurrent saves, including saves from separate
service instances sharing a filesystem, reserve distinct version numbers.

The backend is intended for a local disk or a filesystem that provides normal
atomic file-creation and rename semantics. It is not a distributed database;
coordinate destructive administration such as deleting a storage tree with
application writers.

## Security

Treat the storage root as application-owned data:

- app, user, session, and filename components cannot be empty, `.` or `..`, or
  contain path separators or NUL bytes;
- components are encoded before becoming directory names;
- every resolved path is checked to remain below the configured root;
- a symlink that redirects a generated path outside the root is rejected;
- artifact content and metadata are not encrypted by this service.

Use operating-system permissions to restrict access, avoid placing the root in
a web-served directory, and encrypt the underlying volume when artifacts are
sensitive. Only use a trusted filesystem; users who can modify the storage
tree directly can corrupt or remove records.

## Complete example

See the runnable
[`examples/artifact_service` README](../../../examples/artifact_service/README.md)
for a complete `LlmAgent`, tool, `Runner`, and `FileArtifactService` workflow.
The agent generates a Markdown report, saves it through a tool, and recreates
the filesystem service to verify that the report was persisted.
