# HTTPS 反向代理部署

当 Windows 服务端或 Docker 服务端需要被 iOS、Android 和异地 Windows 客户端访问时，使用 HTTPS 反向代理对外发布服务。应用监听本机或容器内端口，反向代理负责证书与公网入口。

完成代理后，在管理页的“公开服务地址”填写 `https://clipboard.example.com`，然后点击“检测 HTTPS 公开地址”。检测会由服务端请求 `<公开地址>/health` 并验证证书链。

## Caddy

`Caddyfile`：

```caddyfile
clipboard.example.com {
    reverse_proxy 127.0.0.1:9888
}
```

Docker 服务端部署在同一 Docker 网络时，将上游替换为服务名与容器端口：

```caddyfile
clipboard.example.com {
    reverse_proxy clipboard_dispatcher:8000
}
```

Caddy 自动申请和续期证书。域名的 A 或 AAAA 记录必须指向代理所在的公网地址，80 与 443 端口必须可被证书机构访问。

## Nginx

```nginx
server {
    listen 80;
    server_name clipboard.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name clipboard.example.com;

    ssl_certificate /etc/letsencrypt/live/clipboard.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/clipboard.example.com/privkey.pem;

    client_max_body_size 5m;

    location / {
        proxy_pass http://127.0.0.1:9888;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
```

`client_max_body_size` 必须不小于管理页设置的“最大上传字节”。图片默认最大 5 MB，服务端默认最大上传体积为 5 MB。

## 验收

```powershell
curl https://clipboard.example.com/health
```

预期 JSON 中包含 `"status":"healthy"`。随后在管理页保存公开服务地址，运行 HTTPS 检测，并用 iOS 或 Tasker 二维码完成一次设备配对。

## 常见问题

1. HTTPS 检测显示证书错误时，检查域名与证书 SAN 是否一致，证书链是否完整。
2. HTTPS 检测显示连接失败时，检查反向代理所在主机是否能访问配置的公开域名。部分网络不支持回环访问公网域名，此时从外部网络执行健康检查确认。
3. Tasker 拉取断开时，确认 Nginx 未启用 `proxy_buffering`，并保留足够长的 `proxy_read_timeout`。
