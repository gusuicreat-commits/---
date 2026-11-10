#!/usr/bin/env bash
# 从脚本位置定位仓库，允许在任意目录调用。
set -eu
cd -- "$(dirname -- "$0")/.."

cert_dir="${QC_CERT_DIR:-data/certs}"
uvicorn_bin="${QC_UVICORN_BIN:-.venv/bin/uvicorn}"
port="${PORT:-8443}"
lan_ip="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
san="DNS:localhost,IP:127.0.0.1"
if [ -n "$lan_ip" ]; then
    san="$san,IP:$lan_ip"
fi
mkdir -p "$cert_dir"
if [ ! -s "$cert_dir/key.pem" ] || [ ! -s "$cert_dir/cert.pem" ] ||
   ! openssl x509 -checkend 0 -noout -in "$cert_dir/cert.pem" >/dev/null 2>&1; then
    # 私钥只允许当前用户读取。
    umask 077
    openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
        -keyout "$cert_dir/key.pem" -out "$cert_dir/cert.pem" \
        -subj "/CN=qc-local" -addext "subjectAltName=$san"
fi
printf '大屏地址：https://localhost:%s/screen.html\n' "$port"
printf '手机地址：https://%s:%s/phone.html\n' "${lan_ip:-localhost}" "$port"
printf '%s\n' '手机第一次打开会提示证书不受信任，选择继续访问即可；手机和电脑需连同一 Wi-Fi'
exec "$uvicorn_bin" app.main:app --host 0.0.0.0 --port "$port" \
    --ssl-keyfile "$cert_dir/key.pem" --ssl-certfile "$cert_dir/cert.pem"
