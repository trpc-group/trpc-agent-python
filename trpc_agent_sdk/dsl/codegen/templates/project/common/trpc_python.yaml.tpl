global:
  namespace: Development
  env_name: test
  container_name:
  local_ip:

server:
  app: {{ server_app }}
  server: {{ server_name }}
  bin_path: /usr/local/trpc/bin/
  conf_path: /usr/local/trpc/conf/
  data_path: /usr/local/trpc/data/
  worker_num: 1
  service:
    - name: {{ service_name }}
      ip: {{ service_host }}
      port: {{ service_port }}
      network: tcp
      protocol: {{ service_protocol }}
      timeout: 1000000

{% if is_http_mode or is_agui_mode %}
client:
  timeout: 1000
  namespace: Development
  service:
    - name: {{ service_name }}
      network: tcp
      timeout: 1000000
      protocol: http
{% endif %}

plugins:
  log:
    default:
      - writer: console
        level: info
      - writer: file
        level: info
        formatter: json
        writer_config:
          filename: ./trpc.log
          max_size: 10
          max_backups: 10
          max_age: 7
          compress: false

