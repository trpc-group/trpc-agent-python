global:
  namespace: Development
  env_name: test

client:
  timeout: 1000
  namespace: Development
  service:
    - name: {{ service_name }}
      timeout: 1000000
      protocol: a2a
      target: ip://{{ service_host }}:{{ service_port }}
      network: tcp

plugins:
  log:
    default:
      - writer: console
        level: info
      - writer: file
        level: info
        formatter: json
        writer_config:
          filename: ./test_client.log
          max_size: 10
          max_backups: 10
          max_age: 7
          compress: false

