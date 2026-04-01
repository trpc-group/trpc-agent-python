# proxy pypi
--index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/
# private pypi
--extra-index-url https://mirrors.tencent.com/repository/pypi/tencent_pypi/simple/

wheel
setuptools
setproctitle

# add the requirements for trpc project
PyYAML>=5.1.0
aiohttp>=3.12.14
Cython>=0.29.21
six==1.15.0
python-snappy>=0.5.4
uvloop>=0.21.0
python-gflags>=3.1.2
numpy==2.2.5
python-dotenv>=1.0.1
shapely<3.0.0
pydantic==2.11.3
protobuf==5.29.5
multidict==6.4.4
netifaces==0.11.0

mcp==1.10.1
openai==1.93.2
google-genai==1.24.0
graphviz==0.21
opentelemetry-api==1.36.0
opentelemetry-exporter-otlp-proto-common==1.36.0
opentelemetry-exporter-otlp-proto-grpc==1.36.0
opentelemetry-exporter-otlp-proto-http==1.36.0
opentelemetry-instrumentation==0.57b0
opentelemetry-instrumentation-asgi==0.57b0
opentelemetry-instrumentation-fastapi==0.57b0
opentelemetry-instrumentation-logging==0.57b0
opentelemetry-instrumentation-requests==0.57b0
opentelemetry-instrumentation-sqlalchemy==0.57b0
opentelemetry-instrumentation-threading==0.57b0
opentelemetry-instrumentation-urllib3==0.57b0
opentelemetry-proto==1.36.0
opentelemetry-sdk==1.36.0
opentelemetry-semantic-conventions==0.57b0
opentelemetry-util-http==0.57b0

concurrent-log-asyncio==0.10.1
automaxprocs==2.0.0
trpc-pb==0.5.0a0
trpc>=0.9.6a0

langchain==0.3.26
langchain-openai==0.3.28

{% if has_service %}
trpc_naming_polaris>=0.6.1
trpc_metrics_runtime
{% if service_mode in ["a2a", "agui"] %}
trpc_fastapi
{% endif %}
{% if service_mode == "a2a" %}
trpc_a2a
{% endif %}
{% endif %}

trpc-agent[all]
